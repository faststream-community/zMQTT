from typing import Final

# Group-less decorator prefixes the broker strips before delivery (unlike $SYS,
# which it delivers on as-is). Extend via MQTTClient(stripped_prefixes=...).
_DEFAULT_STRIPPED_PREFIXES: Final = ("$queue", "$exclusive")


def topic_matches(
    topic_filter: str,
    topic: str,
    *,
    stripped_prefixes: tuple[str, ...] = _DEFAULT_STRIPPED_PREFIXES,
) -> bool:
    """Return whether an MQTT topic filter matches a published topic.

    MQTT wildcards (``+`` and ``#``), shared subscriptions
    (``$share/<group>/...``), and broker-stripped decorator prefixes are
    supported. By default, ``$queue`` and ``$exclusive`` are treated as
    stripped prefixes; pass ``stripped_prefixes`` to configure another broker.
    """
    actual_filter = _shared_filter_to_actual(topic_filter, stripped_prefixes)
    return _topic_matches(actual_filter, topic)


def _shared_filter_to_actual(
    filter_: str,
    stripped_prefixes: tuple[str, ...],
) -> str:
    """Return the filter the broker matches incoming PUBLISH topics against.

    A shared/decorator subscription is sent with a prefix the broker strips before
    delivery, so matching must run against the filter *without* it:

    - ``$share/<group>/<filter>`` — MQTT 5 shared subscription (the only form that
      carries a group), handled here directly;
    - each entry in ``stripped_prefixes`` — a group-less decorator such as
      ``$queue/<filter>`` or ``$exclusive/<filter>``.

    Anything else — a plain filter, or a real namespace like ``$SYS/#`` the broker
    delivers on unchanged — is returned untouched. The allowlist fails safe: an
    unrecognised prefix is left as-is, so a mismatch surfaces as a loud
    "No subscriber" rather than a silent mis-route.
    """
    if filter_.startswith("$share/"):
        _, separator, actual_filter = filter_.removeprefix("$share/").partition("/")
        if separator:
            return actual_filter
    for prefix in stripped_prefixes:
        if filter_.startswith(f"{prefix}/"):
            return filter_.split("/", 1)[1]
    return filter_


def _topic_matches(actual_filter: str, topic: str) -> bool:
    if topic.startswith("$") != actual_filter.startswith("$"):
        return False
    return _match_parts(actual_filter.split("/"), topic.split("/"))


def _match_parts(fparts: list[str], tparts: list[str]) -> bool:
    if not fparts:
        return not tparts
    if fparts[0] == "#":
        return True
    if not tparts:
        return False
    if fparts[0] != "+" and fparts[0] != tparts[0]:
        return False
    return _match_parts(fparts[1:], tparts[1:])


def _segment_rank(seg: str) -> int:
    if seg == "#":
        return 2
    if seg == "+":
        return 1
    return 0


def _filter_specificity(actual_filter: str) -> tuple[int, ...]:
    return tuple(_segment_rank(s) for s in actual_filter.split("/"))
