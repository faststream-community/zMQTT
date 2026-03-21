import asyncio
from typing import Protocol, runtime_checkable

from zmqtt.errors import MQTTDisconnectedError


@runtime_checkable
class Transport(Protocol):
    async def read(self, n: int) -> bytes: ...
    async def write(self, data: bytes) -> None: ...
    async def close(self) -> None: ...

    @property
    def is_connected(self) -> bool: ...


class StreamTransport:
    """Asyncio StreamReader/StreamWriter pair wrapped as a Transport."""

    def __init__(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._closed = False
        self._writer_closed = False

    async def read(self, n: int) -> bytes:
        data = await self._reader.read(n)
        if not data:
            self._closed = True
            raise MQTTDisconnectedError("Connection closed by remote")
        return data

    async def write(self, data: bytes) -> None:
        self._writer.write(data)
        await self._writer.drain()

    async def close(self) -> None:
        self._closed = True
        if not self._writer_closed:
            self._writer_closed = True
            self._writer.close()
        try:
            await self._writer.wait_closed()
        except Exception:
            pass

    @property
    def is_connected(self) -> bool:
        return not self._closed
