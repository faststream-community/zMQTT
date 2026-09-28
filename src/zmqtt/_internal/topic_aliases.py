from __future__ import annotations

from zmqtt.errors import MQTTTopicAliasError

MAX_ALIAS = 65535
_MIN_ALIAS = 1


def validate_alias_range(alias: int) -> None:
    if not _MIN_ALIAS <= alias <= MAX_ALIAS:
        msg = f"Topic Alias must be in {_MIN_ALIAS}..{MAX_ALIAS}, got {alias}"
        raise MQTTTopicAliasError(msg)


class TopicAliasTable:
    def __init__(self, limit: int = 0) -> None:
        self._limit = limit
        self._aliases: dict[int, str] = {}

    @property
    def limit(self) -> int:
        return self._limit

    def clear(self) -> None:
        self._aliases.clear()

    def validate(self, alias: int) -> None:
        validate_alias_range(alias)
        if alias > self._limit:
            msg = f"Topic Alias {alias} exceeds the peer's Topic Alias Maximum {self._limit}"
            raise MQTTTopicAliasError(msg)

    def set(self, alias: int, topic: str) -> None:
        self.validate(alias)
        self._aliases[alias] = topic

    def get(self, alias: int) -> str | None:
        return self._aliases.get(alias)
