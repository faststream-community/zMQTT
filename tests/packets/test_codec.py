"""Tests for zmqtt.packets.codec — encode/decode roundtrips."""

import pytest

from zmqtt._internal.packets.codec import decode, decode_varint, encode, encode_varint
from zmqtt._internal.packets.connect import ConnAck, Connect, Will
from zmqtt._internal.packets.disconnect import Disconnect
from zmqtt._internal.packets.ping import PingReq, PingResp
from zmqtt._internal.packets.publish import PubAck, PubComp, Publish, PubRec, PubRel
from zmqtt._internal.packets.subscribe import (
    SubAck,
    Subscribe,
    SubscriptionRequest,
    UnsubAck,
    Unsubscribe,
)
from zmqtt._internal.types.qos import QoS


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(0, b"\x00", id="zero"),
        pytest.param(1, b"\x01", id="one"),
        pytest.param(127, b"\x7f", id="max-one-byte"),
        pytest.param(128, b"\x80\x01", id="min-two-byte"),
        pytest.param(16_383, b"\xff\x7f", id="max-two-byte"),
        pytest.param(16_384, b"\x80\x80\x01", id="min-three-byte"),
        pytest.param(268_435_455, b"\xff\xff\xff\x7f", id="max-four-byte"),
    ],
)
def test_encode_varint(value: int, expected: bytes) -> None:
    assert encode_varint(value) == expected


@pytest.mark.parametrize(
    ("buf", "expected_value", "expected_consumed"),
    [
        pytest.param(b"\x00", 0, 1, id="zero"),
        pytest.param(b"\x7f", 127, 1, id="max-one-byte"),
        pytest.param(b"\x80\x01", 128, 2, id="min-two-byte"),
        pytest.param(b"\xff\x7f", 16_383, 2, id="max-two-byte"),
        pytest.param(b"\xff\xff\xff\x7f", 268_435_455, 4, id="max-four-byte"),
    ],
)
def test_decode_varint(buf: bytes, expected_value: int, expected_consumed: int) -> None:
    value, consumed = decode_varint(buf)
    assert value == expected_value
    assert consumed == expected_consumed


def test_decode_varint_offset() -> None:
    buf = b"\xaa\x80\x01\xbb"
    value, consumed = decode_varint(buf, offset=1)
    assert value == 128
    assert consumed == 2


def test_decode_varint_incomplete() -> None:
    with pytest.raises(ValueError, match="too short"):
        decode_varint(b"\x80")  # continuation bit set, no next byte


def test_encode_varint_out_of_range() -> None:
    with pytest.raises(ValueError, match="out of varint range"):
        encode_varint(268_435_456)
    with pytest.raises(ValueError, match="out of varint range"):
        encode_varint(-1)


def test_decode_empty() -> None:
    assert decode(b"") is None


def test_decode_one_byte() -> None:
    assert decode(b"\xc0") is None  # PINGREQ fixed header, missing remaining-length byte


def test_decode_incomplete_body() -> None:
    # PINGREQ is 2 bytes: C0 00. Give only the header byte + remaining-length byte
    data = b"\xc0\x02"  # says body is 2 bytes, but we provide none
    assert decode(data) is None


def test_pingreq_roundtrip() -> None:
    data = encode(PingReq(), version="3.1.1")
    assert data == b"\xc0\x00"
    result = decode(data)
    assert result is not None
    packet, consumed = result
    assert isinstance(packet, PingReq)
    assert consumed == 2


def test_pingresp_roundtrip() -> None:
    data = encode(PingResp(), version="3.1.1")
    assert data == b"\xd0\x00"
    result = decode(data)
    assert result is not None
    packet, _ = result
    assert isinstance(packet, PingResp)


def test_disconnect_roundtrip() -> None:
    data = encode(Disconnect(), version="3.1.1")
    assert data == b"\xe0\x00"
    result = decode(data)
    assert result is not None
    packet, _ = result
    assert isinstance(packet, Disconnect)


def test_connack_accepted() -> None:
    pkt = ConnAck(session_present=False, return_code=0)
    data = encode(pkt, version="3.1.1")
    assert data == b"\x20\x02\x00\x00"
    result = decode(data)
    assert result is not None
    decoded, consumed = result
    assert isinstance(decoded, ConnAck)
    assert decoded.session_present is False
    assert decoded.return_code == 0
    assert consumed == 4


def test_connack_session_present() -> None:
    pkt = ConnAck(session_present=True, return_code=0)
    data = encode(pkt, version="3.1.1")
    decoded_result = decode(data)
    assert decoded_result is not None
    decoded, _ = decoded_result
    assert isinstance(decoded, ConnAck)
    assert decoded.session_present is True


def test_connack_refused() -> None:
    pkt = ConnAck(session_present=False, return_code=4)
    data = encode(pkt, version="3.1.1")
    decoded_result = decode(data)
    assert decoded_result is not None
    decoded, _ = decoded_result
    assert isinstance(decoded, ConnAck)
    assert decoded.return_code == 4


def test_connect_minimal() -> None:
    pkt = Connect(client_id="test-client", clean_session=True, keepalive=60)
    data = encode(pkt, version="3.1.1")
    result = decode(data)
    assert result is not None
    decoded, consumed = result
    assert isinstance(decoded, Connect)
    assert decoded.client_id == "test-client"
    assert decoded.clean_session is True
    assert decoded.keepalive == 60
    assert decoded.username is None
    assert decoded.password is None
    assert decoded.will is None
    assert consumed == len(data)


def test_connect_with_credentials() -> None:
    pkt = Connect(
        client_id="client1",
        clean_session=False,
        keepalive=30,
        username="alice",
        password=b"s3cr3t",
    )
    data = encode(pkt, version="3.1.1")
    result = decode(data)
    assert result is not None
    decoded, _ = result
    assert isinstance(decoded, Connect)
    assert decoded.username == "alice"
    assert decoded.password == b"s3cr3t"
    assert decoded.clean_session is False


def test_connect_with_will() -> None:
    will = Will(
        topic="status/client1",
        payload=b"offline",
        qos=QoS.AT_LEAST_ONCE,
        retain=True,
    )
    pkt = Connect(client_id="client1", clean_session=True, keepalive=0, will=will)
    data = encode(pkt, version="3.1.1")
    result = decode(data)
    assert result is not None
    decoded, _ = result
    assert isinstance(decoded, Connect)
    assert decoded.will is not None
    assert decoded.will.topic == "status/client1"
    assert decoded.will.payload == b"offline"
    assert decoded.will.qos == QoS.AT_LEAST_ONCE
    assert decoded.will.retain is True


def test_connect_full() -> None:
    will = Will(topic="lwt", payload=b"gone", qos=QoS.EXACTLY_ONCE, retain=False)
    pkt = Connect(
        client_id="full-client",
        clean_session=True,
        keepalive=120,
        username="user",
        password=b"pass",
        will=will,
    )
    data = encode(pkt, version="3.1.1")
    result = decode(data)
    assert result is not None
    decoded, consumed = result
    assert isinstance(decoded, Connect)
    assert decoded.client_id == "full-client"
    assert decoded.will is not None
    assert decoded.will.qos == QoS.EXACTLY_ONCE
    assert consumed == len(data)


def test_publish_qos0() -> None:
    pkt = Publish(
        topic="sensors/temp",
        payload=b"23.5",
        qos=QoS.AT_MOST_ONCE,
        retain=False,
        dup=False,
    )
    data = encode(pkt, version="3.1.1")
    # First byte: 0x30 (PUBLISH, no flags)
    assert data[0] == 0x30
    result = decode(data)
    assert result is not None
    decoded, consumed = result
    assert isinstance(decoded, Publish)
    assert decoded.topic == "sensors/temp"
    assert decoded.payload == b"23.5"
    assert decoded.qos == QoS.AT_MOST_ONCE
    assert decoded.packet_id is None
    assert decoded.retain is False
    assert decoded.dup is False
    assert consumed == len(data)


def test_publish_qos1() -> None:
    pkt = Publish(
        topic="cmd/device",
        payload=b"\x01\x02\x03",
        qos=QoS.AT_LEAST_ONCE,
        retain=False,
        dup=False,
        packet_id=42,
    )
    data = encode(pkt, version="3.1.1")
    result = decode(data)
    assert result is not None
    decoded, _ = result
    assert isinstance(decoded, Publish)
    assert decoded.packet_id == 42
    assert decoded.qos == QoS.AT_LEAST_ONCE


def test_publish_qos2_dup_retain() -> None:
    pkt = Publish(
        topic="$SYS/retain",
        payload=b"",
        qos=QoS.EXACTLY_ONCE,
        retain=True,
        dup=True,
        packet_id=1000,
    )
    data = encode(pkt, version="3.1.1")
    # flags: dup=1, qos=2 (10), retain=1 → 1101 = 0x0D
    assert data[0] == 0x30 | 0x0D
    result = decode(data)
    assert result is not None
    decoded, _ = result
    assert isinstance(decoded, Publish)
    assert decoded.dup is True
    assert decoded.retain is True
    assert decoded.qos == QoS.EXACTLY_ONCE
    assert decoded.packet_id == 1000
    assert decoded.payload == b""


@pytest.mark.parametrize(
    ("packet", "expected_first_byte"),
    [
        pytest.param(PubAck(packet_id=1), 0x40, id="puback"),
        pytest.param(PubRec(packet_id=2), 0x50, id="pubrec"),
        pytest.param(PubRel(packet_id=3), 0x62, id="pubrel"),  # flags = 0b0010
        pytest.param(PubComp(packet_id=4), 0x70, id="pubcomp"),
    ],
)
def test_pubxxx_roundtrip(
    packet: PubAck | PubRec | PubRel | PubComp,
    expected_first_byte: int,
) -> None:
    data = encode(packet, version="3.1.1")
    assert data[0] == expected_first_byte
    assert len(data) == 4  # 1 byte header + 1 byte remaining-length + 2 bytes packet_id
    result = decode(data)
    assert result is not None
    decoded, consumed = result
    assert type(decoded) is type(packet)
    assert decoded.packet_id == packet.packet_id  # type: ignore[union-attr]
    assert consumed == 4


def test_subscribe_single() -> None:
    pkt = Subscribe(
        packet_id=10,
        subscriptions=(SubscriptionRequest(topic_filter="sensors/#", qos=QoS.AT_LEAST_ONCE),),
    )
    data = encode(pkt, version="3.1.1")
    assert data[0] == 0x82  # SUBSCRIBE with reserved flags 0b0010
    result = decode(data)
    assert result is not None
    decoded, consumed = result
    assert isinstance(decoded, Subscribe)
    assert decoded.packet_id == 10
    assert len(decoded.subscriptions) == 1
    assert decoded.subscriptions[0].topic_filter == "sensors/#"
    assert decoded.subscriptions[0].qos == QoS.AT_LEAST_ONCE
    assert consumed == len(data)


def test_subscribe_multiple() -> None:
    pkt = Subscribe(
        packet_id=99,
        subscriptions=(
            SubscriptionRequest(topic_filter="a/b", qos=QoS.AT_MOST_ONCE),
            SubscriptionRequest(topic_filter="c/d/+", qos=QoS.EXACTLY_ONCE),
        ),
    )
    data = encode(pkt, version="3.1.1")
    result = decode(data)
    assert result is not None
    decoded, _ = result
    assert isinstance(decoded, Subscribe)
    assert len(decoded.subscriptions) == 2
    assert decoded.subscriptions[1].topic_filter == "c/d/+"
    assert decoded.subscriptions[1].qos == QoS.EXACTLY_ONCE


def test_suback() -> None:
    pkt = SubAck(packet_id=10, return_codes=(0x00, 0x01, 0x80))
    data = encode(pkt, version="3.1.1")
    result = decode(data)
    assert result is not None
    decoded, consumed = result
    assert isinstance(decoded, SubAck)
    assert decoded.packet_id == 10
    assert decoded.return_codes == (0x00, 0x01, 0x80)
    assert consumed == len(data)


def test_unsubscribe() -> None:
    pkt = Unsubscribe(packet_id=7, topic_filters=("sensors/#", "cmd/+"))
    data = encode(pkt, version="3.1.1")
    assert data[0] == 0xA2  # UNSUBSCRIBE with reserved flags 0b0010
    result = decode(data)
    assert result is not None
    decoded, consumed = result
    assert isinstance(decoded, Unsubscribe)
    assert decoded.packet_id == 7
    assert decoded.topic_filters == ("sensors/#", "cmd/+")
    assert consumed == len(data)


def test_unsuback() -> None:
    pkt = UnsubAck(packet_id=7)
    data = encode(pkt, version="3.1.1")
    result = decode(data)
    assert result is not None
    decoded, consumed = result
    assert isinstance(decoded, UnsubAck)
    assert decoded.packet_id == 7
    assert consumed == 4


def test_decode_leaves_trailing_bytes() -> None:
    data = encode(PingReq(), version="3.1.1") + encode(PingResp(), version="3.1.1")
    result = decode(data)
    assert result is not None
    packet, consumed = result
    assert isinstance(packet, PingReq)
    assert consumed == 2  # only the first packet consumed

    result2 = decode(data[consumed:])
    assert result2 is not None
    packet2, _ = result2
    assert isinstance(packet2, PingResp)


def test_decode_accepts_memoryview() -> None:
    data = encode(ConnAck(session_present=False, return_code=0), version="3.1.1")
    result = decode(memoryview(data))
    assert result is not None
    packet, _ = result
    assert isinstance(packet, ConnAck)
