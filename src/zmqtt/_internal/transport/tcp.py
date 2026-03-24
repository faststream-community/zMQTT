import asyncio

from zmqtt._internal.transport.base import StreamTransport


async def open_tcp(host: str, port: int) -> StreamTransport:
    reader, writer = await asyncio.open_connection(host, port)
    return StreamTransport(reader, writer)
