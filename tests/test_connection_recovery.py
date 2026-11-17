"""Connection failures other than a refused CONNACK, exercised through the public client API."""

import asyncio

import pytest

from zmqtt import MQTTClient, MQTTDisconnectedError, ReconnectConfig
from zmqtt._internal._compat import Self
from zmqtt._internal.packets.codec import encode
from zmqtt._internal.packets.connect import ConnAck
from zmqtt._internal.packets.ping import PingResp
from zmqtt._internal.packets.types import PacketType

ACCEPT = encode(ConnAck(session_present=False, return_code=0), version="3.1.1")
CLOSE = b""  # close the connection without a CONNACK
PINGRESP = encode(PingResp(), version="3.1.1")
MALFORMED = b"\x00\x00"  # packet type 0 is reserved


class ScriptedBroker:
    """Local TCP broker that answers each CONNECT with the next scripted reply.

    After :data:`ACCEPT` the connection stays open and answers PINGREQ until the
    client disconnects or :meth:`drop` closes it. Any other reply is written and
    the connection is closed.
    """

    def __init__(self, replies: list[bytes]) -> None:
        self.connects = 0
        self._replies = replies
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

    def send(self, data: bytes) -> None:
        """Write raw bytes to the accepted connection."""
        assert self._accepted is not None
        self._accepted.write(data)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._writers.add(writer)
        try:
            assert await _read_packet_type(reader) == PacketType.CONNECT
            reply = self._replies[self.connects]
            self.connects += 1
            writer.write(reply)
            await writer.drain()
            if reply != ACCEPT:
                return
            self._accepted = writer
            while (packet_type := await _read_packet_type(reader)) != PacketType.DISCONNECT:
                if packet_type == PacketType.PINGREQ:
                    writer.write(PINGRESP)
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


class RecoveryFailed:
    """``on_connection_recovery_failed`` callback that records its calls."""

    def __init__(self) -> None:
        self.calls = 0
        self._called = asyncio.Event()

    async def __call__(self) -> None:
        self.calls += 1
        self._called.set()

    async def wait(self) -> None:
        await asyncio.wait_for(self._called.wait(), timeout=5)


def _client(
    broker: ScriptedBroker, *, max_attempts: int | None = None, on_failed: RecoveryFailed | None = None
) -> MQTTClient:
    return MQTTClient(
        "127.0.0.1",
        broker.port,
        reconnect=ReconnectConfig(initial_delay=0.0, max_attempts=max_attempts),
        on_connection_recovery_failed=on_failed,
    )


async def _reconnected(client: MQTTClient) -> None:
    async def poll() -> None:
        while True:
            try:
                if client.connection_info.connection_id == 2:
                    return
            except MQTTDisconnectedError:
                pass
            await asyncio.sleep(0.01)

    await asyncio.wait_for(poll(), timeout=5)


async def test_connect_retries_connection_closed_before_connack() -> None:
    async with ScriptedBroker([CLOSE, ACCEPT]) as broker, _client(broker) as client:
        assert client.connection_info.connection_id == 1
        await client.ping()

    assert broker.connects == 2


async def test_reconnect_retries_connection_closed_before_connack() -> None:
    on_failed = RecoveryFailed()
    async with ScriptedBroker([ACCEPT, CLOSE, ACCEPT]) as broker, _client(broker, on_failed=on_failed) as client:
        broker.drop()
        await _reconnected(client)
        await client.ping()

    assert broker.connects == 3
    assert on_failed.calls == 0


async def test_reconnect_gives_up_on_connection_closed_before_connack_after_max_attempts() -> None:
    on_failed = RecoveryFailed()
    async with ScriptedBroker([ACCEPT, CLOSE, CLOSE]) as broker:
        async with _client(broker, max_attempts=2, on_failed=on_failed):
            broker.drop()
            await on_failed.wait()

        assert broker.connects == 3
        assert on_failed.calls == 1


@pytest.mark.parametrize("reply", [PINGRESP, MALFORMED], ids=["unexpected", "malformed"])
async def test_protocol_error_during_reconnect_stops_and_reports(reply: bytes) -> None:
    on_failed = RecoveryFailed()
    async with ScriptedBroker([ACCEPT, reply, ACCEPT]) as broker:
        async with _client(broker, on_failed=on_failed):
            broker.drop()
            await on_failed.wait()

        assert broker.connects == 2
        assert on_failed.calls == 1


@pytest.mark.parametrize("packet", [ACCEPT, MALFORMED], ids=["unexpected", "malformed"])
async def test_protocol_error_on_live_connection_stops_and_reports(packet: bytes) -> None:
    on_failed = RecoveryFailed()
    async with ScriptedBroker([ACCEPT, ACCEPT]) as broker:
        async with _client(broker, on_failed=on_failed) as client:
            broker.send(packet)
            await on_failed.wait()
            with pytest.raises(MQTTDisconnectedError):
                client.connection_info  # noqa: B018

        assert broker.connects == 1
        assert on_failed.calls == 1
