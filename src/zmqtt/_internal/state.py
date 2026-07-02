"""Session state and in-flight QoS tracking."""

import asyncio
from dataclasses import dataclass
from enum import Enum

from zmqtt._internal.packets.publish import PubAck, PubComp, Publish
from zmqtt._internal.packets.subscribe import SubAck, UnsubAck
from zmqtt._internal.subscription_index import SubscriptionIndex
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


class OutboundQoS2State(Enum):
    PENDING_PUBREC = "pending_pubrec"
    PENDING_PUBCOMP = "pending_pubcomp"


@dataclass(slots=True)
class OutboundQoS2Flight:
    packet_id: int
    publish: Publish
    state: OutboundQoS2State
    future: asyncio.Future[PubComp]


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


class SubscriptionRegistry:
    def __init__(self, index: SubscriptionIndex) -> None:
        self._items: dict[str, SubscriptionEntry] = {}
        self._index = index

    def find(self, key: str) -> bool:
        return key in self._items

    def get(self, key: str, default: SubscriptionEntry | None = None) -> SubscriptionEntry | None:
        return self._items.get(key, default)

    def add(self, key: str, value: SubscriptionEntry) -> None:
        existing = self._items.get(key)
        if existing is not None:
            self._index.remove(existing.actual_filter, existing)
        self._items[key] = value
        self._index.add(value.actual_filter, value)

    def remove(self, key: str) -> SubscriptionEntry | None:
        value = self._items.pop(key, None)
        if value is None:
            return None
        self._index.remove(value.actual_filter, value)
        return value

    def clear(self) -> None:
        for value in self._items.values():
            self._index.remove(value.actual_filter, value)
        self._items.clear()

    def update(self, other: dict[str, SubscriptionEntry] | None = None, **kwargs: SubscriptionEntry) -> None:
        if other is not None:
            for key, value in other.items():
                self.add(key, value)
        for key, value in kwargs.items():
            self.add(key, value)


class SessionState:
    """All mutable per-connection session state. No I/O."""

    def __init__(self) -> None:
        self.packet_ids: PacketIdPool = PacketIdPool()
        self.inflight_qos1: dict[int, QoS1Flight] = {}
        self.inflight_qos2_out: dict[int, OutboundQoS2Flight] = {}
        self.inflight_qos2_in: dict[int, InboundQoS2Flight] = {}
        # QoS 2 inbound: packet_ids received but not yet acked (PUBREC not sent)
        self.pending_ack_qos2_in: set[int] = set()
        self.subscription_index = SubscriptionIndex()
        # topic filter → subscription entry; registered before SUBSCRIBE is sent
        self.subscriptions: SubscriptionRegistry = SubscriptionRegistry(self.subscription_index)
        # pending protocol acks keyed by packet_id
        self.pending_subs: dict[int, asyncio.Future[SubAck]] = {}
        self.pending_unsubs: dict[int, asyncio.Future[UnsubAck]] = {}

    def clear(self) -> None:
        """Reset all state; called on clean-session connect."""
        self.packet_ids = PacketIdPool()
        self.inflight_qos1.clear()
        self.inflight_qos2_out.clear()
        self.inflight_qos2_in.clear()
        self.pending_ack_qos2_in.clear()
        self.subscriptions.clear()
        self.subscription_index.clear()
        self.pending_subs.clear()
        self.pending_unsubs.clear()
