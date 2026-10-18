adb-shell-wireless
==================

A fork of `adb_shell <https://github.com/JeffLIrion/adb_shell>`_ by Jeff Irion, adding
support for **Android 11+ Wireless Debugging** -- the ``b'STLS'`` TLS handshake -- to both
the synchronous and the asyncio implementations.

The package is still imported as ``adb_shell``, so existing code keeps working unchanged.

What this fork adds
-------------------

* Connecting to an already-paired device over Android 11+ Wireless Debugging, with both
  ``AdbDeviceTcp`` and ``AdbDeviceTcpAsync`` -- see `Wireless Debugging (Android 11+)`_.
* ``adb_shell.tls``, which builds the TLS client certificate from an existing ``adbkey``.
* ``upgrade_to_tls()`` on both TCP transports, with TLS-aware reads and writes.
* ``examples/wireless_debugging_getprop.py``, a runnable end-to-end example.

Pairing is deliberately **not** implemented: pair once with the standard ``adb`` client,
then connect from Python.  Everything else -- USB, plain TCP on port 5555, FileSync -- is
unchanged from upstream, and upstream's test suite passes.

Upstream documentation for the rest of the API is at https://adb-shell.readthedocs.io/
(it does not cover the wireless-debugging support added here).

Installation
------------

This fork is not published on PyPI.  Clone this repository -- the clone URL is on the
green *Code* button above -- and install from the clone:

.. code-block::

   cd adb-shell-wireless
   pip install -e ".[async]"

``pip install adb-shell`` installs the **upstream** package from PyPI, which does *not*
include wireless-debugging support.

Installing straight from a git URL also works:

.. code-block::

   pip install "adb-shell-wireless[async] @ git+<clone URL>"


Async
*****

The asyncio implementation requires the ``async`` extra, shown above.

Developed and tested on Python 3.12.  The asyncio TLS upgrade uses
``StreamWriter.start_tls()`` on Python 3.11+ and falls back to ``loop.start_tls()`` on
3.7--3.10; that fallback path has not been exercised against a device.


USB Support (Experimental)
**************************

.. code-block::

   pip install -e ".[usb]"

USB connections cannot be upgraded to TLS, so wireless debugging does not apply to them.


Example Usage
-------------

(Based on `androidtv/adb_manager.py <https://github.com/JeffLIrion/python-androidtv/blob/133063c8d6793a88259af405d6a69ceb301a0ca0/androidtv/adb_manager.py#L67>`_)

.. code-block:: python

   from adb_shell.adb_device import AdbDeviceTcp, AdbDeviceUsb
   from adb_shell.auth.sign_pythonrsa import PythonRSASigner

   # Load the public and private keys
   adbkey = 'path/to/adbkey'
   with open(adbkey) as f:
       priv = f.read()
   with open(adbkey + '.pub') as f:
        pub = f.read()
   signer = PythonRSASigner(pub, priv)

   # Connect
   device1 = AdbDeviceTcp('192.168.0.222', 5555, default_transport_timeout_s=9.)
   device1.connect(rsa_keys=[signer], auth_timeout_s=0.1)

   # Connect via USB (package must be installed via `pip install adb-shell[usb])`
   device2 = AdbDeviceUsb()
   device2.connect(rsa_keys=[signer], auth_timeout_s=0.1)

   # Send a shell command
   response1 = device1.shell('echo TEST1')
   response2 = device2.shell('echo TEST2')


Generate ADB Key Files
**********************

If you need to generate a key, you can do so as follows.

.. code-block:: python

  from adb_shell.auth.keygen import keygen

  keygen('path/to/adbkey')

Wireless Debugging (Android 11+)
--------------------------------

Android 11 introduced *Wireless Debugging*, in which the device requires the connection to
be upgraded to TLS -- the ``b'STLS'`` handshake -- instead of using the older
token-signing authentication.  ``adb_shell`` can connect to a device that has already been
paired.

Pairing itself is **not** implemented.  Pair once with the standard ``adb`` client:

.. code-block::

   adb pair 192.168.0.222:37419 123456

The pairing port and the 6-digit code are both shown on the device, under *Wireless
debugging* > *Pair device with pairing code*.

Finding the port
****************

The port used for pairing is **not** the port used for connecting, and the connect port
changes frequently -- it is reassigned whenever wireless debugging is toggled off and on,
and after a reboot.  Ask ``adb`` for the current one:

.. code-block::

   $ adb mdns services
   List of discovered mdns services
   adb-XXXXXXXXXXX-XXXXXX   _adb-tls-connect._tcp   192.168.0.222:43771

Use the ``_adb-tls-connect._tcp`` entry; an ``_adb-tls-pairing._tcp`` entry, if present, is
only good for pairing.

Connecting
**********

No ``rsa_keys`` are needed here: on this path the TLS client certificate *is* the
authentication.  The certificate is generated from the ADB key that the device was paired
with, which defaults to ``~/.android/adbkey``.

.. code-block:: python

   from adb_shell.adb_device import AdbDeviceTcp

   device = AdbDeviceTcp('192.168.0.222', 43771, default_transport_timeout_s=9.)
   device.connect()

   print(device.shell('getprop ro.product.model'))

If the device was paired using a key in a different location, pass it explicitly:

.. code-block:: python

   device = AdbDeviceTcp('192.168.0.222', 43771, tls_key_path='path/to/adbkey')

The certificate must wrap the *same* key that the device was paired with; any other key
will be rejected.

Retrying the first connection
*****************************

A device that has had no ADB client connected for a while drops the first connection or
two before it settles, raising ``AdbTlsError`` (the TLS handshake gets an unexpected EOF)
or ``AdbTimeoutError`` (the device never answers).  The ``adb`` command never appears to
suffer from this because its server retries internally.

So retry a few times rather than treating the first failure as fatal:

.. code-block:: python

   import time

   from adb_shell.adb_device import AdbDeviceTcp
   from adb_shell.exceptions import AdbTimeoutError, AdbTlsError, TcpTimeoutException

   TRANSIENT = (AdbTlsError, AdbTimeoutError, TcpTimeoutException, OSError)

   def connect_with_retries(host, port, retries=4):
       for attempt in range(retries):
           device = AdbDeviceTcp(host, port, default_transport_timeout_s=9.)
           try:
               device.connect()
               return device
           except TRANSIENT:
               device.close()
               if attempt == retries - 1:
                   raise
               time.sleep(0.5)

   device = connect_with_retries('192.168.0.222', 43771)
   print(device.shell('getprop ro.product.model'))

Async usage
***********

.. code-block:: python

   import asyncio

   from adb_shell.adb_device_async import AdbDeviceTcpAsync

   async def main():
       device = AdbDeviceTcpAsync('192.168.0.222', 43771, default_transport_timeout_s=9.)
       await device.connect()
       try:
           print(await device.shell('getprop ro.product.model'))
       finally:
           await device.close()

   asyncio.run(main())

Example script
**************

``examples/wireless_debugging_getprop.py`` discovers the current port over mDNS, connects
(retrying as described above), and prints the output of a shell command.  It exercises
either implementation:

.. code-block::

   python examples/wireless_debugging_getprop.py
   python examples/wireless_debugging_getprop.py --sync
   python examples/wireless_debugging_getprop.py --command "getprop ro.product.model"
   python examples/wireless_debugging_getprop.py --host 192.168.0.222 --port 43771
   python examples/wireless_debugging_getprop.py --retries 6

Run ``--help`` for the rest.  Note that a freshly started ``adb`` server needs a moment to
browse for mDNS services, so the script polls until the device appears.

Troubleshooting
***************

* The first attempt fails and a later one succeeds -- expected against a cold device; see
  `Retrying the first connection`_.
* ``ConnectionRefusedError`` -- the connect port has almost certainly changed.  Re-run
  ``adb mdns services`` and use the new one.
* ``AdbTlsError: Could not read the ADB key`` -- the key does not exist at that path.
  Pair the device first, or pass ``tls_key_path``.
* The connection completes but the device closes the stream -- the key is not the one the
  device was paired with.  Pair again, or point ``tls_key_path`` at the right key.
* The device responds with ``b'AUTH'`` rather than ``b'STLS'`` -- that port is not in TLS
  mode.  This is the legacy ``adb tcpip`` path (usually port 5555), which still needs
  ``rsa_keys`` as shown under `Example Usage`_.
* USB connections cannot be upgraded to TLS; ``AdbDeviceUsb`` raises
  ``NotImplementedError`` if a device asks it to.


License and credits
-------------------

``adb_shell`` was written by `Jeff Irion <https://github.com/JeffLIrion>`_ and originated
from `python-adb <https://github.com/google/python-adb>`_.  All credit for the library
belongs to them.

This fork is distributed under the same Apache License 2.0; see ``LICENSE``.  The only
functional change from upstream is the Android 11+ Wireless Debugging support described
above, which touches ``adb_shell/constants.py``, ``adb_shell/exceptions.py``,
``adb_shell/adb_device.py``, ``adb_shell/adb_device_async.py``, both TCP transports, both
base transports, and adds ``adb_shell/tls.py``.
