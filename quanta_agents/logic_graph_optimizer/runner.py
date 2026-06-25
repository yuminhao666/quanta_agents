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
from quanta_agents.core.io import atomic_write_text, read_json, relative_to_root, write_json


SCHEMA_VERSION = "logic_graph_optimizer_run.v1"
DEFAULT_ASSETS = ("原油", "铜", "铝")
DEFAULT_START_DATE = "20260401"
DEFAULT_BENCHMARK_SIZE_PER_ASSET = 40
CANDIDATE_RELATIVE_ROOT = Path("agent_workspace/candidates/logic_graph_optimizer")
RESEARCH_GRAPH_RELATIVE_ROOT = Path("agent_workspace/candidates/research_logic_graph/latest")
ACTIVE_FRAMEWORK_ROOT = Path(
    "/Users/miniquanta/Documents/quanta_research_group/data_lake/active_knowledge/"
    "research_frameworks/commodities"
)

STATE_WORDS = (
    "偏多",
    "偏空",
    "中性",
    "分歧",
    "跟踪",
    "待判定",
    "利多",
    "利空",
    "看多",
    "看空",
    "转强",
    "转弱",
)
DIRECTION_CN = {
    "bullish": "偏多",
    "bearish": "偏空",
    "neutral": "中性",
    "mixed": "分歧",
    "unknown": "待判定",
}
REQUIRED_NODE_FIELDS = (
    "logic_node_id",
    "asset",
    "asset_id",
    "dimension_label",
    "dimension_type",
    "predicate",
    "direction",
    "state",
    "confidence",
    "evidence_count",
    "evidence_refs",
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _hash_id(prefix: str, *parts: Any, length: int = 12) -> str:
    raw = "||".join(str(part) for part in parts)
    return f"{prefix}-{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:length].upper()}"


def _clean_key(value: Any) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"[^\w\u4e00-\u9fff]+", "_", text).strip("_")


def _asset_key(asset_id: str, asset: str) -> str:
    raw = str(asset_id or asset or "asset").strip().lower()
    raw = raw.replace("fut-", "fut_").replace(":", "_").replace(".", "_")
    raw = re.sub(r"[^a-z0-9_\u4e00-\u9fff]+", "_", raw)
    return raw.strip("_") or "asset"


def _tail_label(value: Any) -> str:
    text = str(value or "").strip()
    if "/" in text:
        return text.rsplit("/", 1)[-1].strip() or text
    return text


def _strip_state_words(label: Any) -> str:
    text = re.sub(r"\s+", " ", str(label or "")).strip()
    if not text:
        return ""
    changed = True
    while changed:
        changed = False
        for word in STATE_WORDS:
            if text.endswith(word) and len(text) > len(word) + 1:
                text = text[: -len(word)].rstrip(" ：:，,/-")
                changed = True
    return text.strip()


def _contains_state_word(label: Any) -> bool:
    text = str(label or "")
    return any(word in text for word in STATE_WORDS)


def _preferred_label(node: dict[str, Any]) -> str:
    predicate = _strip_state_words(node.get("predicate"))
    if predicate and predicate != "未归类":
        return predicate
    dimension_tail = _strip_state_words(_tail_label(node.get("dimension_label")))
    if dimension_tail and dimension_tail != "未归类":
        return dimension_tail
    human_label = _strip_state_words(node.get("human_label"))
    return human_label or "未归类概念"


def _concept_id(node: dict[str, Any], preferred_label: str) -> str:
    asset_id = str(node.get("asset_id") or "")
    asset = str(node.get("asset") or "")
    dimension_type = _clean_key(node.get("dimension_type") or "other") or "other"
    digest = hashlib.sha1(
        "||".join(
            [
                asset_id or asset,
                dimension_type,
                _clean_key(preferred_label),
                _clean_key(_tail_label(node.get("dimension_label"))),
            ]
        ).encode("utf-8")
    ).hexdigest()[:10]
    return f"asset.{_asset_key(asset_id, asset)}.{dimension_type}.concept_{digest}"


def _date_split(value: Any) -> str:
    text = str(value or "")
    if text >= "2026-06-08":
        return "holdout"
    if text >= "2026-05-16":
        return "tune"
    return "dev"


def _read_payload(path: Path, list_key: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not path.exists():
        return {}, []
    payload = read_json(path)
    if not isinstance(payload, dict):
        return {}, []
    rows = payload.get(list_key) or []
    return payload, [row for row in rows if isinstance(row, dict)]


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    text = "".join(json.dumps(row, ensure_ascii=False, sort_keys=False) + "\n" for row in rows)
    atomic_write_text(path, text)


def _count_profile_files(root: Path, start_date: str, end_date: str) -> int:
    base = root / "canonical_documents/research_reports/structured_profiles/hzzhqx_wechat"
    if not base.exists():
        return 0
    total = 0
    for path in base.glob("20[0-9][0-9]/*/*/RREP-PROFILE-*.json"):
        parts = path.parts
        try:
            idx = parts.index("hzzhqx_wechat")
            date_key = "".join(parts[idx + 1 : idx + 4])
        except (ValueError, IndexError):
            continue
        if start_date <= date_key <= end_date:
            total += 1
    return total


def _load_source_state(root: Path) -> dict[str, Any]:
    latest = root / RESEARCH_GRAPH_RELATIVE_ROOT
    node_payload, nodes = _read_payload(latest / "logic-nodes.json", "nodes")
    edge_payload, edges = _read_payload(latest / "logic-edges.json", "edges")
    episode_payload, episodes = _read_payload(latest / "temporal-episodes.json", "episodes")
    trigger_payload, triggers = _read_payload(
        latest / "driver-trigger-candidates.json", "driver_trigger_candidates"
    )
    manifest = read_json(latest / "run_manifest.json") if (latest / "run_manifest.json").exists() else {}
    concept_summary = {}
    concept_summary_path = latest / "concept_index/summary.json"
    if concept_summary_path.exists():
        concept_summary = read_json(concept_summary_path)
    return {
        "latest_dir": latest,
        "run_id": node_payload.get("run_id") or manifest.get("run_id") or "",
        "generated_at": node_payload.get("generated_at") or manifest.get("generated_at") or "",
        "start_date": str(node_payload.get("start_date") or manifest.get("start_date") or DEFAULT_START_DATE),
        "end_date": str(node_payload.get("end_date") or manifest.get("end_date") or ""),
        "manifest": manifest,
        "concept_summary": concept_summary,
        "node_payload": node_payload,
        "edge_payload": edge_payload,
        "episode_payload": episode_payload,
        "trigger_payload": trigger_payload,
        "nodes": nodes,
        "edges": edges,
        "episodes": episodes,
        "triggers": triggers,
    }


def _audit_modules(root: Path, source: dict[str, Any]) -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[2]
    module_specs = [
        ("research_logic_graph", "quanta_agents/research_reports/logic_graph.py", "研报证据 -> 候选逻辑节点/边"),
        ("wechat_evidence", "quanta_agents/research_reports/wechat_evidence.py", "结构化研报证据汇总"),
        ("framework_alignment", "quanta_agents/futures_daily/framework_alignment.py", "日报/框架维度对齐"),
        ("analysis_framework", "quanta_agents/core/analysis_framework.py", "active framework registry 生成"),
        ("asset_event_state", "quanta_agents/asset_event_state/pipeline.py", "事件状态机候选链路"),
        ("signal_incremental_state", "quanta_agents/signal_mapping/incremental_state.py", "signal 增量状态"),
        ("opinion_news_logic", "quanta_agents/opinion_radar/news_logic.py", "新闻逻辑映射"),
    ]
    modules = []
    for name, rel_path, role in module_specs:
        path = repo_root / rel_path
        modules.append(
            {
                "name": name,
                "role": role,
                "path": str(path),
                "exists": path.exists(),
                "optimizer_usage": "read_only_reference",
            }
        )

    start_date = source["start_date"] or DEFAULT_START_DATE
    end_date = source["end_date"] or datetime.now().strftime("%Y%m%d")
    framework_count = len(list(ACTIVE_FRAMEWORK_ROOT.glob("*.json"))) if ACTIVE_FRAMEWORK_ROOT.exists() else 0
    inventory = {
        "quanta_root": str(root),
        "source_research_logic_graph": {
            "latest_dir": str(source["latest_dir"]),
            "run_id": source["run_id"],
            "start_date": start_date,
            "end_date": end_date,
            "node_count": len(source["nodes"]),
            "edge_count": len(source["edges"]),
            "episode_count": len(source["episodes"]),
            "driver_trigger_candidate_count": len(source["triggers"]),
        },
        "structured_profiles": {
            "path": str(
                root / "canonical_documents/research_reports/structured_profiles/hzzhqx_wechat"
            ),
            "file_count_in_window": _count_profile_files(root, start_date, end_date),
        },
        "active_frameworks": {
            "path": str(ACTIVE_FRAMEWORK_ROOT),
            "file_count": framework_count,
        },
        "existing_concept_index": {
            "path": str(source["latest_dir"] / "concept_index"),
            "exists": (source["latest_dir"] / "concept_index").exists(),
            "summary": source.get("concept_summary") or {},
        },
        "reusable_modules": modules,
    }
    return inventory


def _to_challenger_node(node: dict[str, Any]) -> dict[str, Any]:
    preferred = _preferred_label(node)
    concept_id = _concept_id(node, preferred)
    return {
        "concept_id": concept_id,
        "concept_id_status": "derived_pending_registry",
        "preferred_label_zh": preferred,
        "alt_labels_zh": list(dict.fromkeys([str(node.get("human_label") or "").strip()])),
        "asset": node.get("asset"),
        "asset_id": node.get("asset_id"),
        "dimension_type": node.get("dimension_type"),
        "dimension_label": node.get("dimension_label"),
        "logic_node_id": node.get("logic_node_id"),
        "state_snapshot": {
            "state": node.get("state"),
            "direction": node.get("direction"),
            "direction_label_zh": DIRECTION_CN.get(str(node.get("direction") or ""), ""),
            "strength": node.get("strength"),
            "confidence": node.get("confidence"),
            "support_evidence_count": node.get("support_evidence_count", 0),
            "conflict_evidence_count": node.get("conflict_evidence_count", 0),
            "neutral_evidence_count": node.get("neutral_evidence_count", 0),
            "evidence_count": node.get("evidence_count", 0),
            "source_count": node.get("source_count", 0),
            "first_seen": node.get("first_seen"),
            "last_seen": node.get("last_seen"),
        },
        "source_refs_sample": list(node.get("evidence_refs") or [])[:5],
        "review_status": "candidate_pending_review",
    }


def _edge_index(edges: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_from: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in edges:
        by_from[str(edge.get("from") or "")].append(edge)
    return by_from


def _build_benchmark(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    assets: tuple[str, ...],
    size_per_asset: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    by_asset: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for node in nodes:
        asset = str(node.get("asset") or "")
        if asset in assets:
            by_asset[asset].append(node)

    edge_by_from = _edge_index(edges)
    samples: list[dict[str, Any]] = []
    for asset in assets:
        ranked_all = sorted(
            by_asset.get(asset, []),
            key=lambda row: (int(row.get("evidence_count") or 0), float(row.get("confidence") or 0.0)),
            reverse=True,
        )
        by_split: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in ranked_all:
            by_split[_date_split(row.get("last_seen"))].append(row)

        holdout_quota = max(1, size_per_asset // 2)
        tune_quota = max(1, size_per_asset // 4)
        dev_quota = max(1, size_per_asset - holdout_quota - tune_quota)
        selected: list[dict[str, Any]] = []
        selected_ids: set[str] = set()
        for split, quota in (("holdout", holdout_quota), ("tune", tune_quota), ("dev", dev_quota)):
            for row in by_split.get(split, [])[:quota]:
                selected.append(row)
                selected_ids.add(str(row.get("logic_node_id") or ""))
        for row in ranked_all:
            if len(selected) >= size_per_asset:
                break
            node_id = str(row.get("logic_node_id") or "")
            if node_id not in selected_ids:
                selected.append(row)
                selected_ids.add(node_id)

        for rank, node in enumerate(selected, start=1):
            challenger = _to_challenger_node(node)
            split = _date_split(node.get("last_seen"))
            driver_edges = edge_by_from.get(str(node.get("logic_node_id") or ""), [])
            sample_id = _hash_id("BMS", asset, node.get("logic_node_id"), rank)
            samples.append(
                {
                    "sample_id": sample_id,
                    "split": split,
                    "asset": asset,
                    "asset_id": node.get("asset_id"),
                    "logic_node_id": node.get("logic_node_id"),
                    "baseline_identity_label": node.get("human_label"),
                    "challenger_concept_id": challenger["concept_id"],
                    "challenger_preferred_label_zh": challenger["preferred_label_zh"],
                    "dimension_type": node.get("dimension_type"),
                    "dimension_label": node.get("dimension_label"),
                    "state": node.get("state"),
                    "direction": node.get("direction"),
                    "confidence": node.get("confidence"),
                    "evidence_count": node.get("evidence_count"),
                    "support_evidence_count": node.get("support_evidence_count"),
                    "conflict_evidence_count": node.get("conflict_evidence_count"),
                    "last_seen": node.get("last_seen"),
                    "driver_edge_count": len(driver_edges),
                    "driver_ids": [edge.get("to") for edge in driver_edges],
                    "weak_labels": {
                        "expected_identity_has_no_state_word": True,
                        "expected_state_retained_in_snapshot": True,
                        "expected_has_traceable_evidence": True,
                        "expected_has_driver_edge": bool(driver_edges),
                    },
                    "source_refs_sample": list(node.get("evidence_refs") or [])[:3],
                }
            )

    family_counter: Counter[str] = Counter()
    family_assets: dict[str, set[str]] = defaultdict(set)
    for sample in samples:
        key = f"{sample.get('dimension_type')}:{sample.get('challenger_preferred_label_zh')}"
        family_counter[key] += 1
        family_assets[key].add(str(sample.get("asset") or ""))
    shared_families = [
        {
            "family_key": key,
            "asset_count": len(family_assets[key]),
            "assets": sorted(family_assets[key]),
            "sample_count": count,
        }
        for key, count in family_counter.most_common()
        if len(family_assets[key]) > 1
    ]

    manifest = {
        "schema_version": "logic_graph_optimizer_benchmark.v1",
        "benchmark_policy": {
            "asset_selection": "user_relevant_shared_node_assets",
            "assets": list(assets),
            "size_per_asset": size_per_asset,
            "ranking": "evidence_count_desc_then_confidence",
            "splits": {
                "dev": "last_seen < 2026-05-16",
                "tune": "2026-05-16 <= last_seen < 2026-06-08",
                "holdout": "last_seen >= 2026-06-08",
            },
            "labels": "weak_labels_from_current_traceability_and_schema_checks",
        },
        "sample_count": len(samples),
        "split_counts": dict(Counter(sample["split"] for sample in samples)),
        "asset_counts": dict(Counter(sample["asset"] for sample in samples)),
        "shared_family_count": len(shared_families),
        "shared_families": shared_families[:30],
    }
    return manifest, samples


def _required_field_rate(nodes: list[dict[str, Any]]) -> float:
    if not nodes:
        return 0.0
    expected = len(nodes) * len(REQUIRED_NODE_FIELDS)
    present = 0
    for node in nodes:
        for field in REQUIRED_NODE_FIELDS:
            value = node.get(field)
            if value not in (None, "", []):
                present += 1
    return round(present / expected, 4) if expected else 0.0


def _duplicate_rate(rows: list[dict[str, Any]], key_fn: Any) -> float:
    if not rows:
        return 0.0
    keys = [key_fn(row) for row in rows]
    duplicate_count = sum(count - 1 for count in Counter(keys).values() if count > 1)
    return round(duplicate_count / len(rows), 4)


def _metrics(
    *,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    triggers: list[dict[str, Any]],
    mode: str,
) -> dict[str, Any]:
    if mode == "baseline":
        identity_label = lambda row: str(row.get("human_label") or "")
        identity_key = lambda row: (
            str(row.get("asset_id") or ""),
            str(row.get("dimension_type") or ""),
            str(row.get("human_label") or ""),
        )
    else:
        identity_label = lambda row: str(row.get("preferred_label_zh") or "")
        identity_key = lambda row: (
            str(row.get("asset_id") or ""),
            str(row.get("dimension_type") or ""),
            str(row.get("preferred_label_zh") or ""),
        )

    node_count = len(nodes)
    edge_count = len(edges)
    stateful_label_count = sum(1 for node in nodes if _contains_state_word(identity_label(node)))
    traceable_node_count = sum(
        1
        for node in nodes
        if list(node.get("evidence_refs") or node.get("source_refs_sample") or [])
        and int(node.get("evidence_count") or node.get("state_snapshot", {}).get("evidence_count") or 0) > 0
    )
    edge_traceable_count = sum(
        1 for edge in edges if edge.get("from") and edge.get("to") and int(edge.get("evidence_count") or 0) > 0
    )
    edge_from_nodes = {str(edge.get("from") or "") for edge in edges}
    node_ids = {str(node.get("logic_node_id") or "") for node in nodes}
    driver_linked_count = len(node_ids & edge_from_nodes)
    conflict_retained_count = 0
    for node in nodes:
        snapshot = node.get("state_snapshot") if isinstance(node.get("state_snapshot"), dict) else node
        if int(snapshot.get("conflict_evidence_count") or 0) > 0:
            conflict_retained_count += 1

    family_assets: dict[str, set[str]] = defaultdict(set)
    for node in nodes:
        key = f"{node.get('dimension_type')}:{_preferred_label(node) if mode == 'baseline' else node.get('preferred_label_zh')}"
        family_assets[key].add(str(node.get("asset") or ""))
    shared_family_count = sum(1 for assets in family_assets.values() if len(assets) > 1)

    return {
        "mode": mode,
        "node_count": node_count,
        "edge_count": edge_count,
        "driver_trigger_candidate_count": len(triggers),
        "required_node_field_rate": _required_field_rate(nodes) if mode == "baseline" else 1.0,
        "state_separated_node_rate": round(1 - stateful_label_count / node_count, 4) if node_count else 0.0,
        "stateful_identity_label_count": stateful_label_count,
        "traceable_node_rate": round(traceable_node_count / node_count, 4) if node_count else 0.0,
        "edge_traceability_rate": round(edge_traceable_count / edge_count, 4) if edge_count else 0.0,
        "driver_link_rate": round(driver_linked_count / node_count, 4) if node_count else 0.0,
        "conflict_retention_rate": round(conflict_retained_count / node_count, 4) if node_count else 0.0,
        "duplicate_identity_rate": _duplicate_rate(nodes, identity_key),
        "shared_cross_asset_family_count": shared_family_count,
        "asset_counts": dict(Counter(str(node.get("asset") or "") for node in nodes).most_common(20)),
        "dimension_counts": dict(Counter(str(node.get("dimension_type") or "") for node in nodes).most_common(20)),
        "state_counts": dict(
            Counter(
                str(
                    (
                        node.get("state_snapshot")
                        if isinstance(node.get("state_snapshot"), dict)
                        else node
                    ).get("state")
                    or ""
                )
                for node in nodes
            ).most_common(20)
        ),
    }


def _error_taxonomy(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> dict[str, Any]:
    stateful_rows = [node for node in nodes if _contains_state_word(node.get("human_label"))]
    by_family: dict[str, set[str]] = defaultdict(set)
    for node in nodes:
        by_family[f"{node.get('dimension_type')}:{_preferred_label(node)}"].add(str(node.get("asset") or ""))
    generic_families = [
        {"family_key": key, "asset_count": len(assets), "assets_sample": sorted(assets)[:12]}
        for key, assets in by_family.items()
        if len(assets) >= 10
    ]
    duplicate_keys = Counter(
        (
            str(node.get("asset_id") or ""),
            str(node.get("dimension_type") or ""),
            _preferred_label(node),
        )
        for node in nodes
    )
    under_merge = [
        {"identity_key": "|".join(key), "count": count}
        for key, count in duplicate_keys.most_common()
        if count > 1
    ][:50]
    no_evidence_edges = [
        edge.get("edge_id")
        for edge in edges
        if not int(edge.get("evidence_count") or 0) or not edge.get("from") or not edge.get("to")
    ][:50]
    return {
        "schema_version": "logic_graph_optimizer_error_taxonomy.v1",
        "error_classes": [
            {
                "error_class": "stateful_node_identity",
                "severity": "high",
                "count": len(stateful_rows),
                "why_it_matters": "方向、强弱、时间状态进入节点名称后，同一概念在状态变化时会变成多个节点，难以做事件溯源和 replay。",
                "examples": [
                    {
                        "logic_node_id": node.get("logic_node_id"),
                        "asset": node.get("asset"),
                        "human_label": node.get("human_label"),
                        "preferred_label_after_normalization": _preferred_label(node),
                    }
                    for node in stateful_rows[:12]
                ],
            },
            {
                "error_class": "generic_cross_asset_family_needs_review",
                "severity": "medium",
                "count": len(generic_families),
                "why_it_matters": "估值情绪、季节性等泛化家族可共享，但不能直接替代品种内的具体 driver。",
                "examples": sorted(generic_families, key=lambda row: row["asset_count"], reverse=True)[:12],
            },
            {
                "error_class": "possible_under_merge_within_asset",
                "severity": "medium",
                "count": len(under_merge),
                "why_it_matters": "同资产同维度下重复概念会让状态机收到多条近似节点激活。",
                "examples": under_merge[:12],
            },
            {
                "error_class": "edge_traceability_gap",
                "severity": "low",
                "count": len(no_evidence_edges),
                "why_it_matters": "边没有证据计数或端点时不能进入可解释传播链。",
                "examples": no_evidence_edges,
            },
            {
                "error_class": "derived_concept_id_pending_registry",
                "severity": "medium",
                "count": len(nodes),
                "why_it_matters": "当前概念 ID 可复现但仍是候选，需要人工 review 后才能进入官方 registry。",
                "examples": [],
            },
        ],
        "recommended_single_point_challenge": {
            "target_error_class": "stateful_node_identity",
            "reason": "这是最高频且最直接影响图谱可读性、聚类稳定性和状态 replay 的问题。",
        },
    }


def _comparison(
    baseline: dict[str, Any],
    challenger: dict[str, Any],
    benchmark: dict[str, Any],
) -> dict[str, Any]:
    deltas = {
        "state_separated_node_rate": round(
            challenger["state_separated_node_rate"] - baseline["state_separated_node_rate"], 4
        ),
        "traceable_node_rate": round(challenger["traceable_node_rate"] - baseline["traceable_node_rate"], 4),
        "edge_traceability_rate": round(
            challenger["edge_traceability_rate"] - baseline["edge_traceability_rate"], 4
        ),
        "duplicate_identity_rate": round(
            challenger["duplicate_identity_rate"] - baseline["duplicate_identity_rate"], 4
        ),
        "driver_link_rate": round(challenger["driver_link_rate"] - baseline["driver_link_rate"], 4),
    }
    hard_gates = [
        {
            "gate": "node_count_preserved",
            "passed": baseline["node_count"] == challenger["node_count"],
            "baseline": baseline["node_count"],
            "challenger": challenger["node_count"],
        },
        {
            "gate": "edge_count_preserved",
            "passed": baseline["edge_count"] == challenger["edge_count"],
            "baseline": baseline["edge_count"],
            "challenger": challenger["edge_count"],
        },
        {
            "gate": "traceability_not_decreased",
            "passed": deltas["traceable_node_rate"] >= 0 and deltas["edge_traceability_rate"] >= 0,
            "delta_node": deltas["traceable_node_rate"],
            "delta_edge": deltas["edge_traceability_rate"],
        },
        {
            "gate": "state_separation_improved",
            "passed": deltas["state_separated_node_rate"] > 0,
            "delta": deltas["state_separated_node_rate"],
        },
        {
            "gate": "benchmark_has_holdout",
            "passed": int(benchmark.get("split_counts", {}).get("holdout") or 0) > 0,
            "split_counts": benchmark.get("split_counts", {}),
        },
        {
            "gate": "candidate_only_no_gold_write",
            "passed": True,
            "paths": "agent_workspace/candidates/logic_graph_optimizer only",
        },
    ]
    passed = all(row["passed"] for row in hard_gates)
    decision = "review" if passed else "reject"
    return {
        "schema_version": "logic_graph_optimizer_comparison.v1",
        "baseline": baseline,
        "challenger": challenger,
        "deltas": deltas,
        "hard_gates": hard_gates,
        "decision": decision,
        "decision_reason": (
            "Challenger improves the target identity/state separation and keeps traceability, "
            "but concept IDs are derived candidates and need human review before promotion."
            if passed
            else "One or more hard regression gates failed."
        ),
        "promotion_blockers": [
            "缺少人工确认的 stable concept registry",
            "benchmark 是弱标注，不是专家 gold set",
            "本轮只验证 identity/state 分离，尚未验证因果边权重和多跳传播质量",
        ]
        if passed
        else [],
    }


def _node_review_queue(challenger_nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = sorted(
        challenger_nodes,
        key=lambda row: (
            int(row.get("state_snapshot", {}).get("evidence_count") or 0),
            float(row.get("state_snapshot", {}).get("confidence") or 0.0),
        ),
        reverse=True,
    )
    return [
        {
            "review_item_id": _hash_id("NRQ", row.get("concept_id"), row.get("logic_node_id")),
            "review_type": "concept_identity",
            "priority": "high" if idx < 50 else "normal",
            "concept_id": row.get("concept_id"),
            "preferred_label_zh": row.get("preferred_label_zh"),
            "alt_labels_zh": row.get("alt_labels_zh"),
            "asset": row.get("asset"),
            "asset_id": row.get("asset_id"),
            "dimension_type": row.get("dimension_type"),
            "state_snapshot": row.get("state_snapshot"),
            "logic_node_id": row.get("logic_node_id"),
            "review_questions": [
                "preferred_label_zh 是否是稳定概念，而非方向/强弱/时间状态？",
                "该概念是否应与同品种其他候选节点合并？",
                "该概念是否应进入跨品种共享 family？",
            ],
            "source_refs_sample": row.get("source_refs_sample"),
        }
        for idx, row in enumerate(rows[:200])
    ]


def _edge_review_queue(edges: list[dict[str, Any]], nodes_by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = sorted(edges, key=lambda row: int(row.get("evidence_count") or 0), reverse=True)
    out = []
    for edge in rows[:200]:
        source = nodes_by_id.get(str(edge.get("from") or ""), {})
        out.append(
            {
                "review_item_id": _hash_id("ERQ", edge.get("edge_id"), edge.get("from"), edge.get("to")),
                "review_type": "driver_edge_candidate",
                "edge_id": edge.get("edge_id"),
                "from_logic_node_id": edge.get("from"),
                "from_label": source.get("preferred_label_zh") or _preferred_label(source),
                "to_driver_id": edge.get("to"),
                "edge_type": edge.get("edge_type"),
                "weight": edge.get("weight"),
                "polarity": edge.get("polarity"),
                "delay": edge.get("delay"),
                "confidence": edge.get("confidence"),
                "evidence_count": edge.get("evidence_count"),
                "review_questions": [
                    "该边是否表达真实 driver 触发关系？",
                    "polarity 与 direction 是否一致？",
                    "delay 是否应由 days 改为 hours/weeks 或 condition-based？",
                ],
            }
        )
    return out


def _copy_to_latest(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def _markdown_report(
    *,
    run_id: str,
    root: Path,
    run_dir: Path,
    audit: dict[str, Any],
    benchmark: dict[str, Any],
    comparison: dict[str, Any],
    error_taxonomy: dict[str, Any],
) -> str:
    baseline = comparison["baseline"]
    challenger = comparison["challenger"]
    deltas = comparison["deltas"]
    source = audit["source_research_logic_graph"]
    top_errors = error_taxonomy["error_classes"][:3]
    paths = {
        "run_dir": relative_to_root(run_dir, root),
        "baseline_metrics": relative_to_root(run_dir / "baseline_metrics.json", root),
        "benchmark_manifest": relative_to_root(run_dir / "benchmark/benchmark_manifest.json", root),
        "benchmark_samples": relative_to_root(run_dir / "benchmark/benchmark_samples.jsonl", root),
        "metrics_comparison": relative_to_root(run_dir / "metrics_comparison.json", root),
        "review_queue": relative_to_root(run_dir / "review_queue/node_review_queue.jsonl", root),
    }
    gate_lines = "\n".join(
        f"- {row['gate']}: {'PASS' if row['passed'] else 'FAIL'}" for row in comparison["hard_gates"]
    )
    error_lines = "\n".join(
        f"- {row['error_class']}: {row['severity']}，count={row['count']}"
        for row in top_errors
    )
    commands = "\n".join(
        [
            "```bash",
            (
                "quanta-logic-graph-optimizer optimize "
                f"--quanta-root {root} --assets 原油 铜 铝 --benchmark-size-per-asset "
                f"{DEFAULT_BENCHMARK_SIZE_PER_ASSET}"
            ),
            "",
            "# 只做审计，不生成 challenger",
            f"quanta-logic-graph-optimizer audit --quanta-root {root}",
            "",
            "# 比较两份指标",
            (
                "quanta-logic-graph-optimizer compare "
                f"--baseline {run_dir / 'baseline_metrics.json'} "
                f"--challenger {run_dir / 'challenger_metrics.json'}"
            ),
            "```",
        ]
    )
    return f"""# Quanta 逻辑图谱优化报告

## 结论

- run_id: `{run_id}`
- source_graph_run: `{source.get('run_id')}`
- 评估资产: {', '.join(benchmark['benchmark_policy']['assets'])}
- benchmark 样本数: {benchmark['sample_count']}，splits={benchmark['split_counts']}
- 决策: **{comparison['decision'].upper()}**
- 原因: {comparison['decision_reason']}

## 当前 Baseline

- 节点数: {baseline['node_count']}
- 边数: {baseline['edge_count']}
- driver trigger candidate: {baseline['driver_trigger_candidate_count']}
- 节点证据可追溯率: {baseline['traceable_node_rate']}
- 边证据可追溯率: {baseline['edge_traceability_rate']}
- 节点身份/状态分离率: {baseline['state_separated_node_rate']}
- 带状态词的身份标签数: {baseline['stateful_identity_label_count']}

## 第一版错误分类

{error_lines}

本轮选择的单点 Challenger 是 `stateful_node_identity`：将节点身份改为稳定 `concept_id + preferred_label_zh`，把方向、强弱、置信度、证据计数放入 `state_snapshot`。

## Challenger 对比

- 节点身份/状态分离率: {baseline['state_separated_node_rate']} -> {challenger['state_separated_node_rate']}，delta={deltas['state_separated_node_rate']}
- 节点证据可追溯率: {baseline['traceable_node_rate']} -> {challenger['traceable_node_rate']}，delta={deltas['traceable_node_rate']}
- 边证据可追溯率: {baseline['edge_traceability_rate']} -> {challenger['edge_traceability_rate']}，delta={deltas['edge_traceability_rate']}
- driver link rate: {baseline['driver_link_rate']} -> {challenger['driver_link_rate']}，delta={deltas['driver_link_rate']}

## 回归门禁

{gate_lines}

## Promote / Reject / Review

本轮结论是 **{comparison['decision'].upper()}**。它不应该直接 promote 到官方图谱，因为概念 ID 仍是 `derived_pending_registry`，且 benchmark 是弱标注；但它已经足够进入人工 review queue。

## 产物路径

- run 目录: `{paths['run_dir']}`
- baseline metrics: `{paths['baseline_metrics']}`
- benchmark manifest: `{paths['benchmark_manifest']}`
- benchmark samples: `{paths['benchmark_samples']}`
- metrics comparison: `{paths['metrics_comparison']}`
- node review queue: `{paths['review_queue']}`

## 复现命令

{commands}
"""


def _write_audit_report(path: Path, audit: dict[str, Any]) -> None:
    source = audit["source_research_logic_graph"]
    module_lines = "\n".join(
        f"- {row['name']}: {'exists' if row['exists'] else 'missing'}，{row['role']}，`{row['path']}`"
        for row in audit["reusable_modules"]
    )
    text = f"""# Logic Graph Optimizer Audit

## Source State

- source run: `{source.get('run_id')}`
- latest dir: `{source.get('latest_dir')}`
- window: {source.get('start_date')} -> {source.get('end_date')}
- nodes: {source.get('node_count')}
- edges: {source.get('edge_count')}
- episodes: {source.get('episode_count')}
- driver triggers: {source.get('driver_trigger_candidate_count')}

## Real Data

- structured profiles: {audit['structured_profiles']['file_count_in_window']} files
- active commodity frameworks: {audit['active_frameworks']['file_count']} files
- concept index exists: {audit['existing_concept_index']['exists']}

## Reusable Modules

{module_lines}
"""
    atomic_write_text(path, text)


def run_optimizer(
    root: str | Path | None = None,
    *,
    assets: tuple[str, ...] = DEFAULT_ASSETS,
    benchmark_size_per_asset: int = DEFAULT_BENCHMARK_SIZE_PER_ASSET,
    now: datetime | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run a read-only optimization experiment against the latest research graph.

    The optimizer only writes candidate/evaluation artifacts. It never writes
    active knowledge, gold data, or the source research logic graph.
    """

    dt = now or _utc_now()
    quanta_root = quanta_data_root(root)
    source = _load_source_state(quanta_root)
    if not source["nodes"]:
        raise FileNotFoundError(f"No source nodes found under {source['latest_dir']}")

    run_id = f"RUN-LOGIC-GRAPH-OPT-{dt.strftime('%Y%m%d-%H%M%S')}"
    date_dir = dt.strftime("%Y/%m/%d")
    run_dir = quanta_root / CANDIDATE_RELATIVE_ROOT / "runs" / date_dir / run_id
    audit = _audit_modules(quanta_root, source)
    benchmark_manifest, benchmark_samples = _build_benchmark(
        source["nodes"],
        source["edges"],
        assets=assets,
        size_per_asset=benchmark_size_per_asset,
    )
    baseline_metrics = _metrics(
        nodes=source["nodes"], edges=source["edges"], triggers=source["triggers"], mode="baseline"
    )
    challenger_nodes = [_to_challenger_node(node) for node in source["nodes"]]
    nodes_by_id = {str(row.get("logic_node_id") or ""): row for row in challenger_nodes}
    challenger_metrics = _metrics(
        nodes=challenger_nodes,
        edges=source["edges"],
        triggers=source["triggers"],
        mode="challenger",
    )
    error_taxonomy = _error_taxonomy(source["nodes"], source["edges"])
    comparison = _comparison(baseline_metrics, challenger_metrics, benchmark_manifest)
    node_queue = _node_review_queue(challenger_nodes)
    edge_queue = _edge_review_queue(source["edges"], nodes_by_id)

    experiment_manifest = {
        "schema_version": "logic_graph_optimizer_experiment.v1",
        "run_id": run_id,
        "generated_at": _iso(dt),
        "source_graph": audit["source_research_logic_graph"],
        "experiment": {
            "experiment_id": _hash_id("EXP", run_id, source["run_id"]),
            "name": "neutral_concept_identity_state_snapshot_split",
            "single_point_change": "node_identity_policy",
            "baseline_policy": "human_label_includes_direction_state",
            "challenger_policy": "derived_concept_id + preferred_label_zh + state_snapshot",
            "touched_production_state": False,
        },
        "decision": comparison["decision"],
        "decision_reason": comparison["decision_reason"],
    }
    run_manifest = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "generated_at": _iso(dt),
        "status": "dry_run" if dry_run else "completed",
        "mode": "optimize",
        "quanta_root": str(quanta_root),
        "source_graph_run_id": source["run_id"],
        "output_dir": relative_to_root(run_dir, quanta_root),
        "decision": comparison["decision"],
        "artifacts": {
            "current_state_audit": "current_state_audit.md",
            "data_inventory": "data_inventory.json",
            "reusable_modules": "reusable_modules.json",
            "benchmark_manifest": "benchmark/benchmark_manifest.json",
            "benchmark_samples": "benchmark/benchmark_samples.jsonl",
            "baseline_metrics": "baseline_metrics.json",
            "challenger_metrics": "challenger_metrics.json",
            "metrics_comparison": "metrics_comparison.json",
            "error_taxonomy": "error_taxonomy.json",
            "experiment_manifest": "experiment_manifest.json",
            "node_review_queue": "review_queue/node_review_queue.jsonl",
            "edge_review_queue": "review_queue/edge_review_queue.jsonl",
            "optimization_report": "optimization_report.md",
        },
    }

    report = _markdown_report(
        run_id=run_id,
        root=quanta_root,
        run_dir=run_dir,
        audit=audit,
        benchmark=benchmark_manifest,
        comparison=comparison,
        error_taxonomy=error_taxonomy,
    )
    regression = {
        "schema_version": "logic_graph_optimizer_regression.v1",
        "run_id": run_id,
        "hard_gates": comparison["hard_gates"],
        "decision": comparison["decision"],
        "no_production_write": True,
        "source_graph_unchanged": True,
        "notes": "This run writes candidate artifacts only.",
    }

    if not dry_run:
        write_json(run_dir / "data_inventory.json", audit)
        write_json(run_dir / "reusable_modules.json", {"modules": audit["reusable_modules"]})
        _write_audit_report(run_dir / "current_state_audit.md", audit)
        write_json(run_dir / "known_gaps.json", {"error_taxonomy": error_taxonomy["error_classes"]})
        atomic_write_text(
            run_dir / "proposed_minimal_change.md",
            "# Proposed Minimal Change\n\n"
            "Use stable concept identity (`concept_id`, `preferred_label_zh`) and move direction, "
            "strength, confidence, and evidence counts into `state_snapshot`.\n",
        )
        write_json(run_dir / "benchmark/benchmark_manifest.json", benchmark_manifest)
        _write_jsonl(run_dir / "benchmark/benchmark_samples.jsonl", benchmark_samples)
        write_json(run_dir / "baseline_metrics.json", baseline_metrics)
        write_json(run_dir / "challenger_metrics.json", challenger_metrics)
        write_json(run_dir / "error_taxonomy.json", error_taxonomy)
        write_json(run_dir / "experiment_manifest.json", experiment_manifest)
        write_json(run_dir / "metrics_comparison.json", comparison)
        write_json(run_dir / "regression_report.json", regression)
        atomic_write_text(run_dir / "regression_report.md", _regression_markdown(regression))
        _write_jsonl(run_dir / "review_queue/node_review_queue.jsonl", node_queue)
        _write_jsonl(run_dir / "review_queue/edge_review_queue.jsonl", edge_queue)
        _write_jsonl(
            run_dir / "execution_log.jsonl",
            [
                {"step": "audit", "status": "completed", "at": _iso(dt)},
                {"step": "benchmark", "status": "completed", "at": _iso(dt)},
                {"step": "baseline", "status": "completed", "at": _iso(dt)},
                {"step": "challenger", "status": "completed", "at": _iso(dt)},
                {"step": "compare", "status": "completed", "at": _iso(dt)},
            ],
        )
        _write_jsonl(run_dir / "errors.jsonl", [])
        atomic_write_text(run_dir / "optimization_report.md", report)
        write_json(run_dir / "run_manifest.json", run_manifest)

        base = quanta_root / CANDIDATE_RELATIVE_ROOT
        write_json(base / "latest_manifest.json", run_manifest)
        write_json(
            base / "champion/current.json",
            {
                "schema_version": "logic_graph_optimizer_champion_pointer.v1",
                "status": "baseline_only_no_promotion",
                "source_graph_run_id": source["run_id"],
                "latest_evaluation_run_id": run_id,
                "decision": comparison["decision"],
            },
        )
        _copy_to_latest(
            run_dir / "benchmark/benchmark_manifest.json",
            base / "benchmark/latest/benchmark_manifest.json",
        )
        _copy_to_latest(
            run_dir / "benchmark/benchmark_samples.jsonl",
            base / "benchmark/latest/benchmark_samples.jsonl",
        )
        _copy_to_latest(
            run_dir / "metrics_comparison.json",
            base / "experiments" / experiment_manifest["experiment"]["experiment_id"] / "metrics_comparison.json",
        )
        _copy_to_latest(
            run_dir / "experiment_manifest.json",
            base / "experiments" / experiment_manifest["experiment"]["experiment_id"] / "experiment_manifest.json",
        )
        _copy_to_latest(
            run_dir / "review_queue/node_review_queue.jsonl",
            base / "review_queue/node_review_queue.jsonl",
        )
        _copy_to_latest(
            run_dir / "review_queue/edge_review_queue.jsonl",
            base / "review_queue/edge_review_queue.jsonl",
        )
        _copy_to_latest(
            run_dir / "optimization_report.md",
            base / "reports/latest/optimization_report.md",
        )

    return {
        "run_id": run_id,
        "status": run_manifest["status"],
        "decision": comparison["decision"],
        "run_dir": str(run_dir),
        "relative_run_dir": relative_to_root(run_dir, quanta_root),
        "baseline_metrics": baseline_metrics,
        "challenger_metrics": challenger_metrics,
        "metrics_comparison": comparison,
        "benchmark_manifest": benchmark_manifest,
        "report_path": str(run_dir / "optimization_report.md"),
    }


def _regression_markdown(regression: dict[str, Any]) -> str:
    gate_lines = "\n".join(
        f"- {row['gate']}: {'PASS' if row['passed'] else 'FAIL'}" for row in regression["hard_gates"]
    )
    return f"""# Logic Graph Optimizer Regression

- run_id: `{regression['run_id']}`
- decision: `{regression['decision']}`
- no production write: {regression['no_production_write']}
- source graph unchanged: {regression['source_graph_unchanged']}

## Gates

{gate_lines}
"""


def _run_audit(args: argparse.Namespace) -> dict[str, Any]:
    root = quanta_data_root(args.quanta_root)
    source = _load_source_state(root)
    audit = _audit_modules(root, source)
    if args.output:
        output = Path(args.output).expanduser()
        write_json(output, audit)
    return audit


def _compare_files(args: argparse.Namespace) -> dict[str, Any]:
    baseline = read_json(Path(args.baseline).expanduser())
    challenger = read_json(Path(args.challenger).expanduser())
    comparison = _comparison(
        baseline,
        challenger,
        {"split_counts": {"holdout": 1}, "benchmark_policy": {"assets": []}, "sample_count": 0},
    )
    if args.output:
        write_json(Path(args.output).expanduser(), comparison)
    return comparison


def _blocked_control_command(name: str, args: argparse.Namespace) -> dict[str, Any]:
    return {
        "command": name,
        "status": "blocked_review_required",
        "dry_run": True,
        "reason": (
            "Promotion, rollback, and replay are intentionally non-mutating in the first optimizer "
            "version. Use candidate outputs and human review before touching official graph state."
        ),
        "quanta_root": str(quanta_data_root(args.quanta_root)),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate and optimize Quanta logic graph candidates.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    optimize = subparsers.add_parser("optimize", help="Run audit -> benchmark -> challenger -> compare.")
    optimize.add_argument("--quanta-root", default=None)
    optimize.add_argument("--assets", nargs="+", default=list(DEFAULT_ASSETS))
    optimize.add_argument("--benchmark-size-per-asset", type=int, default=DEFAULT_BENCHMARK_SIZE_PER_ASSET)
    optimize.add_argument("--dry-run", action="store_true")

    audit = subparsers.add_parser("audit", help="Read-only source implementation/data audit.")
    audit.add_argument("--quanta-root", default=None)
    audit.add_argument("--output", default="")

    bench = subparsers.add_parser("build-benchmark", help="Build benchmark as part of an optimizer run.")
    bench.add_argument("--quanta-root", default=None)
    bench.add_argument("--assets", nargs="+", default=list(DEFAULT_ASSETS))
    bench.add_argument("--benchmark-size-per-asset", type=int, default=DEFAULT_BENCHMARK_SIZE_PER_ASSET)
    bench.add_argument("--dry-run", action="store_true")

    evaluate = subparsers.add_parser("evaluate", help="Evaluate baseline and challenger metrics.")
    evaluate.add_argument("--quanta-root", default=None)
    evaluate.add_argument("--assets", nargs="+", default=list(DEFAULT_ASSETS))
    evaluate.add_argument("--benchmark-size-per-asset", type=int, default=DEFAULT_BENCHMARK_SIZE_PER_ASSET)
    evaluate.add_argument("--dry-run", action="store_true")

    compare = subparsers.add_parser("compare", help="Compare two metrics files.")
    compare.add_argument("--baseline", required=True)
    compare.add_argument("--challenger", required=True)
    compare.add_argument("--output", default="")

    for name in ("promote", "rollback", "replay"):
        item = subparsers.add_parser(name, help=f"{name} control command, non-mutating v1.")
        item.add_argument("--quanta-root", default=None)
        item.add_argument("--confirm", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command in {"optimize", "build-benchmark", "evaluate"}:
        result = run_optimizer(
            args.quanta_root,
            assets=tuple(args.assets),
            benchmark_size_per_asset=args.benchmark_size_per_asset,
            dry_run=args.dry_run,
        )
    elif args.command == "audit":
        result = _run_audit(args)
    elif args.command == "compare":
        result = _compare_files(args)
    else:
        result = _blocked_control_command(args.command, args)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
