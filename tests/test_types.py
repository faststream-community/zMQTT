import pytest

from zmqtt._internal.types.topic import (
    validate_publish,
    validate_response_topic,
    validate_subscribe_topic,
)
from zmqtt.errors import MQTTInvalidTopicError


def test_topic_valid() -> None:
    validate_publish("sensors/temperature/room1")


def test_topic_system_topic() -> None:
    validate_publish("$SYS/broker/uptime")


def test_topic_empty_raises() -> None:
    with pytest.raises(MQTTInvalidTopicError, match="empty"):
        validate_publish("")


def test_topic_wildcard_hash_raises() -> None:
    with pytest.raises(MQTTInvalidTopicError, match="Wildcards"):
        validate_publish("sensors/#")


def test_topic_wildcard_plus_raises() -> None:
    with pytest.raises(MQTTInvalidTopicError, match="Wildcards"):
        validate_publish("sensors/+/temperature")


def test_topic_dollar_mid_raises() -> None:
    with pytest.raises(MQTTInvalidTopicError, match=r"\$"):
        validate_publish("sensors/$SYS/data")


def test_filter_valid_exact() -> None:
    validate_subscribe_topic("sensors/temperature")


def test_filter_valid_hash() -> None:
    validate_subscribe_topic("sensors/#")


def test_filter_valid_plus() -> None:
    validate_subscribe_topic("sensors/+/temperature")


def test_filter_hash_only() -> None:
    validate_subscribe_topic("#")


def test_filter_system_topic() -> None:
    validate_subscribe_topic("$SYS/#")


def test_filter_empty_raises() -> None:
    with pytest.raises(MQTTInvalidTopicError, match="empty"):
        validate_subscribe_topic("")


def test_filter_hash_not_last_raises() -> None:
    with pytest.raises(MQTTInvalidTopicError, match="last character"):
        validate_subscribe_topic("sensors/#/foo")


def test_filter_hash_no_slash_raises() -> None:
    with pytest.raises(MQTTInvalidTopicError, match="preceded by"):
        validate_subscribe_topic("sensors#")


def test_filter_plus_partial_level_raises() -> None:
    with pytest.raises(MQTTInvalidTopicError, match="entire topic level"):
        validate_subscribe_topic("sensors/temp+/data")


def test_filter_dollar_mid_raises() -> None:
    with pytest.raises(MQTTInvalidTopicError, match=r"\$"):
        validate_subscribe_topic("sensors/$SYS/data")


def test_response_topic_valid() -> None:
    validate_response_topic("reply/client/abc123")


def test_response_topic_empty_raises() -> None:
    with pytest.raises(MQTTInvalidTopicError, match="empty"):
        validate_response_topic("")


def test_response_topic_wildcard_hash_raises() -> None:
    with pytest.raises(MQTTInvalidTopicError, match="Wildcards"):
        validate_response_topic("reply/#")


def test_response_topic_wildcard_plus_raises() -> None:
    with pytest.raises(MQTTInvalidTopicError, match="Wildcards"):
        validate_response_topic("reply/+/inbox")


def test_response_topic_dollar_mid_raises() -> None:
    with pytest.raises(MQTTInvalidTopicError, match=r"\$"):
        validate_response_topic("reply/$sys/data")
