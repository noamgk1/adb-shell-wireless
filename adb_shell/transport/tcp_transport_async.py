# Copyright (c) 2021 Jeff Irion and contributors
#
# This file is part of the adb-shell package.

"""A class for creating a socket connection with the device and sending and receiving data.

* :class:`TcpTransportAsync`

    * :meth:`TcpTransportAsync.bulk_read`
    * :meth:`TcpTransportAsync.bulk_write`
    * :meth:`TcpTransportAsync.close`
    * :meth:`TcpTransportAsync.connect`
    * :meth:`TcpTransportAsync.upgrade_to_tls`

"""


import asyncio
import ssl

import async_timeout

from .base_transport_async import BaseTransportAsync
from ..exceptions import AdbTlsError, TcpTimeoutException
from ..tls import make_ssl_context


class TcpTransportAsync(BaseTransportAsync):
    """TCP connection object.

    Parameters
    ----------
    host : str
        The address of the device; may be an IP address or a host name
    port : int
        The device port to which we are connecting (default is 5555)

    Attributes
    ----------
    _host : str
        The address of the device; may be an IP address or a host name
    _port : int
        The device port to which we are connecting (default is 5555)
    _reader : StreamReader, None
        Object for reading data from the socket
    _writer : StreamWriter, None
        Object for writing data to the socket

    """
    def __init__(self, host, port=5555, tls_key_path=None):
        self._host = host
        self._port = port
        self._tls_key_path = tls_key_path

        self._reader = None
        self._writer = None

    async def close(self):
        """Close the socket connection.

        """
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except OSError:
                pass

        self._reader = None
        self._writer = None

    async def connect(self, transport_timeout_s):
        """Create a socket connection to the device.

        Parameters
        ----------
        transport_timeout_s : float, None
            Timeout for connecting to the socket; if it is ``None``, then it will block until the operation completes

        """
        try:
            async with async_timeout.timeout(transport_timeout_s):
                self._reader, self._writer = await asyncio.open_connection(self._host, self._port)
        except asyncio.TimeoutError as exc:
            msg = 'Connecting to {}:{} timed out ({} seconds)'.format(self._host, self._port, transport_timeout_s)
            raise TcpTimeoutException(msg) from exc

    async def upgrade_to_tls(self, transport_timeout_s):
        """Upgrade the established connection to TLS.

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
        TcpTimeoutException
            The TLS handshake timed out.

        """
        ssl_context = make_ssl_context(self._tls_key_path)

        try:
            async with async_timeout.timeout(transport_timeout_s):
                if hasattr(self._writer, 'start_tls'):
                    # Python 3.11+
                    await self._writer.start_tls(ssl_context)
                else:
                    await self._start_tls_legacy(ssl_context)
        except asyncio.TimeoutError as exc:
            msg = 'TLS handshake with {}:{} timed out ({} seconds)'.format(self._host, self._port, transport_timeout_s)
            raise TcpTimeoutException(msg) from exc
        except ssl.SSLError as exc:
            raise AdbTlsError('TLS handshake with {}:{} failed: {}'.format(self._host, self._port, exc)) from exc

    async def _start_tls_legacy(self, ssl_context):
        """Upgrade the connection to TLS on Python versions without ``StreamWriter.start_tls``.

        ``StreamWriter.start_tls`` was added in Python 3.11.  Before that, the upgrade has
        to go through ``loop.start_tls`` and the resulting transport has to be patched into
        the existing reader and writer by hand.

        Parameters
        ----------
        ssl_context : ssl.SSLContext
            The context with which the connection will be upgraded

        """
        loop = asyncio.get_event_loop()
        transport = self._writer.transport
        protocol = transport.get_protocol()

        new_transport = await loop.start_tls(transport, protocol, ssl_context)

        self._reader._transport = new_transport  # pylint: disable=protected-access
        self._writer._transport = new_transport  # pylint: disable=protected-access

    async def bulk_read(self, numbytes, transport_timeout_s):
        """Receive data from the socket.

        Parameters
        ----------
        numbytes : int
            The maximum amount of data to be received
        transport_timeout_s : float, None
            Timeout for reading data from the socket; if it is ``None``, then it will block until the read operation completes

        Returns
        -------
        bytes
            The received data

        Raises
        ------
        TcpTimeoutException
            Reading timed out.

        """
        try:
            async with async_timeout.timeout(transport_timeout_s):
                return await self._reader.read(numbytes)
        except asyncio.TimeoutError as exc:
            msg = 'Reading from {}:{} timed out ({} seconds)'.format(self._host, self._port, transport_timeout_s)
            raise TcpTimeoutException(msg) from exc

    async def bulk_write(self, data, transport_timeout_s):
        """Send data to the socket.

        Parameters
        ----------
        data : bytes
            The data to be sent
        transport_timeout_s : float, None
            Timeout for writing data to the socket; if it is ``None``, then it will block until the write operation completes

        Returns
        -------
        int
            The number of bytes sent

        Raises
        ------
        TcpTimeoutException
            Sending data timed out.  No data was sent.

        """
        try:
            self._writer.write(data)
            async with async_timeout.timeout(transport_timeout_s):
                await self._writer.drain()
                return len(data)
        except asyncio.TimeoutError as exc:
            msg = 'Sending data to {}:{} timed out after {} seconds. No data was sent.'.format(self._host, self._port, transport_timeout_s)
            raise TcpTimeoutException(msg) from exc
