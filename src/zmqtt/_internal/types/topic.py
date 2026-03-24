from typing_extensions import Self


def _validate_topic_name(topic: str) -> None:
    if not topic:
        msg = "Topic must not be empty"
        raise ValueError(msg)
    if "#" in topic or "+" in topic:
        msg = f"Wildcards not allowed in publish topic: {topic!r}"
        raise ValueError(msg)
    if "$" in topic[1:]:
        msg = f"'$' is only valid as the first character of a topic: {topic!r}"
        raise ValueError(
            msg,
        )


def _validate_topic_filter(topic: str) -> None:
    if not topic:
        msg = "Topic filter must not be empty"
        raise ValueError(msg)
    if "$" in topic[1:]:
        msg = f"'$' is only valid as the first character of a topic filter: {topic!r}"
        raise ValueError(
            msg,
        )
    if "#" in topic:
        idx = topic.index("#")
        if idx != len(topic) - 1:
            msg = f"'#' must be the last character in a topic filter: {topic!r}"
            raise ValueError(
                msg,
            )
        if idx > 0 and topic[idx - 1] != "/":
            msg = f"'#' must be preceded by '/' in a topic filter: {topic!r}"
            raise ValueError(
                msg,
            )
    for level in topic.split("/"):
        if "+" in level and level != "+":
            msg = f"'+' must occupy an entire topic level in filter: {topic!r}"
            raise ValueError(
                msg,
            )


class Topic(str):
    """Validated publish topic — no wildcards, '$' only as first character."""

    __slots__ = ()

    def __new__(cls, value: str) -> Self:
        _validate_topic_name(value)
        return super().__new__(cls, value)


class TopicFilter(str):
    """Validated subscription filter. Wildcards allowed, '$' only as first character."""

    __slots__ = ()

    def __new__(cls, value: str) -> Self:
        _validate_topic_filter(value)
        return super().__new__(cls, value)
