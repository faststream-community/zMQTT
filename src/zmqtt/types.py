from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import IntEnum

from typing_extensions import Self

from zmqtt.packets.properties import PublishProperties


class QoS(IntEnum):
    AT_MOST_ONCE = 0
    AT_LEAST_ONCE = 1
    EXACTLY_ONCE = 2


class RetainHandling(IntEnum):
    """
    MQTT 5.0 subscription option controlling which retained messages are delivered.
    """

    SEND_ON_SUBSCRIBE = 0
    SEND_IF_NOT_EXISTS = 1
    DO_NOT_SEND = 2


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


@dataclass(slots=True, kw_only=True)
class Message:
    """Incoming MQTT message as delivered to application code."""

    topic: str
    payload: bytes
    qos: QoS
    retain: bool
    properties: PublishProperties | None = None  # v5 only
    _ack_callback: Callable[[], Awaitable[None]] | None = field(
        default=None,
        repr=False,
        init=False,
    )
    _resolved: bool = field(default=False, repr=False, init=False)

    async def ack(self) -> None:
        """
        Send the protocol-level ack for this message.
        Idempotent; no-op when auto_ack=True.
        """
        if self._resolved:
            return
        object.__setattr__(self, "_resolved", True)
        if self._ack_callback is not None:
            await self._ack_callback()
