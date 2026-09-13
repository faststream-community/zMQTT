"""Tests for zmqtt.packets.reader — the one guarantee no e2e test can hold."""

from zmqtt._internal.packets.codec import encode
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


def _stream(count: int) -> bytes:
    return b"".join(encode(_publish(index), version="3.1.1") for index in range(count))


def test_consumed_bytes_do_not_accumulate() -> None:
    """Parsed bytes are dropped, so a long-lived session does not grow forever."""
    # Invisible from outside: without the compaction every packet still decodes
    # correctly and only memory changes, growing 1:1 with the bytes a connection
    # has received. The suites' connections are far too short-lived to show it.
    wire = _stream(20)
    buf = PacketBuffer()

    for _ in range(10):
        buf.feed(wire)
        assert len(list(buf)) == 20

    buf.feed(b"")
    assert len(buf._buf) == 0
