from dataclasses import dataclass
from typing import ClassVar

from zmqtt.packets.properties import PubAckProperties, PublishProperties
from zmqtt.packets.types import Packet, PacketType
from zmqtt.types import QoS


@dataclass(frozen=True, slots=True, kw_only=True)
class Publish(Packet):
    packet_type: ClassVar[PacketType] = PacketType.PUBLISH

    topic: str
    payload: bytes
    qos: QoS
    retain: bool
    dup: bool
    packet_id: int | None = None  # required for QoS 1 and 2
    properties: PublishProperties | None = None  # v5 only


@dataclass(frozen=True, slots=True, kw_only=True)
class PubAck(Packet):
    packet_type: ClassVar[PacketType] = PacketType.PUBACK

    packet_id: int
    reason_code: int = 0  # v5 only; 0 = success
    properties: PubAckProperties | None = None  # v5 only


@dataclass(frozen=True, slots=True, kw_only=True)
class PubRec(Packet):
    packet_type: ClassVar[PacketType] = PacketType.PUBREC

    packet_id: int
    reason_code: int = 0  # v5 only; 0 = success
    properties: PubAckProperties | None = None  # v5 only


@dataclass(frozen=True, slots=True, kw_only=True)
class PubRel(Packet):
    packet_type: ClassVar[PacketType] = PacketType.PUBREL

    packet_id: int
    reason_code: int = 0  # v5 only; 0 = success
    properties: PubAckProperties | None = None  # v5 only


@dataclass(frozen=True, slots=True, kw_only=True)
class PubComp(Packet):
    packet_type: ClassVar[PacketType] = PacketType.PUBCOMP

    packet_id: int
    reason_code: int = 0  # v5 only; 0 = success
    properties: PubAckProperties | None = None  # v5 only
