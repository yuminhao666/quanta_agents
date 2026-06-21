from __future__ import annotations

import re
from typing import Any


def token_overlap_score(text: str, candidate: str) -> float:
    left = set(re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9_+-]{1,}", text or ""))
    right = set(re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9_+-]{1,}", candidate or ""))
    if not left or not right:
        return 0.0
    return len(left & right) / max(min(len(left), len(right)), 1)


def match_framework_node(text: str, nodes: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Public lightweight matcher for new adapters; legacy private helpers remain untouched."""
    best: tuple[float, dict[str, Any] | None] = (0.0, None)
    for node in nodes:
        label = " ".join(
            str(value)
            for value in [
                node.get("node_id"),
                node.get("label"),
                node.get("name"),
                node.get("display"),
                node.get("description"),
            ]
            if value
        )
        score = token_overlap_score(text, label)
        if score > best[0]:
            best = (score, node)
    if best[1] is None:
        return None
    return {"node": best[1], "confidence": round(min(0.95, 0.35 + best[0]), 3)}
