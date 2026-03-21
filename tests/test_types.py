"""Tests for zmqtt.types and zmqtt.errors."""

import pytest

from zmqtt.types import (
    Topic,
    TopicFilter,
)


def test_topic_valid() -> None:
    t = Topic("sensors/temperature/room1")
    assert t == "sensors/temperature/room1"


def test_topic_system_topic() -> None:
    # '$' as first character is valid
    t = Topic("$SYS/broker/uptime")
    assert t.startswith("$")


def test_topic_empty_raises() -> None:
    with pytest.raises(ValueError, match="empty"):
        Topic("")


def test_topic_wildcard_hash_raises() -> None:
    with pytest.raises(ValueError, match="Wildcards"):
        Topic("sensors/#")


def test_topic_wildcard_plus_raises() -> None:
    with pytest.raises(ValueError, match="Wildcards"):
        Topic("sensors/+/temperature")


def test_topic_dollar_mid_raises() -> None:
    with pytest.raises(ValueError, match=r"\$"):
        Topic("sensors/$SYS/data")


def test_filter_valid_exact() -> None:
    f = TopicFilter("sensors/temperature")
    assert f == "sensors/temperature"


def test_filter_valid_hash() -> None:
    assert TopicFilter("sensors/#") == "sensors/#"


def test_filter_valid_plus() -> None:
    assert TopicFilter("sensors/+/temperature") == "sensors/+/temperature"


def test_filter_hash_only() -> None:
    assert TopicFilter("#") == "#"


def test_filter_system_topic() -> None:
    assert TopicFilter("$SYS/#").startswith("$")


def test_filter_empty_raises() -> None:
    with pytest.raises(ValueError, match="empty"):
        TopicFilter("")


def test_filter_hash_not_last_raises() -> None:
    with pytest.raises(ValueError, match="last character"):
        TopicFilter("sensors/#/foo")


def test_filter_hash_no_slash_raises() -> None:
    with pytest.raises(ValueError, match="preceded by"):
        TopicFilter("sensors#")


def test_filter_plus_partial_level_raises() -> None:
    with pytest.raises(ValueError, match="entire topic level"):
        TopicFilter("sensors/temp+/data")


def test_filter_dollar_mid_raises() -> None:
    with pytest.raises(ValueError, match=r"\$"):
        TopicFilter("sensors/$SYS/data")
