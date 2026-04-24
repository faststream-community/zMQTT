"""Tests for zmqtt.protocol — pure-logic and error-path tests only.

E2E observable behavior (QoS flows, subscribe/receive, ack) lives in
tests/test_brokers/_base.py and runs against real brokers.
"""

import asyncio
import contextlib
import logging
from collections import deque
from collections.abc import Callable

import pytest

from zmqtt._internal.packets.codec import decode, encode
from zmqtt._internal.packets.connect import ConnAck, Connect
from zmqtt._internal.packets.publish import PubAck, PubComp, Publish, PubRec, PubRel
from zmqtt._internal.protocol import MQTTProtocol, ProtocolConfig
from zmqtt._internal.state import SessionState, SubscriptionEntry
from zmqtt._internal.types.message import Message
from zmqtt._internal.types.qos import QoS
from zmqtt.errors import MQTTConnectError, MQTTProtocolError, MQTTTimeoutError


class FakeTransport:
    """In-memory transport: read() drains from rx_queue; write() appends to sent."""

    def __init__(self) -> None:
        self.sent: list[bytes] = []
        self._rx: deque[bytes | Exception] = deque()
        self._closed = False

    def feed(self, data: bytes) -> None:
        self._rx.append(data)

    async def read(self, n: int) -> bytes:  # noqa: ARG002
        while not self._rx:  # noqa: ASYNC110
            await asyncio.sleep(0)
        item = self._rx.popleft()
        if isinstance(item, Exception):
            raise item
        return item

    async def write(self, data: bytes) -> None:
        self.sent.append(data)

    async def close(self) -> None:
        self._closed = True

    @property
    def is_connected(self) -> bool:
        return not self._closed


def make_protocol(
    keepalive: int = 60,
    ping_timeout: float = 5.0,
    retransmit_interval: float = 5.0,
) -> tuple[MQTTProtocol, FakeTransport]:
    transport = FakeTransport()
    state = SessionState()
    protocol = MQTTProtocol(
        transport,
        state,
        config=ProtocolConfig(
            keepalive=keepalive,
            ping_timeout=ping_timeout,
            retransmit_interval=retransmit_interval,
        ),
    )
    return protocol, transport


async def _run_read_loop(protocol: MQTTProtocol) -> asyncio.Task[None]:
    task: asyncio.Task[None] = asyncio.create_task(protocol._read_loop())
    await asyncio.sleep(0)
    return task


async def _stop_task(task: asyncio.Task[None]) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


def _decode_packet(data: bytes) -> object:
    decoded = decode(data, version="3.1.1")
    assert decoded is not None
    return decoded[0]


def _decoded_sent(transport: FakeTransport) -> list[object]:
    return [_decode_packet(data) for data in transport.sent]


async def _wait_until(predicate: Callable[[], bool], timeout: float = 1.0) -> None:
    start = asyncio.get_running_loop().time()

    while not predicate():
        if asyncio.get_running_loop().time() - start > timeout:
            raise asyncio.TimeoutError
        await asyncio.sleep(0.01)


async def test_connect_refused_raises() -> None:
    protocol, transport = make_protocol()
    connect = Connect(client_id="test", clean_session=True, keepalive=60)
    transport.feed(
        encode(ConnAck(session_present=False, return_code=4), version="3.1.1"),
    )
    with pytest.raises(MQTTConnectError) as exc_info:
        await protocol.connect(connect)
    assert exc_info.value.return_code == 4


async def test_connect_wrong_packet_raises() -> None:
    protocol, transport = make_protocol()
    connect = Connect(client_id="test", clean_session=True, keepalive=60)
    transport.feed(encode(PubAck(packet_id=1), version="3.1.1"))
    with pytest.raises(MQTTProtocolError):
        await protocol.connect(connect)


async def test_ping_timeout_raises() -> None:
    protocol, _ = make_protocol(keepalive=0, ping_timeout=0.05)
    ping_task = asyncio.create_task(protocol._ping_loop())
    with pytest.raises(MQTTTimeoutError):
        await ping_task


async def test_deliver_no_match_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    """No matching subscription: warning logged, nothing delivered."""
    protocol, _ = make_protocol()

    with caplog.at_level(logging.WARNING, logger="zmqtt.protocol"):
        await protocol._deliver(
            Publish(
                topic="unknown/topic",
                payload=b"x",
                qos=QoS.AT_MOST_ONCE,
                retain=False,
                dup=False,
            ),
            ack_callback=None,
        )

    assert "unknown/topic" in caplog.text


async def test_inbound_qos2_manual_ack_duplicate_ignored() -> None:
    """Broker retransmit before app calls ack() must not re-queue the message."""
    protocol, transport = make_protocol()
    transport.feed(
        encode(ConnAck(session_present=False, return_code=0), version="3.1.1"),
    )
    await protocol.connect(Connect(client_id="c", clean_session=True, keepalive=60))

    queue: asyncio.Queue[Message] = asyncio.Queue()
    protocol._state.subscriptions["t/#"] = SubscriptionEntry(
        queue=queue,
        auto_ack=False,
        actual_filter="t/#",
    )
    transport.sent.clear()

    publish = Publish(
        topic="t/x",
        payload=b"once",
        qos=QoS.EXACTLY_ONCE,
        retain=False,
        dup=False,
        packet_id=11,
    )
    transport.feed(encode(publish, version="3.1.1"))
    read_task = await _run_read_loop(protocol)
    await asyncio.wait_for(queue.get(), timeout=1.0)

    # Broker retransmits PUBLISH before app calls ack()
    transport.feed(
        encode(
            Publish(
                topic="t/x",
                payload=b"once",
                qos=QoS.EXACTLY_ONCE,
                retain=False,
                dup=True,
                packet_id=11,
            ),
            version="3.1.1",
        ),
    )
    await asyncio.sleep(0.05)

    assert queue.empty()
    assert transport.sent == []  # no PUBREC for duplicate

    await _stop_task(read_task)


async def test_qos1_publish_retransmit_sets_dup() -> None:
    protocol, transport = make_protocol(retransmit_interval=0.02)
    read_task = await _run_read_loop(protocol)

    publish_task = asyncio.create_task(
        protocol.publish(
            Publish(
                topic="t/x",
                payload=b"once",
                qos=QoS.AT_LEAST_ONCE,
                retain=False,
                dup=False,
            ),
        ),
    )

    await _wait_until(lambda: len(transport.sent) >= 2)
    sent = _decoded_sent(transport)
    publishes = [packet for packet in sent if isinstance(packet, Publish)]
    assert len(publishes) >= 2
    assert publishes[0].dup is False
    assert publishes[1].dup is True
    assert publishes[0].packet_id == publishes[1].packet_id

    packet_id = publishes[0].packet_id
    assert packet_id is not None
    transport.feed(encode(PubAck(packet_id=packet_id), version="3.1.1"))

    await asyncio.wait_for(publish_task, timeout=1.0)
    await _stop_task(read_task)


async def test_qos2_publish_retransmits_publish_and_pubrel() -> None:
    protocol, transport = make_protocol(retransmit_interval=0.02)
    read_task = await _run_read_loop(protocol)

    publish_task = asyncio.create_task(
        protocol.publish(
            Publish(
                topic="t/x",
                payload=b"once",
                qos=QoS.EXACTLY_ONCE,
                retain=False,
                dup=False,
            ),
        ),
    )

    await _wait_until(
        lambda: len([packet for packet in _decoded_sent(transport) if isinstance(packet, Publish)]) >= 2,
    )
    sent = _decoded_sent(transport)
    publishes = [packet for packet in sent if isinstance(packet, Publish)]
    assert publishes[0].dup is False
    assert publishes[1].dup is True
    assert publishes[0].packet_id == publishes[1].packet_id

    packet_id = publishes[0].packet_id
    assert packet_id is not None
    transport.feed(encode(PubRec(packet_id=packet_id), version="3.1.1"))

    await _wait_until(lambda: any(isinstance(packet, PubRel) for packet in _decoded_sent(transport)))
    await _wait_until(lambda: len([packet for packet in _decoded_sent(transport) if isinstance(packet, PubRel)]) >= 2)

    transport.feed(encode(PubComp(packet_id=packet_id), version="3.1.1"))
    await asyncio.wait_for(publish_task, timeout=1.0)
    await _stop_task(read_task)


async def test_qos2_duplicate_pubrec_resends_pubrel() -> None:
    protocol, transport = make_protocol(retransmit_interval=1.0)
    read_task = await _run_read_loop(protocol)

    publish_task = asyncio.create_task(
        protocol.publish(
            Publish(
                topic="t/x",
                payload=b"once",
                qos=QoS.EXACTLY_ONCE,
                retain=False,
                dup=False,
            ),
        ),
    )

    await _wait_until(lambda: any(isinstance(packet, Publish) for packet in _decoded_sent(transport)))
    publishes = [packet for packet in _decoded_sent(transport) if isinstance(packet, Publish)]
    packet_id = publishes[0].packet_id
    assert packet_id is not None

    transport.feed(encode(PubRec(packet_id=packet_id), version="3.1.1"))
    await _wait_until(lambda: len([packet for packet in _decoded_sent(transport) if isinstance(packet, PubRel)]) >= 1)

    transport.feed(encode(PubRec(packet_id=packet_id), version="3.1.1"))
    await _wait_until(lambda: len([packet for packet in _decoded_sent(transport) if isinstance(packet, PubRel)]) >= 2)

    transport.feed(encode(PubComp(packet_id=packet_id), version="3.1.1"))
    await asyncio.wait_for(publish_task, timeout=1.0)
    await _stop_task(read_task)


async def test_qos1_publish_timeout_abandons_retransmit() -> None:
    protocol, transport = make_protocol(retransmit_interval=0.02)

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            protocol.publish(
                Publish(
                    topic="t/x",
                    payload=b"once",
                    qos=QoS.AT_LEAST_ONCE,
                    retain=False,
                    dup=False,
                ),
            ),
            timeout=0.001,
        )

    await asyncio.sleep(0.05)

    publishes = [packet for packet in _decoded_sent(transport) if isinstance(packet, Publish)]
    assert len(publishes) == 1
    assert protocol._state.inflight_qos1 == {}


async def test_qos2_publish_timeout_abandons_retransmit() -> None:
    protocol, transport = make_protocol(retransmit_interval=0.02)

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            protocol.publish(
                Publish(
                    topic="t/x",
                    payload=b"once",
                    qos=QoS.EXACTLY_ONCE,
                    retain=False,
                    dup=False,
                ),
            ),
            timeout=0.001,
        )

    await asyncio.sleep(0.05)

    packets = _decoded_sent(transport)
    publishes = [packet for packet in packets if isinstance(packet, Publish)]
    pubrels = [packet for packet in packets if isinstance(packet, PubRel)]
    assert len(publishes) == 1
    assert pubrels == []
    assert protocol._state.inflight_qos2_out == {}
