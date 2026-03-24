from dataclasses import dataclass
from typing import ClassVar

from zmqtt._internal.packets.properties import DisconnectProperties
from zmqtt._internal.packets.types import Packet, PacketType


@dataclass(frozen=True, slots=True, kw_only=True)
class Disconnect(Packet):
    packet_type: ClassVar[PacketType] = PacketType.DISCONNECT

    reason_code: int = 0  # v5 only; 0 = normal disconnection
    properties: DisconnectProperties | None = None  # v5 only
