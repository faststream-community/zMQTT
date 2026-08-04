import asyncio

import pytest

from zmqtt import topic_matches
from zmqtt._internal.subscription_index import SubscriptionEntry, SubscriptionIndex
from zmqtt._internal.topic_matching import _filter_specificity


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
    assert topic_matches(filter_, topic) is expected


@pytest.mark.parametrize(
    "filter_",
    [
        "$share/workers/sensors/+",
        "$queue/sensors/+",
        "$exclusive/sensors/+",
    ],
)
def test_topic_matches_stripped_subscription_prefix(filter_: str) -> None:
    assert topic_matches(filter_, "sensors/temperature")


def test_topic_matches_custom_stripped_prefix() -> None:
    assert topic_matches("$q/sensors/+", "sensors/temperature", stripped_prefixes=("$q",))
    assert not topic_matches("$q/sensors/+", "sensors/temperature")


def test_topic_matches_leaves_malformed_shared_filter_untouched() -> None:
    assert not topic_matches("$share/workers", "workers")


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


def test_subscription_index_keeps_original_filter_identity() -> None:
    index = SubscriptionIndex()
    shared = SubscriptionEntry(
        queue=asyncio.Queue(),
        actual_filter="demo/+/state",
        subscription_identifier=1,
    )
    plain = SubscriptionEntry(
        queue=asyncio.Queue(),
        actual_filter="demo/+/state",
        subscription_identifier=2,
    )

    index.add("$share/group/demo/+/state", shared)
    index.add("demo/+/state", plain)

    assert index.contains("$share/group/demo/+/state")
    assert index.contains("demo/+/state")
    assert index.get("$share/group/demo/+/state") is shared
    assert index.get("demo/+/state") is plain
    assert {filter_ for filter_, _ in index.match("demo/device/state")} == {
        "$share/group/demo/+/state",
        "demo/+/state",
    }
    selection = index.select("demo/device/state")
    assert selection.recipient == ("$share/group/demo/+/state", shared)
    assert selection.tied_filters == (
        "$share/group/demo/+/state",
        "demo/+/state",
    )

    index.remove("demo/+/state")

    assert index.contains("$share/group/demo/+/state")
    assert not index.contains("demo/+/state")
    assert index.match("demo/device/state") == [("$share/group/demo/+/state", shared)]


def test_subscription_index_routes_by_identifier() -> None:
    index = SubscriptionIndex()
    broad = SubscriptionEntry(
        queue=asyncio.Queue(),
        actual_filter="demo/#",
        subscription_identifier=7,
    )
    exact = SubscriptionEntry(
        queue=asyncio.Queue(),
        actual_filter="demo/+/state",
        subscription_identifier=7,
    )
    other = SubscriptionEntry(
        queue=asyncio.Queue(),
        actual_filter="demo/+/state",
        subscription_identifier=8,
    )
    index.add("$share/group/demo/#", broad)
    index.add("$share/group/demo/+/state", exact)
    index.add("demo/+/state", other)

    assert index.by_identifier(7) == [
        ("$share/group/demo/#", broad),
        ("$share/group/demo/+/state", exact),
    ]
    selection = index.select_by_identifier("demo/device/state", identifier=7)
    assert selection.recipient == (
        "$share/group/demo/+/state",
        exact,
    )
    assert not selection.identifier_missing

    fallback = index.select_by_identifier("demo/device/state", identifier=999)
    assert fallback.recipient == ("$share/group/demo/+/state", exact)
    assert fallback.identifier_missing
    assert fallback.tied_filters == (
        "$share/group/demo/+/state",
        "demo/+/state",
    )


def test_subscription_index_select_returns_empty_result() -> None:
    selection = SubscriptionIndex().select_by_identifier("missing/topic", identifier=9)

    assert selection.recipient is None
    assert selection.identifier_missing
    assert selection.tied_filters == ()


def test_subscription_index_clear_removes_all_lookups() -> None:
    index = SubscriptionIndex()
    entry = SubscriptionEntry(
        queue=asyncio.Queue(),
        actual_filter="demo/#",
        subscription_identifier=3,
    )
    index.add("$share/group/demo/#", entry)

    index.clear()

    assert not index.contains("$share/group/demo/#")
    assert index.match("demo/device") == []
    assert index.by_identifier(3) == []


def test_subscription_index_respects_system_topic_wildcard_boundary() -> None:
    index = SubscriptionIndex()
    root_hash = SubscriptionEntry(queue=asyncio.Queue(), actual_filter="#")
    root_plus = SubscriptionEntry(queue=asyncio.Queue(), actual_filter="+/status")
    system = SubscriptionEntry(queue=asyncio.Queue(), actual_filter="$SYS/#")
    index.add("#", root_hash)
    index.add("+/status", root_plus)
    index.add("$SYS/#", system)

    assert index.match("$SYS/status") == [("$SYS/#", system)]


def test_subscription_index_replace_cleans_old_tree_and_identifier() -> None:
    index = SubscriptionIndex()
    old = SubscriptionEntry(
        queue=asyncio.Queue(),
        actual_filter="sensors/#",
        subscription_identifier=1,
    )
    new = SubscriptionEntry(
        queue=asyncio.Queue(),
        actual_filter="devices/+",
        subscription_identifier=2,
    )
    index.add("logical-filter", old)

    index.add("logical-filter", new)

    assert index.match("sensors/temperature") == []
    assert index.match("devices/thermostat") == [("logical-filter", new)]
    assert index.by_identifier(1) == []
    assert index.by_identifier(2) == [("logical-filter", new)]
