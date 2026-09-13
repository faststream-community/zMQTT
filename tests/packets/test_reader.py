"""The two framing guarantees that only a direct test can hold.

Everything else about `PacketBuffer` is already covered from the outside, and
covered well: the e2e suite alone executes 100% of the lines and branches in
`reader.py`, and a behaviour-level fragmentation test — a publish stream pushed
through `MQTTProtocol` in 1/3/7/64-byte reads — catches nothing the broker
tests do not already catch. Those tests were written and dropped again.

What does not survive the move to the outside are the two below. Both were
checked by breaking the reader on purpose and running everything: the unit
suite (239 tests) and the mosquitto e2e suite (113 tests against a real broker)
pass in both cases, while the test here fails.
"""

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
    """Feeding into a live iterator must work, even though nothing does it yet.

    Holding one `memoryview` for the whole of `__iter__` instead of taking and
    releasing one per decode is faster (+26-33% rather than +18-30%) and passes
    every other test. It also makes this raise `BufferError: Existing exports
    of data: object cannot be re-sized`, because a bytearray cannot be resized
    while a view on it is exported.

    No public path reaches that today — `_read_loop` drains the iterator before
    it feeds — so no behaviour test can exist for it. The class does not promise
    the narrower contract either, and `_await_connack` already walks out of the
    middle of the same iterator. This pins the contract so the faster variant
    cannot be adopted silently.
    """
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
    """Parsed bytes must be dropped, or a long-lived session grows forever.

    Deleting the compaction from `feed` leaves every packet decoding correctly
    and the buffer keeping every byte the connection ever received. Nothing
    observable from outside changes; only memory does, and the connections in
    the test suites are too short-lived to show it.

    Measuring it from outside was tried and does not work: with 8 MiB of
    traffic through a lazily generated transport and the subscriber queue
    drained concurrently, `tracemalloc` reports a 45 MiB peak on the correct
    implementation — the messages in flight bury a 64 KiB signal — and the test
    takes 16 seconds.
    """
    wire, _ = _stream(20)
    buf = PacketBuffer()

    for _ in range(10):
        buf.feed(wire)
        assert len(list(buf)) == 20

    buf.feed(b"")
    assert len(buf._buf) == 0
