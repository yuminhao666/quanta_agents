from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from quanta_agents.core.config import first_env, quanta_data_root
from quanta_agents.core.io import read_json


ACTIVE_FRAMEWORKS_RELATIVE = "data_lake/active_knowledge/research_frameworks/commodities"


def _with_source(obj: dict[str, Any], path: Path) -> dict[str, Any]:
    payload = dict(obj)
    payload["_source_path"] = str(path)
    if not payload.get("framework_id"):
        payload["framework_id"] = payload.get("candidate_id") or path.stem
    if not payload.get("standard_name"):
        payload["standard_name"] = payload.get("commodity") or payload.get("generic_name")
    if not payload.get("generic_name"):
        payload["generic_name"] = payload.get("standard_name") or payload.get("commodity")
    if not payload.get("asset_id"):
        asset_key = payload.get("commodity_code") or payload.get("commodity") or payload.get("framework_id")
        payload["asset_id"] = f"candidate.{asset_key}" if asset_key else path.stem
    return payload


def _latest_candidate_dir(root: Path) -> Path | None:
    base = root / "agent_workspace" / "candidates" / "frameworks"
    if not base.exists():
        return None
    dated = [path for path in base.glob("*/*/*") if path.is_dir() and any(path.glob("FWK-*.json"))]
    return sorted(dated, key=lambda item: item.as_posix())[-1] if dated else None


def _load_framework_by_code(root: Path, code: str) -> dict[str, Any] | None:
    active_base = root / "gold" / "frameworks" / code.upper()
    if active_base.exists():
        versions = sorted([path for path in active_base.glob("v*/framework.json") if path.exists()])
        if versions:
            obj = read_json(versions[-1])
            if isinstance(obj, dict):
                return _with_source(obj, versions[-1])

    date_dir = _latest_candidate_dir(root)
    if date_dir:
        matches = sorted(date_dir.glob(f"FWK-{code.upper()}-*.json"))
        if matches:
            obj = read_json(matches[-1])
            if isinstance(obj, dict):
                return _with_source(obj, matches[-1])
    return None


def _active_framework_dirs(root: Path) -> list[Path]:
    explicit = first_env(
        "GJ_RESEARCH_FRAMEWORKS_ROOT",
        "GJ_ACTIVE_FRAMEWORKS_ROOT",
        "QUANTA_RESEARCH_FRAMEWORKS_ROOT",
        "GJ_COMMODITY_FRAMEWORKS_ROOT",
    )
    candidates = [Path(explicit).expanduser()] if explicit else []
    candidates.extend(
        [
            root / "active_knowledge/research_frameworks/commodities",
            root / ACTIVE_FRAMEWORKS_RELATIVE,
            root.parent / "quanta_research_group" / ACTIVE_FRAMEWORKS_RELATIVE,
            root.parent / "quanta_data" / ACTIVE_FRAMEWORKS_RELATIVE,
        ]
    )
    seen: set[Path] = set()
    result = []
    for path in candidates:
        resolved = path.expanduser()
        if resolved in seen or not resolved.exists():
            continue
        seen.add(resolved)
        result.append(resolved)
    return result


def active_framework_dirs(root: str | Path | None = None) -> list[Path]:
    return _active_framework_dirs(quanta_data_root(root))


def iter_active_frameworks(root: str | Path | None = None) -> list[tuple[Path, dict[str, Any]]]:
    frameworks: list[tuple[Path, dict[str, Any]]] = []
    for directory in active_framework_dirs(root):
        for path in sorted(directory.glob("*.json")):
            obj = read_json(path)
            if isinstance(obj, dict):
                frameworks.append((path, _with_source(obj, path)))
    return frameworks


def _norm(value: Any) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", text)


DIMENSION_TYPE_HINTS = (
    ("inventory", ("库存", "仓单", "库容", "库销比")),
    ("supply", ("供给", "供应", "产量", "产能", "开工", "发运", "进口", "出口", "矿山", "装置", "检修")),
    ("demand", ("需求", "消费", "成交", "订单", "终端", "地产", "基建", "汽车", "家电", "饲料")),
    ("cost", ("成本", "利润", "加工费", "原料", "电价", "运费", "升贴水")),
    ("policy", ("政策", "监管", "关税", "收储", "抛储", "环保", "安监")),
    ("geopolitics", ("地缘", "战争", "冲突", "制裁", "停火", "海峡", "OPEC")),
    ("macro", ("宏观", "美元", "利率", "通胀", "美联储", "PMI", "社融", "汇率")),
    ("weather", ("天气", "降水", "干旱", "洪涝", "霜冻", "气温")),
    ("spread", ("价差", "基差", "月差", "期限结构", "比价")),
    ("valuation", ("估值", "情绪", "资金", "成交", "持仓")),
)


def _term_texts(rows: Any, *keys: str) -> list[str]:
    out: list[str] = []
    if not isinstance(rows, list):
        return out
    for row in rows:
        if isinstance(row, str):
            text = row.strip()
            if text:
                out.append(text)
            continue
        if not isinstance(row, dict):
            continue
        for key in keys:
            text = str(row.get(key) or "").strip()
            if text:
                out.append(text)
        for alias in row.get("variants") or row.get("aliases") or []:
            text = str(alias).strip()
            if text:
                out.append(text)
    return list(dict.fromkeys(out))


def leaf_indicator_terms(leaf: dict[str, Any]) -> list[str]:
    terms = []
    terms.extend(_term_texts(leaf.get("typical_indicators"), "key", "display", "name"))
    terms.extend(_term_texts(leaf.get("indicators"), "key", "display", "name"))
    return list(dict.fromkeys(terms))


def leaf_event_terms(leaf: dict[str, Any]) -> list[str]:
    terms = []
    terms.extend(_term_texts(leaf.get("typical_events"), "text", "name", "display"))
    terms.extend(_term_texts(leaf.get("events"), "text", "name", "display"))
    return list(dict.fromkeys(terms))


def leaf_claim_terms(leaf: dict[str, Any]) -> list[str]:
    return _term_texts(leaf.get("claims"), "statement", "text", "name")


def _infer_dimension_type(leaf: dict[str, Any]) -> str:
    explicit = str(leaf.get("dimension_type") or "").strip()
    if explicit:
        return explicit
    values: list[str] = [
        str(leaf.get("name") or ""),
        str(leaf.get("display") or ""),
        str(leaf.get("node_id") or ""),
        "/".join(str(item) for item in leaf.get("path", []) if item),
    ]
    values.extend(leaf_indicator_terms(leaf)[:12])
    values.extend(leaf_event_terms(leaf)[:12])
    text = " ".join(values)
    for dimension_type, keywords in DIMENSION_TYPE_HINTS:
        if any(keyword in text for keyword in keywords):
            return dimension_type
    return "other"


def normalize_leaf_node(framework: dict[str, Any], leaf: dict[str, Any]) -> dict[str, Any]:
    payload = dict(leaf)
    framework_name = framework.get("standard_name") or framework.get("generic_name") or framework.get("framework_id")
    path = payload.get("path") if isinstance(payload.get("path"), list) else []
    name = str(payload.get("name") or payload.get("display") or payload.get("dimension_name") or "").strip()
    if not name and path:
        name = str(path[-1])
    payload.setdefault("name", name)
    payload.setdefault("display", name)
    payload.setdefault("path", [str(framework_name), name] if name else [str(framework_name)])
    payload.setdefault("node_id", str(payload.get("dimension_id") or name or framework.get("framework_id")))
    payload["dimension_type"] = _infer_dimension_type(payload)
    payload["typical_indicators"] = leaf_indicator_terms(payload)
    payload["typical_events"] = leaf_event_terms(payload)
    payload["typical_claims"] = leaf_claim_terms(payload)
    payload["framework_id"] = framework.get("framework_id")
    payload["framework_asset_id"] = framework.get("asset_id")
    return payload


def _asset_name_tokens(asset: dict[str, Any]) -> set[str]:
    values: list[Any] = [
        asset.get("canonical_name"),
        asset.get("display_name"),
        asset.get("standard_name"),
        asset.get("generic_name"),
    ]
    values.extend(asset.get("aliases") or [])
    return {_norm(value) for value in values if _norm(value)}


def _framework_match_score(framework: dict[str, Any], asset: dict[str, Any]) -> int:
    names = _asset_name_tokens(asset)
    canonical = _norm(asset.get("canonical_name") or asset.get("display_name"))
    framework_names = {
        _norm(framework.get("standard_name")),
        _norm(framework.get("generic_name")),
        _norm(framework.get("commodity")),
        _norm(framework.get("commodity_code")),
    }
    asset_id = _norm(framework.get("asset_id"))
    code = _norm(asset.get("commodity_code") or asset.get("exchange_code"))
    score = 0
    for name in framework_names:
        if not name:
            continue
        if name in names:
            score += 120
        if canonical and name == canonical:
            score += 40
        if code and name == code:
            score += 80
    if code and asset_id.endswith(code):
        score += 25
    if framework.get("status") == "active":
        score += 5
    if framework.get("status") == "candidate" or framework.get("candidate_id"):
        score += 3
    return score


def _load_active_framework_by_asset(root: Path, asset: dict[str, Any]) -> dict[str, Any] | None:
    best: tuple[int, Path, dict[str, Any]] | None = None
    for directory in _active_framework_dirs(root):
        for path in sorted(directory.glob("*.json")):
            obj = read_json(path)
            if not isinstance(obj, dict) or obj.get("artifact_type") != "research_framework":
                continue
            score = _framework_match_score(obj, asset)
            if score <= 0:
                continue
            if best is None or score > best[0]:
                best = (score, path, obj)
    if not best:
        return None
    return _with_source(best[2], best[1])


def _load_candidate_framework_by_asset(root: Path, asset: dict[str, Any]) -> dict[str, Any] | None:
    date_dir = _latest_candidate_dir(root)
    if not date_dir:
        return None

    best: tuple[int, Path, dict[str, Any]] | None = None
    for path in sorted(date_dir.glob("FWK-*.json")):
        obj = read_json(path)
        if not isinstance(obj, dict):
            continue
        framework = _with_source(obj, path)
        score = _framework_match_score(framework, asset)
        if score <= 0:
            continue
        if best is None or score > best[0]:
            best = (score, path, obj)
    if not best:
        return None
    return _with_source(best[2], best[1])


def load_framework_for_asset(asset: dict[str, Any], root: str | Path | None = None) -> dict[str, Any] | None:
    root_path = quanta_data_root(root)
    framework_ref = asset.get("framework_ref") if isinstance(asset, dict) else None
    if isinstance(framework_ref, dict):
        path_value = framework_ref.get("path")
        if path_value:
            path = root_path / str(path_value)
            if path.exists():
                obj = read_json(path)
                if isinstance(obj, dict):
                    return _with_source(obj, path)
        code = framework_ref.get("commodity_code") or framework_ref.get("code")
        if code:
            framework = _load_framework_by_code(root_path, str(code))
            if framework:
                return framework

    if isinstance(asset, dict):
        framework = _load_active_framework_by_asset(root_path, asset)
        if framework:
            return framework
        framework = _load_candidate_framework_by_asset(root_path, asset)
        if framework:
            return framework
        code = asset.get("commodity_code") or asset.get("exchange_code")
        return _load_framework_by_code(root_path, str(code)) if code else None
    return None


def _dimension_leaf(framework: dict[str, Any], dimension: dict[str, Any]) -> dict[str, Any]:
    framework_name = framework.get("standard_name") or framework.get("generic_name") or framework.get("framework_id")
    name = str(dimension.get("dimension_name") or dimension.get("dimension_type") or "").strip()
    node_id = str(dimension.get("dimension_id") or name).strip()
    return normalize_leaf_node(
        framework,
        {
        "node_id": node_id,
        "name": name,
        "display": name,
        "path": [str(framework_name), name],
        "dimension_type": dimension.get("dimension_type"),
        "description": dimension.get("description"),
        "typical_indicators": dimension.get("typical_indicators") or [],
        "typical_events": dimension.get("typical_events") or [],
        "prompt_hint": dimension.get("prompt_hint"),
        "graph_node_refs": dimension.get("graph_node_refs") or [],
        "framework_id": framework.get("framework_id"),
        "framework_asset_id": framework.get("asset_id"),
        },
    )


def iter_leaf_nodes(framework: dict[str, Any]) -> list[dict[str, Any]]:
    dimensions = framework.get("core_dimensions")
    if isinstance(dimensions, list):
        return [
            _dimension_leaf(framework, dimension)
            for dimension in dimensions
            if isinstance(dimension, dict)
        ]

    leaves: list[dict[str, Any]] = []

    def walk(nodes: Any) -> None:
        if not isinstance(nodes, list):
            return
        for node in nodes:
            if not isinstance(node, dict):
                continue
            children = node.get("children")
            if node.get("kind") == "leaf" or not children:
                leaves.append(normalize_leaf_node(framework, node))
            walk(children)

    walk(framework.get("tree"))
    return leaves
