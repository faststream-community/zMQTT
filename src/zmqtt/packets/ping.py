from dataclasses import dataclass
from typing import ClassVar

from zmqtt.packets.types import Packet, PacketType


@dataclass(frozen=True, slots=True, kw_only=True)
class PingReq(Packet):
    packet_type: ClassVar[PacketType] = PacketType.PINGREQ


@dataclass(frozen=True, slots=True, kw_only=True)
class PingResp(Packet):
    packet_type: ClassVar[PacketType] = PacketType.PINGRESP
