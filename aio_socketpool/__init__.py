import asyncio
import contextlib
import errno
import logging
import platform
import random
import select
import socket
import time
import weakref

import async_timeout


def can_use_kqueue():
    # kqueue doesn't work on OS X 10.6 and below
    if not hasattr(select, "kqueue"):
        return False

    if platform.system() == "Darwin" and platform.mac_ver()[0] < "10.7":
        return False

    return True


def is_connected(sock):
    try:
        fno = sock.fileno()
    except socket.error as e:
        if e[0] == errno.EBADF:
            return False
        raise

    try:
        if hasattr(select, "epoll"):
            ep = select.epoll()
            ep.register(fno, select.EPOLLOUT | select.EPOLLIN)
            events = ep.poll(0)
            for fd, ev in events:
                if fno == fd and (ev & select.EPOLLOUT or ev & select.EPOLLIN):
                    ep.unregister(fno)
                    return True
            ep.unregister(fno)
        elif hasattr(select, "poll"):
            p = select.poll()
            p.register(fno, select.POLLOUT | select.POLLIN)
            events = p.poll(0)
            for fd, ev in events:
                if fno == fd and (ev & select.POLLOUT or ev & select.POLLIN):
                    p.unregister(fno)
                    return True
            p.unregister(fno)
        elif can_use_kqueue():
            kq = select.kqueue()
            events = [
                select.kevent(fno, select.KQ_FILTER_READ, select.KQ_EV_ADD),
                select.kevent(fno, select.KQ_FILTER_WRITE, select.KQ_EV_ADD),
            ]
            kq.control(events, 0)
            kevents = kq.control(None, 4, 0)
            for ev in kevents:
                if ev.ident == fno:
                    if ev.flags & select.KQ_EV_ERROR:
                        return False
                    else:
                        return True

            events = [
                select.kevent(fno, select.KQ_FILTER_READ, select.KQ_EV_DELETE),
                select.kevent(fno, select.KQ_FILTER_WRITE, select.KQ_EV_DELETE),
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

    We will maintain a pool of `max_size` connections for approximately `max_lifetime`. New
    connections will be created as needed. We will not block if the user requests more than
    `max_size` concurrent connections: that is only a limit on the number of connections we'll keep
    in the pool, not a limit on concurrency.

    Parameters
    ----------
    factory : `AsyncTcpConnector` or similar
    max_lifetime : `int`
        Time (in seconds) that a connection is to be kept in the pool before being destroyed.
    max_size : `int`
        Maximum number of connections to keep in the pool.
    options : `dict`
        Keyword arguments to be passed to the connection factory.
    reap_connection : `bool`
        If True, start a background task to destroy connections that are too old.
    reap_delay : `int`
        Time (in seconds) between loops of connection reaper.
    """

    def __init__(
        self,
        factory,
        max_lifetime=600.0,
        max_size=10,
        options=None,
        reap_connections=True,
        reap_delay=1,
    ):
        self.factory = factory
        self.max_lifetime = max_lifetime
        self.max_size = max_size
        self.options = options or {}

        self.pool = IterablePriorityQueue(max_size)
        self.connections = weakref.WeakSet()

        self._reaper_task = None

        if reap_connections:
            self.reap_delay = reap_delay
            self.start_reaper()

    def __del__(self):
        self.stop_reaper()

    def too_old(self, conn):
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

    def _reap_connection(self, conn):
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
    def size(self):
        return self.pool.qsize()

    def reap_pool(self):
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

    def reap_all(self):
        """Reap all connectors, including those active and idle.

        Returns
        -------
        `int`, the number of connectors reaped.
        """
        connections = list(self.connections)

        for conn in connections:
            self._reap_connection(conn)

        return len(connections)

    def release_connection(self, conn):
        """Place a connector back in the pool or reap it if we cannot.

        Notes
        -----
        If a connector is too old, or we are going to exceed max_size, drop it.
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

    async def get(self, **options):
        """Get a connector matching the given options, creating one if necessary."""
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
        raise Exception("Connection factory failed to connect and did not raise")

    @contextlib.asynccontextmanager
    async def connection(self, **options):
        conn = await self.get(**options)

        try:
            yield conn
        finally:
            self.release_connection(conn)


class AsyncTcpConnector(object):
    def __init__(self, host, port, pool=None, timeout=None):
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
        self._socket.setblocking(0)

        self.host = host
        self.port = port
        self.timeout = timeout

        self._connected = False
        self._connected_at = -1
        self._pool = pool

    def __del__(self):
        self.release()

    async def connect(self):
        if __debug__:
            logging.debug(f"AsyncTcpConnector: new connection to {self.host}:{self.port}")

        loop = asyncio.get_event_loop()

        with async_timeout.timeout(self.timeout):
            result = await loop.sock_connect(self._socket, (self.host, self.port))

        self._connected = True
        self._connected_at = time.time()

        return result

    def matches(self, **match_options):
        """Return True if our socket is connected to the specified host and port."""
        target_host = match_options.get("host")
        target_port = match_options.get("port")

        return target_host == self.host and target_port == self.port

    def is_connected(self):
        if self._connected:
            return is_connected(self._socket)

        return False

    def get_lifetime(self):
        return self._connected_at

    def invalidate(self):
        self._socket.close()
        self._connected = False
        self._connected_at = -1

    def release(self):
        if self._pool is not None:
            if self._connected:
                self._pool.release_connection(self)
            else:
                self._pool = None

    async def sendall(self, *args):
        loop = asyncio.get_event_loop()

        with async_timeout.timeout(self.timeout):
            return await loop.sock_sendall(self._socket, *args)

    async def recv(self, size=1024):
        loop = asyncio.get_event_loop()

        with async_timeout.timeout(self.timeout):
            return await loop.sock_recv(self._socket, size)

    async def recv_exactly(self, size):
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
            raise Exception(f"Expected {size}, got {len(data)}")

        return data
