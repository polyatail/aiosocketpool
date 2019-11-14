import asyncio
import threading
import time

import pytest


@pytest.fixture(scope="function")
def background_event_loop():
    def _run_loop(loop, waiter):
        try:
            loop.run_until_complete(waiter)
        except asyncio.CancelledError:
            pass

    async def _wait_forever():
        while True:
            await asyncio.sleep(0.05)

    loop = asyncio.new_event_loop()
    waiter = loop.create_task(_wait_forever())

    t = threading.Thread(name="BackgroundEventLoop", target=_run_loop, args=[loop, waiter])
    t.start()

    yield loop

    for task in asyncio.all_tasks(loop):
        task.cancel()

    t.join()


@pytest.fixture(scope="function")
def tcp_server(background_event_loop, unused_tcp_port):
    async def _echo_handler(reader, writer):
        writer.write(await reader.read(32))
        await writer.drain()
        writer.close()

    server = background_event_loop.create_task(
        asyncio.start_server(_echo_handler, "127.0.0.1", unused_tcp_port)
    )

    while True:
        if not server.done():
            time.sleep(0.05)
            continue

        server = server.result()
        break

    server_loop = background_event_loop.create_task(server.serve_forever())

    yield server, unused_tcp_port

    server_loop.cancel()
