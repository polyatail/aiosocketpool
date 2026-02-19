import asyncio
import contextlib
import errno
import logging
import platform
import select
import socket
import time
from typing import Optional, Type, AsyncGenerator, Any, cast
import weakref


def can_use_kqueue():
    # kqueue doesn't work on OS X 10.6 and below
    if not hasattr(select, "kqueue"):
        return False

    if platform.system() == "Darwin" and platform.mac_ver()[0] < "10.7":
        return False

    return True


def is_connected(sock: socket.socket):
    """Determine if a `socket.socket` object is actually connected.

    Notes
    -----
    This method tries to determine whether a socket is connected in a platform-agnostic way. It is
    copied verbatim from `https://github.com/benoitc/socketpool/blob/master/socketpool/util.py`.

    Returns
    -------
    `bool`
    """
    try:
        fno = sock.fileno()
    except socket.error as e:
        if e.errno == errno.EBADF:
            return False
        raise

    try:
        if hasattr(select, "epoll"):
            ep = select.epoll()
            ep.register(fno, select.EPOLLOUT | select.EPOLLIN)  # type: ignore
            events = ep.poll(0)
            for fd, ev in events:
                if fno == fd and (ev & select.EPOLLOUT or ev & select.EPOLLIN):  # type: ignore
                    ep.unregister(fno)
                    return True
            ep.unregister(fno)
        elif hasattr(select, "poll"):
            p = select.poll()
            p.register(fno, select.POLLOUT | select.POLLIN)  # type: ignore
            events = p.poll(0)
            for fd, ev in events:
                if fno == fd and (ev & select.POLLOUT or ev & select.POLLIN):  # type: ignore
                    p.unregister(fno)
                    return True
            p.unregister(fno)
        elif can_use_kqueue():
            sel = cast(Any, select)
            kq = sel.kqueue()
            events = [
                sel.kevent(fno, sel.KQ_FILTER_READ, sel.KQ_EV_ADD),
                sel.kevent(fno, sel.KQ_FILTER_WRITE, sel.KQ_EV_ADD),
            ]
            kq.control(events, 0)
            kevents = kq.control(None, 4, 0)
            for ev in kevents:
                if ev.ident == fno:
                    if ev.flags & sel.KQ_EV_ERROR:
                        return False
                    else:
                        return True

            events = [
                sel.kevent(fno, sel.KQ_FILTER_READ, sel.KQ_EV_DELETE),
                sel.kevent(fno, sel.KQ_FILTER_WRITE, sel.KQ_EV_DELETE),
            ]
            kq.control(events, 0)
            kq.close()
            return True
        else:
            r, _, _ = select.select([fno], [], [], 0)
            if not r:
                return True

    except IOError:
        pass
    except (ValueError, select.error):
        pass

    return False


class SocketPoolException(Exception):
    pass


class BaseConnector(object):
    def __init__(self, pool=None):
        self._connected = False
        self._connected_at = -1

        # every connector must accept pool as a kwarg
        self._pool = pool

    def __del__(self):
        self.release()

    def release(self):
        if self._pool is not None:
            if self._connected:
                self._pool.release_connection(self)
            else:
                self._pool = None

    async def connect(self) -> bool:
        self._connected = True
        self._connected_at = time.time()

        return True

    def get_lifetime(self) -> float:
        return self._connected_at

    def invalidate(self):
        self._connected = False
        self._connected_at = -1

    def matches(self, **match_options) -> bool:
        raise NotImplementedError

    def is_connected(self) -> bool:
        raise NotImplementedError


class AsyncTcpConnector(BaseConnector):
    def __init__(self, host: str, port: int, pool=None, timeout: Optional[int] = None):
        """Asyncio TCP socket connection factory.

        Parameters
        ----------
        host : `str`
        port : `int`
        pool : `AsyncConnectionPool`
        timeout : `int`
            Time (in seconds) before giving up on connect, send, and receive operations.
        """
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setblocking(False)

        self.host = host
        self.port = port
        self.timeout = timeout

        super().__init__(pool=pool)

    async def connect(self) -> bool:
        """Establish a connection to the remote host.

        Returns
        -------
        `True` if the connection was successfully established, raises if not.
        """
        if __debug__:
            logging.debug(f"AsyncTcpConnector: new connection to {self.host}:{self.port}")

        loop = asyncio.get_event_loop()

        await asyncio.wait_for(
            loop.sock_connect(self._socket, (self.host, self.port)), self.timeout
        )

        if not is_connected(self._socket):
            raise SocketPoolException(f"Connection to {self.host}:{self.port} failed")

        await super().connect()

        return True

    def matches(self, **match_options) -> bool:
        """Determine if this connector matches the specified parameters.

        Returns
        -------
        `True` if the given host and port match this connector, `False` otherwise.
        """
        target_host = match_options.get("host")
        target_port = match_options.get("port")

        return target_host == self.host and target_port == self.port

    def is_connected(self) -> bool:
        """Determine if the socket held by this connector is actually connected.

        Uses the platform-agnostic `is_connected` method defined above to determine if the socket
        is actually connected, and hasn't, e.g., timed out.

        Returns
        -------
        `bool`
        """
        if self._connected:
            return is_connected(self._socket)

        return False

    def invalidate(self):
        """Close the socket held by this connector."""
        self._socket.close()
        super().invalidate()

    async def sendall(self, data: bytes):
        """Send all given data to the remote host.

        See the documentation for `socket.socket.sendall` for more information.

        Parameters
        ----------
        data : `bytes`

        Returns
        -------
        `None` on success
        """
        loop = asyncio.get_event_loop()

        return await asyncio.wait_for(loop.sock_sendall(self._socket, data), self.timeout)

    async def recv(self, size: int = 1024) -> bytes:
        """Receive some data from the remote host.

        See the documentation for `socket.socket.recv` for more information.

        Parameters
        ----------
        size : `int`
            The maximum amount of data to receive.

        Returns
        -------
        `bytes`
        """
        loop = asyncio.get_event_loop()

        return await asyncio.wait_for(loop.sock_recv(self._socket, size), self.timeout)

    async def recv_exactly(self, size: int) -> bytes:
        """Receive an exact amount of data from the remote host.

        Parameters
        ----------
        size : `int`
            The exact amount of data to receive before returning.

        Notes
        -----
        Unlike `recv`, this method will continuously receive data from the host and add it to a
        buffer until it contains the desired amount of data. This method will receive no more (and
        no less) than `size` bytes of data before returning.

        Returns
        -------
        `bytes`
        """
        data = b""

        while True:
            data_remaining = size - len(data)
            buf = await self.recv(4096 if data_remaining > 4096 else data_remaining)

            if not buf:
                break

            data += buf

            if len(data) >= size:
                break

        if len(data) != size:
            raise SocketPoolException(f"Expected {size}, got {len(data)}")

        return data


class IterablePriorityQueue(asyncio.PriorityQueue):
    def __iter__(self):
        return self

    def __next__(self):
        try:
            result = self.get_nowait()
        except asyncio.QueueEmpty:
            raise StopIteration

        return result

    next = __next__


class AsyncConnectionPool(object):
    """Create a pool of connections, anticipating use in an async/await context.

    Maintains a pool of `max_size` connections for approximately `max_lifetime`. New connections are
    created as needed. Does not block if the user requests more than `max_size` concurrent
    connections: `max_size` is only a limit on the number of connections we'll keep in the pool, not
    a limit on concurrency.

    Parameters
    ----------
    factory : `AsyncTcpConnector` or similar
    max_lifetime : `int`
        Time (in seconds) that a connection is to be kept in the pool before being destroyed.
    max_size : `int`
        Maximum number of connections to keep in the pool.
    options : `dict`
        Keyword arguments to be passed to the connection factory.
    reap_connections : `bool`
        If True, start a background task to destroy connections that are too old.
    reap_delay : `int`
        Time (in seconds) between loops of connection reaper.
    """

    def __init__(
        self,
        factory: Type[BaseConnector],
        max_lifetime: int = 600,
        max_size: int = 10,
        options=None,
        reap_connections: bool = True,
        reap_delay: int = 1,
    ):
        self.factory = factory
        self.max_lifetime = max_lifetime
        self.max_size = max_size
        self.options = options or {}

        self.pool = IterablePriorityQueue(max_size)
        self.connections: weakref.WeakSet[BaseConnector] = weakref.WeakSet()

        self._reaper_task = None

        if reap_connections:
            self.reap_delay = reap_delay
            self.start_reaper()

    def __del__(self):
        self.stop_reaper()

    def too_old(self, conn: BaseConnector) -> bool:
        return time.time() - conn.get_lifetime() > self.max_lifetime

    def start_reaper(self):
        loop = asyncio.get_event_loop()

        self._reaper_task = loop.create_task(self._reaper_loop())
        self.ensure_reaper_started()

    def stop_reaper(self):
        if self._reaper_task is not None:
            self._reaper_task.cancel()

    def ensure_reaper_started(self):
        if self._reaper_task.cancelled() or self._reaper_task.done():
            self.start_reaper()

    def reap_old_connections(self):
        current_pool_size = self.pool.qsize()

        for priority, candidate in self.pool:
            current_pool_size -= 1

            if not self.too_old(candidate):
                try:
                    self.pool.put_nowait((priority, candidate))
                except asyncio.QueueFull:
                    self._reap_connection(candidate)
            else:
                self._reap_connection(candidate)

            if current_pool_size == 0:
                break

    @staticmethod
    def _reap_connection(conn: BaseConnector):
        if conn.is_connected():
            conn.invalidate()

    async def _reaper_loop(self):
        try:
            while True:
                self.reap_old_connections()
                await asyncio.sleep(self.reap_delay)
        except asyncio.CancelledError:
            pass

    @property
    def size(self) -> int:
        return self.pool.qsize()

    def reap_pool(self) -> int:
        """Reap all idle connectors currently in the pool.

        Notes
        -----
        Active connectors, that have been returned by calling get(), will not be in the pool. They
        are held as weak references in AsyncConnectionPool.connections. To reap all connections
        including those currently active, call reap_all().

        Returns
        -------
        `int`, the number of connectors reaped.
        """
        current_pool_size = self.pool.qsize()

        for priority, conn in self.pool:
            self._reap_connection(conn)

        return current_pool_size

    def reap_all(self) -> int:
        """Reap all connectors, including those active and idle.

        Returns
        -------
        `int`, the number of connectors reaped.
        """
        connections = list(self.connections)

        for conn in connections:
            self._reap_connection(conn)

        return len(connections)

    def release_connection(self, conn: BaseConnector):
        """Place a connector back in the pool or reap it if we cannot.

        Parameters
        ----------
        conn : `BaseConnector`

        Notes
        -----
        If a connector is too old, or we are going to exceed `max_size`, drop it.
        """
        if self._reaper_task is not None:
            self.ensure_reaper_started()

        current_pool_size = self.pool.qsize()

        if current_pool_size < self.max_size:
            if conn.is_connected() and not self.too_old(conn):
                try:
                    self.pool.put_nowait((conn.get_lifetime(), conn))
                except asyncio.QueueFull:
                    self._reap_connection(conn)
            else:
                self._reap_connection(conn)
        else:
            self._reap_connection(conn)

    async def get(self, **options) -> BaseConnector:
        """Get a connector matching the given options, creating one if necessary.

        Notes
        -----
        This method will call `BaseConnector.matches(**options)` on every connector in the pool to
        determine if a connection already exists matching the given parameters. If one does not, a
        new `BaseConnector` will be created with the given `options` as keyword arguments.

        Returns
        -------
        `BaseConnector`
        """
        options.update(self.options)
        options["pool"] = self

        found = None
        unmatched = []

        current_pool_size = self.pool.qsize()

        # first let's try to find a matching one from pool
        if current_pool_size:
            for priority, candidate in self.pool:
                current_pool_size -= 1

                # drop connections if they are too old
                if self.too_old(candidate):
                    self._reap_connection(candidate)
                    continue

                matches = candidate.matches(**options)

                if not matches:
                    # put it back in the pool if it doesn't match
                    unmatched.append((priority, candidate))
                else:
                    # return match that is still connected
                    if candidate.is_connected():
                        found = candidate
                        break
                    else:
                        # drop if disconnected
                        self._reap_connection(candidate)

                if current_pool_size == 0:
                    break

        # put back any connections that didn't match
        if unmatched:
            for priority, candidate in unmatched:
                try:
                    self.pool.put_nowait((priority, candidate))
                except asyncio.QueueFull:
                    self._reap_connection(candidate)

        # we found a match, so we'll return it
        if found is not None:
            return found

        # we did not find a match, so we'll create a new connection
        new_item = self.factory(**options)
        await new_item.connect()

        # check that we're actually connected before returning
        if new_item.is_connected():
            self.connections.add(new_item)
            return new_item

        # the connection factory should raise if the connection fails, so we should never get to
        # this point. we'll raise anyway just to make sure.
        raise SocketPoolException("Connection factory failed to connect and did not raise")

    @contextlib.asynccontextmanager
    async def connection(self, **options) -> AsyncGenerator[BaseConnector, None]:
        conn = await self.get(**options)

        try:
            yield conn
        finally:
            self.release_connection(conn)
