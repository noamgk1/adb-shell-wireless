# Copyright (c) 2026 Jeff Irion and contributors
#
# This file is part of the adb-shell package.

"""TLS support for Android 11+ wireless debugging (the ``b'STLS'`` handshake).

When a device is in wireless-debugging mode it answers the initial ``b'CNXN'`` message
with ``b'STLS'``, demanding that the connection be upgraded to TLS.  On that path there
is **no token signing at all** -- the TLS client certificate *is* the authentication.

The certificate must wrap the same RSA public key that was registered with the device
during pairing (``adb pair``), which for the standard ``adb`` client means
``~/.android/adbkey``.  Pointing this at a freshly generated key will produce a
certificate the device rejects.

* :func:`make_ssl_context`

"""


import datetime
import logging
import os
import ssl
import tempfile

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from cryptography.x509.oid import NameOID

from . import exceptions

_LOGGER = logging.getLogger(__name__)

#: The key that the standard ``adb`` client pairs with
DEFAULT_ADBKEY_PATH = os.path.join(os.path.expanduser('~'), '.android', 'adbkey')

#: How long the generated certificates are valid for
_CERT_VALIDITY_DAYS = 365 * 10

#: Cache of generated certificates, keyed by the absolute key path.  Minting a certificate
#: requires an RSA signature, so this avoids repeating that work on every reconnect.
_CERT_CACHE = {}


def _generate_certificate(key_path):
    """Mint a self-signed X.509 certificate around the public key of an existing ADB key.

    Parameters
    ----------
    key_path : str
        Path to an unencrypted PKCS#8 PEM private key (i.e., an ``adbkey`` file)

    Returns
    -------
    bytes
        The certificate, PEM encoded

    Raises
    ------
    adb_shell.exceptions.AdbTlsError
        The key could not be read or parsed

    """
    try:
        with open(key_path, 'rb') as f:
            private_key = load_pem_private_key(f.read(), password=None)
    except IOError as exc:
        raise exceptions.AdbTlsError(
            "Could not read the ADB key '{}': {}.  Pair the device with `adb pair` first, or pass "
            "the path to the key that it was paired with.".format(key_path, exc)
        )
    except (ValueError, TypeError) as exc:
        raise exceptions.AdbTlsError("Could not parse the ADB key '{}': {}".format(key_path, exc))

    # The subject is not checked by `adbd`; it matches on the public key.
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, u'adb_shell')])
    now = datetime.datetime.now(datetime.timezone.utc)

    certificate = x509.CertificateBuilder().subject_name(
        name
    ).issuer_name(
        name
    ).public_key(
        private_key.public_key()
    ).serial_number(
        x509.random_serial_number()
    ).not_valid_before(
        now - datetime.timedelta(days=1)
    ).not_valid_after(
        now + datetime.timedelta(days=_CERT_VALIDITY_DAYS)
    ).sign(private_key, hashes.SHA256())

    return certificate.public_bytes(serialization.Encoding.PEM)


def make_ssl_context(key_path=None):
    """Create an :class:`ssl.SSLContext` that presents the ADB key as a client certificate.

    The device's own certificate is self-signed and cannot be verified, so verification is
    disabled; the security of the channel rests on the device having been paired, which
    means it only accepts the public key it was paired with.

    Note that only the *certificate* is written to a temporary file.  The ``keyfile``
    argument points directly at ``key_path``, so the private key is never copied to a new
    location on disk.

    Parameters
    ----------
    key_path : str, None
        Path to the ADB key that the device was paired with; defaults to
        :const:`DEFAULT_ADBKEY_PATH`

    Returns
    -------
    ssl.SSLContext
        A context configured for connecting to ``adbd``

    Raises
    ------
    adb_shell.exceptions.AdbTlsError
        The key could not be read, or the certificate could not be loaded

    """
    key_path = os.path.abspath(os.path.expanduser(key_path or DEFAULT_ADBKEY_PATH))

    if key_path not in _CERT_CACHE:
        _LOGGER.debug("Generating an ADB TLS certificate from '%s'", key_path)
        _CERT_CACHE[key_path] = _generate_certificate(key_path)

    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE

    # `load_cert_chain` only accepts file paths, so the certificate has to touch the disk.
    # It is read immediately, so the file can be removed as soon as the call returns.
    handle, cert_path = tempfile.mkstemp(prefix='adb_shell_', suffix='.pem')
    try:
        os.write(handle, _CERT_CACHE[key_path])
        os.close(handle)
        handle = None
        ssl_context.load_cert_chain(certfile=cert_path, keyfile=key_path)
    except (ssl.SSLError, IOError, OSError) as exc:
        raise exceptions.AdbTlsError("Could not load the ADB certificate or key '{}': {}".format(key_path, exc))
    finally:
        if handle is not None:
            os.close(handle)
        try:
            os.unlink(cert_path)
        except OSError:  # pragma: no cover
            pass

    return ssl_context
