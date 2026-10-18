#!/usr/bin/env python3
"""Connect to an Android 11+ device over Wireless Debugging and print ``getprop``.

The device must already be paired, e.g. with::

    adb pair <host>:<pairing-port> <6-digit-code>

This script only performs the everyday *connect*, which uses the ``b'STLS'`` handshake and
authenticates with the ADB key (``~/.android/adbkey``) as a TLS client certificate.

The wireless-debugging connect port changes frequently, so by default the current one is
discovered via mDNS instead of being hard-coded.

Examples
--------
::

    python examples/wireless_debugging_getprop.py
    python examples/wireless_debugging_getprop.py --sync
    python examples/wireless_debugging_getprop.py --host 192.168.3.25 --port 43771
    python examples/wireless_debugging_getprop.py --command "getprop ro.product.model"

"""

import argparse
import asyncio
import re
import subprocess
import sys
import time

from adb_shell.adb_device import AdbDeviceTcp
from adb_shell.adb_device_async import AdbDeviceTcpAsync
from adb_shell.exceptions import AdbTimeoutError, AdbTlsError, TcpTimeoutException
from adb_shell.tls import DEFAULT_ADBKEY_PATH

#: The mDNS service that Android advertises for wireless-debugging connections
MDNS_CONNECT_SERVICE = '_adb-tls-connect._tcp'

#: Errors that a cold device raises before it settles; see ``--retries``.
#:
#: When no ADB client has been connected for a while, ``adbd`` drops the first connection
#: or two: either during the TLS handshake (``AdbTlsError``) or by not answering at all
#: (``AdbTimeoutError``).  The ``adb`` client hides this because its server retries
#: internally, so a plain ``adb connect`` always appears to work first time.
TRANSIENT_ERRORS = (AdbTlsError, AdbTimeoutError, TcpTimeoutException, OSError)

#: Seconds to wait between connection attempts
RETRY_DELAY_S = 0.5


def _query_mdns():
    """Ask the ``adb`` client for the mDNS services it has discovered.

    Returns
    -------
    str
        The device's address, or ``None`` if no connect service was found
    int
        The device's current connect port, or ``None``

    """
    try:
        result = subprocess.run(
            ['adb', 'mdns', 'services'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20,
        )
    except OSError as exc:
        raise SystemExit("Could not run `adb mdns services` ({}).\n"
                         "Pass --host and --port explicitly instead.".format(exc))
    except subprocess.SubprocessError as exc:
        raise SystemExit("`adb mdns services` failed: {}".format(exc))

    for line in result.stdout.decode('utf-8', 'replace').splitlines():
        if MDNS_CONNECT_SERVICE in line:
            match = re.search(r'(\d{1,3}(?:\.\d{1,3}){3}):(\d+)', line)
            if match:
                return match.group(1), int(match.group(2))

    return None, None


def discover_endpoint(timeout_s=15.0):
    """Find the device's current wireless-debugging endpoint via mDNS.

    Shells out to the ``adb`` client, which already runs an mDNS responder, rather than
    pulling in a pure-Python mDNS dependency.

    A newly started ``adb`` server has not finished browsing for services yet, so the
    first query usually comes back empty; this polls until the device shows up.

    Parameters
    ----------
    timeout_s : float
        How long to wait for the device to be discovered

    Returns
    -------
    str
        The device's address
    int
        The device's current connect port

    """
    deadline = time.time() + timeout_s
    announced = False

    while True:
        host, port = _query_mdns()
        if host is not None:
            return host, port

        if time.time() >= deadline:
            break

        if not announced:
            print('[*] waiting for mDNS to discover the device...', file=sys.stderr)
            announced = True

        time.sleep(1.0)

    raise SystemExit(
        "No '{}' service found after {:.0f}s.\n"
        "Check that Wireless Debugging is switched on, that the phone is on this network, "
        "and that it has been paired with `adb pair`.\n"
        "You can also skip discovery with --host and --port.".format(MDNS_CONNECT_SERVICE, timeout_s)
    )


async def getprop_async(host, port, key_path, command, timeout_s, retries):
    """Run a command on the device using the asyncio implementation.

    Parameters
    ----------
    host : str
        The device's address
    port : int
        The device's wireless-debugging connect port
    key_path : str, None
        Path to the ADB key that the device was paired with
    command : str
        The shell command to run
    timeout_s : float
        Timeout for the transport operations
    retries : int
        How many times to attempt the connection; see :const:`TRANSIENT_ERRORS`

    Returns
    -------
    str
        The command's output

    """
    last_exc = None

    for attempt in range(1, retries + 1):
        device = AdbDeviceTcpAsync(host, port, default_transport_timeout_s=timeout_s, tls_key_path=key_path)
        try:
            await device.connect(transport_timeout_s=timeout_s, auth_timeout_s=timeout_s)
            try:
                return await device.shell(command)
            finally:
                await device.close()
        except TRANSIENT_ERRORS as exc:
            last_exc = exc
            await device.close()
            _report_retry(attempt, retries, exc)
            if attempt < retries:
                await asyncio.sleep(RETRY_DELAY_S)

    raise SystemExit('Could not connect to {}:{} after {} {}: {}'.format(
        host, port, retries, 'attempt' if retries == 1 else 'attempts', last_exc))


def _report_retry(attempt, retries, exc):
    """Tell the user that an attempt failed and that another one is coming.

    Parameters
    ----------
    attempt : int
        The attempt that just failed, 1-indexed
    retries : int
        The total number of attempts that will be made
    exc : Exception
        The error that the attempt raised

    """
    print('[*] attempt {}/{} failed ({}: {}){}'.format(
        attempt, retries, type(exc).__name__, str(exc)[:60],
        '; retrying' if attempt < retries else ''), file=sys.stderr)


def getprop_sync(host, port, key_path, command, timeout_s, retries):
    """Run a command on the device using the synchronous implementation.

    Parameters
    ----------
    host : str
        The device's address
    port : int
        The device's wireless-debugging connect port
    key_path : str, None
        Path to the ADB key that the device was paired with
    command : str
        The shell command to run
    timeout_s : float
        Timeout for the transport operations
    retries : int
        How many times to attempt the connection; see :const:`TRANSIENT_ERRORS`

    Returns
    -------
    str
        The command's output

    """
    last_exc = None

    for attempt in range(1, retries + 1):
        device = AdbDeviceTcp(host, port, default_transport_timeout_s=timeout_s, tls_key_path=key_path)
        try:
            device.connect(transport_timeout_s=timeout_s, auth_timeout_s=timeout_s)
            try:
                return device.shell(command)
            finally:
                device.close()
        except TRANSIENT_ERRORS as exc:
            last_exc = exc
            device.close()
            _report_retry(attempt, retries, exc)
            if attempt < retries:
                time.sleep(RETRY_DELAY_S)

    raise SystemExit('Could not connect to {}:{} after {} {}: {}'.format(
        host, port, retries, 'attempt' if retries == 1 else 'attempts', last_exc))


def main():
    """Parse the arguments, connect to the device and print the output."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--host', help='device address (default: discovered via mDNS)')
    parser.add_argument('--port', type=int, help='wireless-debugging connect port (default: discovered via mDNS)')
    parser.add_argument('--key', default=None,
                        help='ADB key the device was paired with (default: {})'.format(DEFAULT_ADBKEY_PATH))
    parser.add_argument('--command', default='getprop persist.sys.timezone', help='shell command to run (default: getprop persist.sys.timezone)')
    parser.add_argument('--timeout', type=float, default=10.0, help='transport timeout in seconds (default: 10)')
    parser.add_argument('--retries', type=int, default=4,
                        help='connection attempts before giving up; a cold device drops the first '
                             'one or two (default: 4)')
    parser.add_argument('--sync', action='store_true', help='use the synchronous implementation')
    args = parser.parse_args()

    # Windows consoles default to a legacy codepage (cp1255, cp1252, ...) that cannot
    # encode everything `getprop` returns, which would otherwise raise UnicodeEncodeError
    # after the command has already run successfully.
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    if args.host and args.port:
        host, port = args.host, args.port
    else:
        host, port = discover_endpoint()
        print('[*] discovered {}:{} via mDNS'.format(host, port), file=sys.stderr)

    which = 'sync' if args.sync else 'async'
    print('[*] connecting to {}:{} ({}, key={})'.format(host, port, which, args.key or DEFAULT_ADBKEY_PATH),
          file=sys.stderr)

    if args.sync:
        output = getprop_sync(host, port, args.key, args.command, args.timeout, args.retries)
    else:
        output = asyncio.run(getprop_async(host, port, args.key, args.command, args.timeout, args.retries))

    print('[*] connected; output of `{}`:'.format(args.command), file=sys.stderr)
    print(output)


if __name__ == '__main__':
    main()
