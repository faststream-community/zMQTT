from dataclasses import dataclass
from typing import ClassVar

from zmqtt.packets.properties import (
    SubAckProperties,
    SubscribeProperties,
    UnsubAckProperties,
    UnsubscribeProperties,
)
from zmqtt.packets.types import Packet, PacketType
from zmqtt.types import QoS, RetainHandling


@dataclass(frozen=True, slots=True, kw_only=True)
class SubscriptionRequest:
    """A single topic subscription within a SUBSCRIBE packet."""

    topic_filter: str
    qos: QoS
    no_local: bool = False  # v5 only
    retain_as_published: bool = False  # v5 only
    retain_handling: RetainHandling = RetainHandling.SEND_ON_SUBSCRIBE  # v5 only


@dataclass(frozen=True, slots=True, kw_only=True)
class Subscribe(Packet):
    packet_type: ClassVar[PacketType] = PacketType.SUBSCRIBE

    packet_id: int
    subscriptions: tuple[SubscriptionRequest, ...]
    properties: SubscribeProperties | None = None  # v5 only


@dataclass(frozen=True, slots=True, kw_only=True)
class SubAck(Packet):
    packet_type: ClassVar[PacketType] = PacketType.SUBACK

    packet_id: int
    return_codes: tuple[
        int, ...
    ]  # per sub: 0x00/0x01/0x02 = QoS granted, 0x80 = failure
    properties: SubAckProperties | None = None  # v5 only


@dataclass(frozen=True, slots=True, kw_only=True)
class Unsubscribe(Packet):
    packet_type: ClassVar[PacketType] = PacketType.UNSUBSCRIBE

    packet_id: int
    topic_filters: tuple[str, ...]
    properties: UnsubscribeProperties | None = None  # v5 only


@dataclass(frozen=True, slots=True, kw_only=True)
class UnsubAck(Packet):
    packet_type: ClassVar[PacketType] = PacketType.UNSUBACK

    packet_id: int
    reason_codes: tuple[int, ...] = ()  # v5 only — one per unsubscribed topic
    properties: UnsubAckProperties | None = None  # v5 only
