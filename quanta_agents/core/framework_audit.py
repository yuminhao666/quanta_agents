from __future__ import annotations

import argparse
import hashlib
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from quanta_agents.core.config import quanta_data_root
from quanta_agents.core.frameworks import iter_active_frameworks
from quanta_agents.core.io import dated_parts, utc_now_iso, write_json
from quanta_agents.core.taxonomy import load_asset_taxonomy


TARGET_SCHEMA_VERSION = "commodity_research_framework.v1"
TARGET_ARTIFACT_TYPE = "commodity_research_framework"

DIMENSION_TYPE_ALIASES = {
    "cost_profit": "cost",
    "feedstock": "cost",
    "feed_cost": "cost",
    "cost_environment": "cost",
    "policy_macro": "policy",
    "monetary_policy": "macro",
    "dollar_fx": "macro",
    "inflation_growth": "macro",
    "macro_geopolitics": "geopolitics",
    "weather_yield": "weather",
    "seasonal": "seasonality",
    "physical_supply_demand": "supply_demand",
    "capacity_supply": "supply",
    "trade_demand": "demand",
    "port_logistics": "logistics",
    "substitution_competition": "substitution",
    "synthetic_substitution": "substitution",
    "tech_substitution": "substitution",
    "investment_flow": "funding",
    "valuation_sentiment": "valuation",
    "monetary_liquidity": "funding",
    "global_capital_flow": "funding",
    "term_credit_structure": "spread",
}

DEFAULT_DIMENSION_WEIGHTS = {
    "supply": 0.18,
    "demand": 0.18,
    "supply_demand": 0.18,
    "inventory": 0.15,
    "cost": 0.12,
    "policy": 0.10,
    "macro": 0.10,
    "geopolitics": 0.10,
    "funding": 0.10,
    "valuation": 0.10,
    "weather": 0.10,
    "spread": 0.08,
    "substitution": 0.07,
    "logistics": 0.07,
    "seasonality": 0.06,
    "disease": 0.08,
    "capacity_cycle": 0.10,
    "other": 0.05,
}


def _hash_id(prefix: str, *parts: str) -> str:
    raw = "||".join(str(part) for part in parts)
    return f"{prefix}-{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:16].upper()}"


def _norm_dimension_type(raw: Any) -> str:
    text = str(raw or "other").strip()
    return DIMENSION_TYPE_ALIASES.get(text, text or "other")


def _taxonomy_names(root: str | Path | None = None) -> set[str]:
    taxonomy = load_asset_taxonomy(root)
    names = set(taxonomy.names())
    for asset in taxonomy.assets:
        for value in [asset.get("display_name"), asset.get("canonical_name"), asset.get("generic_name")]:
            if value:
                names.add(str(value))
        for alias in asset.get("aliases") or []:
            names.add(str(alias))
    return names


def _framework_issues(path: Path, fw: dict[str, Any], taxonomy_names: set[str]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if fw.get("schema_version") != TARGET_SCHEMA_VERSION:
        issues.append(
            {
                "code": "schema_version_mismatch",
                "severity": "high",
                "message": f"schema_version={fw.get('schema_version')}，建议迁移到 {TARGET_SCHEMA_VERSION}",
            }
        )
    if fw.get("artifact_type") != TARGET_ARTIFACT_TYPE:
        issues.append(
            {
                "code": "artifact_type_mismatch",
                "severity": "medium",
                "message": f"artifact_type={fw.get('artifact_type')}，建议迁移到 {TARGET_ARTIFACT_TYPE}",
            }
        )
    if not fw.get("logic_templates"):
        issues.append(
            {
                "code": "empty_logic_templates",
                "severity": "medium",
                "message": "logic_templates 为空，无法表达维度之间的因果链。",
            }
        )
    if not isinstance(fw.get("prompt_pack_fragments"), dict):
        issues.append(
            {
                "code": "prompt_pack_fragments_type",
                "severity": "low",
                "message": "prompt_pack_fragments 当前不是 object，建议统一为 system_hint/dimension_prompt/output_requirements。",
            }
        )
    if fw.get("review_status") == "pending_review":
        issues.append(
            {
                "code": "pending_review_active",
                "severity": "medium",
                "message": "active framework 仍处于 pending_review，需要审核状态收敛。",
            }
        )

    standard = str(fw.get("standard_name") or "").strip()
    generic = str(fw.get("generic_name") or "").strip()
    if standard and generic and standard != generic and generic in taxonomy_names and standard not in taxonomy_names:
        issues.append(
            {
                "code": "standard_name_noncanonical",
                "severity": "medium",
                "message": f"standard_name={standard} 可能是合约简称，generic_name={generic} 更接近标准资产名。",
            }
        )

    dimensions = [dim for dim in fw.get("core_dimensions") or [] if isinstance(dim, dict)]
    if not dimensions:
        issues.append(
            {
                "code": "missing_core_dimensions",
                "severity": "high",
                "message": "缺少 core_dimensions。",
            }
        )
        return issues

    embedded_term_count = sum(
        len(dim.get("typical_indicators") or []) + len(dim.get("typical_events") or [])
        for dim in dimensions
    )
    if embedded_term_count:
        issues.append(
            {
                "code": "embedded_indicator_event_terms",
                "severity": "medium",
                "message": f"core_dimensions 内嵌 {embedded_term_count} 个指标/事件词条，建议迁移到独立 catalog 文件，framework 仅保留 catalog_refs。",
            }
        )

    seen_ids: set[str] = set()
    for dim in dimensions:
        dim_id = str(dim.get("dimension_id") or "")
        raw_type = str(dim.get("dimension_type") or "")
        norm_type = _norm_dimension_type(raw_type)
        if dim_id in seen_ids:
            issues.append(
                {
                    "code": "duplicate_dimension_id",
                    "severity": "high",
                    "message": f"重复 dimension_id={dim_id}",
                }
            )
        seen_ids.add(dim_id)
        if norm_type != raw_type:
            issues.append(
                {
                    "code": "legacy_dimension_type",
                    "severity": "low",
                    "message": f"dimension_type={raw_type} 建议规范为 {norm_type}",
                    "dimension_id": dim_id,
                }
            )
        if "default_weight" not in dim:
            issues.append(
                {
                    "code": "missing_default_weight",
                    "severity": "medium",
                    "message": f"{dim_id} 缺少 default_weight。",
                    "dimension_id": dim_id,
                }
            )
        if "scoring_rules" not in dim:
            issues.append(
                {
                    "code": "missing_scoring_rules",
                    "severity": "medium",
                    "message": f"{dim_id} 缺少 scoring_rules。",
                    "dimension_id": dim_id,
                }
            )
        if not dim.get("typical_indicators") and not dim.get("typical_events"):
            issues.append(
                {
                    "code": "thin_dimension_evidence_vocab",
                    "severity": "low",
                    "message": f"{dim_id} 缺少 typical_indicators/typical_events。",
                    "dimension_id": dim_id,
                }
            )
    return issues


def _optimized_dimension(dim: dict[str, Any]) -> dict[str, Any]:
    norm_type = _norm_dimension_type(dim.get("dimension_type"))
    payload = dict(dim)
    payload["dimension_type"] = norm_type
    payload.setdefault("default_weight", DEFAULT_DIMENSION_WEIGHTS.get(norm_type, DEFAULT_DIMENSION_WEIGHTS["other"]))
    payload.setdefault("weight_bounds", {"min": 0.0, "max": 0.35})
    payload.setdefault("catalog_refs", [])
    payload.setdefault(
        "activation_rules",
        {
            "evidence_count_boost": True,
            "important_news_boost": True,
            "report_consensus_boost": True,
            "time_decay_half_life_hours": 72,
        },
    )
    payload.setdefault(
        "scoring_rules",
        {
            "direction": "infer_from_evidence",
            "strength": "evidence_weighted",
            "confidence": "source_and_match_confidence",
            "time_decay": "enabled",
        },
    )
    return payload


def _optimization_candidate(path: Path, fw: dict[str, Any], issues: list[dict[str, Any]]) -> dict[str, Any]:
    framework_id = str(fw.get("framework_id") or path.stem)
    optimized = dict(fw)
    optimized["schema_version"] = TARGET_SCHEMA_VERSION
    optimized["artifact_type"] = TARGET_ARTIFACT_TYPE
    optimized["review_status"] = "pending_review"
    optimized["core_dimensions"] = [
        _optimized_dimension(dim)
        for dim in fw.get("core_dimensions") or []
        if isinstance(dim, dict)
    ]
    if not optimized.get("logic_templates"):
        optimized["logic_templates"] = [
            {
                "template_id": f"{framework_id}_default_driver_chain",
                "template_name": "默认驱动链",
                "template": "外部事件/数据 -> 框架维度 -> 方向影响 -> 交易主线 -> 跟踪信号",
                "dimension_ids": [dim.get("dimension_id") for dim in optimized["core_dimensions"] if dim.get("dimension_id")],
                "graph_path_hints": [],
                "prompt_hint": "用于研报和新闻快讯的初始逻辑链梳理，后续由人工审核细化。",
            }
        ]
    if not isinstance(optimized.get("prompt_pack_fragments"), dict):
        optimized["prompt_pack_fragments"] = {
            "system_hint": "你是商品期货研究员，基于框架维度解释证据对价格的影响。",
            "dimension_prompt": "逐维度提取触发因素、方向、强度、证据来源和后续跟踪信号。",
            "output_requirements": "输出结构化 JSON，保留证据文本，不编造未出现的信息。",
        }
    optimized.setdefault("evolution_policy", {"write_back": "human_review_required", "candidate_store": True})
    optimized.setdefault(
        "catalog_migration",
        {
            "status": "pending_split",
            "source_fields": [
                "core_dimensions.typical_indicators",
                "core_dimensions.typical_events",
            ],
            "target_layout": [
                "knowledge_base/catalogs/commodities/{asset_id}/indicators.v1.json",
                "knowledge_base/catalogs/commodities/{asset_id}/event-triggers.v1.json",
                "knowledge_base/catalogs/commodities/{asset_id}/claim-patterns.v1.json",
            ],
            "principle": "framework-core stores stable dimensions and catalog_refs; concrete indicators, event triggers, and report claim patterns live in catalog files.",
        },
    )
    return {
        "candidate_id": _hash_id("FWKOPT", framework_id, path.as_posix()),
        "candidate_type": "framework_schema_normalization",
        "target_framework_id": framework_id,
        "asset_id": fw.get("asset_id"),
        "standard_name": fw.get("standard_name"),
        "source_path": str(path),
        "issue_count": len(issues),
        "issue_codes": dict(Counter(issue["code"] for issue in issues)),
        "proposal_summary": "统一 schema/artifact_type，补齐维度权重、激活规则、打分规则、默认逻辑链和 prompt fragments；将具体指标/事件词条迁移到独立 catalog。",
        "proposed_payload": optimized,
        "status": "candidate",
        "review_status": "pending_review",
        "created_at": utc_now_iso(),
    }


def audit_active_frameworks(root: str | Path | None = None) -> dict[str, Any]:
    taxonomy_names = _taxonomy_names(root)
    frameworks = iter_active_frameworks(root)
    issue_counter: Counter[str] = Counter()
    severity_counter: Counter[str] = Counter()
    dimension_type_counter: Counter[str] = Counter()
    framework_rows = []
    candidates = []

    for path, fw in frameworks:
        dimensions = [dim for dim in fw.get("core_dimensions") or [] if isinstance(dim, dict)]
        for dim in dimensions:
            dimension_type_counter[str(dim.get("dimension_type") or "")] += 1
        issues = _framework_issues(path, fw, taxonomy_names)
        issue_counter.update(issue["code"] for issue in issues)
        severity_counter.update(issue["severity"] for issue in issues)
        framework_rows.append(
            {
                "framework_id": fw.get("framework_id"),
                "asset_id": fw.get("asset_id"),
                "standard_name": fw.get("standard_name"),
                "generic_name": fw.get("generic_name"),
                "source_path": str(path),
                "dimension_count": len(dimensions),
                "issue_count": len(issues),
                "issues": issues,
            }
        )
        if issues:
            candidates.append(_optimization_candidate(path, fw, issues))

    return {
        "schema_version": "framework_audit.v1",
        "status": "candidate",
        "generated_at": utc_now_iso(),
        "target_schema_version": TARGET_SCHEMA_VERSION,
        "target_artifact_type": TARGET_ARTIFACT_TYPE,
        "summary": {
            "framework_count": len(frameworks),
            "frameworks_with_issues": sum(1 for row in framework_rows if row["issue_count"]),
            "issue_counts": dict(issue_counter),
            "severity_counts": dict(severity_counter),
            "dimension_type_counts": dict(dimension_type_counter),
            "optimization_candidate_count": len(candidates),
        },
        "recommended_schema_extensions": {
            "core_dimensions.default_weight": "长期默认维度权重。",
            "core_dimensions.weight_bounds": "短期激活权重上下限。",
            "core_dimensions.activation_rules": "近期研报/新闻/数据如何提高维度重要性。",
            "core_dimensions.scoring_rules": "证据方向、强度、置信度和时间衰减规则。",
            "logic_templates": "品种逻辑链模板，表达事件/数据到价格的传导。",
            "core_dimensions.catalog_refs": "维度引用的指标、事件和 claim catalog 文件。",
            "catalog_migration": "将内嵌 typical_indicators/typical_events 拆分到独立 catalog 的迁移计划。",
            "evolution_policy": "候选如何审核、接受、写回 active framework。",
        },
        "frameworks": sorted(framework_rows, key=lambda row: row["issue_count"], reverse=True),
        "optimization_candidates": candidates,
    }


def publish_framework_audit(root: str | Path | None = None, output_dir: str | Path | None = None) -> dict[str, Any]:
    payload = audit_active_frameworks(root)
    root_path = quanta_data_root(root)
    now = datetime.now()
    yyyy, mm, dd = dated_parts(now.strftime("%Y%m%d"))
    target_dir = (
        Path(output_dir).expanduser()
        if output_dir
        else root_path / "agent_workspace" / "candidates" / "framework_audit" / yyyy / mm / dd
    )
    audit_path = target_dir / f"framework-audit-{now.strftime('%H%M%S')}.json"
    latest_path = root_path / "agent_workspace" / "candidates" / "framework_audit" / "latest" / "framework-audit.json"
    write_json(audit_path, payload)
    write_json(latest_path, payload)
    return {"audit": str(audit_path), "latest": str(latest_path), "payload": payload}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Audit active commodity frameworks and build optimization candidates.")
    parser.add_argument("--quanta-root", help="Override GJ_QUANTA_DATA_ROOT.")
    parser.add_argument("--output-dir", help="Optional output directory.")
    args = parser.parse_args(argv)
    result = publish_framework_audit(args.quanta_root, args.output_dir)
    summary = result["payload"]["summary"]
    print(f"框架审计 → {result['audit']}")
    print(
        "frameworks={framework_count} issues={frameworks_with_issues} candidates={optimization_candidate_count}".format(
            **summary
        )
    )


if __name__ == "__main__":
    main()
