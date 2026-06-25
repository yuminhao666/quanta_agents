from __future__ import annotations

import argparse
import hashlib
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from quanta_agents.core.config import quanta_data_root
from quanta_agents.core.frameworks import (
    iter_leaf_nodes,
    leaf_claim_terms,
    leaf_event_terms,
    leaf_indicator_terms,
    load_framework_for_asset,
)
from quanta_agents.core.io import dated_parts, read_json, relative_to_root, utc_now_iso, write_json
from quanta_agents.core.taxonomy import load_asset_taxonomy


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


def _hash_id(prefix: str, *parts: Any) -> str:
    raw = "||".join(str(part) for part in parts)
    return f"{prefix}-{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:16].upper()}"


def normalize_dimension_type(raw: Any) -> str:
    text = str(raw or "other").strip() or "other"
    return DIMENSION_TYPE_ALIASES.get(text, text)


def _path_label(path: Any, fallback: str) -> str:
    if isinstance(path, list) and path:
        return "/".join(str(item) for item in path if str(item).strip())
    return fallback


def _dimension_row(
    *,
    asset: dict[str, Any],
    framework: dict[str, Any],
    leaf: dict[str, Any],
    source_path: str,
) -> dict[str, Any]:
    dimension_type = normalize_dimension_type(leaf.get("dimension_type"))
    dimension_id = str(leaf.get("node_id") or leaf.get("dimension_id") or leaf.get("name") or "").strip()
    dimension_name = str(leaf.get("name") or leaf.get("display") or dimension_id).strip()
    label = _path_label(leaf.get("path"), dimension_name)
    default_weight = leaf.get("default_weight")
    if default_weight is None:
        default_weight = DEFAULT_DIMENSION_WEIGHTS.get(dimension_type, DEFAULT_DIMENSION_WEIGHTS["other"])
    return {
        "dimension_id": dimension_id,
        "dimension_name": dimension_name,
        "dimension_label": label,
        "dimension_type": dimension_type,
        "asset": asset.get("canonical_name"),
        "asset_id": asset.get("asset_id"),
        "framework_id": framework.get("framework_id"),
        "framework_asset_id": framework.get("asset_id"),
        "source_path": source_path,
        "path": leaf.get("path") if isinstance(leaf.get("path"), list) else [dimension_name],
        "default_weight": round(float(default_weight), 4),
        "weight_bounds": leaf.get("weight_bounds") or {"min": 0.0, "max": 0.35},
        "activation_rules": leaf.get("activation_rules")
        or {
            "report_evidence_boost": True,
            "news_confirmation_boost": True,
            "time_decay_half_life_hours": 72,
        },
        "scoring_rules": leaf.get("scoring_rules")
        or {
            "direction": "llm_infer_from_evidence",
            "strength": "dimension_weighted",
            "confidence": "source_quality_and_framework_match",
        },
        "term_counts": {
            "indicator": len(leaf_indicator_terms(leaf)),
            "event": len(leaf_event_terms(leaf)),
            "claim": len(leaf_claim_terms(leaf)),
        },
        "review_status": "candidate",
    }


def _term_row(
    *,
    asset: dict[str, Any],
    dimension: dict[str, Any],
    term_type: str,
    term: str,
    source_path: str,
) -> dict[str, Any]:
    name = str(term).strip()
    return {
        "term_id": _hash_id(
            "FWKTERM",
            asset.get("canonical_name"),
            dimension.get("dimension_id"),
            term_type,
            name,
        ),
        "term_type": term_type,
        "name": name,
        "aliases": [name],
        "asset": asset.get("canonical_name"),
        "asset_id": asset.get("asset_id"),
        "framework_id": dimension.get("framework_id"),
        "dimension_id": dimension.get("dimension_id"),
        "dimension_label": dimension.get("dimension_label"),
        "dimension_type": dimension.get("dimension_type"),
        "source_path": source_path,
        "review_status": "candidate",
    }


def _framework_issue_rows(asset: dict[str, Any], dimensions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if not dimensions:
        issues.append(
            {
                "issue_type": "missing_framework_dimensions",
                "severity": "high",
                "asset": asset.get("canonical_name"),
                "message": "该品种没有可用分析维度，研报/新闻只能落到未归类。",
            }
        )
        return issues
    for row in dimensions:
        counts = row.get("term_counts") or {}
        if not counts.get("indicator") and not counts.get("event"):
            issues.append(
                {
                    "issue_type": "thin_indicator_event_vocab",
                    "severity": "medium",
                    "asset": asset.get("canonical_name"),
                    "dimension_id": row.get("dimension_id"),
                    "dimension_label": row.get("dimension_label"),
                    "message": "该维度缺少独立维护的指标/事件词条，后续映射依赖维度名称，需补充词条。",
                }
            )
        if row.get("dimension_type") == "other":
            issues.append(
                {
                    "issue_type": "unclear_dimension_type",
                    "severity": "low",
                    "asset": asset.get("canonical_name"),
                    "dimension_id": row.get("dimension_id"),
                    "dimension_label": row.get("dimension_label"),
                    "message": "维度类型未能规范归类，建议人工确认。",
                }
            )
    return issues


def build_analysis_framework_registry(root: str | Path | None = None) -> dict[str, Any]:
    root_path = quanta_data_root(root)
    taxonomy = load_asset_taxonomy(root_path, required=True)
    assets: list[dict[str, Any]] = []
    dimensions: list[dict[str, Any]] = []
    terms: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    framework_sources: Counter[str] = Counter()
    dimension_type_counts: Counter[str] = Counter()

    for asset in sorted(taxonomy.assets, key=lambda row: str(row.get("canonical_name") or "")):
        framework = load_framework_for_asset(asset, root_path)
        source_path = str((framework or {}).get("_source_path") or "")
        leaves = iter_leaf_nodes(framework) if framework else []
        framework_sources[source_path or "missing"] += 1
        asset_dimensions: list[dict[str, Any]] = []
        for leaf in leaves:
            row = _dimension_row(asset=asset, framework=framework or {}, leaf=leaf, source_path=source_path)
            dimensions.append(row)
            asset_dimensions.append(row)
            dimension_type_counts[row["dimension_type"]] += 1
            for term in leaf_indicator_terms(leaf):
                terms.append(
                    _term_row(
                        asset=asset,
                        dimension=row,
                        term_type="indicator",
                        term=term,
                        source_path=source_path,
                    )
                )
            for term in leaf_event_terms(leaf):
                terms.append(
                    _term_row(
                        asset=asset,
                        dimension=row,
                        term_type="event",
                        term=term,
                        source_path=source_path,
                    )
                )
            for term in leaf_claim_terms(leaf):
                terms.append(
                    _term_row(
                        asset=asset,
                        dimension=row,
                        term_type="claim",
                        term=term,
                        source_path=source_path,
                    )
                )
        issues.extend(_framework_issue_rows(asset, asset_dimensions))
        assets.append(
            {
                "asset": asset.get("canonical_name"),
                "asset_id": asset.get("asset_id"),
                "commodity_code": asset.get("commodity_code"),
                "category": asset.get("category"),
                "sector": asset.get("sector"),
                "framework_id": (framework or {}).get("framework_id"),
                "framework_asset_id": (framework or {}).get("asset_id"),
                "source_path": source_path,
                "dimension_count": len(asset_dimensions),
                "term_count": sum(sum((row.get("term_counts") or {}).values()) for row in asset_dimensions),
                "review_status": "candidate",
            }
        )

    terms_by_id = {row["term_id"]: row for row in terms}
    generated_at = utc_now_iso()
    registry = {
        "schema_version": "analysis_framework_registry.v1",
        "status": "candidate",
        "generated_at": generated_at,
        "taxonomy_source": str(taxonomy.source_path) if taxonomy.source_path else "",
        "governance": {
            "write_back": "human_review_required",
            "principle": "维度框架为长期知识；研报/新闻只写证据和候选补充，不直接覆盖框架。",
        },
        "stats": {
            "asset_count": len(assets),
            "asset_with_framework_count": sum(1 for row in assets if row.get("framework_id")),
            "dimension_count": len(dimensions),
            "term_count": len(terms_by_id),
            "issue_count": len(issues),
            "dimension_type_counts": dict(dimension_type_counts),
            "framework_source_counts": dict(framework_sources),
        },
        "assets": assets,
        "dimensions": dimensions,
        "issues": issues,
    }
    catalog = {
        "schema_version": "framework_indicator_event_catalog.v1",
        "status": "candidate",
        "generated_at": generated_at,
        "taxonomy_source": str(taxonomy.source_path) if taxonomy.source_path else "",
        "stats": {
            "term_count": len(terms_by_id),
            "term_type_counts": dict(Counter(row["term_type"] for row in terms_by_id.values())),
            "asset_count": len({row["asset"] for row in terms_by_id.values()}),
            "dimension_count": len({row["dimension_id"] for row in terms_by_id.values()}),
        },
        "terms": sorted(terms_by_id.values(), key=lambda row: (str(row["asset"]), str(row["dimension_label"]), row["term_type"], row["name"])),
    }
    return {"registry": registry, "catalog": catalog}


def publish_analysis_framework_registry(root: str | Path | None = None) -> dict[str, Any]:
    root_path = quanta_data_root(root)
    bundle = build_analysis_framework_registry(root_path)
    now = datetime.now()
    yyyy, mm, dd = dated_parts(now.strftime("%Y%m%d"))
    base = root_path / "agent_workspace" / "candidates" / "analysis_framework" / yyyy / mm / dd
    latest = root_path / "agent_workspace" / "candidates" / "analysis_framework" / "latest"
    registry_path = base / f"analysis-framework-registry-{now.strftime('%H%M%S')}.json"
    catalog_path = base / f"indicator-event-catalog-{now.strftime('%H%M%S')}.json"
    latest_registry = latest / "analysis-framework-registry.json"
    latest_catalog = latest / "indicator-event-catalog.json"
    write_json(registry_path, bundle["registry"])
    write_json(catalog_path, bundle["catalog"])
    write_json(latest_registry, bundle["registry"])
    write_json(latest_catalog, bundle["catalog"])
    return {
        "registry": relative_to_root(registry_path, root_path),
        "catalog": relative_to_root(catalog_path, root_path),
        "latest_registry": relative_to_root(latest_registry, root_path),
        "latest_catalog": relative_to_root(latest_catalog, root_path),
        "payload": bundle,
    }


def load_latest_analysis_framework_registry(root: str | Path | None = None) -> dict[str, Any] | None:
    root_path = quanta_data_root(root)
    path = root_path / "agent_workspace" / "candidates" / "analysis_framework" / "latest" / "analysis-framework-registry.json"
    if not path.exists():
        return None
    payload = read_json(path)
    return payload if isinstance(payload, dict) else None


def load_latest_indicator_event_catalog(root: str | Path | None = None) -> dict[str, Any] | None:
    root_path = quanta_data_root(root)
    path = root_path / "agent_workspace" / "candidates" / "analysis_framework" / "latest" / "indicator-event-catalog.json"
    if not path.exists():
        return None
    payload = read_json(path)
    return payload if isinstance(payload, dict) else None


def latest_analysis_framework_refs(root: str | Path | None = None) -> dict[str, Any]:
    root_path = quanta_data_root(root)
    registry_path = root_path / "agent_workspace" / "candidates" / "analysis_framework" / "latest" / "analysis-framework-registry.json"
    catalog_path = root_path / "agent_workspace" / "candidates" / "analysis_framework" / "latest" / "indicator-event-catalog.json"
    registry = read_json(registry_path) if registry_path.exists() else {}
    catalog = read_json(catalog_path) if catalog_path.exists() else {}
    return {
        "registry": relative_to_root(registry_path, root_path) if registry_path.exists() else "",
        "catalog": relative_to_root(catalog_path, root_path) if catalog_path.exists() else "",
        "registry_generated_at": registry.get("generated_at") if isinstance(registry, dict) else "",
        "catalog_generated_at": catalog.get("generated_at") if isinstance(catalog, dict) else "",
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Build normalized analysis framework registry and indicator/event catalog.")
    parser.add_argument("--quanta-root", help="Override GJ_QUANTA_DATA_ROOT.")
    args = parser.parse_args(argv)
    result = publish_analysis_framework_registry(args.quanta_root)
    stats = result["payload"]["registry"]["stats"]
    print(f"分析框架注册表 → {result['registry']}")
    print(f"指标事件目录 → {result['catalog']}")
    print(
        "assets={asset_count} frameworks={asset_with_framework_count} dimensions={dimension_count} terms={term_count} issues={issue_count}".format(
            **stats
        )
    )


if __name__ == "__main__":
    main()
