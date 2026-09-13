"""Tests for zmqtt.packets.reader — only the guarantees no e2e test can hold."""

from zmqtt._internal.packets.codec import AnyPacket, encode
from zmqtt._internal.packets.publish import Publish
from zmqtt._internal.packets.reader import PacketBuffer
from zmqtt._internal.types.qos import QoS


def _publish(index: int, payload_size: int = 32) -> Publish:
    return Publish(
        topic=f"sensors/{index}/temperature",
        payload=bytes([index % 256]) * payload_size,
        qos=QoS.AT_LEAST_ONCE,
        retain=False,
        dup=False,
        packet_id=index + 1,
    )


def _stream(count: int) -> tuple[bytes, list[Publish]]:
    packets = [_publish(index) for index in range(count)]
    return b"".join(encode(p, version="3.1.1") for p in packets), packets


def test_feeding_while_iterating() -> None:
    """A feed() during iteration is accepted and the rest of the stream follows."""
    # No public path reaches this — _read_loop drains before it feeds — so no
    # e2e test can hold it. It rules out keeping one memoryview for the whole
    # __iter__: faster, passes everything else, raises BufferError here.
    wire, expected = _stream(3)
    buf = PacketBuffer()
    buf.feed(wire[: len(wire) // 2])

    decoded: list[AnyPacket] = []
    for packet in buf:
        decoded.append(packet)
        if len(decoded) == 1:
            buf.feed(wire[len(wire) // 2 :])

    decoded.extend(buf)
    assert decoded == expected


def test_consumed_bytes_do_not_accumulate() -> None:
    """Parsed bytes are dropped, so a long-lived session does not grow forever."""
    # Invisible from outside: without the compaction every packet still decodes
    # correctly and only memory changes. Measured through the client it drowns —
    # 45 MiB of tracemalloc noise on correct code against a 64 KiB signal.
    wire, _ = _stream(20)
    buf = PacketBuffer()

    for _ in range(10):
        buf.feed(wire)
        assert len(list(buf)) == 20

    buf.feed(b"")
    assert len(buf._buf) == 0
