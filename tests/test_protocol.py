"""Tests for zmqtt.protocol — pure-logic and error-path tests only.

E2E observable behavior (QoS flows, subscribe/receive, ack) lives in
tests/test_brokers/_base.py and runs against real brokers.
"""

import asyncio
import logging
from collections import deque

import pytest

from zmqtt.errors import MQTTConnectError, MQTTProtocolError, MQTTTimeoutError
from zmqtt.packets.codec import encode
from zmqtt.packets.connect import ConnAck, Connect
from zmqtt.packets.publish import PubAck, Publish
from zmqtt.protocol import MQTTProtocol, _filter_specificity, _topic_matches
from zmqtt.state import SessionState, SubscriptionEntry
from zmqtt.types import Message, QoS


class FakeTransport:
    """In-memory transport: read() drains from rx_queue; write() appends to sent."""

    def __init__(self) -> None:
        self.sent: list[bytes] = []
        self._rx: deque[bytes | Exception] = deque()
        self._closed = False

    def feed(self, data: bytes) -> None:
        self._rx.append(data)

    async def read(self, n: int) -> bytes:
        while not self._rx:
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
    keepalive: int = 60, ping_timeout: float = 5.0
) -> tuple[MQTTProtocol, FakeTransport]:
    transport = FakeTransport()
    state = SessionState()
    protocol = MQTTProtocol(
        transport, state, keepalive=keepalive, ping_timeout=ping_timeout
    )
    return protocol, transport


async def _run_read_loop(protocol: MQTTProtocol) -> asyncio.Task[None]:
    task: asyncio.Task[None] = asyncio.create_task(protocol._read_loop())
    await asyncio.sleep(0)
    return task


async def _stop_task(task: asyncio.Task[None]) -> None:
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


# ------------------------------------------------------------------ topic matching


@pytest.mark.parametrize(
    ("filter_", "topic", "expected"),
    [
        pytest.param("sensors/#", "sensors/temp", True, id="hash-single-level"),
        pytest.param("sensors/#", "sensors/temp/room1", True, id="hash-multi-level"),
        pytest.param("sensors/#", "sensors", True, id="hash-bare"),
        pytest.param("sensors/+/temp", "sensors/room1/temp", True, id="plus-match"),
        pytest.param(
            "sensors/+/temp", "sensors/room1/humidity", False, id="plus-no-match"
        ),
        pytest.param("#", "any/topic", True, id="bare-hash-multi"),
        pytest.param("#", "any", True, id="bare-hash-single"),
        pytest.param("#", "$SYS/broker", False, id="bare-hash-dollar"),
        pytest.param("+/foo", "$SYS/foo", False, id="plus-dollar"),
        pytest.param("$SYS/#", "$SYS/broker/uptime", True, id="sys-hash"),
        pytest.param("exact/match", "exact/match", True, id="exact-match"),
        pytest.param("exact/match", "exact/other", False, id="exact-no-match"),
        pytest.param("a/+/c", "a/b/c", True, id="plus-middle-match"),
        pytest.param("a/+/c", "a/b/c/d", False, id="plus-middle-no-match"),
    ],
)
def test_topic_matches(filter_: str, topic: str, expected: bool) -> None:
    assert _topic_matches(filter_, topic) is expected


# ------------------------------------------------------------------ filter specificity


def test_filter_specificity_exact() -> None:
    assert _filter_specificity("a/b") == (0, 0)


def test_filter_specificity_plus() -> None:
    assert _filter_specificity("a/+/c") == (0, 1, 0)


def test_filter_specificity_hash() -> None:
    assert _filter_specificity("a/#") == (0, 2)


def test_filter_specificity_bare_hash() -> None:
    assert _filter_specificity("#") == (2,)


# ------------------------------------------------------------------ error paths unreachable E2E


async def test_connect_refused_raises() -> None:
    protocol, transport = make_protocol()
    connect = Connect(client_id="test", clean_session=True, keepalive=60)
    transport.feed(
        encode(ConnAck(session_present=False, return_code=4), version="3.1.1")
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
    protocol, transport = make_protocol(keepalive=0, ping_timeout=0.05)
    ping_task = asyncio.create_task(protocol._ping_loop())
    with pytest.raises(MQTTTimeoutError):
        await ping_task


# ------------------------------------------------------------------ internal delivery logic


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


async def test_deliver_tie_first_wins(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Forced specificity tie: first insertion-order entry wins, WARNING logged."""
    import zmqtt.protocol as proto_module

    protocol, _ = make_protocol()
    q1: asyncio.Queue[Message] = asyncio.Queue()
    q2: asyncio.Queue[Message] = asyncio.Queue()
    protocol._state.subscriptions["a/+"] = SubscriptionEntry(queue=q1, auto_ack=True)
    protocol._state.subscriptions["a/x"] = SubscriptionEntry(queue=q2, auto_ack=True)
    monkeypatch.setattr(proto_module, "_filter_specificity", lambda _f: (0, 0))

    with caplog.at_level(logging.WARNING, logger="zmqtt.protocol"):
        await protocol._deliver(
            Publish(
                topic="a/x", payload=b"v", qos=QoS.AT_MOST_ONCE, retain=False, dup=False
            ),
            ack_callback=None,
        )

    assert q1.qsize() == 1
    assert q2.qsize() == 0
    assert "equally-specific" in caplog.text


async def test_inbound_qos2_manual_ack_duplicate_ignored() -> None:
    """Broker retransmit before app calls ack() must not re-queue the message."""
    protocol, transport = make_protocol()
    transport.feed(
        encode(ConnAck(session_present=False, return_code=0), version="3.1.1")
    )
    await protocol.connect(Connect(client_id="c", clean_session=True, keepalive=60))

    queue: asyncio.Queue[Message] = asyncio.Queue()
    protocol._state.subscriptions["t/#"] = SubscriptionEntry(
        queue=queue, auto_ack=False
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
        )
    )
    await asyncio.sleep(0.05)

    assert queue.empty()
    assert transport.sent == []  # no PUBREC for duplicate

    await _stop_task(read_task)
