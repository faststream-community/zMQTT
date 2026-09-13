"""Incremental buffer reader for MQTT packet framing over TCP."""

from collections.abc import Iterator
from typing import Final, Literal

from zmqtt._internal.packets.codec import AnyPacket, decode


class PacketBuffer:
    """Accumulates incoming bytes and yields complete packets.

    TCP delivers bytes in arbitrary chunks — a packet may arrive across
    multiple reads, or multiple packets in a single read. Feed bytes as they
    arrive; iterate to consume all fully-received packets.

    Drain the iterator before feeding again. Iteration holds a ``memoryview``
    of the buffer so that consumed bytes are skipped rather than copied out,
    and a ``bytearray`` cannot be resized while a view on it is exported, so a
    ``feed`` mid-iteration raises ``BufferError``. Leaving the loop early is
    fine: closing the iterator releases the view.
    """

    def __init__(self, version: Literal["3.1.1", "5.0"] = "3.1.1") -> None:
        self._buf: bytearray = bytearray()
        self._offset = 0
        self._version: Final = version

    def feed(self, data: bytes) -> None:
        """Add bytes from one socket read, dropping whatever has been parsed."""
        if self._offset:
            del self._buf[: self._offset]
            self._offset = 0
        self._buf += data

    def __iter__(self) -> Iterator[AnyPacket]:
        view = memoryview(self._buf)
        try:
            while True:
                result = decode(view[self._offset :], version=self._version)
                if result is None:
                    return
                packet, consumed = result
                self._offset += consumed
                yield packet
        finally:
            view.release()
