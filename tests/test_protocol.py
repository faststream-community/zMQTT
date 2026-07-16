"""Tests for zmqtt.protocol — pure-logic and error-path tests only.

E2E observable behavior (QoS flows, subscribe/receive, ack) lives in
tests/test_brokers/_base.py and runs against real brokers.
"""

import asyncio
import contextlib
import logging
from collections import deque
from typing import Literal

import pytest

from zmqtt._internal.packets.codec import encode
from zmqtt._internal.packets.connect import ConnAck, Connect
from zmqtt._internal.packets.properties import PublishProperties
from zmqtt._internal.packets.publish import PubAck, Publish
from zmqtt._internal.packets.reader import PacketBuffer
from zmqtt._internal.packets.subscribe import SubAck, Subscribe, SubscriptionRequest
from zmqtt._internal.protocol import MQTTProtocol
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
    version: Literal["3.1.1", "5.0"] = "3.1.1",
) -> tuple[MQTTProtocol, FakeTransport]:
    transport = FakeTransport()
    state = SessionState()
    protocol = MQTTProtocol(
        transport,
        state,
        keepalive=keepalive,
        ping_timeout=ping_timeout,
        version=version,
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


async def test_connect_timeout_raises() -> None:
    transport = FakeTransport()
    protocol = MQTTProtocol(transport, SessionState(), connect_timeout=0.05)
    connect = Connect(client_id="test", clean_session=True, keepalive=60)
    # Transport is never fed -> CONNACK never arrives -> the bounded wait fires.
    with pytest.raises(MQTTTimeoutError):
        await protocol.connect(connect)


async def test_connect_succeeds_within_timeout() -> None:
    transport = FakeTransport()
    protocol = MQTTProtocol(transport, SessionState(), connect_timeout=5.0)
    connect = Connect(client_id="test", clean_session=True, keepalive=60)
    transport.feed(encode(ConnAck(session_present=False, return_code=0), version="3.1.1"))
    ack = await protocol.connect(connect)
    assert isinstance(ack, ConnAck)
    assert ack.return_code == 0
    assert ack.session_present is False


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


async def test_subscription_identifier_routes_delivery() -> None:
    """The broker's echoed identifier picks the exact subscription that matched.

    Two overlapping filters — a $share subscription and its plain twin — normalise
    to the same actual filter, so filter matching alone cannot tell them apart:
    one used to receive everything (both broker copies) and the other starved.
    """
    protocol, _ = make_protocol()
    shared = SubscriptionEntry(
        queue=asyncio.Queue(),
        actual_filter="demo/+/state",
        subscription_identifier=1,
    )
    plain = SubscriptionEntry(
        queue=asyncio.Queue(),
        actual_filter="demo/+/state",
        subscription_identifier=2,
    )
    protocol._state.subscriptions["$share/g/demo/+/state"] = shared
    protocol._state.subscriptions["demo/+/state"] = plain

    for echoed, entry in ((1, shared), (2, plain)):
        await protocol._deliver(
            Publish(
                topic="demo/dev-1/state",
                payload=b"x",
                qos=QoS.AT_MOST_ONCE,
                retain=False,
                dup=False,
                properties=PublishProperties(subscription_identifier=echoed),
            ),
            ack_callback=None,
        )
        assert entry.queue.qsize() == 1

    assert shared.queue.qsize() == 1
    assert plain.queue.qsize() == 1


async def test_multi_filter_subscription_delivers_once_per_publish() -> None:
    """One subscribe() with MANY filters is ONE subscription: one identifier, one
    per-filter entry (each with its own relay queue, all draining into the same
    application queue). A broker PUBLISH echoing that identifier used to be
    enqueued once PER ENTRY — 20 filters meant 20 duplicates of every message
    (found live: a device ack recorded 20 times per command). It must land in
    exactly ONE entry: the one whose filter matches the topic."""
    protocol, _ = make_protocol()
    filters = ["demo/+/server/state", "demo/+/server/status", "demo/+/server/state/ack"]
    entries = {}
    for topic_filter in filters:
        entry = SubscriptionEntry(
            queue=asyncio.Queue(),
            actual_filter=topic_filter,
            subscription_identifier=1,
        )
        entries[topic_filter] = entry
        protocol._state.subscriptions[f"$share/g/{topic_filter}"] = entry

    await protocol._deliver(
        Publish(
            topic="demo/dev-1/server/state/ack",
            payload=b"x",
            qos=QoS.AT_MOST_ONCE,
            retain=False,
            dup=False,
            properties=PublishProperties(subscription_identifier=1),
        ),
        ack_callback=None,
    )
    sizes = {f: e.queue.qsize() for f, e in entries.items()}
    assert sum(sizes.values()) == 1  # once per subscription, not once per filter
    assert sizes["demo/+/server/state/ack"] == 1  # and via the filter that matched


async def test_subscribe_sends_identifier_in_properties() -> None:
    """The identifier must actually go out on the wire in the SUBSCRIBE packet."""
    protocol, transport = make_protocol(version="5.0")

    async def subscribe() -> None:
        await protocol.subscribe(
            [SubscriptionRequest(topic_filter="a/b", qos=QoS.AT_LEAST_ONCE)],
            subscription_identifier=7,
        )

    task = asyncio.create_task(subscribe())
    await asyncio.sleep(0)
    transport.feed(encode(SubAck(packet_id=1, return_codes=(0x01,)), version="5.0"))
    read = asyncio.create_task(protocol._read_loop())
    await task
    read.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await read

    buf = PacketBuffer(version="5.0")
    buf.feed(transport.sent[0])
    (packet,) = list(buf)
    assert isinstance(packet, Subscribe)
    assert packet.properties is not None
    assert packet.properties.subscription_identifier == 7


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
