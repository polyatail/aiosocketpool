[![Circle CI](https://circleci.com/gh/polyatail/aiosocketpool.svg?style=shield&circle-token=f7f570b230ecf72d3df817cca445c5a28809068a)](https://circleci.com/gh/onecodex/mainline)
![Black Code Style](https://camo.githubusercontent.com/28a51fe3a2c05048d8ca8ecd039d6b1619037326/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f636f64652532307374796c652d626c61636b2d3030303030302e737667)

# aiosocketpool
An asyncio-compatible socket pool. Simple, compact, easily extended.

If your application needs to connect to many remote hosts simultaneously (and often), it probably
makes sense to keep connections open and re-use them rather than establishing a new connection for
every request. Combining an `asyncio` event loop and a socket pool might be the way to go!

Based on [socketpool](https://github.com/benoitc/socketpool).

**Requires Python 3.7 or above.**

## Examples

Run a simple TCP echo server in a background thread, using the `asyncio` library.

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
    writer.write(await reader.read(32))
    await writer.drain()
    writer.close()


async def echo_server(tcp_port):
    server = await asyncio.start_server(echo_handler, "127.0.0.1", tcp_port)
    await server.serve_forever()


asyncio.run_coroutine_threadsafe(echo_server(12345), loop)
```

Create a new TCP connection pool in the main thread, get a connection, and send and receive data.

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
        await conn.sendall(b"hello world")
        print(await conn.recv(32))


await hello_world()
```

Create a bunch of connections and run them all concurrently.

```python
loop = asyncio.get_event_loop()

tasks = []

for _ in range(25):
    tasks.append(loop.create_task(hello_world()))

loop.run_until_complete(asyncio.gather(*tasks))
```