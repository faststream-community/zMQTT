"""Failure handling tests for the asyncio stream transport."""

import asyncio
from typing import NoReturn, cast

import pytest

from zmqtt import MQTTDisconnectedError
from zmqtt._internal.transport import base
from zmqtt._internal.transport.base import StreamTransport


class FailingReader:
    def __init__(self, error: OSError) -> None:
        self._error = error

    async def read(self, n: int) -> NoReturn:  # noqa: ARG002
        raise self._error


class FakeSocketTransport:
    def __init__(self) -> None:
        self.abort_calls = 0

    def abort(self) -> None:
        self.abort_calls += 1


class FakeWriter:
    def __init__(
        self,
        *,
        drain_error: OSError | None = None,
        hang_drain: bool = False,
        hang_close: bool = False,
    ) -> None:
        self.drain_error = drain_error
        self.hang_drain = hang_drain
        self.hang_close = hang_close
        self.writes: list[bytes] = []
        self.close_calls = 0
        self.transport = FakeSocketTransport()

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    async def drain(self) -> None:
        if self.drain_error is not None:
            raise self.drain_error
        if self.hang_drain:
            await asyncio.Event().wait()

    def close(self) -> None:
        self.close_calls += 1

    async def wait_closed(self) -> None:
        if self.hang_close:
            await asyncio.Event().wait()


async def test_read_wraps_socket_error_as_disconnection() -> None:
    error = ConnectionResetError("Connection reset by peer")
    transport = StreamTransport(
        cast("asyncio.StreamReader", FailingReader(error)),
        cast("asyncio.StreamWriter", FakeWriter()),
    )

    with pytest.raises(MQTTDisconnectedError, match="Connection lost") as caught:
        await transport.read(4096)

    assert caught.value.__cause__ is error
    assert not transport.is_connected


async def test_read_eof_becomes_disconnection() -> None:
    reader = asyncio.StreamReader()
    reader.feed_eof()
    transport = StreamTransport(reader, cast("asyncio.StreamWriter", FakeWriter()))

    with pytest.raises(MQTTDisconnectedError, match="Connection closed by remote"):
        await transport.read(4096)

    assert not transport.is_connected


async def test_write_wraps_socket_error_as_disconnection() -> None:
    error = BrokenPipeError("Broken pipe")
    writer = FakeWriter(drain_error=error)
    transport = StreamTransport(asyncio.StreamReader(), cast("asyncio.StreamWriter", writer))

    with pytest.raises(MQTTDisconnectedError, match="Connection lost") as caught:
        await transport.write(b"payload")

    assert writer.writes == [b"payload"]
    assert caught.value.__cause__ is error
    assert not transport.is_connected


async def test_write_timeout_becomes_disconnection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(base, "_DRAIN_TIMEOUT_SECONDS", 0.01)
    writer = FakeWriter(hang_drain=True)
    transport = StreamTransport(asyncio.StreamReader(), cast("asyncio.StreamWriter", writer))

    with pytest.raises(MQTTDisconnectedError, match="Connection lost") as caught:
        await transport.write(b"payload")

    assert isinstance(caught.value.__cause__, asyncio.TimeoutError)
    assert not transport.is_connected


async def test_close_of_live_socket_sends_fin_without_abort() -> None:
    writer = FakeWriter()
    transport = StreamTransport(asyncio.StreamReader(), cast("asyncio.StreamWriter", writer))

    await transport.close()

    assert writer.close_calls == 1
    assert writer.transport.abort_calls == 0  # a clean disconnect must not RST the broker
    assert not transport.is_connected


async def test_repeated_close_closes_writer_once() -> None:
    writer = FakeWriter()
    transport = StreamTransport(asyncio.StreamReader(), cast("asyncio.StreamWriter", writer))

    await transport.close()
    await transport.close()

    assert writer.close_calls == 1
    assert writer.transport.abort_calls == 0


async def test_close_aborts_half_open_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(base, "_CLOSE_TIMEOUT_SECONDS", 0.01)
    writer = FakeWriter(hang_close=True)
    transport = StreamTransport(asyncio.StreamReader(), cast("asyncio.StreamWriter", writer))

    await asyncio.wait_for(transport.close(), timeout=0.2)

    assert writer.close_calls == 1
    assert writer.transport.abort_calls == 1
    assert not transport.is_connected
