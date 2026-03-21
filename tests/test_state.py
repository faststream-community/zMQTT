"""Tests for zmqtt.state."""

import asyncio

import pytest

from zmqtt.packets.publish import PubAck, PubComp, Publish
from zmqtt.state import (
    InboundQoS2Flight,
    InboundQoS2State,
    OutboundQoS2Flight,
    OutboundQoS2State,
    PacketIdPool,
    QoS1Flight,
    SessionState,
    SubscriptionEntry,
)
from zmqtt.types import QoS


def test_acquire_returns_sequential_ids() -> None:
    pool = PacketIdPool()
    assert pool.acquire() == 1
    assert pool.acquire() == 2
    assert pool.acquire() == 3


def test_release_allows_reuse() -> None:
    pool = PacketIdPool()
    pid = pool.acquire()
    pool.release(pid)
    assert pool.acquire() == pid


def test_acquire_never_returns_zero() -> None:
    pool = PacketIdPool()
    ids = {pool.acquire() for _ in range(100)}
    assert 0 not in ids


def test_acquire_wraps_around() -> None:
    pool = PacketIdPool()
    # Exhaust up to some point
    for _ in range(65534):
        pool.acquire()
    # Release all then acquire — should wrap to 1
    for i in range(1, 65535):
        pool.release(i)
    next_id = pool.acquire()
    assert 1 <= next_id <= 65535


def test_acquire_raises_when_exhausted() -> None:
    pool = PacketIdPool()
    for _ in range(65535):
        pool.acquire()
    with pytest.raises(RuntimeError):
        pool.acquire()


def test_release_unknown_id_is_noop() -> None:
    pool = PacketIdPool()
    pool.release(999)  # should not raise


def test_qos1_flight_fields() -> None:
    loop = asyncio.new_event_loop()
    future: asyncio.Future[PubAck] = loop.create_future()
    publish = Publish(
        topic="a/b",
        payload=b"",
        qos=QoS.AT_LEAST_ONCE,
        retain=False,
        dup=False,
        packet_id=1,
    )
    flight = QoS1Flight(packet_id=1, publish=publish, future=future)
    assert flight.packet_id == 1
    assert flight.publish is publish
    loop.close()


def test_outbound_qos2_initial_state() -> None:
    loop = asyncio.new_event_loop()
    future: asyncio.Future[PubComp] = loop.create_future()
    publish = Publish(
        topic="a/b",
        payload=b"",
        qos=QoS.EXACTLY_ONCE,
        retain=False,
        dup=False,
        packet_id=2,
    )
    flight = OutboundQoS2Flight(
        packet_id=2,
        publish=publish,
        state=OutboundQoS2State.PENDING_PUBREC,
        future=future,
    )
    assert flight.state is OutboundQoS2State.PENDING_PUBREC
    flight.state = OutboundQoS2State.PENDING_PUBCOMP
    assert flight.state is OutboundQoS2State.PENDING_PUBCOMP
    loop.close()


def test_inbound_qos2_flight_fields() -> None:
    publish = Publish(
        topic="t",
        payload=b"x",
        qos=QoS.EXACTLY_ONCE,
        retain=False,
        dup=False,
        packet_id=3,
    )
    flight = InboundQoS2Flight(
        packet_id=3, publish=publish, state=InboundQoS2State.PENDING_PUBREL
    )
    assert flight.state is InboundQoS2State.PENDING_PUBREL


def test_session_state_starts_empty() -> None:
    state = SessionState()
    assert state.inflight_qos1 == {}
    assert state.inflight_qos2_out == {}
    assert state.inflight_qos2_in == {}
    assert state.subscriptions == {}
    assert state.pending_subs == {}
    assert state.pending_unsubs == {}


def test_session_state_clear() -> None:
    state = SessionState()
    state.packet_ids.acquire()
    state.inflight_qos1[1] = None  # type: ignore[assignment]
    state.subscriptions["test/#"] = SubscriptionEntry(queue=asyncio.Queue())
    state.clear()
    assert state.inflight_qos1 == {}
    assert state.subscriptions == {}
    # packet_ids reset
    assert state.packet_ids.acquire() == 1
