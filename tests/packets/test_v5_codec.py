"""Tests for MQTT 5.0 packet encode/decode via zmqtt.packets.codec."""

from typing import Literal

import pytest

from zmqtt._internal.packets.auth import Auth
from zmqtt._internal.packets.codec import AnyPacket, decode, encode
from zmqtt._internal.packets.connect import ConnAck, Connect, Will
from zmqtt._internal.packets.disconnect import Disconnect
from zmqtt._internal.packets.properties import (
    AuthProperties,
    ConnAckProperties,
    ConnectProperties,
    DisconnectProperties,
    PubAckProperties,
    PublishProperties,
    SubAckProperties,
    SubscribeProperties,
    UnsubAckProperties,
    UnsubscribeProperties,
    WillProperties,
)
from zmqtt._internal.packets.publish import PubAck, PubComp, Publish, PubRec, PubRel
from zmqtt._internal.packets.subscribe import (
    SubAck,
    Subscribe,
    SubscriptionRequest,
    UnsubAck,
    Unsubscribe,
)
from zmqtt._internal.types.qos import QoS
from zmqtt._internal.types.retain_handling import RetainHandling


def roundtrip(packet: AnyPacket, version: Literal["3.1.1", "5.0"] = "5.0") -> AnyPacket:
    raw = encode(packet, version=version)
    result = decode(raw, version=version)
    assert result is not None
    packet_out, consumed = result
    assert consumed == len(raw)
    return packet_out


def test_v5_connect_minimal() -> None:
    pkt = Connect(client_id="test", clean_session=True, keepalive=60)
    out = roundtrip(pkt)
    assert isinstance(out, Connect)
    assert out.client_id == "test"
    assert out.properties is None


def test_v5_connect_with_properties() -> None:
    props = ConnectProperties(
        session_expiry_interval=300,
        receive_maximum=100,
        authentication_method="PLAIN",
    )
    pkt = Connect(client_id="c1", clean_session=False, keepalive=30, properties=props)
    out = roundtrip(pkt)
    assert isinstance(out, Connect)
    assert out.properties is not None
    assert out.properties.session_expiry_interval == 300
    assert out.properties.receive_maximum == 100
    assert out.properties.authentication_method == "PLAIN"


def test_v5_connect_with_will_and_will_properties() -> None:
    will_props = WillProperties(will_delay_interval=10, content_type="text/plain")
    will = Will(
        topic="lwt/topic",
        payload=b"offline",
        qos=QoS.AT_LEAST_ONCE,
        retain=False,
        properties=will_props,
    )
    pkt = Connect(client_id="c2", clean_session=True, keepalive=0, will=will)
    out = roundtrip(pkt)
    assert isinstance(out, Connect)
    assert out.will is not None
    assert out.will.topic == "lwt/topic"
    assert out.will.properties is not None
    assert out.will.properties.will_delay_interval == 10
    assert out.will.properties.content_type == "text/plain"


def test_v5_connect_full() -> None:
    connect_props = ConnectProperties(
        session_expiry_interval=60,
        user_properties=(("k", "v"),),
    )
    will_props = WillProperties(message_expiry_interval=120, correlation_data=b"\xab")
    will = Will(
        topic="t/lwt",
        payload=b"bye",
        qos=QoS.AT_MOST_ONCE,
        retain=True,
        properties=will_props,
    )
    pkt = Connect(
        client_id="full",
        clean_session=True,
        keepalive=90,
        username="user",
        password=b"pass",
        will=will,
        properties=connect_props,
    )
    out = roundtrip(pkt)
    assert isinstance(out, Connect)
    assert out.username == "user"
    assert out.password == b"pass"
    assert out.properties is not None
    assert out.properties.session_expiry_interval == 60
    assert out.properties.user_properties == (("k", "v"),)
    assert out.will is not None
    assert out.will.properties is not None
    assert out.will.properties.message_expiry_interval == 120


def test_v5_connack_no_properties() -> None:
    pkt = ConnAck(session_present=False, return_code=0)
    out = roundtrip(pkt)
    assert isinstance(out, ConnAck)
    assert out.return_code == 0
    assert out.properties is None


def test_v5_connack_with_properties() -> None:
    props = ConnAckProperties(
        session_expiry_interval=600,
        receive_maximum=50,
        assigned_client_identifier="srv-assigned-id",
        retain_available=True,
        wildcard_subscription_available=True,
        subscription_identifier_available=True,
        shared_subscription_available=False,
    )
    pkt = ConnAck(session_present=True, return_code=0, properties=props)
    out = roundtrip(pkt)
    assert isinstance(out, ConnAck)
    assert out.session_present is True
    assert out.properties is not None
    assert out.properties.assigned_client_identifier == "srv-assigned-id"
    assert out.properties.retain_available is True
    assert out.properties.shared_subscription_available is False


def test_v5_publish_qos0_no_properties() -> None:
    pkt = Publish(
        topic="a/b",
        payload=b"hello",
        qos=QoS.AT_MOST_ONCE,
        retain=False,
        dup=False,
    )
    out = roundtrip(pkt)
    assert isinstance(out, Publish)
    assert out.topic == "a/b"
    assert out.payload == b"hello"
    assert out.properties is None


def test_v5_publish_with_properties() -> None:
    props = PublishProperties(
        payload_format_indicator=1,
        content_type="application/json",
        response_topic="resp/a",
        correlation_data=b"\x01\x02",
        topic_alias=5,
        message_expiry_interval=30,
        user_properties=(("trace", "abc"),),
    )
    pkt = Publish(
        topic="a/b",
        payload=b'{"x":1}',
        qos=QoS.AT_LEAST_ONCE,
        retain=False,
        dup=False,
        packet_id=42,
        properties=props,
    )
    out = roundtrip(pkt)
    assert isinstance(out, Publish)
    assert out.properties is not None
    assert out.properties.content_type == "application/json"
    assert out.properties.topic_alias == 5
    assert out.properties.user_properties == (("trace", "abc"),)


def test_v5_publish_subscription_identifier() -> None:
    props = PublishProperties(subscription_identifier=128)
    pkt = Publish(
        topic="t",
        payload=b"x",
        qos=QoS.AT_MOST_ONCE,
        retain=False,
        dup=False,
        properties=props,
    )
    out = roundtrip(pkt)
    assert isinstance(out, Publish)
    assert out.properties is not None
    assert out.properties.subscription_identifier == 128


@pytest.mark.parametrize(
    ("cls", "reason"),
    [
        pytest.param(PubAck, 0x10, id="puback"),
        pytest.param(PubRec, 0x10, id="pubrec"),
        pytest.param(PubRel, 0x92, id="pubrel"),
        pytest.param(PubComp, 0x00, id="pubcomp"),
    ],
)
def test_v5_pub_ack_variants_with_reason(cls: type, reason: int) -> None:
    pkt = cls(packet_id=100, reason_code=reason)
    out = roundtrip(pkt)
    assert out
    assert out.packet_id == 100  # type: ignore[union-attr]
    assert out.reason_code == reason  # type: ignore[union-attr]


def test_v5_puback_success_compact() -> None:
    """reason_code=0 + no properties → compact form (just packet ID)."""
    pkt = PubAck(packet_id=1)
    raw = encode(pkt, version="5.0")
    assert len(raw) == 4  # fixed(1) + remlen(1) + pid(2)


def test_v5_puback_with_properties() -> None:
    props = PubAckProperties(reason_string="not auth", user_properties=(("a", "b"),))
    pkt = PubAck(packet_id=7, reason_code=0x87, properties=props)
    out = roundtrip(pkt)
    assert isinstance(out, PubAck)
    assert out.reason_code == 0x87
    assert out.properties is not None
    assert out.properties.reason_string == "not auth"


def test_v5_subscribe_basic_options() -> None:
    sub = SubscriptionRequest(
        topic_filter="a/+",
        qos=QoS.EXACTLY_ONCE,
        no_local=True,
        retain_as_published=True,
        retain_handling=RetainHandling.DO_NOT_SEND,
    )
    pkt = Subscribe(packet_id=1, subscriptions=(sub,))
    out = roundtrip(pkt)
    assert isinstance(out, Subscribe)
    assert len(out.subscriptions) == 1
    s = out.subscriptions[0]
    assert s.qos == QoS.EXACTLY_ONCE
    assert s.no_local is True
    assert s.retain_as_published is True
    assert s.retain_handling == RetainHandling.DO_NOT_SEND


def test_v5_subscribe_with_properties() -> None:
    props = SubscribeProperties(subscription_identifier=3)
    sub = SubscriptionRequest(topic_filter="t/#", qos=QoS.AT_LEAST_ONCE)
    pkt = Subscribe(packet_id=2, subscriptions=(sub,), properties=props)
    out = roundtrip(pkt)
    assert isinstance(out, Subscribe)
    assert out.properties is not None
    assert out.properties.subscription_identifier == 3


def test_v5_subscribe_multiple_subs() -> None:
    subs = (
        SubscriptionRequest(topic_filter="a", qos=QoS.AT_MOST_ONCE, no_local=False),
        SubscriptionRequest(topic_filter="b", qos=QoS.AT_LEAST_ONCE, no_local=True),
    )
    pkt = Subscribe(packet_id=5, subscriptions=subs)
    out = roundtrip(pkt)
    assert isinstance(out, Subscribe)
    assert len(out.subscriptions) == 2
    assert out.subscriptions[1].no_local is True


def test_v5_suback_with_properties() -> None:
    props = SubAckProperties(reason_string="ok")
    pkt = SubAck(packet_id=3, return_codes=(0x00, 0x01, 0x80), properties=props)
    out = roundtrip(pkt)
    assert isinstance(out, SubAck)
    assert out.return_codes == (0x00, 0x01, 0x80)
    assert out.properties is not None
    assert out.properties.reason_string == "ok"


def test_v5_unsubscribe_with_properties() -> None:

    props = UnsubscribeProperties(user_properties=(("req", "1"),))
    pkt = Unsubscribe(packet_id=9, topic_filters=("a/b", "c/d"), properties=props)
    out = roundtrip(pkt)
    assert isinstance(out, Unsubscribe)
    assert out.topic_filters == ("a/b", "c/d")
    assert out.properties is not None
    assert out.properties.user_properties == (("req", "1"),)


def test_v5_unsuback_with_reason_codes() -> None:
    props = UnsubAckProperties(reason_string="done")
    pkt = UnsubAck(packet_id=10, reason_codes=(0x00, 0x11), properties=props)
    out = roundtrip(pkt)
    assert isinstance(out, UnsubAck)
    assert out.reason_codes == (0x00, 0x11)
    assert out.properties is not None
    assert out.properties.reason_string == "done"


def test_v5_disconnect_normal() -> None:
    pkt = Disconnect()
    raw = encode(pkt, version="5.0")
    assert len(raw) == 2  # reason_code=0, no props → compact form


def test_v5_disconnect_with_reason() -> None:
    pkt = Disconnect(reason_code=0x8E)
    out = roundtrip(pkt)
    assert isinstance(out, Disconnect)
    assert out.reason_code == 0x8E


def test_v5_disconnect_with_properties() -> None:
    props = DisconnectProperties(
        session_expiry_interval=0,
        reason_string="session taken over",
        server_reference="other.broker",
    )
    pkt = Disconnect(reason_code=0x8E, properties=props)
    out = roundtrip(pkt)
    assert isinstance(out, Disconnect)
    assert out.reason_code == 0x8E
    assert out.properties is not None
    assert out.properties.reason_string == "session taken over"
    assert out.properties.server_reference == "other.broker"


def test_v5_auth_basic() -> None:
    pkt = Auth(reason_code=0x18)
    out = roundtrip(pkt)
    assert isinstance(out, Auth)
    assert out.reason_code == 0x18
    assert out.properties is None


def test_v5_auth_with_properties() -> None:
    props = AuthProperties(
        authentication_method="SCRAM-SHA-256",
        authentication_data=b"\x01\x02\x03",
        reason_string="continue",
    )
    pkt = Auth(reason_code=0x18, properties=props)
    out = roundtrip(pkt)
    assert isinstance(out, Auth)
    assert out.properties is not None
    assert out.properties.authentication_method == "SCRAM-SHA-256"
    assert out.properties.authentication_data == b"\x01\x02\x03"


def test_v311_connect_ignores_properties() -> None:
    """Properties on a Connect packet are not encoded in v3.1.1 mode."""
    props = ConnectProperties(session_expiry_interval=60)
    pkt = Connect(client_id="c", clean_session=True, keepalive=0, properties=props)
    raw = encode(pkt, version="3.1.1")
    # In v3.1.1, protocol level byte is 0x04
    # Decode the same bytes as v3.1.1 — should succeed with no properties
    result = decode(raw, version="3.1.1")
    assert result is not None
    out, _ = result
    assert isinstance(out, Connect)
    assert out.properties is None


def test_v311_disconnect_empty() -> None:
    pkt = Disconnect(
        reason_code=0x8E,
        properties=DisconnectProperties(reason_string="x"),
    )
    raw = encode(pkt, version="3.1.1")
    result = decode(raw, version="3.1.1")
    assert result is not None
    out, _ = result
    assert isinstance(out, Disconnect)
    assert out.reason_code == 0  # not encoded in v3.1.1


def test_v311_subscribe_options_ignored() -> None:
    sub = SubscriptionRequest(
        topic_filter="t/#",
        qos=QoS.AT_LEAST_ONCE,
        no_local=True,
        retain_as_published=True,
        retain_handling=RetainHandling.DO_NOT_SEND,
    )
    pkt = Subscribe(packet_id=1, subscriptions=(sub,))
    raw = encode(pkt, version="3.1.1")
    result = decode(raw, version="3.1.1")
    assert result is not None
    out, _ = result
    assert isinstance(out, Subscribe)
    # v3.1.1 decode — options byte only carries QoS
    assert out.subscriptions[0].qos == QoS.AT_LEAST_ONCE
    assert out.subscriptions[0].no_local is False
