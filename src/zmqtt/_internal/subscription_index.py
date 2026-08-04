from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from zmqtt._internal.topic_matching import _segment_rank, _topic_matches

if TYPE_CHECKING:
    from zmqtt._internal.state import SubscriptionEntry


@dataclass(slots=True)
class _Node:
    children: dict[str, _Node] = field(default_factory=dict)
    wildcard_children: dict[str, _Node] = field(default_factory=dict)
    entries: list[tuple[str, SubscriptionEntry]] = field(default_factory=list)


class SubscriptionIndex:
    def __init__(self) -> None:
        self._root = _Node()
        self._entries: dict[str, SubscriptionEntry] = {}
        self._by_identifier: dict[int, dict[str, SubscriptionEntry]] = {}

    def add(self, filter_: str, entry: SubscriptionEntry) -> None:
        if filter_ in self._entries:
            self.remove(filter_)

        tree_filter = entry.actual_filter or filter_
        node = self._root
        for part in tree_filter.split("/"):
            mapping = node.wildcard_children if part in {"+", "#"} else node.children
            node = mapping.setdefault(part, _Node())

        node.entries.append((filter_, entry))
        self._entries[filter_] = entry
        identifier = entry.subscription_identifier
        if identifier is not None:
            self._by_identifier.setdefault(identifier, {})[filter_] = entry

    def contains(self, filter_: str) -> bool:
        return filter_ in self._entries

    def get(self, filter_: str, default: SubscriptionEntry | None = None) -> SubscriptionEntry | None:
        return self._entries.get(filter_, default)

    def remove(self, filter_: str) -> SubscriptionEntry | None:
        entry = self._entries.pop(filter_, None)
        if entry is None:
            return None

        tree_filter = entry.actual_filter or filter_
        self._remove_entry(tree_filter.split("/"), filter_, entry, self._root)

        identifier = entry.subscription_identifier
        if identifier is not None:
            identified = self._by_identifier.get(identifier)
            if identified is not None:
                identified.pop(filter_, None)
                if not identified:
                    self._by_identifier.pop(identifier)
        return entry

    def clear(self) -> None:
        self._root = _Node()
        self._entries.clear()
        self._by_identifier.clear()

    def update(self, other: dict[str, SubscriptionEntry] | None = None, **kwargs: SubscriptionEntry) -> None:
        if other is not None:
            for filter_, entry in other.items():
                self.add(filter_, entry)
        for filter_, entry in kwargs.items():
            self.add(filter_, entry)

    def match(self, topic: str) -> list[tuple[str, SubscriptionEntry]]:
        matches: list[tuple[str, SubscriptionEntry]] = []
        self._collect(self._root, topic.split("/"), 0, matches)
        filtered = [item for item in matches if _topic_matches(self._actual_filter(*item), topic)]
        return sorted(filtered, key=lambda item: self._specificity(self._actual_filter(*item)))

    def best(self, topic: str) -> tuple[str, SubscriptionEntry] | None:
        matches = self.match(topic)
        if not matches:
            return None
        return matches[0]

    def by_identifier(self, identifier: int) -> list[tuple[str, SubscriptionEntry]]:
        return list(self._by_identifier.get(identifier, {}).items())

    def best_for_identifier(self, identifier: int, topic: str) -> tuple[str, SubscriptionEntry] | None:
        identified = self.by_identifier(identifier)
        if not identified:
            return None

        matching = [item for item in identified if _topic_matches(self._actual_filter(*item), topic)]
        pool = matching or identified
        return min(pool, key=lambda item: self._specificity(self._actual_filter(*item)))

    def _remove_entry(
        self,
        parts: list[str],
        filter_: str,
        entry: SubscriptionEntry,
        node: _Node,
    ) -> None:
        if not parts:
            node.entries = [item for item in node.entries if not (item[0] == filter_ and item[1] is entry)]
            return

        part = parts[0]
        mapping = node.wildcard_children if part in {"#", "+"} else node.children

        child = mapping.get(part)
        if child is None:
            return

        self._remove_entry(parts[1:], filter_, entry, child)

        if not child.children and not child.wildcard_children and not child.entries:
            mapping.pop(part)

    def _collect(
        self,
        node: _Node,
        parts: list[str],
        idx: int,
        matches: list[tuple[str, SubscriptionEntry]],
    ) -> None:
        if "#" in node.wildcard_children:
            matches.extend(node.wildcard_children["#"].entries)
        if idx == len(parts):
            matches.extend(node.entries)
            return

        part = parts[idx]
        if part in node.children:
            self._collect(node.children[part], parts, idx + 1, matches)
        if "+" in node.wildcard_children:
            self._collect(node.wildcard_children["+"], parts, idx + 1, matches)

    def _actual_filter(self, filter_: str, entry: SubscriptionEntry) -> str:
        return entry.actual_filter or filter_

    def _specificity(self, filter_: str) -> tuple[int, ...]:
        return tuple(_segment_rank(segment) for segment in filter_.split("/"))
