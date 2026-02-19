[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![CI](https://github.com/polyatail/aiosocketpool/actions/workflows/ci.yml/badge.svg)](https://github.com/polyatail/aiosocketpool/actions/workflows/ci.yml)

# aiosocketpool
An asyncio-compatible socket pool. Simple, compact, easily extended.

If your application needs to connect to many remote hosts simultaneously (and often), it probably
makes sense to keep connections open and re-use them rather than establishing a new connection for
every request. Combining an `asyncio` event loop and a socket pool might be the way to go!

Based on [socketpool](https://github.com/benoitc/socketpool).

**Requires Python 3.8 or above.**

## Examples
You can run these examples, one after another, in a `python -m asyncio` shell.

1. Run a simple TCP echo server in a background thread, using the `asyncio` library.

```python
import asyncio
import threading


# start a new event loop running in a background thread
def run_loop_forever(loop):
    loop.run_forever()


loop = asyncio.new_event_loop()

t = threading.Thread(name="BackgroundEventLoop", target=run_loop_forever, args=[loop], daemon=True)
t.start()


# run a tcp echo server using asyncio in the background event loop
async def echo_handler(reader, writer):
    try:
        while True:
            try:
                data = await reader.readuntil(b'\n')
            except asyncio.IncompleteReadError:
                break

            writer.write(data)
            await writer.drain()

    finally:
        writer.close()
        await writer.wait_closed()


async def echo_server(tcp_port):
    server = await asyncio.start_server(echo_handler, "127.0.0.1", tcp_port)
    await server.serve_forever()


asyncio.run_coroutine_threadsafe(echo_server(12345), loop)
```

2. Create a new TCP connection pool in the main thread, get a connection, and send and receive data.

```python
from aiosocketpool import AsyncConnectionPool, AsyncTcpConnector


pool = AsyncConnectionPool(
    factory=AsyncTcpConnector,
    reap_connections=True,  # a background task will destroy old and idle connections
    max_lifetime=10,  # connections will remain idle at most 10 seconds
    max_size=10,  # we will maintain at most 10 idle connections in the pool
)


async def hello_world():
    async with pool.connection(host="127.0.0.1", port=12345) as conn:
        await conn.sendall(b"hello world\n")
        print(await conn.recv(32))


await hello_world()
```

3. Create a bunch of connections and run them all concurrently.

```python
tasks = []

for _ in range(25):
    tasks.append(asyncio.create_task(hello_world()))

await asyncio.gather(*tasks)
```
