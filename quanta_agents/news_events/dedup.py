from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from .ids import syndication_group_id


def _fingerprint(text: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", str(text or "").lower())


def _char_ngrams(text: str, n: int = 3) -> set[str]:
    fp = _fingerprint(text)
    if len(fp) <= n:
        return {fp} if fp else set()
    return {fp[index : index + n] for index in range(len(fp) - n + 1)}


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def text_similarity(left: str, right: str) -> float:
    return _jaccard(_char_ngrams(left), _char_ngrams(right))


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "")
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _hours_apart(left: Any, right: Any) -> float:
    a = _parse_time(left)
    b = _parse_time(right)
    if not a or not b:
        return 9999.0
    return abs((a - b).total_seconds()) / 3600.0


def exact_duplicate(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if left.get("source_system") == right.get("source_system"):
        left_row = str(left.get("source_row_id") or "")
        right_row = str(right.get("source_row_id") or "")
        if left_row and left_row == right_row:
            return True
    if left.get("content_hash") and left.get("content_hash") == right.get("content_hash"):
        return True
    if left.get("canonical_url") and left.get("canonical_url") == right.get("canonical_url"):
        return True
    return False


def near_duplicate(left: dict[str, Any], right: dict[str, Any]) -> tuple[bool, float]:
    if _hours_apart(left.get("publish_time"), right.get("publish_time")) > 12:
        return False, 0.0
    left_content = _fingerprint(left.get("content") or "")
    right_content = _fingerprint(right.get("content") or "")
    if left_content and left_content == right_content:
        return True, 0.96
    title_score = text_similarity(left.get("title") or "", right.get("title") or "")
    body_score = text_similarity(left.get("normalized_text") or "", right.get("normalized_text") or "")
    score = max(body_score, 0.55 * body_score + 0.45 * title_score)
    return score >= 0.82 or (title_score >= 0.72 and body_score >= 0.68), round(score, 3)


def _dedup_features(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "item": item,
        "news_id": item["news_id"],
        "publish_at": _parse_time(item.get("publish_time")),
        "publish_time": item.get("publish_time") or "",
        "source_system": item.get("source_system"),
        "source_row_id": str(item.get("source_row_id") or ""),
        "content_hash": item.get("content_hash"),
        "canonical_url": item.get("canonical_url"),
        "content_fp": _fingerprint(item.get("content") or ""),
        "title_ngrams": _char_ngrams(item.get("title") or ""),
        "text_ngrams": _char_ngrams(item.get("normalized_text") or ""),
    }


def _hours_apart_features(left: dict[str, Any], right: dict[str, Any]) -> float:
    left_at = left.get("publish_at")
    right_at = right.get("publish_at")
    if not left_at or not right_at:
        return 9999.0
    return abs((left_at - right_at).total_seconds()) / 3600.0


def _exact_duplicate_features(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if left.get("source_system") == right.get("source_system"):
        left_row = str(left.get("source_row_id") or "")
        right_row = str(right.get("source_row_id") or "")
        if left_row and left_row == right_row:
            return True
    if left.get("content_hash") and left.get("content_hash") == right.get("content_hash"):
        return True
    if left.get("canonical_url") and left.get("canonical_url") == right.get("canonical_url"):
        return True
    return False


def _near_duplicate_features(left: dict[str, Any], right: dict[str, Any]) -> tuple[bool, float]:
    if _hours_apart_features(left, right) > 12:
        return False, 0.0
    if left.get("content_fp") and left.get("content_fp") == right.get("content_fp"):
        return True, 0.96
    title_score = _jaccard(left["title_ngrams"], right["title_ngrams"])
    # News flashes are short and dense; use title similarity as a conservative
    # blocking gate before the heavier body comparison.
    if title_score < 0.28:
        return False, round(title_score, 3)
    body_score = _jaccard(left["text_ngrams"], right["text_ngrams"])
    score = max(body_score, 0.55 * body_score + 0.45 * title_score)
    return score >= 0.82 or (title_score >= 0.72 and body_score >= 0.68), round(score, 3)


def build_syndication_groups(news_items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
    """Group duplicates conservatively; each group counts as one independent source."""
    ordered = sorted(
        (_dedup_features(item) for item in news_items),
        key=lambda feature: (feature["publish_time"], feature["news_id"]),
    )
    assigned: set[str] = set()
    groups: list[dict[str, Any]] = []
    members: list[dict[str, Any]] = []
    news_to_group: dict[str, str] = {}

    for index, feature in enumerate(ordered):
        item = feature["item"]
        news_id = feature["news_id"]
        if news_id in assigned:
            continue
        assigned.add(news_id)
        group_items = [(item, "representative", 1.0)]
        # The list is sorted by publish_time, so a representative only needs to
        # compare against following rows until the 12-hour duplicate window ends.
        for other_index in range(index + 1, len(ordered)):
            other_feature = ordered[other_index]
            other = other_feature["item"]
            other_id = other_feature["news_id"]
            if other_id in assigned:
                continue
            hours_apart = _hours_apart_features(feature, other_feature)
            if hours_apart > 12:
                break
            duplicate_type = ""
            similarity = 0.0
            if _exact_duplicate_features(feature, other_feature):
                duplicate_type = "exact_duplicate"
                similarity = 1.0
            else:
                is_near, similarity = _near_duplicate_features(feature, other_feature)
                if is_near:
                    duplicate_type = "near_duplicate"
            if duplicate_type:
                assigned.add(other_id)
                group_items.append((other, duplicate_type, similarity))

        representative = group_items[0][0]
        group_id = syndication_group_id(representative["news_id"], representative["content_hash"])
        member_ids = [row["news_id"] for row, _, _ in group_items]
        duplicate_types = {row["news_id"]: kind for row, kind, _ in group_items}
        groups.append(
            {
                "syndication_group_id": group_id,
                "member_news_ids": member_ids,
                "root_source_id": representative.get("source_row_id") or representative["news_id"],
                "representative_news_id": representative["news_id"],
                "independent_source_count": 1,
                "duplicate_types": duplicate_types,
            }
        )
        for row, duplicate_type, similarity in group_items:
            news_to_group[row["news_id"]] = group_id
            members.append(
                {
                    "syndication_group_id": group_id,
                    "news_id": row["news_id"],
                    "duplicate_type": duplicate_type,
                    "similarity": similarity,
                }
            )
    return groups, members, news_to_group
