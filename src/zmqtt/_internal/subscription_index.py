from __future__ import annotations

from dataclasses import dataclass, field
from itertools import chain
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

    def add(self, filter_: str, entry: SubscriptionEntry) -> None:
        tree_filter = entry.actual_filter or filter_
        node = self._root
        for part in tree_filter.split("/"):
            mapping = node.wildcard_children if part in {"+", "#"} else node.children
            node = mapping.setdefault(part, _Node())

        node.entries.append((tree_filter, entry))

    def contains(self, filter_: str) -> bool:
        return any(self._entry_matches(entry, filter_) for _, entry in self._iter_entries())

    def get(self, filter_: str, default: SubscriptionEntry | None = None) -> SubscriptionEntry | None:
        for _, entry in self._iter_entries():
            if self._entry_matches(entry, filter_):
                return entry
        return default

    def remove(self, filter_: str, entry: SubscriptionEntry | None = None) -> None:
        if entry is not None:
            self._remove_entry(filter_.split("/"), filter_, entry, self._root)
            return
        self._remove_matching(filter_, self._root)

    def clear(self) -> None:
        self._root = _Node()

    def update(self, other: dict[str, SubscriptionEntry] | None = None, **kwargs: SubscriptionEntry) -> None:
        if other is not None:
            for filter_, entry in other.items():
                self.add(filter_, entry)
        for filter_, entry in kwargs.items():
            self.add(filter_, entry)

    def match(self, topic: str) -> list[tuple[str, SubscriptionEntry]]:
        matches: list[tuple[str, SubscriptionEntry]] = []
        self._collect(self._root, topic.split("/"), 0, matches)
        filtered = [item for item in matches if _topic_matches(item[0], topic)]
        return sorted(filtered, key=lambda item: self._specificity(item[0]))

    def best(self, topic: str) -> tuple[str, SubscriptionEntry] | None:
        matches = self.match(topic)
        if not matches:
            return None
        return min(matches, key=lambda item: self._specificity(item[0]))

    def _remove_matching(self, filter_: str, node: _Node) -> None:
        node.entries = [item for item in node.entries if not self._entry_matches(item[1], filter_)]
        for child_name, child in chain(list(node.children.items()), list(node.wildcard_children.items())):
            self._remove_matching(filter_, child)
            if not child.children and not child.wildcard_children and not child.entries:
                node.children.pop(child_name, None)

    def _remove_entry(
        self,
        parts: list[str],
        filter_: str,
        entry: SubscriptionEntry,
        node: _Node,
    ) -> None:
        if not parts:
            node.entries = [item for item in node.entries if item[1] is not entry]
            return

        part = parts[0]
        mapping = node.wildcard_children if part in {"#", "+"} else node.children

        child = mapping.get(part)
        if child is None:
            return

        self._remove_entry(parts[1:], filter_, entry, child)

        if not child.children and not child.wildcard_children and not child.entries:
            mapping.pop(part)

    def _iter_entries(self) -> list[tuple[str, SubscriptionEntry]]:
        matches: list[tuple[str, SubscriptionEntry]] = []
        self._collect_entries(self._root, matches)
        return matches

    def _collect_entries(self, node: _Node, matches: list[tuple[str, SubscriptionEntry]]) -> None:
        matches.extend(node.entries)
        for child in chain(node.children.values(), node.wildcard_children.values()):
            self._collect_entries(child, matches)

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

    def _entry_matches(self, entry: SubscriptionEntry, filter_: str) -> bool:
        return filter_ in {entry.topic_filter, entry.actual_filter}

    def _specificity(self, filter_: str) -> tuple[int, ...]:
        return tuple(_segment_rank(segment) for segment in filter_.split("/"))
