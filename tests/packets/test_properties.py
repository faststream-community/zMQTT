"""Tests for zmqtt.packets.properties — encode/decode roundtrips."""

import pytest

from zmqtt.packets._wire import encode_varint
from zmqtt.packets.properties import (
    AuthProperties,
    ConnAckProperties,
    ConnectProperties,
    DisconnectProperties,
    PropertyID,
    PubAckProperties,
    PublishProperties,
    SubAckProperties,
    SubscribeProperties,
    UnsubAckProperties,
    UnsubscribeProperties,
    WillProperties,
    decode_auth_properties,
    decode_connack_properties,
    decode_connect_properties,
    decode_disconnect_properties,
    decode_props_block,
    decode_puback_properties,
    decode_publish_properties,
    decode_suback_properties,
    decode_subscribe_properties,
    decode_unsuback_properties,
    decode_unsubscribe_properties,
    decode_will_properties,
    encode_auth_properties,
    encode_connack_properties,
    encode_connect_properties,
    encode_disconnect_properties,
    encode_props_block,
    encode_puback_properties,
    encode_publish_properties,
    encode_suback_properties,
    encode_subscribe_properties,
    encode_unsuback_properties,
    encode_unsubscribe_properties,
    encode_will_properties,
)


def test_empty_props_block() -> None:
    encoded = encode_props_block(None)
    assert encoded == b"\x00"  # varint(0)


def test_empty_props_block_decode() -> None:
    d, n = decode_props_block(b"\x00", 0)
    assert d == {}
    assert n == 1


def test_props_block_roundtrip_single_uint32() -> None:
    d = {PropertyID.SESSION_EXPIRY_INTERVAL: 3600}
    raw = encode_props_block(d)
    decoded, n = decode_props_block(raw, 0)
    assert n == len(raw)
    assert decoded[PropertyID.SESSION_EXPIRY_INTERVAL] == 3600


def test_props_block_roundtrip_uint16() -> None:
    d = {PropertyID.RECEIVE_MAXIMUM: 256}
    raw = encode_props_block(d)
    decoded, _ = decode_props_block(raw, 0)
    assert decoded[PropertyID.RECEIVE_MAXIMUM] == 256


def test_props_block_roundtrip_byte() -> None:
    d = {PropertyID.PAYLOAD_FORMAT_INDICATOR: 1}
    raw = encode_props_block(d)
    decoded, _ = decode_props_block(raw, 0)
    assert decoded[PropertyID.PAYLOAD_FORMAT_INDICATOR] == 1


def test_props_block_roundtrip_varint() -> None:
    d = {PropertyID.SUBSCRIPTION_IDENTIFIER: 500}
    raw = encode_props_block(d)
    decoded, _ = decode_props_block(raw, 0)
    assert decoded[PropertyID.SUBSCRIPTION_IDENTIFIER] == 500


def test_props_block_roundtrip_string() -> None:
    d = {PropertyID.CONTENT_TYPE: "application/json"}
    raw = encode_props_block(d)
    decoded, _ = decode_props_block(raw, 0)
    assert decoded[PropertyID.CONTENT_TYPE] == "application/json"


def test_props_block_roundtrip_bytes_data() -> None:
    d = {PropertyID.CORRELATION_DATA: b"\xde\xad\xbe\xef"}
    raw = encode_props_block(d)
    decoded, _ = decode_props_block(raw, 0)
    assert decoded[PropertyID.CORRELATION_DATA] == b"\xde\xad\xbe\xef"


def test_props_block_roundtrip_user_property_multiple() -> None:
    d = {PropertyID.USER_PROPERTY: [("key1", "val1"), ("key2", "val2")]}
    raw = encode_props_block(d)
    decoded, _ = decode_props_block(raw, 0)
    assert decoded[PropertyID.USER_PROPERTY] == [("key1", "val1"), ("key2", "val2")]


def test_props_block_offset() -> None:
    prefix = b"\xff\xff"
    d = {PropertyID.REASON_STRING: "error"}
    raw = prefix + encode_props_block(d)
    decoded, n = decode_props_block(raw, 2)
    assert decoded[PropertyID.REASON_STRING] == "error"
    assert 2 + n == len(raw)


def test_props_block_unknown_property_id_raises() -> None:
    # Craft a block with an unknown property ID (0x00)
    inner = b"\x00\x01"  # id=0, 1 byte (bogus)
    raw = encode_varint(len(inner)) + inner
    with pytest.raises(ValueError, match="Unknown property ID"):
        decode_props_block(raw, 0)


def test_connect_properties_none() -> None:
    encoded = encode_connect_properties(None)
    assert encoded == b"\x00"


def test_connect_properties_roundtrip_minimal() -> None:
    props = ConnectProperties(session_expiry_interval=60)
    raw = encode_connect_properties(props)
    decoded, n = decode_connect_properties(raw, 0)
    assert n == len(raw)
    assert decoded is not None
    assert decoded.session_expiry_interval == 60
    assert decoded.receive_maximum is None


def test_connect_properties_roundtrip_full() -> None:
    props = ConnectProperties(
        session_expiry_interval=120,
        receive_maximum=100,
        maximum_packet_size=65536,
        topic_alias_maximum=10,
        request_response_information=True,
        request_problem_information=False,
        authentication_method="SCRAM-SHA-256",
        authentication_data=b"\x01\x02\x03",
        user_properties=(("client", "test"),),
    )
    raw = encode_connect_properties(props)
    decoded, n = decode_connect_properties(raw, 0)
    assert n == len(raw)
    assert decoded == props


def test_connect_properties_bool_flags() -> None:
    props = ConnectProperties(
        request_response_information=False,
        request_problem_information=True,
    )
    raw = encode_connect_properties(props)
    decoded, _ = decode_connect_properties(raw, 0)
    assert decoded is not None
    assert decoded.request_response_information is False
    assert decoded.request_problem_information is True


def test_will_properties_roundtrip() -> None:
    props = WillProperties(
        will_delay_interval=30,
        payload_format_indicator=1,
        message_expiry_interval=3600,
        content_type="text/plain",
        response_topic="response/topic",
        correlation_data=b"\xab\xcd",
        user_properties=(("k", "v"),),
    )
    raw = encode_will_properties(props)
    decoded, n = decode_will_properties(raw, 0)
    assert n == len(raw)
    assert decoded == props


def test_connack_properties_roundtrip() -> None:
    props = ConnAckProperties(
        session_expiry_interval=0,
        receive_maximum=65535,
        maximum_qos=1,
        retain_available=True,
        maximum_packet_size=256 * 1024,
        assigned_client_identifier="auto-id-42",
        topic_alias_maximum=5,
        reason_string="ok",
        wildcard_subscription_available=True,
        subscription_identifier_available=False,
        shared_subscription_available=True,
        server_keep_alive=60,
        response_information="/response",
        server_reference="other.broker",
        authentication_method="PLAIN",
        authentication_data=b"\x00user\x00pass",
        user_properties=(("srv", "1"),),
    )
    raw = encode_connack_properties(props)
    decoded, n = decode_connack_properties(raw, 0)
    assert n == len(raw)
    assert decoded == props


def test_publish_properties_roundtrip() -> None:
    props = PublishProperties(
        payload_format_indicator=1,
        message_expiry_interval=60,
        topic_alias=3,
        response_topic="resp/topic",
        correlation_data=b"\xff",
        subscription_identifier=7,
        content_type="application/cbor",
        user_properties=(("trace", "id-1"),),
    )
    raw = encode_publish_properties(props)
    decoded, n = decode_publish_properties(raw, 0)
    assert n == len(raw)
    assert decoded == props


def test_puback_properties_roundtrip() -> None:
    props = PubAckProperties(
        reason_string="not authorised",
        user_properties=(("x", "y"),),
    )
    raw = encode_puback_properties(props)
    decoded, n = decode_puback_properties(raw, 0)
    assert n == len(raw)
    assert decoded == props


def test_puback_properties_empty() -> None:
    raw = encode_puback_properties(None)
    assert raw == b"\x00"


def test_subscribe_properties_roundtrip() -> None:
    props = SubscribeProperties(
        subscription_identifier=42,
        user_properties=(("a", "b"),),
    )
    raw = encode_subscribe_properties(props)
    decoded, n = decode_subscribe_properties(raw, 0)
    assert n == len(raw)
    assert decoded == props


def test_suback_properties_roundtrip() -> None:
    props = SubAckProperties(reason_string="ok", user_properties=(("k", "v"),))
    raw = encode_suback_properties(props)
    decoded, n = decode_suback_properties(raw, 0)
    assert n == len(raw)
    assert decoded == props


def test_unsubscribe_properties_roundtrip() -> None:
    props = UnsubscribeProperties(user_properties=(("req", "1"),))
    raw = encode_unsubscribe_properties(props)
    decoded, n = decode_unsubscribe_properties(raw, 0)
    assert n == len(raw)
    assert decoded == props


def test_unsubscribe_properties_empty_user() -> None:
    raw = encode_unsubscribe_properties(None)
    decoded, _ = decode_unsubscribe_properties(raw, 0)
    assert decoded is None


def test_unsuback_properties_roundtrip() -> None:
    props = UnsubAckProperties(reason_string="no sub", user_properties=(("a", "b"),))
    raw = encode_unsuback_properties(props)
    decoded, n = decode_unsuback_properties(raw, 0)
    assert n == len(raw)
    assert decoded == props


def test_disconnect_properties_roundtrip() -> None:
    props = DisconnectProperties(
        session_expiry_interval=0,
        reason_string="session taken over",
        server_reference="backup.broker",
        user_properties=(("bye", "now"),),
    )
    raw = encode_disconnect_properties(props)
    decoded, n = decode_disconnect_properties(raw, 0)
    assert n == len(raw)
    assert decoded == props


def test_auth_properties_roundtrip() -> None:
    props = AuthProperties(
        authentication_method="SCRAM-SHA-1",
        authentication_data=b"\xde\xad",
        reason_string="continue",
        user_properties=(("step", "1"),),
    )
    raw = encode_auth_properties(props)
    decoded, n = decode_auth_properties(raw, 0)
    assert n == len(raw)
    assert decoded == props
