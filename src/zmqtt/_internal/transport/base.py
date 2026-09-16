import asyncio
import contextlib
from typing import Protocol, runtime_checkable

from zmqtt.errors import MQTTDisconnectedError

_DRAIN_TIMEOUT_SECONDS = 30.0
_CLOSE_TIMEOUT_SECONDS = 1.0


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
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._closed = False
        self._writer_closed = False

    async def read(self, n: int) -> bytes:
        try:
            data = await self._reader.read(n)
        except OSError as exc:
            # RST / ENETUNREACH / TLS drop on a live socket: same reconnect path as EOF.
            self._closed = True
            msg = "Connection lost"
            raise MQTTDisconnectedError(msg) from exc
        if not data:
            self._closed = True
            msg = "Connection closed by remote"
            raise MQTTDisconnectedError(msg)
        return data

    async def write(self, data: bytes) -> None:
        try:
            self._writer.write(data)
            await asyncio.wait_for(self._writer.drain(), timeout=_DRAIN_TIMEOUT_SECONDS)
        except (OSError, asyncio.TimeoutError) as exc:
            # The file descriptor is released by close(), same as on the read side.
            self._closed = True
            msg = "Connection lost"
            raise MQTTDisconnectedError(msg) from exc

    async def close(self) -> None:
        self._closed = True
        if not self._writer_closed:
            self._writer_closed = True
            self._writer.close()
        try:
            await asyncio.wait_for(self._writer.wait_closed(), timeout=_CLOSE_TIMEOUT_SECONDS)
        except Exception:  # noqa: BLE001 - a hung or failed close must not block reconnect
            with contextlib.suppress(Exception):
                self._writer.transport.abort()

    @property
    def is_connected(self) -> bool:
        return not self._closed
