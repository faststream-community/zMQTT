from dataclasses import dataclass
from typing import ClassVar

from zmqtt.packets.properties import (
    ConnAckProperties,
    ConnectProperties,
    WillProperties,
)
from zmqtt.packets.types import Packet, PacketType
from zmqtt.types import QoS


@dataclass(frozen=True, slots=True, kw_only=True)
class Will:
    """Last Will and Testament embedded in CONNECT."""

    topic: str
    payload: bytes
    qos: QoS
    retain: bool
    properties: WillProperties | None = None  # v5 only


@dataclass(frozen=True, slots=True, kw_only=True)
class Connect(Packet):
    packet_type: ClassVar[PacketType] = PacketType.CONNECT

    client_id: str
    clean_session: bool
    keepalive: int
    username: str | None = None
    password: bytes | None = None
    will: Will | None = None
    properties: ConnectProperties | None = None  # v5 only


@dataclass(frozen=True, slots=True, kw_only=True)
class ConnAck(Packet):
    packet_type: ClassVar[PacketType] = PacketType.CONNACK

    session_present: bool
    return_code: int  # 0 = accepted; 1–5 = refused (v3.1.1) / reason code (v5)
    properties: ConnAckProperties | None = None  # v5 only
