"""Session state and in-flight QoS tracking."""

import asyncio
from dataclasses import dataclass
from enum import Enum

from zmqtt._internal.packets.publish import PubAck, PubComp, Publish
from zmqtt._internal.packets.subscribe import SubAck, UnsubAck
from zmqtt._internal.types.message import Message


class PacketIdPool:
    """Allocates 16-bit packet IDs (range 1-65535); reuses after release."""

    def __init__(self) -> None:
        self._next: int = 1
        self._in_use: set[int] = set()

    def acquire(self) -> int:
        if len(self._in_use) >= 65535:  # noqa: PLR2004
            msg = "All 65535 packet IDs are in use"
            raise RuntimeError(msg)
        while self._next in self._in_use:
            self._next = self._next % 65535 + 1
        pid = self._next
        self._in_use.add(pid)
        self._next = pid % 65535 + 1
        return pid

    def release(self, pid: int) -> None:
        self._in_use.discard(pid)
        self._next = min(self._next, pid)


@dataclass(slots=True)
class QoS1Flight:
    packet_id: int
    publish: Publish
    future: asyncio.Future[PubAck]
    retransmit_task: asyncio.Task[None] | None = None


class OutboundQoS2State(Enum):
    PENDING_PUBREC = "pending_pubrec"
    PENDING_PUBCOMP = "pending_pubcomp"


@dataclass(slots=True)
class OutboundQoS2Flight:
    packet_id: int
    publish: Publish
    state: OutboundQoS2State
    future: asyncio.Future[PubComp]
    retransmit_task: asyncio.Task[None] | None = None


class InboundQoS2State(Enum):
    PENDING_PUBREL = "pending_pubrel"


@dataclass(slots=True)
class InboundQoS2Flight:
    packet_id: int
    publish: Publish
    state: InboundQoS2State


@dataclass(slots=True, kw_only=True)
class SubscriptionEntry:
    queue: asyncio.Queue[Message]
    auto_ack: bool = True
    actual_filter: str = ""  # filter with $share/<group>/ stripped; set on creation


class SessionState:
    """All mutable per-connection session state. No I/O."""

    def __init__(self) -> None:
        self.packet_ids: PacketIdPool = PacketIdPool()
        self.inflight_qos1: dict[int, QoS1Flight] = {}
        self.inflight_qos2_out: dict[int, OutboundQoS2Flight] = {}
        self.inflight_qos2_in: dict[int, InboundQoS2Flight] = {}
        # QoS 2 inbound: packet_ids received but not yet acked (PUBREC not sent)
        self.pending_ack_qos2_in: set[int] = set()
        # topic filter → subscription entry; registered before SUBSCRIBE is sent
        self.subscriptions: dict[str, SubscriptionEntry] = {}
        # pending protocol acks keyed by packet_id
        self.pending_subs: dict[int, asyncio.Future[SubAck]] = {}
        self.pending_unsubs: dict[int, asyncio.Future[UnsubAck]] = {}

    def clear(self) -> None:
        """Reset all state; called on clean-session connect."""
        self.packet_ids = PacketIdPool()
        for qos1_flight in self.inflight_qos1.values():
            if qos1_flight is not None and qos1_flight.retransmit_task is not None:
                qos1_flight.retransmit_task.cancel()
        for qos2_flight in self.inflight_qos2_out.values():
            if qos2_flight is not None and qos2_flight.retransmit_task is not None:
                qos2_flight.retransmit_task.cancel()
        self.inflight_qos1.clear()
        self.inflight_qos2_out.clear()
        self.inflight_qos2_in.clear()
        self.pending_ack_qos2_in.clear()
        self.subscriptions.clear()
        self.pending_subs.clear()
        self.pending_unsubs.clear()
