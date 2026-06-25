from __future__ import annotations

from pathlib import Path
from typing import Any

from quanta_agents.core.config import quanta_data_root
from quanta_agents.core.frameworks import iter_leaf_nodes, load_framework_for_asset
from quanta_agents.core.taxonomy import load_asset_taxonomy
from quanta_agents.futures_daily.framework_alignment import _match_framework_node

from .ids import asset_link_id, framework_link_id, stable_hash


def _dimension_label(label: str) -> str:
    text = str(label or "").strip()
    if "/" in text:
        return text.rsplit("/", 1)[-1].strip()
    return text or "未归类"


def _asset_by_label(root: Path) -> dict[str, dict[str, Any]]:
    taxonomy = load_asset_taxonomy(root)
    return {str(item.get("canonical_name") or ""): item for item in taxonomy.assets if item.get("canonical_name")}


def _fallback_asset(label: str, asset_id: str) -> dict[str, Any]:
    return {"asset_id": asset_id, "canonical_name": label, "aliases": [label]}


def _strategic_asset_candidates(text: str, root: Path) -> list[dict[str, Any]]:
    by_label = _asset_by_label(root)
    candidates: list[dict[str, Any]] = []
    if any(word in text for word in ("霍尔木兹", "伊朗", "中东", "地缘", "封锁", "战争", "冲突")):
        for label, asset_id in [("原油", "FUT-SC"), ("黄金", "FUT-AU"), ("铜", "FUT-CU")]:
            candidates.append(by_label.get(label) or _fallback_asset(label, asset_id))
    if any(word in text for word in ("美联储", "美元", "利率", "降息", "加息")):
        for label, asset_id in [("黄金", "FUT-AU"), ("铜", "FUT-CU")]:
            candidates.append(by_label.get(label) or _fallback_asset(label, asset_id))
    return candidates


def event_mapping_text(event: dict[str, Any]) -> str:
    signature = event.get("core_signature") or {}
    return " ".join(
        str(item or "")
        for item in [
            event.get("canonical_summary"),
            " ".join(event.get("core_entities") or []),
            " ".join(signature.get("subject") or []),
            " ".join(signature.get("object") or []),
            " ".join(signature.get("location") or []),
        ]
    )


def retrieve_asset_candidates(event: dict[str, Any], root: str | Path | None = None) -> list[dict[str, Any]]:
    root_path = quanta_data_root(root)
    taxonomy = load_asset_taxonomy(root_path)
    text = event_mapping_text(event)
    assets: list[dict[str, Any]] = []
    seen_labels: set[str] = set()
    for hit in taxonomy.classify(text):
        if hit.kind != "variety" or hit.label in seen_labels:
            continue
        asset = next((item for item in taxonomy.assets if item.get("canonical_name") == hit.label), None)
        if asset:
            assets.append(asset)
            seen_labels.add(hit.label)
    for asset in _strategic_asset_candidates(text, root_path):
        label = str(asset.get("canonical_name") or "")
        if label and label not in seen_labels:
            assets.append(asset)
            seen_labels.add(label)
    return assets


def map_event_assets_and_frameworks(
    event: dict[str, Any],
    root: str | Path | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Map one canonical event to asset links and framework-node links.

    This runs after canonical event creation, so multi-asset news shares one
    durable event and fans out only at the link/projection layer.
    """
    root_path = quanta_data_root(root)
    text = event_mapping_text(event)
    asset_links: list[dict[str, Any]] = []
    framework_links: list[dict[str, Any]] = []
    for asset in retrieve_asset_candidates(event, root_path):
        label = str(asset.get("canonical_name") or asset.get("display_name") or asset.get("asset_id") or "")
        asset_id = str(asset.get("asset_id") or stable_hash(label))
        direct = label and label in text
        relation = "direct" if direct else ("indirect" if event.get("event_type") in {"geopolitics", "macro"} else "contextual")
        confidence = 0.86 if direct else 0.68
        asset_links.append(
            {
                "link_id": asset_link_id(event["event_id"], asset_id),
                "event_id": event["event_id"],
                "asset_id": asset_id,
                "asset_label": label or asset_id,
                "relation": relation,
                "confidence": confidence,
                "mapping_method": "taxonomy_rule" if direct else "strategic_macro_rule",
            }
        )
        framework = load_framework_for_asset(asset, root_path)
        leaves = iter_leaf_nodes(framework) if framework else []
        if leaves:
            node_label, node_id, node_confidence, method = _match_framework_node(text, leaves)
            framework_id = framework.get("framework_id")
        else:
            node_label, node_id, node_confidence, method = "未归类", "default::未归类", 0.2, "no_framework"
            framework_id = ""
        framework_links.append(
            {
                "link_id": framework_link_id(event["event_id"], asset_id, node_id),
                "event_id": event["event_id"],
                "asset_id": asset_id,
                "framework_id": framework_id,
                "node_id": node_id,
                "node_label": node_label,
                "dimension_label": _dimension_label(node_label),
                "confidence": round(float(node_confidence), 3),
                "mapping_method": method,
            }
        )
    return asset_links, framework_links
