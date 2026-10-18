# Copyright (c) 2021 Jeff Irion and contributors
#
# This file is part of the adb-shell package.

"""A base class for transports used to communicate with a device.

* :class:`BaseTransportAsync`

    * :meth:`BaseTransportAsync.bulk_read`
    * :meth:`BaseTransportAsync.bulk_write`
    * :meth:`BaseTransportAsync.close`
    * :meth:`BaseTransportAsync.connect`

"""


from abc import ABC, abstractmethod


class BaseTransportAsync(ABC):
    """A base transport class.

    """

    @abstractmethod
    async def close(self):
        """Close the connection.

        """

    @abstractmethod
    async def connect(self, transport_timeout_s):
        """Create a connection to the device.

        Parameters
        ----------
        transport_timeout_s : float, None
            A connection timeout

        """

    @abstractmethod
    async def bulk_read(self, numbytes, transport_timeout_s):
        """Read data from the device.

        Parameters
        ----------
        numbytes : int
            The maximum amount of data to be received
        transport_timeout_s : float, None
            A timeout for the read operation

        Returns
        -------
        bytes
            The received data

        """

    @abstractmethod
    async def bulk_write(self, data, transport_timeout_s):
        """Send data to the device.

        Parameters
        ----------
        data : bytes
            The data to be sent
        transport_timeout_s : float, None
            A timeout for the write operation

        Returns
        -------
        int
            The number of bytes sent

        """

    async def upgrade_to_tls(self, transport_timeout_s):
        """Upgrade the established connection to TLS, as required by Android 11+ wireless debugging.

        Transports that cannot be upgraded (e.g., USB) leave this unimplemented.

        Parameters
        ----------
        transport_timeout_s : float, None
            A timeout for the TLS handshake

        Raises
        ------
        NotImplementedError
            This transport does not support TLS

        """
        raise NotImplementedError('{} does not support TLS (Android 11+ wireless debugging)'.format(type(self).__name__))
