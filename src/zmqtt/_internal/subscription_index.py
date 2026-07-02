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

    def add(self, filter_: str, entry: SubscriptionEntry) -> None:
        node = self._root
        for part in filter_.split("/"):
            mapping = node.wildcard_children if part in {"+", "#"} else node.children
            node = mapping.setdefault(part, _Node())

        node.entries.append((filter_, entry))

    def remove(self, filter_: str, entry: SubscriptionEntry | None = None) -> None:
        if entry is None:
            self._remove(filter_.split("/"), filter_, self._root)
            return
        self._remove_entry(filter_.split("/"), filter_, entry, self._root)

    def clear(self) -> None:
        self._root = _Node()

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

    def _remove(self, parts: list[str], filter_: str, node: _Node) -> None:
        if not parts:
            node.entries = [item for item in node.entries if item[0] != filter_]
            return

        part = parts[0]

        mapping = node.wildcard_children if part in {"+", "#"} else node.children

        child = mapping.get(part)
        if child is not None:
            self._remove(parts[1:], filter_, child)

            if not child.children and not child.wildcard_children and not child.entries:
                mapping.pop(part, None)

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

    def _specificity(self, filter_: str) -> tuple[int, ...]:
        return tuple(_segment_rank(segment) for segment in filter_.split("/"))
