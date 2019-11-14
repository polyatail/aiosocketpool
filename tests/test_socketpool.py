import asyncio
import struct
import random

import pytest

from aio_socketpool import AsyncConnectionPool, AsyncTcpConnector


@pytest.mark.asyncio
async def test_basic_function(tcp_server):
    server, port = tcp_server

    pool = AsyncConnectionPool(
        factory=AsyncTcpConnector, reap_connections=False, max_lifetime=10, max_size=10
    )

    conn = await pool.get(host="127.0.0.1", port=port)

    send = await conn.sendall(b"hello, world!")
    assert send is None

    reply = await conn.recv()
    assert reply == b"hello, world!"

    assert pool.size == 0
    assert len(pool.connections) == 1

    conn.release()

    assert pool.size == 1
    assert len(pool.connections) == 1

    with pytest.raises(Exception):
        conn = await pool.get(host="127.0.0.1", port=port + 1)


@pytest.mark.asyncio
async def test_reaper(tcp_server):
    server, port = tcp_server

    pool = AsyncConnectionPool(
        factory=AsyncTcpConnector, reap_connections=True, max_lifetime=1, max_size=10
    )

    conn1 = await pool.get(host="127.0.0.1", port=port)
    conn2 = await pool.get(host="127.0.0.1", port=port)
    conn3 = await pool.get(host="127.0.0.1", port=port)

    del conn1, conn2

    assert pool.size == 2
    assert len(pool.connections) == 3

    await asyncio.sleep(2)

    assert pool.size == 0
    assert len(pool.connections) == 1

    pool.stop_reaper()


@pytest.mark.asyncio
async def test_many_connections(background_event_loop, tcp_server):
    server, port = tcp_server

    pool = AsyncConnectionPool(
        factory=AsyncTcpConnector, reap_connections=True, max_lifetime=1, max_size=10
    )

    async def _make_a_request():
        my_request = struct.pack("Q", random.getrandbits(64))

        conn = await pool.get(host="127.0.0.1", port=port)
        await conn.sendall(my_request)
        reply = await conn.recv_exactly(len(my_request))

        assert reply == my_request

        return True

    tasks = []

    for _ in range(15):
        tasks.append(background_event_loop.create_task(_make_a_request()))

    while any([not task.done() for task in tasks]):
        await asyncio.sleep(1)

    assert all([task.result() for task in tasks])

    assert pool.size == 10
    assert len(pool.connections) == 10

    await asyncio.sleep(2)

    assert pool.size == 0
    assert len(pool.connections) == 0

    pool.stop_reaper()