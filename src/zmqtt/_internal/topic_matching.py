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
