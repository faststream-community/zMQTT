from dataclasses import dataclass
from typing import ClassVar

from zmqtt._internal.packets.properties import AuthProperties
from zmqtt._internal.packets.types import Packet, PacketType


@dataclass(frozen=True, slots=True, kw_only=True)
class Auth(Packet):
    """AUTH packet is MQTT 5.0 only, used for enhanced authentication."""

    packet_type: ClassVar[PacketType] = PacketType.AUTH

    reason_code: int = 0x00  # 0x00 = success, 0x18 = continue, 0x19 = re-authenticate
    properties: AuthProperties | None = None
