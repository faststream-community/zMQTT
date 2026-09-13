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
        self._offset = 0
        self._version: Final = version

    def feed(self, data: bytes) -> None:
        if self._offset:
            del self._buf[: self._offset]
            self._offset = 0
        self._buf += data

    def __iter__(self) -> Iterator[AnyPacket]:
        while True:
            # Decode through a view so that consumed bytes are skipped rather
            # than copied out, and release it before yielding: callers abandon
            # this iterator mid-flight and await inside it, and a bytearray
            # cannot be resized while a memoryview on it is still exported.
            view = memoryview(self._buf)[self._offset :]
            try:
                result = decode(view, version=self._version)
            finally:
                view.release()

            if result is None:
                return
            packet, consumed = result
            self._offset += consumed
            yield packet
