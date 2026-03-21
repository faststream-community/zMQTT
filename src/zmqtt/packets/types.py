from dataclasses import dataclass
from enum import IntEnum
from typing import ClassVar


class PacketType(IntEnum):
    CONNECT = 1
    CONNACK = 2
    PUBLISH = 3
    PUBACK = 4
    PUBREC = 5
    PUBREL = 6
    PUBCOMP = 7
    SUBSCRIBE = 8
    SUBACK = 9
    UNSUBSCRIBE = 10
    UNSUBACK = 11
    PINGREQ = 12
    PINGRESP = 13
    DISCONNECT = 14
    AUTH = 15  # MQTT 5.0 only


@dataclass(frozen=True, slots=True, kw_only=True)
class FixedHeader:
    packet_type: PacketType
    flags: int  # lower 4 bits of first byte
    remaining_length: int


class Packet:
    """Concrete subclasses must declare: packet_type: ClassVar[PacketType]"""

    packet_type: ClassVar[PacketType]
