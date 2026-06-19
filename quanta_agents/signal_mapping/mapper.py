from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quanta_agents.core.config import quanta_data_root
from quanta_agents.core.io import dated_parts, read_json, relative_to_root, utc_now_iso, write_json


MAPPER_VERSION = "signal_theme_mapper.v1"
DEFAULT_WORK_ORDER_ID = "WO-DEV-20260620-009"

ALLOWED_DIRECTIONS = {"bullish", "bearish", "neutral", "mixed", "unknown", "risk_up", "risk_down"}
CONFLICT_RELATIONS = {"conflicts_with_brief_anchor", "counter_evidence"}
PENDING_RELATIONS = {"pending_observation", "pending_counter_observation", "pending_conflict_review"}


def _hash_id(prefix: str, *parts: Any) -> str:
    raw = "||".join(str(part or "") for part in parts)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16].upper()
    return f"{prefix}-{digest}"


def _clean_text(value: Any, *, limit: int | None = None) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if limit is not None and len(text) > limit:
        return text[:limit].rstrip(" ,，;；") + "..."
    return text


def _date_key(value: Any) -> str:
    text = str(value or "").strip()
    if re.fullmatch(r"\d{8}", text):
        return text
    match = re.search(r"(20\d{2})[-/年.]?([01]\d)[-/月.]?([0-3]\d)", text)
    if match:
        return "".join(match.groups())
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def _date_iso(value: Any) -> str:
    key = _date_key(value)
    return f"{key[:4]}-{key[4:6]}-{key[6:8]}"


def _safe_path(value: str | Path | None, root: Path | None = None) -> str:
    if value is None:
        return ""
    path = Path(value)
    if root is not None:
        return relative_to_root(path, root)
    return str(path)


def _source_ref(
    ref_type: str,
    source_name: str,
    *,
    artifact_id: str | None = None,
    path: str | Path | None = None,
    root: Path | None = None,
    content_hash: str | None = None,
    url: str | None = None,
) -> dict[str, Any]:
    ref: dict[str, Any] = {
        "ref_type": ref_type,
        "source_name": source_name,
    }
    if artifact_id is not None:
        ref["id"] = artifact_id
    if path is not None:
        ref["path"] = _safe_path(path, root)
    if content_hash is not None:
        ref["hash"] = content_hash
    if url is not None:
        ref["url"] = url
    return ref


def _asset_ref(label: Any, asset_id: Any = None, *, path: str | None = None) -> dict[str, Any]:
    label_text = _clean_text(label) or "未识别资产"
    ref_id = _clean_text(asset_id) or _hash_id("ASSET", label_text)
    ref = {"id": ref_id, "label": label_text, "ref_type": "asset"}
    if path:
        ref["path"] = path
    return ref


def _framework_ref(node_id: Any, label: Any) -> dict[str, Any] | None:
    node = _clean_text(node_id)
    node_label = _clean_text(label)
    if not node and not node_label:
        return None
    return {
        "id": node or _hash_id("FWNODE", node_label),
        "label": node_label or node,
        "ref_type": "framework_node",
    }


def _theme_ref(theme: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(theme["theme_anchor_id"]),
        "label": str(theme["title"]),
        "ref_type": "theme",
    }


def _signal_ref(signal: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(signal["signal_id"]),
        "label": str(signal.get("notes") or signal["signal_id"])[:80],
        "ref_type": "signal",
    }


def _time_window(date_value: Any, *, horizon: str = "daily") -> dict[str, Any]:
    date = _date_iso(date_value)
    return {"start": date, "end": date, "horizon": horizon}


def _event_layers(
    fact_layer: Any = None,
    *,
    political_layer: Any = None,
    time_window_layer: Any = None,
    settlement_rule_layer: Any = None,
) -> dict[str, Any]:
    return {
        "fact_layer": _clean_text(fact_layer, limit=360) or None,
        "political_layer": _clean_text(political_layer, limit=220) or None,
        "time_window_layer": _clean_text(time_window_layer, limit=180) or None,
        "settlement_rule_layer": _clean_text(settlement_rule_layer, limit=220) or None,
    }


def _normalize_direction(value: Any, *, score: Any = None) -> str:
    raw = str(value or "").strip().lower()
    direction_map = {
        "positive": "bullish",
        "negative": "bearish",
        "support": "bullish",
        "pressure": "bearish",
        "event": "unknown",
        "forecast": "unknown",
    }
    raw = direction_map.get(raw, raw)
    if raw in ALLOWED_DIRECTIONS:
        return raw
    try:
        numeric = float(score)
    except (TypeError, ValueError):
        return "unknown"
    if numeric > 0.25:
        return "bullish"
    if numeric < -0.25:
        return "bearish"
    return "neutral"


def _bounded_float(value: Any, *, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(0.0, min(1.0, number))


def _strength_from_score(value: Any, *, default: float = 0.55) -> float:
    try:
        number = abs(float(value))
    except (TypeError, ValueError):
        number = default
    return round(max(0.15, min(1.0, number)), 3)


def _confidence_label(confidence: float, *, human_reviewed: bool = False) -> str:
    if human_reviewed:
        return "human_reviewed_signal"
    if confidence >= 0.75:
        return "high_confidence_signal"
    if confidence >= 0.45:
        return "medium_confidence_signal"
    return "low_confidence_signal"


def _evidence_strength(confidence: Any) -> str:
    value = _bounded_float(confidence, default=0.5)
    if value >= 0.75:
        return "strong"
    if value >= 0.45:
        return "medium"
    return "weak"


def _infer_theme_type(*texts: Any) -> str:
    text = " ".join(_clean_text(item) for item in texts)
    rules = (
        ("geopolitics", ("地缘", "冲突", "制裁", "霍尔木兹", "战争", "伊朗", "中东")),
        ("policy", ("政策", "监管", "关税", "税", "论坛", "央行", "证监会")),
        ("macro", ("美联储", "利率", "通胀", "GDP", "汇率", "流动性", "资金面")),
        ("inventory", ("库存", "库容", "仓单")),
        ("weather", ("天气", "降雨", "干旱", "台风", "气温")),
        ("logistics", ("航运", "运费", "港口", "到港", "发运", "通航")),
        ("supply_demand", ("供给", "供应", "需求", "消费", "开工", "产量", "减产", "增产")),
        ("price_validation", ("收涨", "收跌", "涨幅", "跌幅", "价格", "盘面")),
        ("sentiment", ("情绪", "风险偏好", "避险")),
    )
    for theme_type, keywords in rules:
        if any(word in text for word in keywords):
            return theme_type
    return "other"


def _brief_evidence_refs(anchor: dict[str, Any], source_path: str) -> list[dict[str, Any]]:
    refs = []
    for item in anchor.get("key_evidence") or []:
        if not isinstance(item, dict):
            continue
        refs.append(
            {
                "evidence_id": str(item.get("evidence_id") or anchor.get("anchor_id")),
                "path": str(source_path or "agent_workspace/candidates/futures_daily_raw_runs"),
                "stance": "supports",
                "strength": _evidence_strength(anchor.get("source_sentiment_score")),
                "snippet_ref": str(item.get("source_field") or "") or None,
            }
        )
    if not refs:
        refs.append(
            {
                "evidence_id": str(anchor.get("anchor_id") or _hash_id("BRFEV", anchor.get("main_thesis"))),
                "path": str(source_path or "agent_workspace/candidates/futures_daily_raw_runs"),
                "stance": "supports",
                "strength": "medium",
            }
        )
    return refs


def _futures_theme_from_anchor(
    asset_key: str,
    anchor: dict[str, Any],
    *,
    report_date: Any,
    created_at: str,
    source_path: str,
) -> dict[str, Any]:
    asset = str(anchor.get("asset") or asset_key)
    title = _clean_text(anchor.get("thesis_title") or anchor.get("main_thesis"), limit=96)
    theme_id = _hash_id("THA-BRIEF", report_date, anchor.get("anchor_id"), asset, title)
    asset_ref = _asset_ref(asset, anchor.get("asset_ref"))
    evidence_refs = _brief_evidence_refs(anchor, source_path)
    main_thesis = _clean_text(anchor.get("main_thesis"), limit=420)
    return {
        "schema_version": "theme_anchor.v1",
        "theme_anchor_id": theme_id,
        "status": "candidate",
        "created_at": created_at,
        "updated_at": None,
        "title": title or f"{asset}期市速递主线",
        "description": main_thesis or None,
        "anchor_kind": "market_brief_thesis",
        "theme_type": _infer_theme_type(title, main_thesis, *(item.get("text") for item in anchor.get("key_evidence") or [] if isinstance(item, dict))),
        "source_roles": ["agent"],
        "source_refs": [
            _source_ref(
                "candidate",
                "brief_thesis_anchor",
                artifact_id=str(anchor.get("anchor_id") or ""),
                path=source_path,
            )
        ],
        "asset_refs": [asset_ref],
        "framework_node_refs": [],
        "event_definition_layers": _event_layers(
            main_thesis,
            political_layer=main_thesis if _infer_theme_type(title, main_thesis) in {"policy", "geopolitics"} else None,
            time_window_layer=f"{_date_iso(report_date)} daily futures brief baseline",
        ),
        "signal_refs": [],
        "evidence_refs": evidence_refs,
        "polymarket_refs": [],
        "aliases": [asset, str(anchor.get("direction_label") or "")],
        "lifecycle": {
            "current_phase": "tracking",
            "first_seen_at": _date_iso(report_date),
            "last_seen_at": _date_iso(report_date),
            "support_count": len(evidence_refs),
            "conflict_count": 0,
            "review_state": "machine_candidate",
        },
        "promotion_policy": "review_required",
    }


def _brief_signal_from_anchor(
    anchor: dict[str, Any],
    theme: dict[str, Any],
    *,
    report_date: Any,
    created_at: str,
    source_path: str,
) -> dict[str, Any]:
    confidence = 0.68
    asset_label = anchor.get("asset") or theme["asset_refs"][0]["label"]
    direction = _normalize_direction(anchor.get("direction"))
    return {
        "schema_version": "research_signal.v1",
        "signal_id": _hash_id("SIG-BRIEF", report_date, anchor.get("anchor_id"), anchor.get("main_thesis")),
        "status": "candidate",
        "created_at": created_at,
        "updated_at": None,
        "source_role": "agent",
        "source_ref": _source_ref(
            "candidate",
            "brief_thesis_anchor",
            artifact_id=str(anchor.get("anchor_id") or ""),
            path=source_path,
        ),
        "signal_kind": "thesis_support",
        "asset_refs": [_asset_ref(asset_label, anchor.get("asset_ref"))],
        "theme_refs": [_theme_ref(theme)],
        "framework_node_refs": [],
        "direction": direction,
        "strength": _strength_from_score(anchor.get("source_sentiment_score"), default=0.62),
        "confidence": confidence,
        "confidence_label": _confidence_label(confidence),
        "time_window": _time_window(report_date),
        "event_definition_layers": theme["event_definition_layers"],
        "evidence_refs": _brief_evidence_refs(anchor, source_path),
        "source_signal_refs": [],
        "conflict_refs": [],
        "mapping": {
            "method": "brief_thesis_anchor_to_research_signal.v1",
            "confidence": confidence,
            "mapped_by": MAPPER_VERSION,
            "requires_human_review_reason": "market brief baseline is a teacher signal and must remain candidate until editorial review.",
        },
        "human_review_required": True,
        "promotion_policy": "review_required",
        "expires_at": None,
        "notes": _clean_text(anchor.get("main_thesis"), limit=160),
    }


def _relation_signal_kind(relation: str) -> str:
    if relation in CONFLICT_RELATIONS:
        return "thesis_conflict"
    if relation in PENDING_RELATIONS:
        return "agent_observation"
    return "thesis_support"


def _relation_stance(relation: str) -> str:
    if relation in CONFLICT_RELATIONS:
        return "conflicts"
    if relation in PENDING_RELATIONS:
        return "neutral"
    return "supports"


def _benchmark_signal(
    row: dict[str, Any],
    chain_map: dict[str, Any],
    theme: dict[str, Any],
    *,
    report_date: Any,
    created_at: str,
    source_path: str,
) -> dict[str, Any] | None:
    relation = str(row.get("relation") or "")
    text = _clean_text(row.get("text"), limit=420)
    if not relation or not text:
        return None
    confidence = _bounded_float(row.get("confidence"), default=0.55)
    source_signal_refs = []
    if row.get("signal_id"):
        source_signal_refs.append(
            {
                "id": str(row.get("signal_id")),
                "label": str(row.get("signal_id")),
                "ref_type": "external",
            }
        )
    framework = _framework_ref(chain_map.get("dimension_id"), chain_map.get("dimension_label"))
    conflict_refs = []
    if relation in CONFLICT_RELATIONS:
        conflict_refs.append(
            {
                "conflict_id": _hash_id("CONFLICT", chain_map.get("chain_id"), text),
                "path": source_path,
                "severity": "medium",
            }
        )
    return {
        "schema_version": "research_signal.v1",
        "signal_id": _hash_id("SIG-LB", report_date, chain_map.get("chain_id"), relation, text),
        "status": "candidate",
        "created_at": created_at,
        "updated_at": None,
        "source_role": "agent",
        "source_ref": _source_ref(
            "candidate",
            "brief_logic_benchmark_map",
            artifact_id=str(chain_map.get("chain_id") or ""),
            path=source_path,
        ),
        "signal_kind": _relation_signal_kind(relation),
        "asset_refs": theme["asset_refs"],
        "theme_refs": [_theme_ref(theme)],
        "framework_node_refs": [framework] if framework else [],
        "direction": _normalize_direction(row.get("direction")),
        "strength": _strength_from_score(row.get("confidence"), default=0.5),
        "confidence": confidence,
        "confidence_label": _confidence_label(confidence),
        "time_window": _time_window(report_date),
        "event_definition_layers": _event_layers(
            text,
            time_window_layer=f"{_date_iso(report_date)} logic benchmark relation={relation}",
        ),
        "evidence_refs": [
            {
                "evidence_id": str(row.get("signal_id") or chain_map.get("chain_id") or _hash_id("BMSIG", text)),
                "path": source_path,
                "stance": _relation_stance(relation),
                "strength": _evidence_strength(confidence),
                "snippet_ref": relation,
            }
        ],
        "source_signal_refs": source_signal_refs,
        "conflict_refs": conflict_refs,
        "mapping": {
            "method": "brief_logic_benchmark_map_to_research_signal.v1",
            "confidence": confidence,
            "mapped_by": MAPPER_VERSION,
            "requires_human_review_reason": "logic-chain benchmark relations are explanatory candidates, not primary report edits.",
        },
        "human_review_required": True,
        "promotion_policy": "review_required",
        "expires_at": None,
        "notes": text,
    }


def map_futures_brief_candidates(
    brief_thesis_anchor: dict[str, Any] | None,
    brief_logic_benchmark_map: dict[str, Any] | None = None,
    *,
    brief_anchor_path: str = "",
    benchmark_map_path: str = "",
    created_at: str | None = None,
    max_benchmark_signals_per_asset: int = 12,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Map futures brief anchors/benchmark maps into research_signal/theme_anchor objects."""
    created = created_at or utc_now_iso()
    themes: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    theme_by_asset: dict[str, dict[str, Any]] = {}
    report_date = (
        (brief_thesis_anchor or {}).get("report_date")
        or (brief_logic_benchmark_map or {}).get("report_date")
        or datetime.now(timezone.utc).strftime("%Y%m%d")
    )

    for asset_key, anchor in sorted(((brief_thesis_anchor or {}).get("assets") or {}).items()):
        if not isinstance(anchor, dict):
            continue
        theme = _futures_theme_from_anchor(
            str(asset_key),
            anchor,
            report_date=report_date,
            created_at=created,
            source_path=brief_anchor_path,
        )
        signal = _brief_signal_from_anchor(
            anchor,
            theme,
            report_date=report_date,
            created_at=created,
            source_path=brief_anchor_path,
        )
        theme["signal_refs"].append(_signal_ref(signal))
        themes.append(theme)
        signals.append(signal)
        theme_by_asset[str(asset_key)] = theme

    for asset_key, payload in sorted(((brief_logic_benchmark_map or {}).get("assets") or {}).items()):
        if not isinstance(payload, dict):
            continue
        theme = theme_by_asset.get(str(asset_key))
        if theme is None:
            theme = {
                "schema_version": "theme_anchor.v1",
                "theme_anchor_id": _hash_id("THA-LB", report_date, asset_key, payload.get("anchor_id")),
                "status": "candidate",
                "created_at": created,
                "updated_at": None,
                "title": f"{asset_key}动态逻辑链候选主线",
                "description": "Generated from brief_logic_benchmark_map without a matching brief_thesis_anchor.",
                "anchor_kind": "agent_cluster_candidate",
                "theme_type": "other",
                "source_roles": ["agent"],
                "source_refs": [
                    _source_ref(
                        "candidate",
                        "brief_logic_benchmark_map",
                        artifact_id=str(payload.get("anchor_id") or ""),
                        path=benchmark_map_path,
                    )
                ],
                "asset_refs": [_asset_ref(asset_key, payload.get("asset_ref"))],
                "framework_node_refs": [],
                "event_definition_layers": _event_layers(
                    f"{asset_key} dynamic logic-chain benchmark candidate",
                    time_window_layer=f"{_date_iso(report_date)} logic benchmark",
                ),
                "signal_refs": [],
                "evidence_refs": [],
                "polymarket_refs": [],
                "aliases": [str(asset_key)],
                "lifecycle": {
                    "current_phase": "new",
                    "first_seen_at": _date_iso(report_date),
                    "last_seen_at": _date_iso(report_date),
                    "support_count": 0,
                    "conflict_count": 0,
                    "review_state": "machine_candidate",
                },
                "promotion_policy": "review_required",
            }
            themes.append(theme)
            theme_by_asset[str(asset_key)] = theme

        emitted = 0
        for chain_map in payload.get("chain_maps") or []:
            if not isinstance(chain_map, dict):
                continue
            for bucket in ("inherited_signals", "new_signals", "conflict_signals", "pending_observations"):
                for row in chain_map.get(bucket) or []:
                    if emitted >= max_benchmark_signals_per_asset:
                        break
                    if not isinstance(row, dict):
                        continue
                    signal = _benchmark_signal(
                        row,
                        chain_map,
                        theme,
                        report_date=report_date,
                        created_at=created,
                        source_path=benchmark_map_path,
                    )
                    if signal is None:
                        continue
                    signals.append(signal)
                    theme["signal_refs"].append(_signal_ref(signal))
                    if signal["evidence_refs"]:
                        theme["evidence_refs"].append(signal["evidence_refs"][0])
                    if signal["signal_kind"] == "thesis_conflict":
                        theme["lifecycle"]["conflict_count"] += 1
                        theme["lifecycle"]["current_phase"] = "conflicted"
                    else:
                        theme["lifecycle"]["support_count"] += 1
                    emitted += 1

    return signals, themes


def _research_asset_ref(evidence: dict[str, Any]) -> dict[str, Any]:
    asset = evidence.get("asset") if isinstance(evidence.get("asset"), dict) else {}
    return _asset_ref(asset.get("name") or "未识别资产", asset.get("asset_id") or asset.get("commodity_code"))


def _is_noisy_research_heading(text: str) -> bool:
    if not text:
        return True
    noisy_prefixes = ("编辑日期", "资讯速递", "宏观要闻", "行业要闻", "金融衍生品")
    if any(text.startswith(prefix) for prefix in noisy_prefixes):
        return True
    return len(text) > 48


def _derived_research_theme_title(text: str, asset_label: str) -> str:
    rules = (
        ("美联储政策转鹰", ("美联储", "加息", "利率", "点阵图", "核心PCE")),
        ("美伊协议与霍尔木兹通航", ("美伊", "伊朗", "霍尔木兹", "海峡", "原油出口")),
        ("陆家嘴论坛金融开放政策", ("陆家嘴论坛", "金融改革", "离岸人民币", "外汇期货")),
        ("科技产业政策与AI主线", ("DeepSeek", "Copilot", "人工智能", "科技主线", "半导体")),
        ("库存变化与供需验证", ("库存", "仓单", "去库", "累库")),
        ("开工需求与终端消费", ("开工", "需求", "消费", "订单", "终端")),
        ("供应扰动与产量变化", ("供应", "供给", "产量", "检修", "减产", "增产")),
        ("价格表现与资金情绪", ("收涨", "收跌", "涨幅", "跌幅", "资金面")),
    )
    for title, keywords in rules:
        if any(keyword in text for keyword in keywords):
            return title
    sentence = re.split(r"[。！？；;\n]", text, maxsplit=1)[0].strip()
    sentence = _clean_text(sentence, limit=34)
    if sentence:
        return f"{asset_label}：{sentence}" if asset_label and asset_label not in sentence else sentence
    return f"{asset_label}研报主题候选" if asset_label else "研报主题候选"


def _research_theme_title(evidence: dict[str, Any]) -> str:
    snippet = evidence.get("snippet") if isinstance(evidence.get("snippet"), dict) else {}
    heading = _clean_text(snippet.get("section_heading"), limit=72)
    heading = re.sub(r"^\d{1,2}[、.．]\s*", "", heading)
    claim = evidence.get("claim") if isinstance(evidence.get("claim"), dict) else {}
    asset = evidence.get("asset") if isinstance(evidence.get("asset"), dict) else {}
    claim_text = _clean_text(claim.get("text") or snippet.get("text"), limit=420)
    if heading and not _is_noisy_research_heading(heading):
        return heading
    return _derived_research_theme_title(claim_text, str(asset.get("name") or ""))


def _research_source_date(evidence: dict[str, Any]) -> str:
    source = evidence.get("source") if isinstance(evidence.get("source"), dict) else {}
    return _date_iso(source.get("published_at") or evidence.get("generated_at"))


def _research_signal_from_evidence(
    evidence: dict[str, Any],
    *,
    evidence_path: str,
    theme_id: str,
    theme_title: str,
    created_at: str,
) -> dict[str, Any]:
    claim = evidence.get("claim") if isinstance(evidence.get("claim"), dict) else {}
    snippet = evidence.get("snippet") if isinstance(evidence.get("snippet"), dict) else {}
    source = evidence.get("source") if isinstance(evidence.get("source"), dict) else {}
    quality = evidence.get("quality") if isinstance(evidence.get("quality"), dict) else {}
    canonical_ref = evidence.get("canonical_ref") if isinstance(evidence.get("canonical_ref"), dict) else {}
    confidence = _bounded_float(quality.get("confidence"), default=0.45)
    direction = _normalize_direction(claim.get("direction"), score=claim.get("direction_score"))
    source_name = str(source.get("source_system") or "hzzhqx_wechat")
    published = _research_source_date(evidence)
    text = _clean_text(claim.get("text") or snippet.get("text"), limit=420)
    return {
        "schema_version": "research_signal.v1",
        "signal_id": _hash_id("SIG-RREP", evidence.get("evidence_id"), text),
        "status": "candidate",
        "created_at": created_at,
        "updated_at": None,
        "source_role": "research_report",
        "source_ref": _source_ref(
            "evidence_capsule",
            source_name,
            artifact_id=str(evidence.get("evidence_id") or ""),
            path=evidence_path,
            content_hash=evidence.get("content_hash"),
            url=source.get("canonical_url") or source.get("source_url"),
        ),
        "signal_kind": "theme_update",
        "asset_refs": [_research_asset_ref(evidence)],
        "theme_refs": [{"id": theme_id, "label": theme_title, "ref_type": "theme"}],
        "framework_node_refs": [],
        "direction": direction,
        "strength": _strength_from_score(claim.get("direction_score"), default=confidence),
        "confidence": confidence,
        "confidence_label": _confidence_label(confidence),
        "time_window": {"start": published, "end": published, "horizon": "daily"},
        "event_definition_layers": _event_layers(
            text,
            political_layer=text if _infer_theme_type(theme_title, text) in {"policy", "geopolitics"} else None,
            time_window_layer=f"{published} research report publication window",
        ),
        "evidence_refs": [
            {
                "evidence_id": str(evidence.get("evidence_id") or ""),
                "path": evidence_path,
                "stance": "source",
                "strength": _evidence_strength(confidence),
                "snippet_ref": str(canonical_ref.get("chunk_id") or canonical_ref.get("section_id") or "") or None,
            }
        ],
        "source_signal_refs": [],
        "conflict_refs": [],
        "mapping": {
            "method": "wechat_research_evidence_to_research_signal.v1",
            "confidence": confidence,
            "mapped_by": MAPPER_VERSION,
            "requires_human_review_reason": "research-report evidence is machine extracted and needs editorial review before gold promotion.",
        },
        "human_review_required": bool(quality.get("human_review_required", True)),
        "promotion_policy": "review_required",
        "expires_at": None,
        "notes": text,
    }


def _source_ref_key(ref: dict[str, Any]) -> tuple[str, str, str]:
    return (str(ref.get("ref_type") or ""), str(ref.get("source_name") or ""), str(ref.get("id") or ref.get("path") or ""))


def _dedupe_list(items: list[dict[str, Any]], key_fn) -> list[dict[str, Any]]:
    seen = set()
    out = []
    for item in items:
        key = key_fn(item)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def map_research_evidence_candidates(
    canonical_document: dict[str, Any] | None,
    evidence_units: list[dict[str, Any]],
    *,
    canonical_path: str = "",
    evidence_paths: dict[str, str] | None = None,
    created_at: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Map research-report evidence capsules into research_signal/theme_anchor objects."""
    created = created_at or utc_now_iso()
    evidence_paths = evidence_paths or {}
    canonical_id = str((canonical_document or {}).get("document_id") or "")
    canonical_source = str((canonical_document or {}).get("source_system") or "hzzhqx_wechat")
    canonical_ref = _source_ref(
        "canonical_document",
        canonical_source,
        artifact_id=canonical_id or None,
        path=canonical_path or None,
        content_hash=(canonical_document or {}).get("content_hash"),
    )

    grouped: dict[str, dict[str, Any]] = {}
    signals: list[dict[str, Any]] = []
    for evidence in evidence_units:
        if not isinstance(evidence, dict):
            continue
        evidence_id = str(evidence.get("evidence_id") or "")
        evidence_path = evidence_paths.get(evidence_id) or evidence_paths.get("*") or ""
        title = _research_theme_title(evidence)
        asset = _research_asset_ref(evidence)
        theme_id = _hash_id("THA-RREP", canonical_id, title, asset["id"])
        signal = _research_signal_from_evidence(
            evidence,
            evidence_path=evidence_path,
            theme_id=theme_id,
            theme_title=title,
            created_at=created,
        )
        signals.append(signal)
        group = grouped.setdefault(
            theme_id,
            {
                "title": title,
                "asset_refs": [],
                "source_refs": [canonical_ref],
                "signals": [],
                "evidence_refs": [],
                "directions": Counter(),
                "first_seen": _research_source_date(evidence),
                "last_seen": _research_source_date(evidence),
                "texts": [],
            },
        )
        group["asset_refs"].append(asset)
        group["source_refs"].append(signal["source_ref"])
        group["signals"].append(signal)
        group["evidence_refs"].extend(signal["evidence_refs"])
        group["directions"][signal["direction"]] += 1
        group["first_seen"] = min(str(group["first_seen"]), _research_source_date(evidence))
        group["last_seen"] = max(str(group["last_seen"]), _research_source_date(evidence))
        group["texts"].append(signal.get("notes") or "")

    themes = []
    for theme_id, group in sorted(grouped.items()):
        directions: Counter = group["directions"]
        bullish = directions.get("bullish", 0)
        bearish = directions.get("bearish", 0)
        conflict_count = min(bullish, bearish)
        support_count = len(group["signals"])
        first_text = _clean_text((group["texts"] or [""])[0], limit=420)
        theme_type = _infer_theme_type(group["title"], first_text, *group["texts"])
        source_refs = _dedupe_list(group["source_refs"], _source_ref_key)
        evidence_refs = _dedupe_list(group["evidence_refs"], lambda item: (item.get("evidence_id"), item.get("path")))
        signal_refs = [_signal_ref(signal) for signal in group["signals"]]
        themes.append(
            {
                "schema_version": "theme_anchor.v1",
                "theme_anchor_id": theme_id,
                "status": "candidate",
                "created_at": created,
                "updated_at": None,
                "title": group["title"],
                "description": first_text or None,
                "anchor_kind": "research_report_repeated_theme" if support_count >= 2 else "agent_cluster_candidate",
                "theme_type": theme_type,
                "source_roles": ["research_report"],
                "source_refs": source_refs,
                "asset_refs": _dedupe_list(group["asset_refs"], lambda item: (item.get("id"), item.get("label"))),
                "framework_node_refs": [],
                "event_definition_layers": _event_layers(
                    first_text,
                    political_layer=first_text if theme_type in {"policy", "geopolitics"} else None,
                    time_window_layer=f"{group['first_seen']} to {group['last_seen']} research-report observation window",
                ),
                "signal_refs": signal_refs,
                "evidence_refs": evidence_refs,
                "polymarket_refs": [],
                "aliases": [group["title"]],
                "lifecycle": {
                    "current_phase": "conflicted" if conflict_count else "tracking" if support_count >= 2 else "new",
                    "first_seen_at": group["first_seen"],
                    "last_seen_at": group["last_seen"],
                    "support_count": support_count,
                    "conflict_count": conflict_count,
                    "review_state": "machine_candidate",
                },
                "promotion_policy": "review_required",
            }
        )
    return signals, themes


def build_polymarket_event_definition_signal(
    *,
    market_id: str,
    question: str,
    price: float | None,
    spread: float | None,
    liquidity: float | None,
    liquidity_label: str = "unknown",
    settlement_rule: str,
    time_window: dict[str, Any],
    source_path: str = "",
    created_at: str | None = None,
    theme_refs: list[dict[str, Any]] | None = None,
    asset_refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the low-confidence Polymarket hook without changing polymarket runtime."""
    created = created_at or utc_now_iso()
    market_time_window = {
        "start": time_window.get("start"),
        "end": time_window.get("end"),
        "horizon": time_window.get("horizon") or "event_window",
    }
    return {
        "schema_version": "research_signal.v1",
        "signal_id": _hash_id("SIG-POLY", market_id, question),
        "status": "candidate",
        "created_at": created,
        "updated_at": None,
        "source_role": "web_info",
        "source_ref": _source_ref(
            "external",
            "Polymarket",
            artifact_id=market_id,
            path=source_path or None,
        ),
        "signal_kind": "event_definition",
        "asset_refs": asset_refs or [],
        "theme_refs": theme_refs or [],
        "framework_node_refs": [],
        "direction": "unknown",
        "strength": 0.2,
        "confidence": 0.25,
        "confidence_label": "low_confidence_signal",
        "time_window": market_time_window,
        "event_definition_layers": _event_layers(
            question,
            time_window_layer=f"{market_time_window.get('start')} to {market_time_window.get('end')}",
            settlement_rule_layer=settlement_rule,
        ),
        "evidence_refs": [],
        "source_signal_refs": [],
        "conflict_refs": [],
        "market_observation": {
            "market_type": "prediction_market",
            "price": price,
            "probability": None,
            "spread": spread,
            "liquidity": liquidity,
            "liquidity_label": liquidity_label if liquidity_label in {"low", "medium", "high", "unknown"} else "unknown",
            "settlement_rule": settlement_rule,
            "time_window": market_time_window,
        },
        "mapping": {
            "method": "polymarket_event_definition_hook.v1",
            "confidence": 0.25,
            "mapped_by": MAPPER_VERSION,
            "requires_human_review_reason": "prediction-market prices are web-info event-definition observations, not truth probabilities.",
        },
        "human_review_required": True,
        "promotion_policy": "review_required",
        "expires_at": None,
        "notes": "Polymarket is mapped only as a low-confidence event-definition signal.",
    }


def _dedupe_by_id(items: list[dict[str, Any]], id_key: str) -> list[dict[str, Any]]:
    seen = set()
    out = []
    for item in items:
        item_id = str(item.get(id_key) or "")
        if not item_id or item_id in seen:
            continue
        seen.add(item_id)
        out.append(item)
    return out


def _attach_missing_theme_signal_refs(signals: list[dict[str, Any]], themes: list[dict[str, Any]]) -> None:
    by_id = {str(theme.get("theme_anchor_id")): theme for theme in themes}
    for signal in signals:
        signal_ref = _signal_ref(signal)
        for ref in signal.get("theme_refs") or []:
            theme = by_id.get(str(ref.get("id") or ""))
            if not theme:
                continue
            refs = theme.setdefault("signal_refs", [])
            if all(item.get("id") != signal_ref["id"] for item in refs):
                refs.append(signal_ref)


def validate_signal_theme_objects(
    root: str | Path,
    *,
    signals: list[dict[str, Any]],
    themes: list[dict[str, Any]],
) -> dict[str, Any]:
    """Validate generated objects with quanta_data JSON Schema files."""
    try:
        from jsonschema import Draft202012Validator, FormatChecker
    except ModuleNotFoundError as exc:
        raise RuntimeError("jsonschema is required for schema validation; use the project venv or install dependencies") from exc

    root_path = quanta_data_root(root)
    signal_schema = read_json(root_path / "configs/schemas/research_signal.v1.schema.json")
    theme_schema = read_json(root_path / "configs/schemas/theme_anchor.v1.schema.json")
    signal_validator = Draft202012Validator(signal_schema, format_checker=FormatChecker())
    theme_validator = Draft202012Validator(theme_schema, format_checker=FormatChecker())
    for signal in signals:
        signal_validator.validate(signal)
    for theme in themes:
        theme_validator.validate(theme)
    return {
        "schema_validation": "passed",
        "signal_count": len(signals),
        "theme_anchor_count": len(themes),
    }


def _load_optional_json(path: str | Path | None) -> dict[str, Any] | None:
    if not path:
        return None
    file_path = Path(path).expanduser()
    if not file_path.exists():
        return None
    payload = read_json(file_path)
    return payload if isinstance(payload, dict) else None


def _load_evidence_units(evidence_files: list[Path], *, limit: int) -> tuple[list[dict[str, Any]], dict[str, str]]:
    units: list[dict[str, Any]] = []
    paths: dict[str, str] = {}
    for path in evidence_files[:limit]:
        payload = read_json(path)
        if not isinstance(payload, dict):
            continue
        evidence_id = str(payload.get("evidence_id") or path.stem)
        units.append(payload)
        paths[evidence_id] = str(path)
    return units, paths


def _candidate_dirs(root: Path, date_key: str, stamp: str) -> tuple[Path, Path]:
    yyyy, mm, dd = dated_parts(date_key)
    signal_dir = root / "agent_workspace/candidates/signal_map" / yyyy / mm / dd / f"CAND-SIGNAL-MAP-{date_key}-{stamp}"
    theme_dir = root / "agent_workspace/candidates/theme_anchor" / yyyy / mm / dd / f"CAND-THEME-ANCHOR-{date_key}-{stamp}"
    return signal_dir, theme_dir


def _run_dir(root: Path, date_key: str, run_id: str) -> Path:
    yyyy, mm, dd = dated_parts(date_key)
    return root / "agent_workspace/runs/signal_mapping" / yyyy / mm / dd / run_id


def run_signal_theme_mapping(
    root: str | Path | None = None,
    *,
    futures_run_dir: str | Path | None = None,
    brief_anchor_path: str | Path | None = None,
    benchmark_map_path: str | Path | None = None,
    canonical_document_path: str | Path | None = None,
    evidence_dir: str | Path | None = None,
    evidence_paths: list[str | Path] | None = None,
    report_date: str | None = None,
    work_order_id: str = DEFAULT_WORK_ORDER_ID,
    max_evidence_units: int = 12,
    validate: bool = True,
    write_outputs: bool = True,
) -> dict[str, Any]:
    root_path = quanta_data_root(root)
    futures_dir = Path(futures_run_dir).expanduser() if futures_run_dir else None
    anchor_file = Path(brief_anchor_path).expanduser() if brief_anchor_path else (futures_dir / "brief_thesis_anchor.json" if futures_dir else None)
    benchmark_file = Path(benchmark_map_path).expanduser() if benchmark_map_path else (futures_dir / "brief_logic_benchmark_map.json" if futures_dir else None)
    canonical_file = Path(canonical_document_path).expanduser() if canonical_document_path else None

    brief_anchor = _load_optional_json(anchor_file)
    benchmark_map = _load_optional_json(benchmark_file)
    canonical = _load_optional_json(canonical_file)

    evidence_files: list[Path] = []
    if evidence_dir:
        evidence_files.extend(sorted(Path(evidence_dir).expanduser().glob("*.json")))
    if evidence_paths:
        evidence_files.extend(Path(path).expanduser() for path in evidence_paths)
    evidence_units, evidence_path_map_abs = _load_evidence_units(evidence_files, limit=max_evidence_units)

    created_at = utc_now_iso()
    inferred_date = (
        report_date
        or (brief_anchor or {}).get("report_date")
        or (benchmark_map or {}).get("report_date")
        or ((canonical or {}).get("metadata") or {}).get("published_at")
        or ((evidence_units[0].get("source") or {}).get("published_at") if evidence_units else None)
    )
    date_key = _date_key(inferred_date)
    stamp = datetime.now().strftime("%H%M%S%f")
    run_id = f"RUN-{re.sub(r'[^A-Z0-9_-]+', '-', work_order_id.upper())}-SIGNAL-THEME-{stamp}"

    futures_signals, futures_themes = map_futures_brief_candidates(
        brief_anchor,
        benchmark_map,
        brief_anchor_path=_safe_path(anchor_file, root_path) if anchor_file else "",
        benchmark_map_path=_safe_path(benchmark_file, root_path) if benchmark_file else "",
        created_at=created_at,
    )
    evidence_path_map = {
        evidence_id: _safe_path(path, root_path) for evidence_id, path in evidence_path_map_abs.items()
    }
    research_signals, research_themes = map_research_evidence_candidates(
        canonical,
        evidence_units,
        canonical_path=_safe_path(canonical_file, root_path) if canonical_file else "",
        evidence_paths=evidence_path_map,
        created_at=created_at,
    )

    signals = _dedupe_by_id([*futures_signals, *research_signals], "signal_id")
    themes = _dedupe_by_id([*futures_themes, *research_themes], "theme_anchor_id")
    _attach_missing_theme_signal_refs(signals, themes)

    validation_result: dict[str, Any] = {"schema_validation": "not_run"}
    if validate:
        validation_result = validate_signal_theme_objects(root_path, signals=signals, themes=themes)

    input_refs = []
    for path, ref_type, source_name in (
        (anchor_file, "candidate", "brief_thesis_anchor"),
        (benchmark_file, "candidate", "brief_logic_benchmark_map"),
        (canonical_file, "canonical_document", "hzzhqx_wechat"),
    ):
        if path and path.exists():
            input_refs.append(_source_ref(ref_type, source_name, path=path, root=root_path, artifact_id=path.stem))
    for evidence_id, path in evidence_path_map_abs.items():
        input_refs.append(_source_ref("evidence_capsule", "hzzhqx_wechat", artifact_id=evidence_id, path=path, root=root_path))

    paths: dict[str, str] = {}
    output_refs: list[dict[str, Any]] = []
    if write_outputs:
        signal_dir, theme_dir = _candidate_dirs(root_path, date_key, stamp)
        run_path = _run_dir(root_path, date_key, run_id)
        signal_file = signal_dir / "research_signals.json"
        theme_file = theme_dir / "theme_anchors.json"
        signal_manifest = signal_dir / "manifest.json"
        theme_manifest = theme_dir / "manifest.json"
        run_manifest = run_path / "run_manifest.json"

        signal_payload = {
            "schema_version": "research_signal_candidate_set.v1",
            "status": "candidate",
            "candidate_id": signal_dir.name,
            "generated_at": created_at,
            "work_order_id": work_order_id,
            "mapper_version": MAPPER_VERSION,
            "signals": signals,
        }
        theme_payload = {
            "schema_version": "theme_anchor_candidate_set.v1",
            "status": "candidate",
            "candidate_id": theme_dir.name,
            "generated_at": created_at,
            "work_order_id": work_order_id,
            "mapper_version": MAPPER_VERSION,
            "theme_anchors": themes,
        }
        write_json(signal_file, signal_payload)
        write_json(theme_file, theme_payload)
        write_json(
            signal_manifest,
            {
                "schema_version": "signal_map_candidate_manifest.v1",
                "status": "candidate",
                "candidate_id": signal_dir.name,
                "run_id": run_id,
                "date": date_key,
                "generated_at": created_at,
                "work_order_id": work_order_id,
                "schema_validated": validation_result.get("schema_validation") == "passed",
                "outputs": {"research_signals": relative_to_root(signal_file, root_path)},
                "signal_count": len(signals),
                "requires_review": True,
            },
        )
        write_json(
            theme_manifest,
            {
                "schema_version": "theme_anchor_candidate_manifest.v1",
                "status": "candidate",
                "candidate_id": theme_dir.name,
                "run_id": run_id,
                "date": date_key,
                "generated_at": created_at,
                "work_order_id": work_order_id,
                "schema_validated": validation_result.get("schema_validation") == "passed",
                "outputs": {"theme_anchors": relative_to_root(theme_file, root_path)},
                "theme_anchor_count": len(themes),
                "requires_review": True,
            },
        )
        output_refs = [
            _source_ref("candidate", "signal_map", artifact_id=signal_dir.name, path=signal_manifest, root=root_path),
            _source_ref("candidate", "theme_anchor", artifact_id=theme_dir.name, path=theme_manifest, root=root_path),
        ]
        write_json(
            run_manifest,
            {
                "schema_version": "signal_theme_mapping_run.v1",
                "status": "succeeded",
                "run_id": run_id,
                "run_type": "signal_theme_mapper",
                "date": date_key,
                "generated_at": created_at,
                "work_order_id": work_order_id,
                "mapper_version": MAPPER_VERSION,
                "input_refs": input_refs,
                "output_refs": output_refs,
                "signal_count": len(signals),
                "theme_anchor_count": len(themes),
                "schema_validation": validation_result,
                "human_review_required": True,
                "notes": [
                    "market_brief remains the primary display contract",
                    "opinion_radar and Polymarket runtime are not changed by this mapper",
                    "Polymarket must enter later as web_info/event_definition/low_confidence_signal",
                ],
            },
        )
        paths = {
            "signal_candidate_dir": relative_to_root(signal_dir, root_path),
            "signal_manifest": relative_to_root(signal_manifest, root_path),
            "research_signals": relative_to_root(signal_file, root_path),
            "theme_candidate_dir": relative_to_root(theme_dir, root_path),
            "theme_manifest": relative_to_root(theme_manifest, root_path),
            "theme_anchors": relative_to_root(theme_file, root_path),
            "run_manifest": relative_to_root(run_manifest, root_path),
        }

    return {
        "status": "succeeded",
        "run_id": run_id,
        "date": date_key,
        "signal_count": len(signals),
        "theme_anchor_count": len(themes),
        "schema_validation": validation_result,
        "paths": paths,
        "input_refs": input_refs,
        "output_refs": output_refs,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Map futures/research artifacts into research_signal/theme_anchor candidates.")
    parser.add_argument("--root", default="", help="quanta_data root. Defaults to GJ_QUANTA_DATA_ROOT discovery.")
    parser.add_argument("--futures-run-dir", default="", help="Directory containing brief_thesis_anchor.json and brief_logic_benchmark_map.json.")
    parser.add_argument("--brief-anchor", default="", help="Explicit brief_thesis_anchor.json path.")
    parser.add_argument("--benchmark-map", default="", help="Explicit brief_logic_benchmark_map.json path.")
    parser.add_argument("--canonical-document", default="", help="Canonical WeChat research report document path.")
    parser.add_argument("--evidence-dir", default="", help="Directory of WeChat research evidence capsules.")
    parser.add_argument("--evidence", action="append", default=[], help="Specific evidence capsule path. Can be repeated.")
    parser.add_argument("--report-date", default="", help="YYYYMMDD/YYY-MM-DD report date override.")
    parser.add_argument("--work-order-id", default=DEFAULT_WORK_ORDER_ID)
    parser.add_argument("--max-evidence-units", type=int, default=12)
    parser.add_argument("--no-validate", action="store_true", help="Skip JSON Schema validation.")
    parser.add_argument("--dry-run", action="store_true", help="Build and validate without writing candidates.")
    args = parser.parse_args(argv)

    result = run_signal_theme_mapping(
        args.root or None,
        futures_run_dir=args.futures_run_dir or None,
        brief_anchor_path=args.brief_anchor or None,
        benchmark_map_path=args.benchmark_map or None,
        canonical_document_path=args.canonical_document or None,
        evidence_dir=args.evidence_dir or None,
        evidence_paths=args.evidence,
        report_date=args.report_date or None,
        work_order_id=args.work_order_id,
        max_evidence_units=args.max_evidence_units,
        validate=not args.no_validate,
        write_outputs=not args.dry_run,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
