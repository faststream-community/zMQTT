"""Incremental buffer reader for MQTT packet framing over TCP."""

from collections.abc import Iterator
from typing import Final, Literal

from zmqtt._internal.packets.codec import AnyPacket, decode


class PacketBuffer:
    """Accumulates incoming bytes and yields complete packets.

    TCP delivers bytes in arbitrary chunks — a packet may arrive across
    multiple reads, or multiple packets in a single read. Feed bytes as they
    arrive; iterate to consume all fully-received packets.
    """

    def __init__(self, version: Literal["3.1.1", "5.0"] = "3.1.1") -> None:
        self._buf: bytearray = bytearray()
        self._version: Final = version

    def feed(self, data: bytes) -> None:
        self._buf += data

    def __iter__(self) -> Iterator[AnyPacket]:
        while True:
            result = decode(bytes(self._buf), version=self._version)
            if result is None:
                return
            packet, consumed = result
            del self._buf[:consumed]
            yield packet
