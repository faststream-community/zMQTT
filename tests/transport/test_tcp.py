import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from zmqtt.errors import MQTTDisconnectedError
from zmqtt.transport.base import StreamTransport


def make_stream_pair(
    read_data: bytes = b"hello",
) -> tuple[asyncio.StreamReader, MagicMock]:
    reader = asyncio.StreamReader()
    if read_data:
        reader.feed_data(read_data)
    writer = MagicMock(spec=asyncio.StreamWriter)
    writer.drain = AsyncMock()
    writer.wait_closed = AsyncMock()
    return reader, writer


class TestStreamTransportRead:
    async def test_read_returns_data(self) -> None:
        reader, writer = make_stream_pair(b"hello world")
        t = StreamTransport(reader, writer)
        data = await t.read(5)
        assert data == b"hello"

    async def test_read_eof_raises_disconnected(self) -> None:
        reader = asyncio.StreamReader()
        reader.feed_eof()
        writer = MagicMock(spec=asyncio.StreamWriter)

        t = StreamTransport(reader, writer)
        with pytest.raises(MQTTDisconnectedError):
            await t.read(10)

    async def test_read_eof_marks_disconnected(self) -> None:
        reader = asyncio.StreamReader()
        reader.feed_eof()
        writer = MagicMock(spec=asyncio.StreamWriter)

        t = StreamTransport(reader, writer)
        with pytest.raises(MQTTDisconnectedError):
            await t.read(10)
        assert not t.is_connected


class TestStreamTransportWrite:
    async def test_write_sends_data(self) -> None:
        reader, writer = make_stream_pair()
        t = StreamTransport(reader, writer)
        await t.write(b"test")
        writer.write.assert_called_once_with(b"test")
        writer.drain.assert_awaited_once()


class TestStreamTransportClose:
    async def test_close_marks_disconnected(self) -> None:
        reader, writer = make_stream_pair()
        t = StreamTransport(reader, writer)
        assert t.is_connected
        await t.close()
        assert not t.is_connected

    async def test_close_calls_writer_close(self) -> None:
        reader, writer = make_stream_pair()
        t = StreamTransport(reader, writer)
        await t.close()
        writer.close.assert_called_once()
        writer.wait_closed.assert_awaited_once()

    async def test_close_idempotent(self) -> None:
        reader, writer = make_stream_pair()
        t = StreamTransport(reader, writer)
        await t.close()
        await t.close()
        writer.close.assert_called_once()

    async def test_close_swallows_exceptions(self) -> None:
        reader, writer = make_stream_pair()
        writer.wait_closed = AsyncMock(side_effect=OSError("connection lost"))
        t = StreamTransport(reader, writer)
        await t.close()  # should not raise
        assert not t.is_connected
