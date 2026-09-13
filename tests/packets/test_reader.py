"""Tests for zmqtt.packets.reader — framing packets out of a TCP byte stream."""

import pytest

from zmqtt._internal.packets.codec import AnyPacket, encode
from zmqtt._internal.packets.publish import PubAck, Publish
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


def test_several_packets_in_one_read() -> None:
    wire, expected = _stream(5)
    buf = PacketBuffer()
    buf.feed(wire)

    assert list(buf) == expected


def test_packet_split_across_reads_is_held_until_complete() -> None:
    wire, expected = _stream(1)
    buf = PacketBuffer()

    buf.feed(wire[:4])
    assert list(buf) == []

    buf.feed(wire[4:])
    assert list(buf) == expected


@pytest.mark.parametrize("chunk_size", [1, 3, 7, 64], ids=lambda n: f"{n}-byte-reads")
def test_arbitrary_fragmentation_yields_the_same_packets(chunk_size: int) -> None:
    """TCP splits wherever it likes; the reader must not care where."""
    wire, expected = _stream(8)
    buf = PacketBuffer()

    decoded: list[AnyPacket] = []
    for offset in range(0, len(wire), chunk_size):
        buf.feed(wire[offset : offset + chunk_size])
        decoded.extend(buf)

    assert decoded == expected


def test_feeding_while_iterating() -> None:
    """The read loop awaits inside the iterator, so feeding into it must work."""
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


def test_abandoned_iterator_leaves_the_buffer_usable() -> None:
    """`_await_connack` returns out of the middle of the loop."""
    wire, expected = _stream(4)
    buf = PacketBuffer()
    buf.feed(wire)

    for packet in buf:
        first = packet
        break

    buf.feed(encode(PubAck(packet_id=99), version="3.1.1"))

    assert first == expected[0]
    assert list(buf) == [*expected[1:], PubAck(packet_id=99)]


def test_consumed_bytes_do_not_accumulate() -> None:
    """Consumed packets must be dropped, or a long-lived session grows forever."""
    wire, _ = _stream(20)
    buf = PacketBuffer()

    for _ in range(10):
        buf.feed(wire)
        assert len(list(buf)) == 20

    buf.feed(b"")
    assert len(buf._buf) == 0
