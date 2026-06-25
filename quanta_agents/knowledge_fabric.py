from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quanta_agents.core.config import quanta_data_root
from quanta_agents.core.frameworks import iter_leaf_nodes, load_framework_for_asset
from quanta_agents.core.io import dated_parts, read_json, relative_to_root, utc_now_iso, write_json
from quanta_agents.domain.enums import TopicType
from quanta_agents.domain.ids import topic_id as make_topic_id
from quanta_agents.futures_daily.framework_alignment import _match_framework_node


KNOWLEDGE_FABRIC_VERSION = "knowledge_fabric_closure.v1"
ATOMIC_CLAIM_SET_VERSION = "atomic_claim_candidate_set.v1"
NEWS_EVENT_SET_VERSION = "news_event_canonical_sidecar.v1"
DEFAULT_WORK_ORDER_ID = "WO-DEV-20260622-001"

SCHEMA_REFS = [
    "configs/schemas/research_signal.v1.schema.json",
    "configs/schemas/theme_anchor.v1.schema.json",
    "configs/schemas/asset_canonical_event.v1.schema.json",
    "configs/schemas/candidate_manifest.v1.schema.json",
]

CONTRADICTING_SIGNAL_KINDS = {"thesis_conflict"}
CONTRADICTING_CONSISTENCY = {"dimension_conflict", "thesis_conflict"}


def _hash_id(prefix: str, *parts: Any, length: int = 16) -> str:
    material = "||".join(str(part or "") for part in parts)
    digest = hashlib.sha1(material.encode("utf-8")).hexdigest()[:length].upper()
    return f"{prefix}-{digest}"


def _clean_text(value: Any, *, limit: int | None = None) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if limit is not None and len(text) > limit:
        return text[:limit].rstrip(" ,，;；") + "..."
    return text


def _date_key(value: Any = None) -> str:
    text = _clean_text(value)
    if re.fullmatch(r"\d{8}", text):
        return text
    match = re.search(r"(20\d{2})[-/年.]?([01]\d)[-/月.]?([0-3]\d)", text)
    if match:
        return "".join(match.groups())
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def _parse_time(value: Any) -> datetime:
    text = _clean_text(value)
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    if text:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y%m%d"):
            try:
                parsed = datetime.strptime(text, fmt)
                return parsed.replace(tzinfo=timezone.utc)
            except ValueError:
                pass
        try:
            parsed = datetime.fromisoformat(text)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _iso(value: Any) -> str:
    return _parse_time(value).isoformat(timespec="seconds")


def _safe_rel(path: str | Path | None, root: Path) -> str:
    if path is None:
        return ""
    return relative_to_root(Path(path), root)


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _source_ref(
    ref_type: str,
    source_name: str,
    *,
    artifact_id: str | None = None,
    path: str | Path | None = None,
    root: Path | None = None,
    url: str | None = None,
) -> dict[str, Any]:
    ref: dict[str, Any] = {"ref_type": ref_type, "source_name": source_name}
    if artifact_id:
        ref["id"] = artifact_id
    if path:
        ref["path"] = _safe_rel(path, root) if root else str(path)
    if url:
        ref["url"] = url
    return ref


def _asset_ref_from_evidence(evidence: dict[str, Any]) -> dict[str, Any] | None:
    asset = evidence.get("asset") if isinstance(evidence.get("asset"), dict) else {}
    label = _clean_text(asset.get("name") or evidence.get("asset"))
    if not label:
        return None
    return {
        "id": _clean_text(asset.get("asset_id") or asset.get("commodity_code")) or _hash_id("ASSET", label),
        "label": label,
        "ref_type": "asset",
    }


def _framework_ref_from_node(node: dict[str, Any]) -> dict[str, Any] | None:
    node_id = _clean_text(node.get("node_id"))
    label = _clean_text(node.get("label") or node.get("dimension_label"))
    if not node_id and not label:
        return None
    return {"id": node_id or _hash_id("FWNODE", label), "label": label or node_id, "ref_type": "framework_node"}


def _topic_type_from_theme(theme: dict[str, Any]) -> str:
    theme_type = _clean_text(theme.get("theme_type")).lower()
    if theme_type in {"macro", "geopolitics", "policy"}:
        return TopicType.MACRO_THEME.value
    if len(_as_list(theme.get("asset_refs"))) == 1:
        return TopicType.ASSET_THEME.value
    return TopicType.MARKET_THEME.value


def persistent_topic_ref_from_theme(theme: dict[str, Any]) -> dict[str, Any]:
    title = _clean_text(theme.get("title") or theme.get("canonical_title") or theme.get("theme_anchor_id"))
    topic_type = _topic_type_from_theme(theme)
    return {
        "topic_id": make_topic_id(title, topic_type=topic_type),
        "canonical_title": title,
        "topic_type": topic_type,
        "source_theme_anchor_id": _clean_text(theme.get("theme_anchor_id")),
        "theme_type": _clean_text(theme.get("theme_type")),
    }


def _persistent_topic_ref_from_theme_ref(ref: dict[str, Any]) -> dict[str, Any] | None:
    title = _clean_text(ref.get("label") or ref.get("title") or ref.get("id"))
    if not title:
        return None
    topic_type = _topic_type_from_theme(
        {
            "theme_type": ref.get("theme_type"),
            "asset_refs": ref.get("asset_refs") if isinstance(ref.get("asset_refs"), list) else [],
        }
    )
    return {
        "topic_id": make_topic_id(title, topic_type=topic_type),
        "canonical_title": title,
        "topic_type": topic_type,
        "source_theme_anchor_id": _clean_text(ref.get("id")),
        "theme_type": _clean_text(ref.get("theme_type")),
    }


def _theme_lookup(themes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(theme.get("theme_anchor_id")): theme
        for theme in themes
        if isinstance(theme, dict) and theme.get("theme_anchor_id")
    }


def _signal_lookup_by_evidence(signals: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_evidence: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for signal in signals:
        if not isinstance(signal, dict):
            continue
        for ref in _as_list(signal.get("evidence_refs")):
            if not isinstance(ref, dict):
                continue
            evidence_id = _clean_text(ref.get("evidence_id"))
            if evidence_id:
                by_evidence[evidence_id].append(signal)
    return by_evidence


def _logic_node_candidates(evidence: dict[str, Any], root: Path) -> list[dict[str, Any]]:
    explicit_node = evidence.get("framework_node") if isinstance(evidence.get("framework_node"), dict) else {}
    explicit = _framework_ref_from_node(explicit_node)
    if explicit:
        return [
            {
                **explicit,
                "mapping_method": "existing_evidence_framework_node",
                "confidence": float((evidence.get("match") or {}).get("confidence") or 0.55)
                if isinstance(evidence.get("match"), dict)
                else 0.55,
            }
        ]

    asset = evidence.get("asset") if isinstance(evidence.get("asset"), dict) else {}
    if not asset:
        return []
    framework = load_framework_for_asset(asset, root)
    leaves = iter_leaf_nodes(framework) if framework else []
    if not leaves:
        return []
    claim = evidence.get("claim") if isinstance(evidence.get("claim"), dict) else {}
    snippet = evidence.get("snippet") if isinstance(evidence.get("snippet"), dict) else {}
    text = _clean_text(claim.get("text") or snippet.get("text"))
    if not text:
        return []
    label, node_id, confidence, method = _match_framework_node(text, leaves)
    return [
        {
            "id": node_id,
            "label": label,
            "ref_type": "framework_node",
            "mapping_method": method,
            "confidence": round(float(confidence), 3),
        }
    ]


def build_atomic_claims(
    evidence_units: list[dict[str, Any]],
    *,
    root: str | Path | None = None,
    evidence_paths: dict[str, str] | None = None,
    themes: list[dict[str, Any]] | None = None,
    signals: list[dict[str, Any]] | None = None,
    created_at: str | None = None,
) -> list[dict[str, Any]]:
    root_path = quanta_data_root(root)
    evidence_paths = evidence_paths or {}
    themes = themes or []
    signals = signals or []
    themes_by_id = _theme_lookup(themes)
    signals_by_evidence = _signal_lookup_by_evidence(signals)
    generated_at = created_at or utc_now_iso()
    claims: list[dict[str, Any]] = []

    for evidence in evidence_units:
        if not isinstance(evidence, dict):
            continue
        evidence_id = _clean_text(evidence.get("evidence_id"))
        claim = evidence.get("claim") if isinstance(evidence.get("claim"), dict) else {}
        snippet = evidence.get("snippet") if isinstance(evidence.get("snippet"), dict) else {}
        text = _clean_text(claim.get("text") or snippet.get("text"), limit=640)
        if not evidence_id or not text:
            continue

        source = evidence.get("source") if isinstance(evidence.get("source"), dict) else {}
        quality = evidence.get("quality") if isinstance(evidence.get("quality"), dict) else {}
        canonical_ref = evidence.get("canonical_ref") if isinstance(evidence.get("canonical_ref"), dict) else {}
        evidence_path = evidence_paths.get(evidence_id) or evidence_paths.get("*") or ""
        asset_ref = _asset_ref_from_evidence(evidence)
        logic_nodes = _logic_node_candidates(evidence, root_path)
        related_signals = signals_by_evidence.get(evidence_id, [])

        theme_refs: list[dict[str, Any]] = []
        persistent_refs: list[dict[str, Any]] = []
        relation_to_topic = "supports"
        for signal in related_signals:
            if signal.get("signal_kind") in CONTRADICTING_SIGNAL_KINDS:
                relation_to_topic = "contradicts"
            for ref in _as_list(signal.get("theme_refs")):
                if not isinstance(ref, dict):
                    continue
                if ref not in theme_refs:
                    theme_refs.append(ref)
                theme = themes_by_id.get(str(ref.get("id") or ""))
                persistent = persistent_topic_ref_from_theme(theme) if theme else _persistent_topic_ref_from_theme_ref(ref)
                if persistent and all(item["topic_id"] != persistent["topic_id"] for item in persistent_refs):
                    persistent_refs.append(persistent)

        claim_id = _hash_id("CLAIM-RREP", evidence_id, text)
        confidence = max(
            [float(signal.get("confidence") or 0) for signal in related_signals if isinstance(signal, dict)]
            or [float(quality.get("confidence") or 0.45)]
        )
        claims.append(
            {
                "schema_version": "atomic_claim.v1",
                "claim_id": claim_id,
                "object_type": "atomic_claim",
                "status": "candidate",
                "generated_at": generated_at,
                "source_role": "research_report",
                "source_evidence_id": evidence_id,
                "source_ref": _source_ref(
                    "evidence_capsule",
                    _clean_text(source.get("source_system") or "hzzhqx_wechat"),
                    artifact_id=evidence_id,
                    path=evidence_path,
                    root=root_path,
                    url=source.get("canonical_url") or source.get("source_url"),
                ),
                "canonical_ref": canonical_ref,
                "claim": {
                    "text": text,
                    "claim_type": _clean_text(claim.get("claim_type") or "research_report_statement"),
                    "direction": _clean_text(claim.get("direction") or "unknown"),
                    "direction_score": float(claim.get("direction_score") or 0.0),
                    "scoring_role": _clean_text(claim.get("scoring_role") or "fundamental_evidence"),
                },
                "asset_refs": [asset_ref] if asset_ref else [],
                "logic_node_candidates": logic_nodes,
                "theme_refs": theme_refs,
                "persistent_topic_refs": persistent_refs,
                "relation_to_topic": relation_to_topic,
                "evidence_refs": [
                    {
                        "evidence_id": evidence_id,
                        "path": evidence_path,
                        "snippet_ref": _clean_text(canonical_ref.get("chunk_id") or canonical_ref.get("section_id")),
                    }
                ],
                "quality": {
                    "confidence": round(min(1.0, max(0.0, confidence)), 3),
                    "human_review_required": bool(quality.get("human_review_required", True)),
                    "extraction_method": _clean_text(quality.get("extraction_method") or "evidence_unit_to_atomic_claim"),
                },
                "lineage": evidence.get("lineage") if isinstance(evidence.get("lineage"), dict) else {},
                "promotion_policy": "review_required",
            }
        )
    return claims


def build_atomic_claim_candidate_set(
    evidence_units: list[dict[str, Any]],
    *,
    root: str | Path | None = None,
    evidence_paths: dict[str, str] | None = None,
    themes: list[dict[str, Any]] | None = None,
    signals: list[dict[str, Any]] | None = None,
    candidate_id: str = "",
    work_order_id: str = DEFAULT_WORK_ORDER_ID,
    generated_at: str | None = None,
) -> dict[str, Any]:
    created_at = generated_at or utc_now_iso()
    claims = build_atomic_claims(
        evidence_units,
        root=root,
        evidence_paths=evidence_paths,
        themes=themes,
        signals=signals,
        created_at=created_at,
    )
    if not candidate_id:
        candidate_id = f"CAND-ATOMIC-CLAIM-{_date_key(created_at)}-{datetime.now().strftime('%H%M%S%f')}"
    return {
        "schema_version": ATOMIC_CLAIM_SET_VERSION,
        "status": "candidate",
        "candidate_id": candidate_id,
        "generated_at": created_at,
        "work_order_id": work_order_id,
        "schema_refs": SCHEMA_REFS,
        "source_pipeline": "existing_research_report_evidence_unit.v1",
        "atomic_claims": claims,
        "stats": {
            "evidence_unit_count": len([item for item in evidence_units if isinstance(item, dict)]),
            "atomic_claim_count": len(claims),
            "mapped_topic_claim_count": sum(1 for item in claims if item.get("persistent_topic_refs")),
            "logic_node_candidate_count": sum(len(item.get("logic_node_candidates") or []) for item in claims),
        },
    }


def _news_direction_relation(row: dict[str, Any]) -> str:
    consistency = row.get("consistency") if isinstance(row.get("consistency"), dict) else {}
    if str(consistency.get("status") or "") in CONTRADICTING_CONSISTENCY:
        return "contradicts"
    return "updates"


def _news_event_type(text: str) -> str:
    rules = (
        ("geopolitics", ("霍尔木兹", "伊朗", "中东", "地缘", "冲突", "制裁", "战争")),
        ("policy", ("政策", "监管", "关税", "财政", "央行")),
        ("macro", ("美联储", "利率", "通胀", "美元", "PMI")),
        ("supply", ("供应", "供给", "产量", "减产", "增产", "OPEC")),
        ("demand", ("需求", "消费", "订单", "开工")),
        ("inventory", ("库存", "仓单", "去库", "累库")),
        ("logistics", ("航运", "通航", "港口", "运费", "发运")),
        ("market", ("收涨", "收跌", "涨幅", "跌幅", "主力合约")),
    )
    for event_type, keywords in rules:
        if any(keyword in text for keyword in keywords):
            return event_type
    return "other"


def build_news_event_sidecar(
    news_logic_payload: dict[str, Any],
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    events = [event for event in _as_list(news_logic_payload.get("events")) if isinstance(event, dict)]
    created_at = generated_at or news_logic_payload.get("generated_at") or utc_now_iso()
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        flash_id = _clean_text(event.get("flash_id") or event.get("source_id") or event.get("event_id"))
        if flash_id:
            grouped[flash_id].append(event)

    event_mentions: list[dict[str, Any]] = []
    canonical_events: list[dict[str, Any]] = []
    topic_memberships: list[dict[str, Any]] = []
    persistent_topics: dict[str, dict[str, Any]] = {}

    for flash_id, rows in sorted(grouped.items()):
        first = rows[0]
        text = _clean_text(first.get("text") or first.get("summary"), limit=900)
        publish_time = _iso(first.get("publish_time") or created_at)
        event_type = _news_event_type(text)
        mention_id = _hash_id("MENTION-NEWS", flash_id, text)
        canonical_event_id = _hash_id("EVENT-NEWS", flash_id, text, publish_time)
        event_mentions.append(
            {
                "schema_version": "event_mention.v1",
                "mention_id": mention_id,
                "source_type": "news",
                "source_id": flash_id,
                "source_ref": {
                    "ref_type": "news_flash",
                    "id": flash_id,
                    "url": first.get("url"),
                    "channel": first.get("channel"),
                },
                "text": text,
                "event_type": event_type,
                "event_stage": "observed",
                "event_time": publish_time,
                "truth_status": "unverified",
                "extraction_confidence": round(max(float(row.get("heat") or 0) for row in rows) / 10.0, 3),
                "canonical_event_id": canonical_event_id,
            }
        )
        asset_refs = []
        logic_node_refs = []
        theme_refs = []
        memberships_for_event = []
        for row in rows:
            asset_id = _clean_text(row.get("asset_id") or row.get("asset"))
            asset_label = _clean_text(row.get("asset") or asset_id)
            if asset_label:
                asset_ref = {"id": asset_id or _hash_id("ASSET", asset_label), "label": asset_label, "ref_type": "asset"}
                if asset_ref not in asset_refs:
                    asset_refs.append(asset_ref)
            node = row.get("framework_node") if isinstance(row.get("framework_node"), dict) else {}
            node_ref = _framework_ref_from_node(node)
            if node_ref and node_ref not in logic_node_refs:
                logic_node_refs.append(node_ref)
            for theme_ref in _as_list(row.get("theme_anchor_refs")):
                if not isinstance(theme_ref, dict):
                    continue
                if theme_ref not in theme_refs:
                    theme_refs.append(theme_ref)
                persistent_ref = _persistent_topic_ref_from_theme_ref(theme_ref)
                if not persistent_ref:
                    continue
                persistent_topics[persistent_ref["topic_id"]] = {
                    "schema_version": "persistent_topic.v1",
                    "topic_id": persistent_ref["topic_id"],
                    "canonical_title": persistent_ref["canonical_title"],
                    "topic_type": persistent_ref["topic_type"],
                    "theme_type": persistent_ref.get("theme_type") or "",
                    "source_theme_anchor_ids": [persistent_ref.get("source_theme_anchor_id")],
                    "asset_refs": asset_refs,
                    "framework_node_refs": logic_node_refs,
                    "source_roles": ["news"],
                    "status": "candidate",
                }
                relation = _news_direction_relation(row)
                membership = {
                    "schema_version": "topic_membership.v1",
                    "membership_id": _hash_id("TMEM", persistent_ref["topic_id"], canonical_event_id, relation),
                    "object_id": canonical_event_id,
                    "object_type": "canonical_event",
                    "topic_id": persistent_ref["topic_id"],
                    "membership_role": "primary",
                    "membership_score": round(float(theme_ref.get("match_score") or row.get("heat") or 0.5), 3),
                    "relation_to_topic": relation,
                    "stance": "contradicts" if relation == "contradicts" else "supports",
                    "effective_time": publish_time,
                    "assigned_by": "news_logic_event_sidecar",
                    "agent_run_id": "",
                    "method": _clean_text(theme_ref.get("match_method") or row.get("theme_anchor_match_method") or "rule"),
                    "status": "candidate",
                    "reason": _clean_text((row.get("consistency") or {}).get("reason"), limit=240)
                    if isinstance(row.get("consistency"), dict)
                    else "",
                    "source_refs": [{"ref_type": "news_flash", "id": flash_id}],
                    "truth_status": "unverified",
                    "epistemic_status": "event_report",
                    "evidence_weight": 1.0,
                }
                memberships_for_event.append(membership)
                topic_memberships.append(membership)

        canonical_events.append(
            {
                "schema_version": "canonical_event.v1",
                "event_id": canonical_event_id,
                "canonical_summary": text[:260],
                "event_type": event_type,
                "event_time": publish_time,
                "first_seen_at": publish_time,
                "last_seen_at": publish_time,
                "truth_status": "unverified",
                "market_attention": round(min(1.0, max(float(row.get("heat") or 0.0) for row in rows) / 10.0), 3),
                "lifecycle_state": "candidate",
                "source_type": "news",
                "source_id": flash_id,
                "mention_ids": [mention_id],
                "asset_refs": asset_refs,
                "logic_node_refs": logic_node_refs,
                "theme_anchor_refs": theme_refs,
                "topic_memberships": memberships_for_event,
                "legacy_event_ids": [row.get("event_id") for row in rows],
                "status": "candidate",
            }
        )

    return {
        "schema_version": NEWS_EVENT_SET_VERSION,
        "status": "candidate",
        "generated_at": created_at,
        "source_schema_version": news_logic_payload.get("schema_version"),
        "event_mentions": event_mentions,
        "canonical_events": canonical_events,
        "persistent_topics": list(persistent_topics.values()),
        "topic_memberships": topic_memberships,
        "stats": {
            "legacy_news_logic_event_count": len(events),
            "event_mention_count": len(event_mentions),
            "canonical_event_count": len(canonical_events),
            "topic_membership_count": len(topic_memberships),
        },
    }


def _membership_from_claim(claim: dict[str, Any], topic_ref: dict[str, Any], run_id: str) -> dict[str, Any]:
    relation = _clean_text(claim.get("relation_to_topic") or "supports")
    evidence_ref = (_as_list(claim.get("evidence_refs")) or [{}])[0]
    return {
        "schema_version": "topic_membership.v1",
        "membership_id": _hash_id("TMEM", topic_ref["topic_id"], claim["claim_id"], relation),
        "object_id": claim["claim_id"],
        "object_type": "atomic_claim",
        "topic_id": topic_ref["topic_id"],
        "membership_role": "primary",
        "membership_score": float((claim.get("quality") or {}).get("confidence") or 0.45),
        "relation_to_topic": relation,
        "stance": "contradicts" if relation == "contradicts" else "supports",
        "effective_time": _iso((claim.get("source_ref") or {}).get("published_at") or claim.get("generated_at")),
        "assigned_by": "knowledge_fabric_closure",
        "agent_run_id": run_id,
        "method": "atomic_claim_theme_ref_to_persistent_topic",
        "status": "candidate",
        "reason": _clean_text((claim.get("claim") or {}).get("text"), limit=240),
        "source_refs": [claim.get("source_ref")],
        "truth_status": "not_applicable",
        "epistemic_status": "claim",
        "evidence_weight": 1.0,
        "evidence_refs": [evidence_ref] if evidence_ref else [],
    }


def build_knowledge_fabric_payload(
    *,
    news_sidecar: dict[str, Any] | None = None,
    atomic_claim_sets: list[dict[str, Any]] | None = None,
    theme_anchor_sets: list[dict[str, Any]] | None = None,
    generated_at: str | None = None,
    run_id: str = "",
    work_order_id: str = DEFAULT_WORK_ORDER_ID,
    input_refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    created_at = generated_at or utc_now_iso()
    atomic_claim_sets = atomic_claim_sets or []
    theme_anchor_sets = theme_anchor_sets or []
    news_sidecar = news_sidecar or {}
    run_id = run_id or f"RUN-KNOWLEDGE-FABRIC-{_date_key(created_at)}-{datetime.now().strftime('%H%M%S')}"

    topics: dict[str, dict[str, Any]] = {}
    for theme_set in theme_anchor_sets:
        for theme in _as_list(theme_set.get("theme_anchors")):
            if not isinstance(theme, dict):
                continue
            ref = persistent_topic_ref_from_theme(theme)
            topic = topics.setdefault(
                ref["topic_id"],
                {
                    "schema_version": "persistent_topic.v1",
                    "topic_id": ref["topic_id"],
                    "canonical_title": ref["canonical_title"],
                    "topic_type": ref["topic_type"],
                    "theme_type": ref.get("theme_type") or "",
                    "source_theme_anchor_ids": [],
                    "asset_refs": [],
                    "framework_node_refs": [],
                    "source_roles": [],
                    "status": "candidate",
                    "canonical_event_ids": [],
                    "atomic_claim_ids": [],
                    "evidence_unit_ids": [],
                    "supporting_relations": [],
                    "contradicting_relations": [],
                },
            )
            if ref.get("source_theme_anchor_id") and ref["source_theme_anchor_id"] not in topic["source_theme_anchor_ids"]:
                topic["source_theme_anchor_ids"].append(ref["source_theme_anchor_id"])
            for key in ("asset_refs", "framework_node_refs", "source_roles"):
                for item in _as_list(theme.get(key)):
                    if item not in topic[key]:
                        topic[key].append(item)

    for topic in _as_list(news_sidecar.get("persistent_topics")):
        if not isinstance(topic, dict):
            continue
        existing = topics.setdefault(
            topic["topic_id"],
            {
                "schema_version": "persistent_topic.v1",
                "topic_id": topic["topic_id"],
                "canonical_title": topic.get("canonical_title") or topic["topic_id"],
                "topic_type": topic.get("topic_type") or TopicType.MARKET_THEME.value,
                "theme_type": topic.get("theme_type") or "",
                "source_theme_anchor_ids": [],
                "asset_refs": [],
                "framework_node_refs": [],
                "source_roles": [],
                "status": "candidate",
                "canonical_event_ids": [],
                "atomic_claim_ids": [],
                "evidence_unit_ids": [],
                "supporting_relations": [],
                "contradicting_relations": [],
            },
        )
        for key in ("source_theme_anchor_ids", "asset_refs", "framework_node_refs", "source_roles"):
            for item in _as_list(topic.get(key)):
                if item and item not in existing[key]:
                    existing[key].append(item)

    topic_memberships = list(_as_list(news_sidecar.get("topic_memberships")))
    for membership in topic_memberships:
        topic = topics.get(str(membership.get("topic_id") or ""))
        if not topic:
            continue
        event_id = _clean_text(membership.get("object_id"))
        if event_id and event_id not in topic["canonical_event_ids"]:
            topic["canonical_event_ids"].append(event_id)
        relation_list = topic["contradicting_relations"] if membership.get("stance") == "contradicts" else topic["supporting_relations"]
        relation_list.append(membership["membership_id"])

    atomic_claims: list[dict[str, Any]] = []
    for claim_set in atomic_claim_sets:
        atomic_claims.extend(item for item in _as_list(claim_set.get("atomic_claims")) if isinstance(item, dict))

    for claim in atomic_claims:
        for topic_ref in _as_list(claim.get("persistent_topic_refs")):
            if not isinstance(topic_ref, dict) or not topic_ref.get("topic_id"):
                continue
            topic = topics.setdefault(
                topic_ref["topic_id"],
                {
                    "schema_version": "persistent_topic.v1",
                    "topic_id": topic_ref["topic_id"],
                    "canonical_title": topic_ref.get("canonical_title") or topic_ref["topic_id"],
                    "topic_type": topic_ref.get("topic_type") or TopicType.MARKET_THEME.value,
                    "theme_type": topic_ref.get("theme_type") or "",
                    "source_theme_anchor_ids": [],
                    "asset_refs": [],
                    "framework_node_refs": [],
                    "source_roles": [],
                    "status": "candidate",
                    "canonical_event_ids": [],
                    "atomic_claim_ids": [],
                    "evidence_unit_ids": [],
                    "supporting_relations": [],
                    "contradicting_relations": [],
                },
            )
            membership = _membership_from_claim(claim, topic_ref, run_id)
            topic_memberships.append(membership)
            if claim["claim_id"] not in topic["atomic_claim_ids"]:
                topic["atomic_claim_ids"].append(claim["claim_id"])
            for evidence_ref in _as_list(claim.get("evidence_refs")):
                evidence_id = _clean_text(evidence_ref.get("evidence_id")) if isinstance(evidence_ref, dict) else ""
                if evidence_id and evidence_id not in topic["evidence_unit_ids"]:
                    topic["evidence_unit_ids"].append(evidence_id)
            for asset_ref in _as_list(claim.get("asset_refs")):
                if asset_ref not in topic["asset_refs"]:
                    topic["asset_refs"].append(asset_ref)
            for node_ref in _as_list(claim.get("logic_node_candidates")):
                if node_ref not in topic["framework_node_refs"]:
                    topic["framework_node_refs"].append(node_ref)
            if "research_report" not in topic["source_roles"]:
                topic["source_roles"].append("research_report")
            relation_list = topic["contradicting_relations"] if membership["stance"] == "contradicts" else topic["supporting_relations"]
            relation_list.append(membership["membership_id"])

    persistent_topics = sorted(topics.values(), key=lambda item: item["canonical_title"])
    return {
        "schema_version": KNOWLEDGE_FABRIC_VERSION,
        "status": "candidate",
        "generated_at": created_at,
        "run_id": run_id,
        "work_order_id": work_order_id,
        "schema_refs": SCHEMA_REFS,
        "input_refs": input_refs or [],
        "current_implementation_summary": {
            "research_report_pipeline": "existing canonical document and research_report_evidence_unit.v1 are reused; atomic_claims are sidecars.",
            "news_pipeline": "existing news_logic events remain compatibility output; event_mentions and canonical_events are appended as sidecars.",
            "topic_pipeline": "theme_anchor candidates are projected into persistent_topic refs without writing gold.",
        },
        "persistent_topics": persistent_topics,
        "event_mentions": _as_list(news_sidecar.get("event_mentions")),
        "canonical_events": _as_list(news_sidecar.get("canonical_events")),
        "atomic_claims": atomic_claims,
        "topic_memberships": topic_memberships,
        "stats": {
            "persistent_topic_count": len(persistent_topics),
            "canonical_event_count": len(_as_list(news_sidecar.get("canonical_events"))),
            "atomic_claim_count": len(atomic_claims),
            "topic_membership_count": len(topic_memberships),
            "converged_topic_count": sum(
                1
                for topic in persistent_topics
                if topic.get("canonical_event_ids") and topic.get("atomic_claim_ids")
            ),
        },
        "compatibility": {
            "news_logic_output_preserved": True,
            "research_signal_output_preserved": True,
            "theme_anchor_output_preserved": True,
            "gj_chainplatform_contract_unchanged": True,
        },
    }


def _load_payload(path: str | Path | None) -> dict[str, Any] | None:
    if not path:
        return None
    file_path = Path(path).expanduser()
    if not file_path.exists():
        return None
    payload = read_json(file_path)
    return payload if isinstance(payload, dict) else None


def _load_payloads(paths: list[str | Path] | None) -> list[dict[str, Any]]:
    payloads = []
    for path in paths or []:
        payload = _load_payload(path)
        if payload is not None:
            payloads.append(payload)
    return payloads


def _collect_evidence(paths: list[str | Path] | None) -> tuple[list[dict[str, Any]], dict[str, str]]:
    evidence_units: list[dict[str, Any]] = []
    evidence_paths: dict[str, str] = {}
    for raw_path in paths or []:
        path = Path(raw_path).expanduser()
        files = sorted(path.glob("*.json")) if path.is_dir() else [path]
        for file_path in files:
            if not file_path.exists():
                continue
            payload = read_json(file_path)
            if not isinstance(payload, dict):
                continue
            evidence_id = _clean_text(payload.get("evidence_id") or file_path.stem)
            evidence_units.append(payload)
            evidence_paths[evidence_id] = str(file_path)
    return evidence_units, evidence_paths


def _candidate_dir(root: Path, date_key: str, stamp: str) -> Path:
    yyyy, mm, dd = dated_parts(date_key)
    return root / "agent_workspace/candidates/knowledge_fabric" / yyyy / mm / dd / f"CAND-KNOWLEDGE-FABRIC-{date_key}-{stamp}"


def _run_dir(root: Path, date_key: str, run_id: str) -> Path:
    yyyy, mm, dd = dated_parts(date_key)
    return root / "agent_workspace/runs/knowledge_fabric" / yyyy / mm / dd / run_id


def run_knowledge_fabric_closure(
    root: str | Path | None = None,
    *,
    news_logic_path: str | Path | None = None,
    theme_anchor_paths: list[str | Path] | None = None,
    research_signal_paths: list[str | Path] | None = None,
    atomic_claim_paths: list[str | Path] | None = None,
    evidence_paths: list[str | Path] | None = None,
    date: str | None = None,
    work_order_id: str = DEFAULT_WORK_ORDER_ID,
    write_latest: bool = True,
) -> dict[str, Any]:
    root_path = quanta_data_root(root)
    created_at = utc_now_iso()
    date_key = _date_key(date or created_at)
    stamp = datetime.now().strftime("%H%M%S%f")
    run_id = f"RUN-{re.sub(r'[^A-Z0-9_-]+', '-', work_order_id.upper())}-KNOWLEDGE-FABRIC-{stamp}"

    news_payload = _load_payload(news_logic_path)
    news_sidecar = build_news_event_sidecar(news_payload, generated_at=created_at) if news_payload else {}
    theme_sets = _load_payloads(theme_anchor_paths)
    signal_sets = _load_payloads(research_signal_paths)
    atomic_sets = _load_payloads(atomic_claim_paths)

    if evidence_paths:
        evidence_units, evidence_path_map = _collect_evidence(evidence_paths)
        themes = [theme for payload in theme_sets for theme in _as_list(payload.get("theme_anchors")) if isinstance(theme, dict)]
        signals = [signal for payload in signal_sets for signal in _as_list(payload.get("signals")) if isinstance(signal, dict)]
        atomic_sets.append(
            build_atomic_claim_candidate_set(
                evidence_units,
                root=root_path,
                evidence_paths={key: _safe_rel(value, root_path) for key, value in evidence_path_map.items()},
                themes=themes,
                signals=signals,
                candidate_id=f"CAND-ATOMIC-CLAIM-{date_key}-{stamp}",
                work_order_id=work_order_id,
                generated_at=created_at,
            )
        )

    input_refs: list[dict[str, Any]] = []
    for path, ref_type, source_name in [
        (news_logic_path, "candidate", "news_logic"),
        *((path, "candidate", "theme_anchor") for path in theme_anchor_paths or []),
        *((path, "candidate", "research_signal") for path in research_signal_paths or []),
        *((path, "candidate", "atomic_claim") for path in atomic_claim_paths or []),
        *((path, "evidence_capsule", "research_report_evidence") for path in evidence_paths or []),
    ]:
        if path:
            input_refs.append(_source_ref(ref_type, source_name, path=path, root=root_path))

    payload = build_knowledge_fabric_payload(
        news_sidecar=news_sidecar,
        atomic_claim_sets=atomic_sets,
        theme_anchor_sets=theme_sets,
        generated_at=created_at,
        run_id=run_id,
        work_order_id=work_order_id,
        input_refs=input_refs,
    )

    candidate_dir = _candidate_dir(root_path, date_key, stamp)
    run_dir = _run_dir(root_path, date_key, run_id)
    payload_path = candidate_dir / "knowledge-fabric.json"
    manifest_path = candidate_dir / "manifest.json"
    run_manifest_path = run_dir / "run_manifest.json"
    write_json(payload_path, payload)
    manifest = {
        "schema_version": "candidate_manifest.v1",
        "candidate_id": candidate_dir.name,
        "candidate_type": "source_mapping",
        "lifecycle_state": "candidate",
        "created_at": created_at,
        "generated_by": {
            "project": "quanta_agents",
            "run_id": run_id,
            "agent_name": "knowledge_fabric_closure",
            "model": None,
        },
        "title": "News and research evidence knowledge fabric closure",
        "content_refs": [
            {
                "ref_type": "candidate",
                "id": "knowledge-fabric",
                "path": relative_to_root(payload_path, root_path),
            }
        ],
        "evidence_refs": [
            {
                "evidence_id": ref.get("id") or ref.get("path") or "input",
                "path": ref.get("path"),
                "stance": "source",
                "strength": "medium",
            }
            for ref in input_refs
        ],
        "human_review_required": True,
        "promotion_policy": "review_required",
        "claim_summary": "First-stage closure over news canonical events, research atomic claims, and persistent topics.",
    }
    write_json(manifest_path, manifest)
    run_manifest = {
        "schema_version": "knowledge_fabric_closure_run.v1",
        "status": "succeeded",
        "run_id": run_id,
        "run_type": "knowledge_fabric_closure",
        "generated_at": created_at,
        "work_order_id": work_order_id,
        "input_refs": input_refs,
        "output_refs": [
            _source_ref("candidate", "knowledge_fabric", artifact_id=candidate_dir.name, path=payload_path, root=root_path)
        ],
        "schema_refs": SCHEMA_REFS,
        "human_review_required": True,
        "stats": payload["stats"],
    }
    write_json(run_manifest_path, run_manifest)

    latest_path = None
    if write_latest:
        latest_dir = root_path / "agent_workspace/candidates/knowledge_fabric/latest"
        latest_dir.mkdir(parents=True, exist_ok=True)
        latest_path = latest_dir / "knowledge-fabric.json"
        shutil.copy2(payload_path, latest_path)

    return {
        "status": "succeeded",
        "run_id": run_id,
        "stats": payload["stats"],
        "paths": {
            "candidate_dir": relative_to_root(candidate_dir, root_path),
            "knowledge_fabric": relative_to_root(payload_path, root_path),
            "manifest": relative_to_root(manifest_path, root_path),
            "run_manifest": relative_to_root(run_manifest_path, root_path),
            "latest": relative_to_root(latest_path, root_path) if latest_path else "",
        },
        "payload": payload,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Close news_logic and research evidence sidecars into Persistent Topic candidates."
    )
    parser.add_argument("--root", default="", help="quanta_data root. Defaults to GJ_QUANTA_DATA_ROOT discovery.")
    parser.add_argument("--news-logic", default="", help="news-logic.json path.")
    parser.add_argument("--theme-anchor", action="append", default=[], help="theme_anchors.json path. Can repeat.")
    parser.add_argument("--research-signal", action="append", default=[], help="research_signals.json path. Can repeat.")
    parser.add_argument("--atomic-claims", action="append", default=[], help="atomic_claims.json path. Can repeat.")
    parser.add_argument("--evidence", action="append", default=[], help="Evidence capsule file or directory. Can repeat.")
    parser.add_argument("--date", default="", help="YYYYMMDD/YYY-MM-DD date override.")
    parser.add_argument("--work-order-id", default=DEFAULT_WORK_ORDER_ID)
    parser.add_argument("--no-write-latest", action="store_true")
    args = parser.parse_args(argv)

    result = run_knowledge_fabric_closure(
        args.root or None,
        news_logic_path=args.news_logic or None,
        theme_anchor_paths=args.theme_anchor,
        research_signal_paths=args.research_signal,
        atomic_claim_paths=args.atomic_claims,
        evidence_paths=args.evidence,
        date=args.date or None,
        work_order_id=args.work_order_id,
        write_latest=not args.no_write_latest,
    )
    print(json.dumps({key: value for key, value in result.items() if key != "payload"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
