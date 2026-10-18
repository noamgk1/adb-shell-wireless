# Copyright (c) 2021 Jeff Irion and contributors
#
# This file is part of the adb-shell package.  It incorporates work
# covered by the following license notice:
#
#
#   Copyright 2014 Google Inc. All rights reserved.
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.

"""A class for creating a socket connection with the device and sending and receiving data.

* :class:`TcpTransport`

    * :meth:`TcpTransport.bulk_read`
    * :meth:`TcpTransport.bulk_write`
    * :meth:`TcpTransport.close`
    * :meth:`TcpTransport.connect`
    * :meth:`TcpTransport.upgrade_to_tls`

"""


import select
import socket
import ssl
import time

from .base_transport import BaseTransport
from ..exceptions import AdbTlsError, TcpTimeoutException
from ..tls import make_ssl_context


class TcpTransport(BaseTransport):
    """TCP connection object.

    Parameters
    ----------
    host : str
        The address of the device; may be an IP address or a host name
    port : int
        The device port to which we are connecting (default is 5555)

    Attributes
    ----------
    _connection : socket.socket, None
        A socket connection to the device
    _host : str
        The address of the device; may be an IP address or a host name
    _port : int
        The device port to which we are connecting (default is 5555)

    """
    def __init__(self, host, port=5555, tls_key_path=None):
        self._host = host
        self._port = port
        self._tls_key_path = tls_key_path

        self._connection = None
        self._tls_enabled = False
        self._nonblocking = False

    def close(self):
        """Close the socket connection.

        """
        if self._connection:
            try:
                self._connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

            self._connection.close()
            self._connection = None

        self._tls_enabled = False
        self._nonblocking = False

    def connect(self, transport_timeout_s):
        """Create a socket connection to the device.

        Parameters
        ----------
        transport_timeout_s : float, None
            Set the timeout on the socket instance

        """
        self._tls_enabled = False
        self._connection = socket.create_connection((self._host, self._port), timeout=transport_timeout_s)
        if transport_timeout_s:
            # Put the socket in non-blocking mode
            # https://docs.python.org/3/library/socket.html#socket.socket.settimeout
            self._connection.setblocking(False)
            self._nonblocking = True
        else:
            self._nonblocking = False

    def upgrade_to_tls(self, transport_timeout_s):
        """Upgrade the established socket connection to TLS.

        This is required by Android 11+ wireless debugging, where the device responds to
        the initial ``b'CNXN'`` message with ``b'STLS'``.

        Parameters
        ----------
        transport_timeout_s : float, None
            A timeout for the TLS handshake; if it is ``None``, then it will block until
            the handshake completes

        Raises
        ------
        adb_shell.exceptions.AdbTlsError
            The TLS handshake failed

        """
        ssl_context = make_ssl_context(self._tls_key_path)

        # The handshake cannot drive itself on a non-blocking socket, so restore blocking
        # behavior (with a timeout) for the duration of the handshake.
        if transport_timeout_s:
            self._connection.settimeout(transport_timeout_s)
        else:
            self._connection.setblocking(True)

        try:
            self._connection = ssl_context.wrap_socket(self._connection, server_side=False, do_handshake_on_connect=True)
        except (ssl.SSLError, socket.error) as exc:
            raise AdbTlsError('TLS handshake with {}:{} failed: {}'.format(self._host, self._port, exc))

        self._tls_enabled = True

        # Put the socket back the way `connect` left it
        if self._nonblocking:
            self._connection.setblocking(False)

    def bulk_read(self, numbytes, transport_timeout_s):
        """Receive data from the socket.

        Parameters
        ----------
        numbytes : int
            The maximum amount of data to be received
        transport_timeout_s : float, None
            When the timeout argument is omitted, ``select.select`` blocks until at least one file descriptor is ready. A time-out value of zero specifies a poll and never blocks.

        Returns
        -------
        bytes
            The received data

        Raises
        ------
        TcpTimeoutException
            Reading timed out.

        """
        if self._tls_enabled:
            return self._bulk_read_tls(numbytes, transport_timeout_s)

        readable, _, _ = select.select([self._connection], [], [], transport_timeout_s)
        if readable:
            return self._connection.recv(numbytes)

        msg = 'Reading from {}:{} timed out ({} seconds)'.format(self._host, self._port, transport_timeout_s)
        raise TcpTimeoutException(msg)

    def _bulk_read_tls(self, numbytes, transport_timeout_s):
        """Receive data from a TLS socket.

        ``select`` reports readability of the *underlying* socket, which does not line up
        with the availability of decrypted data:

        * The SSL layer may already hold decrypted data while the socket has nothing
          pending, in which case ``select`` would block despite data being available.
        * The socket may be readable without a complete TLS record having arrived, in
          which case ``recv`` raises :class:`ssl.SSLWantReadError` and must be retried.

        Parameters
        ----------
        numbytes : int
            The maximum amount of data to be received
        transport_timeout_s : float, None
            The total time to wait for data

        Returns
        -------
        bytes
            The received data

        Raises
        ------
        TcpTimeoutException
            Reading timed out.

        """
        msg = 'Reading from {}:{} timed out ({} seconds)'.format(self._host, self._port, transport_timeout_s)
        deadline = None if transport_timeout_s is None else time.time() + transport_timeout_s

        while True:
            # Decrypted data already buffered in the SSL layer
            if self._connection.pending():
                return self._connection.recv(numbytes)

            timeout = None if deadline is None else max(deadline - time.time(), 0)
            readable, _, _ = select.select([self._connection], [], [], timeout)
            if not readable:
                raise TcpTimeoutException(msg)

            try:
                return self._connection.recv(numbytes)
            except ssl.SSLWantReadError:
                # An incomplete TLS record; wait for the rest of it
                if deadline is not None and time.time() >= deadline:
                    raise TcpTimeoutException(msg)

    def bulk_write(self, data, transport_timeout_s):
        """Send data to the socket.

        Parameters
        ----------
        data : bytes
            The data to be sent
        transport_timeout_s : float, None
            When the timeout argument is omitted, ``select.select`` blocks until at least one file descriptor is ready. A time-out value of zero specifies a poll and never blocks.

        Returns
        -------
        int
            The number of bytes sent

        Raises
        ------
        TcpTimeoutException
            Sending data timed out.  No data was sent.

        """
        if self._tls_enabled:
            return self._bulk_write_tls(data, transport_timeout_s)

        _, writeable, _ = select.select([], [self._connection], [], transport_timeout_s)
        if writeable:
            return self._connection.send(data)

        msg = 'Sending data to {}:{} timed out after {} seconds. No data was sent.'.format(self._host, self._port, transport_timeout_s)
        raise TcpTimeoutException(msg)

    def _bulk_write_tls(self, data, transport_timeout_s):
        """Send data to a TLS socket.

        A writeable socket does not guarantee that the SSL layer can accept the write, so
        :class:`ssl.SSLWantWriteError` must be retried.  OpenSSL requires that the retry
        use the same buffer, which it does here.

        Parameters
        ----------
        data : bytes
            The data to be sent
        transport_timeout_s : float, None
            The total time to wait to send the data

        Returns
        -------
        int
            The number of bytes sent

        Raises
        ------
        TcpTimeoutException
            Sending data timed out.  No data was sent.

        """
        msg = 'Sending data to {}:{} timed out after {} seconds. No data was sent.'.format(self._host, self._port, transport_timeout_s)
        deadline = None if transport_timeout_s is None else time.time() + transport_timeout_s

        while True:
            timeout = None if deadline is None else max(deadline - time.time(), 0)
            _, writeable, _ = select.select([], [self._connection], [], timeout)
            if not writeable:
                raise TcpTimeoutException(msg)

            try:
                return self._connection.send(data)
            except ssl.SSLWantWriteError:
                if deadline is not None and time.time() >= deadline:
                    raise TcpTimeoutException(msg)
