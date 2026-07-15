import asyncio

import pytest

from zmqtt._internal.state import SubscriptionEntry
from zmqtt._internal.subscription_index import SubscriptionIndex
from zmqtt._internal.topic_matching import _filter_specificity, _topic_matches


@pytest.mark.parametrize(
    ("filter_", "topic", "expected"),
    [
        pytest.param("sensors/#", "sensors/temp", True, id="hash-single-level"),
        pytest.param("sensors/#", "sensors/temp/room1", True, id="hash-multi-level"),
        pytest.param("sensors/#", "sensors", True, id="hash-bare"),
        pytest.param("sensors/+/temp", "sensors/room1/temp", True, id="plus-match"),
        pytest.param(
            "sensors/+/temp",
            "sensors/room1/humidity",
            False,
            id="plus-no-match",
        ),
        pytest.param("#", "any/topic", True, id="bare-hash-multi"),
        pytest.param("#", "any", True, id="bare-hash-single"),
        pytest.param("#", "$SYS/broker", False, id="bare-hash-dollar"),
        pytest.param("+/foo", "$SYS/foo", False, id="plus-dollar"),
        pytest.param("$SYS/#", "$SYS/broker/uptime", True, id="sys-hash"),
        pytest.param("exact/match", "exact/match", True, id="exact-match"),
        pytest.param("exact/match", "exact/other", False, id="exact-no-match"),
        pytest.param("a/+/c", "a/b/c", True, id="plus-middle-match"),
        pytest.param("a/+/c", "a/b/c/d", False, id="plus-middle-no-match"),
    ],
)
def test_topic_matches(filter_: str, topic: str, expected: bool) -> None:
    assert _topic_matches(filter_, topic) is expected


def test_filter_specificity_exact() -> None:
    assert _filter_specificity("a/b") == (0, 0)


def test_filter_specificity_plus() -> None:
    assert _filter_specificity("a/+/c") == (0, 1, 0)


def test_filter_specificity_hash() -> None:
    assert _filter_specificity("a/#") == (0, 2)


def test_filter_specificity_bare_hash() -> None:
    assert _filter_specificity("#") == (2,)


def test_subscription_index_returns_best_match() -> None:
    index = SubscriptionIndex()
    index.add("#", SubscriptionEntry(queue=asyncio.Queue(), actual_filter="#"))
    index.add("sensors/#", SubscriptionEntry(queue=asyncio.Queue(), actual_filter="sensors/#"))
    index.add("sensors/room/temp", SubscriptionEntry(queue=asyncio.Queue(), actual_filter="sensors/room/temp"))

    matches = index.match("sensors/room/temp")

    assert {match[0] for match in matches} == {"#", "sensors/#", "sensors/room/temp"}
    best_match = index.best("sensors/room/temp")
    assert best_match is not None
    assert best_match[0] == "sensors/room/temp"


def test_subscription_index_removes_filters() -> None:
    index = SubscriptionIndex()
    entry = SubscriptionEntry(queue=asyncio.Queue(), actual_filter="sensors/#")
    index.add("sensors/#", entry)

    index.remove("sensors/#")

    assert index.match("sensors/temp") == []
    assert index.best("sensors/temp") is None
