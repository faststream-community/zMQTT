import asyncio
import ssl

from zmqtt.transport.base import StreamTransport


async def open_tls(
    host: str,
    port: int,
    ssl_context: ssl.SSLContext | None = None,
) -> StreamTransport:
    ctx = ssl_context or ssl.create_default_context()
    reader, writer = await asyncio.open_connection(host, port, ssl=ctx)
    return StreamTransport(reader, writer)
