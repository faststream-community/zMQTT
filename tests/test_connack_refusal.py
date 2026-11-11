"""Retrying refused CONNACKs, exercised through the public client API against a scripted broker."""

import asyncio
from collections.abc import Callable
from typing import Literal

import pytest

from zmqtt import MQTTClient, MQTTConnectError, MQTTDisconnectedError, ReconnectConfig
from zmqtt._internal._compat import Self
from zmqtt._internal.packets.codec import encode
from zmqtt._internal.packets.connect import ConnAck
from zmqtt._internal.packets.ping import PingResp
from zmqtt._internal.packets.types import PacketType


class ScriptedBroker:
    """Local TCP broker that answers each CONNECT with the next scripted CONNACK code.

    An accepted connection answers PINGREQ until the client disconnects or
    :meth:`drop` closes it. A refused connection is closed after its CONNACK.
    """

    def __init__(self, return_codes: list[int], version: Literal["3.1.1", "5.0"]) -> None:
        self.connects: list[int] = []
        self._return_codes = return_codes
        self._version: Literal["3.1.1", "5.0"] = version
        self._accepted: asyncio.StreamWriter | None = None
        self._server: asyncio.Server | None = None
        self._writers: set[asyncio.StreamWriter] = set()

    @property
    def port(self) -> int:
        assert self._server is not None
        return int(self._server.sockets[0].getsockname()[1])

    async def __aenter__(self) -> Self:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return self

    async def __aexit__(self, *exc: object) -> None:
        assert self._server is not None
        self._server.close()
        for writer in self._writers:
            writer.transport.abort()
        await self._server.wait_closed()

    def drop(self) -> None:
        """Abort the accepted connection, as a broker restart would."""
        assert self._accepted is not None
        self._accepted.transport.abort()
        self._accepted = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._writers.add(writer)
        try:
            assert await _read_packet_type(reader) == PacketType.CONNECT
            code = self._return_codes[len(self.connects)]
            self.connects.append(code)
            writer.write(encode(ConnAck(session_present=False, return_code=code), version=self._version))
            await writer.drain()
            if code != 0:
                return
            self._accepted = writer
            while (packet_type := await _read_packet_type(reader)) != PacketType.DISCONNECT:
                if packet_type == PacketType.PINGREQ:
                    writer.write(encode(PingResp(), version=self._version))
                    await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            self._writers.discard(writer)
            writer.close()


async def _read_packet_type(reader: asyncio.StreamReader) -> PacketType:
    """Read one whole packet and return its type."""
    header = (await reader.readexactly(1))[0]
    length, shift = 0, 0
    while True:
        byte = (await reader.readexactly(1))[0]
        length |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            break
    await reader.readexactly(length)
    return PacketType(header >> 4)


def _client(
    broker: ScriptedBroker,
    version: Literal["3.1.1", "5.0"],
    *,
    max_attempts: int | None = None,
    retry_refusal: Callable[[MQTTConnectError], bool] | None = None,
) -> MQTTClient:
    return MQTTClient(
        "127.0.0.1",
        broker.port,
        version=version,
        reconnect=ReconnectConfig(initial_delay=0.0, max_attempts=max_attempts, retry_refusal=retry_refusal),
    )


@pytest.mark.parametrize(
    ("version", "code"),
    [
        ("3.1.1", 0x03),  # Server unavailable
        ("5.0", 0x88),  # Server unavailable
        ("5.0", 0x89),  # Server busy
        ("5.0", 0x97),  # Quota exceeded
        ("5.0", 0x9F),  # Connection rate exceeded
    ],
)
async def test_connect_retries_transient_refusal(version: Literal["3.1.1", "5.0"], code: int) -> None:
    async with ScriptedBroker([code, code, 0], version) as broker, _client(broker, version) as client:
        assert client.connection_info.connection_id == 1
        await client.ping()

    assert broker.connects == [code, code, 0]


async def test_connect_gives_up_on_transient_refusal_after_max_attempts() -> None:
    async with ScriptedBroker([0x03, 0x03, 0], "3.1.1") as broker:
        client = _client(broker, "3.1.1", max_attempts=2)

        with pytest.raises(MQTTConnectError) as exc_info:
            await client.connect()

    assert exc_info.value.return_code == 0x03
    assert broker.connects == [0x03, 0x03]


@pytest.mark.parametrize(
    ("version", "code"),
    [
        ("3.1.1", 0x04),  # Bad user name or password
        ("3.1.1", 0x05),  # Not authorized
        ("5.0", 0x86),  # Bad User Name or Password
        ("5.0", 0x87),  # Not authorized
        ("5.0", 0x9C),  # Use another server
        ("5.0", 0x9D),  # Server moved
    ],
)
async def test_connect_does_not_retry_permanent_refusal(version: Literal["3.1.1", "5.0"], code: int) -> None:
    async with ScriptedBroker([code, 0], version) as broker:
        client = _client(broker, version)

        with pytest.raises(MQTTConnectError) as exc_info:
            await client.connect()

    assert exc_info.value.return_code == code
    assert broker.connects == [code]


@pytest.mark.parametrize(("version", "code"), [("3.1.1", 0x05), ("5.0", 0x87)])
async def test_retry_refusal_hook_retries_not_authorized(version: Literal["3.1.1", "5.0"], code: int) -> None:
    # RabbitMQ refuses with "not authorized" while a vhost starts or before
    # the user's permissions are applied.
    seen: list[int] = []

    def retry_refusal(refusal: MQTTConnectError) -> bool:
        seen.append(refusal.return_code)
        return refusal.is_transient or refusal.return_code in {0x05, 0x87}

    async with (
        ScriptedBroker([code, code, 0], version) as broker,
        _client(broker, version, retry_refusal=retry_refusal) as client,
    ):
        await client.ping()

    assert seen == [code, code]
    assert broker.connects == [code, code, 0]


async def test_retry_refusal_hook_is_bounded_by_max_attempts() -> None:
    async with ScriptedBroker([0x87, 0x87, 0], "5.0") as broker:
        client = _client(broker, "5.0", max_attempts=2, retry_refusal=lambda _: True)

        with pytest.raises(MQTTConnectError):
            await client.connect()

    assert broker.connects == [0x87, 0x87]


async def test_retry_refusal_hook_vetoes_transient_refusal() -> None:
    async with ScriptedBroker([0x88, 0], "5.0") as broker:
        client = _client(broker, "5.0", retry_refusal=lambda _: False)

        with pytest.raises(MQTTConnectError) as exc_info:
            await client.connect()

    assert exc_info.value.return_code == 0x88
    assert broker.connects == [0x88]


@pytest.mark.parametrize(("version", "code"), [("3.1.1", 0x03), ("5.0", 0x88)])
async def test_reconnect_retries_transient_refusal(version: Literal["3.1.1", "5.0"], code: int) -> None:
    # A broker that is still booting (e.g. RabbitMQ) refuses with "server
    # unavailable"; that must not end the reconnect task.
    async with ScriptedBroker([0, code, 0], version) as broker, _client(broker, version) as client:
        broker.drop()

        async def reconnected() -> None:
            while True:
                try:
                    if client.connection_info.connection_id == 2:
                        return
                except MQTTDisconnectedError:
                    pass
                await asyncio.sleep(0.01)

        await asyncio.wait_for(reconnected(), timeout=5)
        await client.ping()

    assert broker.connects == [0, code, 0]


@pytest.mark.parametrize(("code", "transient"), [(0x03, True), (0x88, True), (0x04, False), (0x87, False)])
def test_connect_error_is_transient(code: int, *, transient: bool) -> None:
    assert MQTTConnectError(code).is_transient is transient
