from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from quanta_agents.core.config import quanta_data_root
from quanta_agents.core.io import read_json, relative_to_root
from quanta_agents.repositories.catalog_repository import CatalogRepository, default_catalog_path, object_catalog_enabled


GENERIC_TERMS = {
    "期货",
    "市场",
    "主题",
    "资讯",
    "速递",
    "要闻",
    "宏观",
    "金融",
    "行业",
    "商品",
    "观察",
    "未识别资产",
}

DOMAIN_TERMS = (
    "美联储",
    "FOMC",
    "点阵图",
    "加息",
    "降息",
    "鹰派",
    "鸽派",
    "利率",
    "美元",
    "美债",
    "通胀",
    "流动性",
    "库存",
    "仓单",
    "去库",
    "累库",
    "供应",
    "供给",
    "需求",
    "消费",
    "开工",
    "产量",
    "减产",
    "增产",
    "进口",
    "出口",
    "检修",
    "复产",
    "霍尔木兹",
    "伊朗",
    "中东",
    "地缘",
    "冲突",
    "制裁",
    "通航",
    "航运",
    "风险溢价",
    "OPEC",
    "EIA",
    "API",
    "关税",
    "政策",
    "监管",
    "陆家嘴论坛",
    "AI",
    "人工智能",
    "DeepSeek",
    "半导体",
)


def _clean(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text


def _text_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [_clean(value)] if _clean(value) else []
    if isinstance(value, dict):
        out: list[str] = []
        for item in value.values():
            out.extend(_text_values(item))
        return out
    if isinstance(value, list):
        out = []
        for item in value:
            out.extend(_text_values(item))
        return out
    return []


def _asset_labels(anchor: dict[str, Any]) -> list[str]:
    labels = []
    for ref in anchor.get("asset_refs") or []:
        if not isinstance(ref, dict):
            continue
        for key in ("label", "id"):
            text = _clean(ref.get(key))
            if text and text not in labels and text not in GENERIC_TERMS:
                labels.append(text)
    return labels


def _phrase_terms(anchor: dict[str, Any]) -> list[str]:
    terms = []
    for value in [anchor.get("title"), *(anchor.get("aliases") or [])]:
        text = _clean(value)
        if not text:
            continue
        candidates = [text]
        candidates.extend(part for part in re.split(r"[/、,，;；\s]+", text) if part)
        for candidate in candidates:
            candidate = _clean(candidate)
            if len(candidate) >= 2 and candidate not in GENERIC_TERMS and candidate not in terms:
                terms.append(candidate)
    return terms


def _domain_terms(anchor: dict[str, Any]) -> list[str]:
    source_text = " ".join(
        [
            _clean(anchor.get("title")),
            _clean(anchor.get("description")),
            " ".join(_text_values(anchor.get("event_definition_layers"))),
            " ".join(_clean(item) for item in anchor.get("aliases") or []),
        ]
    )
    terms = [term for term in DOMAIN_TERMS if term in source_text]
    return list(dict.fromkeys(terms))


def _rel_path(path: Path, root: Path) -> str:
    return relative_to_root(path, root)


def _load_theme_payload(path: Path) -> dict[str, Any]:
    payload = read_json(path)
    return payload if isinstance(payload, dict) else {}


def _candidate_files(root: Path, *, date_key: str | None = None, max_files: int = 3) -> list[Path]:
    base = root / "agent_workspace" / "candidates" / "theme_anchor"
    if date_key:
        yyyy, mm, dd = date_key[:4], date_key[4:6], date_key[6:8]
        files = sorted((base / yyyy / mm / dd).glob("CAND-THEME-ANCHOR-*/theme_anchors.json"))
    else:
        files = sorted(base.glob("*/*/*/CAND-THEME-ANCHOR-*/theme_anchors.json"))
    return files[-max_files:] if max_files > 0 else files


@dataclass(frozen=True)
class ThemeAnchorIndex:
    anchors: list[dict[str, Any]]
    source_paths: list[str]
    candidate_ids: list[str]

    @property
    def anchor_count(self) -> int:
        return len(self.anchors)

    def best_match(
        self,
        text: str,
        *,
        asset_labels: list[str] | tuple[str, ...] | None = None,
        min_score: float = 0.52,
    ) -> dict[str, Any] | None:
        haystack = _clean(text)
        if not haystack:
            return None
        supplied_assets = [_clean(item) for item in (asset_labels or []) if _clean(item)]
        best: dict[str, Any] | None = None
        for anchor in self.anchors:
            scored = self._score_anchor(anchor, haystack, supplied_assets)
            if not scored or scored["match_score"] < min_score:
                continue
            if best is None or scored["match_score"] > best["match_score"]:
                best = scored
        return best

    def _score_anchor(
        self,
        anchor: dict[str, Any],
        haystack: str,
        supplied_assets: list[str],
    ) -> dict[str, Any] | None:
        phrase_terms = _phrase_terms(anchor)
        domain_terms = _domain_terms(anchor)
        anchor_assets = _asset_labels(anchor)

        phrase_hits = [term for term in phrase_terms if term and term in haystack]
        domain_hits = [term for term in domain_terms if term and term in haystack]
        asset_hits = [
            term
            for term in anchor_assets
            if term and (term in haystack or term in supplied_assets or any(term in item for item in supplied_assets))
        ]
        if not (phrase_hits or domain_hits or asset_hits):
            return None

        score = 0.0
        method = "keyword_overlap"
        if phrase_hits:
            score += 0.64 if max(len(term) for term in phrase_hits) >= 4 else 0.42
            method = "theme_anchor_title_alias"
        if domain_hits:
            score += min(0.42, 0.18 * len(domain_hits))
        if asset_hits:
            score += 0.22
            method = "asset_keyword_theme_anchor" if domain_hits else method
        if not phrase_hits and not asset_hits and len(domain_hits) < 2:
            return None
        if asset_hits and not phrase_hits and not domain_hits:
            return None

        lifecycle = anchor.get("lifecycle") if isinstance(anchor.get("lifecycle"), dict) else {}
        support_count = int(lifecycle.get("support_count") or 0)
        score += min(0.08, support_count * 0.015)
        score = round(min(score, 1.0), 3)
        matched_terms = list(dict.fromkeys([*phrase_hits, *domain_hits, *asset_hits]))
        return {
            "theme_anchor_id": str(anchor.get("theme_anchor_id") or ""),
            "title": str(anchor.get("title") or ""),
            "theme_type": str(anchor.get("theme_type") or ""),
            "anchor_kind": str(anchor.get("anchor_kind") or ""),
            "source_roles": anchor.get("source_roles") if isinstance(anchor.get("source_roles"), list) else [],
            "asset_refs": anchor.get("asset_refs") if isinstance(anchor.get("asset_refs"), list) else [],
            "source_refs": anchor.get("source_refs") if isinstance(anchor.get("source_refs"), list) else [],
            "promotion_policy": str(anchor.get("promotion_policy") or "review_required"),
            "review_state": str(lifecycle.get("review_state") or "machine_candidate"),
            "match_score": score,
            "match_method": method,
            "matched_terms": matched_terms,
        }


EMPTY_THEME_ANCHOR_INDEX = ThemeAnchorIndex(anchors=[], source_paths=[], candidate_ids=[])


def _catalog_topic_index(root: Path) -> ThemeAnchorIndex | None:
    if not object_catalog_enabled():
        return None
    db_path = default_catalog_path(root)
    if not db_path.exists():
        return None
    repository = CatalogRepository(root, db_path=db_path, initialize=False)
    anchors: list[dict[str, Any]] = []
    for topic in repository.list_topics(limit=1000):
        metadata = {}
        try:
            import json

            metadata = json.loads(topic.get("metadata_json") or "{}")
        except Exception:
            metadata = {}
        aliases = [row.get("alias") for row in repository.topic_aliases(topic["topic_id"]) if row.get("alias")]
        topic_text = f"{topic.get('canonical_title') or ''} {topic.get('description') or ''} {metadata.get('event_chain') or ''}"
        aliases.extend(term for term in DOMAIN_TERMS if term in topic_text)
        anchors.append(
            {
                "schema_version": "persistent_topic_registry.v1",
                "theme_anchor_id": topic["topic_id"],
                "status": topic.get("status") or "candidate",
                "title": topic.get("canonical_title") or topic["topic_id"],
                "description": topic.get("description") or "",
                "anchor_kind": "persistent_topic_registry",
                "theme_type": metadata.get("theme_type") or topic.get("topic_type") or "",
                "source_roles": metadata.get("source_roles") if isinstance(metadata.get("source_roles"), list) else [],
                "asset_refs": metadata.get("asset_refs") if isinstance(metadata.get("asset_refs"), list) else [],
                "source_refs": metadata.get("source_refs") if isinstance(metadata.get("source_refs"), list) else [],
                "aliases": aliases,
                "event_definition_layers": {"fact_layer": metadata.get("event_chain") or topic.get("description") or ""},
                "lifecycle": {
                    "current_phase": topic.get("lifecycle_state") or "candidate",
                    "first_seen_at": topic.get("first_seen_at"),
                    "last_seen_at": topic.get("last_active_at"),
                    "support_count": int(
                        (
                            (metadata.get("lifecycle") or {}).get("support_count")
                            if isinstance(metadata.get("lifecycle"), dict)
                            else None
                        )
                        or float(topic.get("heat_score") or 0) * 10
                    ),
                    "review_state": "machine_candidate",
                },
                "promotion_policy": "review_required",
            }
        )
    if not anchors:
        return None
    return ThemeAnchorIndex(
        anchors=anchors,
        source_paths=[relative_to_root(db_path, root)],
        candidate_ids=["persistent_topic_registry"],
    )


def load_theme_anchor_index(
    root: str | Path | None = None,
    *,
    candidate_path: str | Path | None = None,
    date_key: str | None = None,
    max_files: int = 3,
) -> ThemeAnchorIndex:
    root_path = quanta_data_root(root)
    paths: list[Path]
    if candidate_path:
        path = Path(candidate_path).expanduser()
        paths = [path / "theme_anchors.json"] if path.is_dir() else [path]
    else:
        catalog_index = _catalog_topic_index(root_path)
        if catalog_index is not None:
            return catalog_index
        paths = _candidate_files(root_path, date_key=date_key, max_files=max_files)

    anchors: list[dict[str, Any]] = []
    source_paths: list[str] = []
    candidate_ids: list[str] = []
    seen_ids: set[str] = set()
    for path in paths:
        if not path.exists():
            continue
        payload = _load_theme_payload(path)
        source_paths.append(_rel_path(path, root_path))
        if payload.get("candidate_id"):
            candidate_ids.append(str(payload["candidate_id"]))
        for anchor in payload.get("theme_anchors") or []:
            if not isinstance(anchor, dict):
                continue
            anchor_id = str(anchor.get("theme_anchor_id") or "")
            if not anchor_id or anchor_id in seen_ids:
                continue
            seen_ids.add(anchor_id)
            anchors.append(anchor)
    if not anchors:
        return EMPTY_THEME_ANCHOR_INDEX
    return ThemeAnchorIndex(
        anchors=anchors,
        source_paths=list(dict.fromkeys(source_paths)),
        candidate_ids=list(dict.fromkeys(candidate_ids)),
    )


def theme_anchor_ref(match: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": match.get("theme_anchor_id"),
        "label": match.get("title"),
        "ref_type": "theme_anchor",
        "theme_type": match.get("theme_type"),
        "anchor_kind": match.get("anchor_kind"),
        "source_roles": match.get("source_roles") or [],
        "asset_refs": match.get("asset_refs") or [],
        "match_score": match.get("match_score"),
        "match_method": match.get("match_method"),
        "matched_terms": match.get("matched_terms") or [],
        "promotion_policy": match.get("promotion_policy") or "review_required",
        "review_state": match.get("review_state") or "machine_candidate",
    }
