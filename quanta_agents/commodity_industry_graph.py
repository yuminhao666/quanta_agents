from __future__ import annotations

import argparse
from collections import Counter, defaultdict, deque
from datetime import datetime
from pathlib import Path
from typing import Any

from quanta_agents.asset_event_state.models import clean_text, hash_id
from quanta_agents.core.analysis_framework import build_analysis_framework_registry, normalize_dimension_type
from quanta_agents.core.config import quanta_data_root
from quanta_agents.core.io import dated_parts, relative_to_root, utc_now_iso, write_json
from quanta_agents.core.taxonomy import load_asset_taxonomy


SCHEMA_VERSION = "commodity_industry_graph.v1"
LATEST_RELATIVE = Path("agent_workspace/candidates/industry_graph/latest/commodity-industry-graph.json")
NON_COMMODITY_SECTORS = {"金融期货", "指数"}
KEY_DIMENSION_TYPES = {
    "supply",
    "supply_demand",
    "demand",
    "inventory",
    "cost",
    "policy",
    "macro",
    "geopolitics",
    "spread",
    "valuation",
    "substitution",
    "weather",
    "logistics",
    "funding",
    "seasonality",
    "disease",
    "capacity_cycle",
}


CHAIN_SPECS: list[dict[str, Any]] = [
    {
        "chain_code": "black_metals",
        "chain_name": "黑色金属产业链",
        "asset_codes": ["I", "JM", "J", "ZC", "SF", "SM", "RB", "HC", "SS"],
        "stages": [
            ("raw_material", "铁矿/煤焦/合金原料", "upstream"),
            ("smelting", "焦化与钢铁冶炼", "midstream"),
            ("steel_products", "钢材成品", "downstream"),
            ("terminal", "地产/基建/制造终端", "end_market"),
        ],
        "relationships": [
            ("I", "RB", "原料成本传导", 0.72, 1),
            ("I", "HC", "原料成本传导", 0.72, 1),
            ("JM", "J", "煤焦转化", 0.82, 1),
            ("J", "RB", "高炉成本传导", 0.74, 1),
            ("J", "HC", "高炉成本传导", 0.74, 1),
            ("ZC", "SF", "电煤成本传导", 0.58, 1),
            ("ZC", "SM", "电煤成本传导", 0.58, 1),
            ("SF", "RB", "合金成本传导", 0.48, 1),
            ("SM", "RB", "合金成本传导", 0.48, 1),
            ("NI", "SS", "不锈钢镍成本传导", 0.64, 1),
        ],
    },
    {
        "chain_code": "base_metals",
        "chain_name": "有色金属产业链",
        "asset_codes": ["CU", "BC", "AL", "AO", "ZN", "PB", "NI", "SN"],
        "stages": [
            ("ore", "矿端与原料", "upstream"),
            ("smelting", "冶炼/精炼", "midstream"),
            ("fabrication", "加工材", "downstream"),
            ("terminal", "电力/建筑/汽车/家电", "end_market"),
        ],
        "relationships": [
            ("AO", "AL", "氧化铝-电解铝成本传导", 0.86, 1),
            ("CU", "AL", "铜铝替代与比价", 0.38, -1),
            ("AL", "CU", "铝铜替代与比价", 0.34, -1),
            ("NI", "SS", "镍-不锈钢成本传导", 0.64, 1),
            ("CU", "BC", "内外铜价联动", 0.92, 1),
            ("BC", "CU", "内外铜价联动", 0.92, 1),
        ],
    },
    {
        "chain_code": "energy_chemicals",
        "chain_name": "能源化工产业链",
        "asset_codes": [
            "SC",
            "FU",
            "LU",
            "BU",
            "PG",
            "L",
            "PP",
            "V",
            "EG",
            "EB",
            "BZ",
            "TA",
            "MA",
            "PX",
            "PF",
            "FG",
            "SA",
            "UR",
            "SH",
            "RU",
            "NR",
            "BR",
        ],
        "stages": [
            ("energy", "原油/煤炭/气体原料", "upstream"),
            ("basic_chemicals", "基础化工品", "midstream"),
            ("polymers", "聚合物/化纤/橡胶", "downstream"),
            ("terminal", "纺织/包装/地产/交通", "end_market"),
        ],
        "relationships": [
            ("SC", "FU", "炼厂油品传导", 0.78, 1),
            ("SC", "LU", "炼厂油品传导", 0.78, 1),
            ("SC", "BU", "沥青成本传导", 0.66, 1),
            ("SC", "PX", "芳烃成本传导", 0.58, 1),
            ("PX", "TA", "PX-PTA成本传导", 0.86, 1),
            ("TA", "PF", "聚酯原料传导", 0.72, 1),
            ("EG", "PF", "聚酯原料传导", 0.72, 1),
            ("BZ", "EB", "纯苯-苯乙烯成本传导", 0.84, 1),
            ("MA", "PP", "甲醇制烯烃传导", 0.52, 1),
            ("SA", "FG", "纯碱-玻璃成本传导", 0.82, 1),
            ("SH", "AO", "烧碱-氧化铝成本传导", 0.72, 1),
            ("RU", "NR", "天然橡胶联动", 0.86, 1),
            ("RU", "BR", "橡胶替代联动", 0.46, -1),
        ],
    },
    {
        "chain_code": "agriculture",
        "chain_name": "农产品产业链",
        "asset_codes": [
            "A",
            "B",
            "M",
            "Y",
            "P",
            "C",
            "CS",
            "RR",
            "SR",
            "CF",
            "CY",
            "OI",
            "RM",
            "PK",
            "AP",
            "CJ",
            "PM",
            "WH",
            "RI",
            "LR",
            "JR",
            "JD",
            "LH",
            "SP",
        ],
        "stages": [
            ("planting", "种植/养殖供给", "upstream"),
            ("processing", "压榨/深加工/纺纱", "midstream"),
            ("feed_food", "饲料/食品/纺织", "downstream"),
            ("terminal", "消费与出口终端", "end_market"),
        ],
        "relationships": [
            ("A", "M", "大豆压榨粕", 0.78, 1),
            ("B", "M", "大豆压榨粕", 0.78, 1),
            ("A", "Y", "大豆压榨油", 0.78, 1),
            ("B", "Y", "大豆压榨油", 0.78, 1),
            ("OI", "Y", "油脂替代联动", 0.54, 1),
            ("P", "Y", "油脂替代联动", 0.54, 1),
            ("RM", "M", "蛋白粕替代联动", 0.52, 1),
            ("C", "CS", "玉米深加工", 0.84, 1),
            ("C", "JD", "饲料成本传导", 0.42, 1),
            ("M", "JD", "饲料成本传导", 0.46, 1),
            ("C", "LH", "饲料成本传导", 0.44, 1),
            ("M", "LH", "饲料成本传导", 0.48, 1),
            ("CF", "CY", "棉花-棉纱成本传导", 0.86, 1),
        ],
    },
    {
        "chain_code": "new_energy",
        "chain_name": "新能源材料产业链",
        "asset_codes": ["SI", "PS", "LC"],
        "stages": [
            ("mineral_energy", "矿产/工业硅/锂盐", "upstream"),
            ("materials", "多晶硅/电池材料", "midstream"),
            ("equipment", "光伏/电池/储能制造", "downstream"),
            ("terminal", "新能源车/光伏/储能终端", "end_market"),
        ],
        "relationships": [
            ("SI", "PS", "工业硅-多晶硅成本传导", 0.88, 1),
            ("LC", "SI", "新能源景气联动", 0.24, 1),
            ("LC", "PS", "新能源景气联动", 0.24, 1),
        ],
    },
    {
        "chain_code": "precious_metals",
        "chain_name": "贵金属与金融属性链",
        "asset_codes": ["AU", "AG", "PT", "PD"],
        "stages": [
            ("mine", "矿端与回收供给", "upstream"),
            ("refining", "精炼与库存", "midstream"),
            ("financial", "利率/美元/避险定价", "supporting"),
            ("terminal", "珠宝/工业/投资需求", "end_market"),
        ],
        "relationships": [
            ("AU", "AG", "贵金属金融属性联动", 0.72, 1),
            ("AG", "AU", "贵金属金融属性联动", 0.62, 1),
            ("PT", "PD", "铂钯替代联动", 0.42, -1),
            ("PD", "PT", "铂钯替代联动", 0.42, -1),
        ],
    },
]


def build_commodity_industry_graph(
    root: str | Path | None = None,
    *,
    max_hops: int = 4,
    include_test_events: bool = True,
) -> dict[str, Any]:
    root_path = quanta_data_root(root)
    taxonomy = load_asset_taxonomy(root_path, required=True)
    registry = build_analysis_framework_registry(root_path)["registry"]
    commodity_assets = [asset for asset in taxonomy.assets if _is_commodity_asset(asset)]
    asset_by_code = {
        str(asset.get("commodity_code") or asset.get("exchange_code") or "").upper(): asset
        for asset in commodity_assets
        if asset.get("commodity_code") or asset.get("exchange_code")
    }
    chains = _chain_rows(asset_by_code)
    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[str, dict[str, Any]] = {}

    for chain in chains:
        _add_chain_stage_nodes(nodes, edges, chain)
    for asset in commodity_assets:
        _add_asset_node(nodes, asset, _asset_chain_codes(asset))
    for chain in chains:
        _add_stage_asset_edges(nodes, edges, chain, asset_by_code)
        _add_relationship_edges(edges, chain, asset_by_code)
    _add_dimension_nodes_and_edges(nodes, edges, registry.get("dimensions", []), commodity_assets)

    graph = {
        "schema_version": SCHEMA_VERSION,
        "status": "candidate",
        "generated_at": utc_now_iso(),
        "source_refs": {
            "taxonomy": relative_to_root(taxonomy.source_path, root_path) if taxonomy.source_path else "",
            "analysis_framework_registry": "agent_workspace/candidates/analysis_framework/latest/analysis-framework-registry.json",
        },
        "governance": {
            "write_back": "human_review_required",
            "principle": "产业链图谱是低频结构骨架；测试事件只生成候选计算结果，不直接改写 gold。",
        },
        "chains": chains,
        "nodes": sorted(nodes.values(), key=lambda item: (str(item.get("chain_codes")), str(item.get("node_type")), str(item.get("label")))),
        "edges": sorted(edges.values(), key=lambda item: (str(item.get("source_node_id")), str(item.get("target_node_id")), str(item.get("edge_type")))),
    }
    graph["stats"] = _graph_stats(graph)
    graph["test_calculation"] = (
        run_test_propagation(graph, max_hops=max_hops, test_events=default_test_events())
        if include_test_events
        else {"schema_version": "commodity_industry_graph_test_calculation.v1", "test_events": [], "paths": [], "node_activations": []}
    )
    return graph


def run_test_propagation(
    graph: dict[str, Any],
    *,
    max_hops: int = 4,
    test_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    nodes = {str(node.get("node_id")): node for node in graph.get("nodes", []) if isinstance(node, dict)}
    edges = [edge for edge in graph.get("edges", []) if isinstance(edge, dict)]
    edges_by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in edges:
        edges_by_source[str(edge.get("source_node_id") or "")].append(edge)

    paths: list[dict[str, Any]] = []
    for event in test_events or default_test_events():
        start_node = _event_start_node(event, graph)
        if start_node not in nodes:
            continue
        paths.extend(_propagate_event(event, start_node, edges_by_source, nodes, max_hops=max_hops))

    activations = _aggregate_activations(paths, nodes)
    return {
        "schema_version": "commodity_industry_graph_test_calculation.v1",
        "generated_at": utc_now_iso(),
        "rule": "event.dimension_node -> graph edge BFS; path_signal *= edge.polarity * edge.weight; confidence decays per hop",
        "max_hops": max_hops,
        "test_events": test_events or default_test_events(),
        "paths": paths,
        "node_activations": activations,
        "stats": {
            "test_event_count": len(test_events or default_test_events()),
            "path_count": len(paths),
            "activated_node_count": len(activations),
        },
    }


def default_test_events() -> list[dict[str, Any]]:
    now = utc_now_iso()
    return [
        {
            "event_id": "TEST-CU-SUPPLY-001",
            "asset": "铜",
            "commodity_code": "CU",
            "dimension_type": "supply",
            "summary": "铜矿端扰动导致精矿供应偏紧，冶炼原料约束增强。",
            "signal": 0.62,
            "confidence": 0.84,
            "source_type": "test_event",
            "event_time": now,
        },
        {
            "event_id": "TEST-SC-COST-001",
            "asset": "原油",
            "commodity_code": "SC",
            "dimension_type": "cost",
            "summary": "原油价格上行推升下游化工品成本曲线。",
            "signal": 0.55,
            "confidence": 0.78,
            "source_type": "test_event",
            "event_time": now,
        },
        {
            "event_id": "TEST-I-DEMAND-001",
            "asset": "铁矿石",
            "commodity_code": "I",
            "dimension_type": "demand",
            "summary": "钢材终端需求验证改善，带动黑色原料链条预期修复。",
            "signal": 0.48,
            "confidence": 0.76,
            "source_type": "test_event",
            "event_time": now,
        },
        {
            "event_id": "TEST-LC-DEMAND-001",
            "asset": "碳酸锂",
            "commodity_code": "LC",
            "dimension_type": "demand",
            "summary": "储能与新能源车需求改善，带动锂盐材料链景气度上行。",
            "signal": 0.52,
            "confidence": 0.75,
            "source_type": "test_event",
            "event_time": now,
        },
    ]


def publish_commodity_industry_graph(
    root: str | Path | None = None,
    *,
    max_hops: int = 4,
    include_test_events: bool = True,
) -> dict[str, Any]:
    root_path = quanta_data_root(root)
    payload = build_commodity_industry_graph(root_path, max_hops=max_hops, include_test_events=include_test_events)
    now = datetime.now()
    yyyy, mm, dd = dated_parts(now.strftime("%Y%m%d"))
    dated_dir = root_path / "agent_workspace" / "candidates" / "industry_graph" / yyyy / mm / dd
    latest_path = root_path / LATEST_RELATIVE
    output_path = dated_dir / f"commodity-industry-graph-{now.strftime('%H%M%S')}.json"
    write_json(output_path, payload)
    write_json(latest_path, payload)
    return {
        "graph": relative_to_root(output_path, root_path),
        "latest_graph": relative_to_root(latest_path, root_path),
        "payload": payload,
    }


def _is_commodity_asset(asset: dict[str, Any]) -> bool:
    sector = clean_text(asset.get("sector"))
    category = clean_text(asset.get("category"))
    if sector in NON_COMMODITY_SECTORS or category in NON_COMMODITY_SECTORS:
        return False
    if clean_text(asset.get("asset_class")) and clean_text(asset.get("asset_class")) != "futures":
        return False
    return bool(asset.get("canonical_name") and (asset.get("commodity_code") or asset.get("exchange_code")))


def _chain_rows(asset_by_code: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    chains: list[dict[str, Any]] = []
    for spec in CHAIN_SPECS:
        codes = [code for code in spec["asset_codes"] if code in asset_by_code]
        if not codes:
            continue
        chains.append(
            {
                "chain_code": spec["chain_code"],
                "chain_name": spec["chain_name"],
                "asset_codes": codes,
                "asset_count": len(codes),
                "stage_count": len(spec["stages"]),
                "relationship_count": len([rel for rel in spec["relationships"] if rel[0] in asset_by_code and rel[1] in asset_by_code]),
                "review_status": "candidate",
            }
        )
    return chains


def _asset_chain_codes(asset: dict[str, Any]) -> list[str]:
    code = str(asset.get("commodity_code") or asset.get("exchange_code") or "").upper()
    return [spec["chain_code"] for spec in CHAIN_SPECS if code in spec["asset_codes"]]


def _stage_node_id(chain_code: str, stage_id: str) -> str:
    return f"CHAIN:{chain_code}:{stage_id}"


def _asset_node_id(asset_or_code: dict[str, Any] | str) -> str:
    if isinstance(asset_or_code, dict):
        asset_id = clean_text(asset_or_code.get("asset_id"))
        code = clean_text(asset_or_code.get("commodity_code") or asset_or_code.get("exchange_code"))
        return f"ASSET:{asset_id or code}"
    return f"ASSET:{asset_or_code.upper()}"


def _dimension_node_id(asset: dict[str, Any], dimension_type: str) -> str:
    return f"DIM:{clean_text(asset.get('asset_id'))}:{dimension_type}"


def _add_chain_stage_nodes(nodes: dict[str, dict[str, Any]], edges: dict[str, dict[str, Any]], chain: dict[str, Any]) -> None:
    spec = _spec_for_chain(chain["chain_code"])
    previous_id = ""
    for index, (stage_id, label, layer) in enumerate(spec["stages"]):
        node_id = _stage_node_id(chain["chain_code"], stage_id)
        nodes[node_id] = {
            "node_id": node_id,
            "label": label,
            "node_type": "chain_stage",
            "layer": layer,
            "chain_codes": [chain["chain_code"]],
            "chain_names": [chain["chain_name"]],
            "description": f"{chain['chain_name']}的第 {index + 1} 层产业链结构节点。",
            "review_status": "candidate",
        }
        if previous_id:
            _put_edge(
                edges,
                previous_id,
                node_id,
                "stage_flow",
                "产业链顺序传导",
                0.72,
                1,
                0.82,
                chain["chain_code"],
            )
        previous_id = node_id


def _add_asset_node(nodes: dict[str, dict[str, Any]], asset: dict[str, Any], chain_codes: list[str]) -> None:
    node_id = _asset_node_id(asset)
    nodes[node_id] = {
        "node_id": node_id,
        "label": clean_text(asset.get("canonical_name")),
        "node_type": "asset",
        "layer": "tracking",
        "asset_id": asset.get("asset_id"),
        "commodity_code": asset.get("commodity_code"),
        "sector": asset.get("sector"),
        "category": asset.get("category"),
        "chain_codes": chain_codes,
        "chain_names": [_chain_name(code) for code in chain_codes],
        "description": f"{asset.get('canonical_name')}期货资产节点，承接框架维度、产业链传导和事件映射。",
        "aliases": asset.get("aliases") or [],
        "review_status": "candidate",
    }


def _add_stage_asset_edges(
    nodes: dict[str, dict[str, Any]],
    edges: dict[str, dict[str, Any]],
    chain: dict[str, Any],
    asset_by_code: dict[str, dict[str, Any]],
) -> None:
    spec = _spec_for_chain(chain["chain_code"])
    stage_ids = [stage[0] for stage in spec["stages"]]
    for index, code in enumerate(chain["asset_codes"]):
        asset = asset_by_code.get(code)
        if not asset:
            continue
        stage_id = stage_ids[min(index * len(stage_ids) // max(1, len(chain["asset_codes"])), len(stage_ids) - 1)]
        source = _stage_node_id(chain["chain_code"], stage_id)
        target = _asset_node_id(asset)
        if source in nodes and target in nodes:
            _put_edge(edges, source, target, "stage_contains_asset", "阶段包含品种", 0.62, 1, 0.76, chain["chain_code"])


def _add_relationship_edges(edges: dict[str, dict[str, Any]], chain: dict[str, Any], asset_by_code: dict[str, dict[str, Any]]) -> None:
    for source_code, target_code, label, weight, polarity in _spec_for_chain(chain["chain_code"])["relationships"]:
        source_asset = asset_by_code.get(source_code)
        target_asset = asset_by_code.get(target_code)
        if not source_asset or not target_asset:
            continue
        _put_edge(
            edges,
            _asset_node_id(source_asset),
            _asset_node_id(target_asset),
            "industry_transmission",
            label,
            weight,
            polarity,
            0.74,
            chain["chain_code"],
        )


def _add_dimension_nodes_and_edges(
    nodes: dict[str, dict[str, Any]],
    edges: dict[str, dict[str, Any]],
    dimensions: list[dict[str, Any]],
    commodity_assets: list[dict[str, Any]],
) -> None:
    asset_by_id = {clean_text(asset.get("asset_id")): asset for asset in commodity_assets}
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in dimensions:
        asset_id = clean_text(row.get("asset_id"))
        dimension_type = normalize_dimension_type(row.get("dimension_type"))
        if asset_id in asset_by_id and dimension_type in KEY_DIMENSION_TYPES:
            grouped[(asset_id, dimension_type)].append(row)

    for (asset_id, dimension_type), rows in grouped.items():
        asset = asset_by_id[asset_id]
        node_id = _dimension_node_id(asset, dimension_type)
        sample_labels = [clean_text(row.get("dimension_label") or row.get("dimension_name")) for row in rows[:5]]
        nodes[node_id] = {
            "node_id": node_id,
            "label": f"{asset.get('canonical_name')} · {_dimension_type_label(dimension_type)}",
            "node_type": "framework_dimension",
            "layer": _dimension_layer(dimension_type),
            "asset_id": asset_id,
            "asset": asset.get("canonical_name"),
            "commodity_code": asset.get("commodity_code"),
            "sector": asset.get("sector"),
            "dimension_type": dimension_type,
            "dimension_count": len(rows),
            "chain_codes": _asset_chain_codes(asset),
            "chain_names": [_chain_name(code) for code in _asset_chain_codes(asset)],
            "description": "；".join(sample_labels) if sample_labels else f"{asset.get('canonical_name')}的{dimension_type}框架维度。",
            "source_dimension_refs": [
                {
                    "dimension_id": row.get("dimension_id"),
                    "dimension_label": row.get("dimension_label"),
                    "source_path": row.get("source_path"),
                }
                for row in rows[:12]
            ],
            "review_status": "candidate",
        }
        weight = _dimension_weight(rows)
        _put_edge(
            edges,
            node_id,
            _asset_node_id(asset),
            "driver_activation",
            "框架维度激活资产状态",
            weight,
            1,
            0.78,
            (_asset_chain_codes(asset) or ["unassigned"])[0],
        )


def _put_edge(
    edges: dict[str, dict[str, Any]],
    source: str,
    target: str,
    edge_type: str,
    label: str,
    weight: float,
    polarity: int,
    confidence: float,
    chain_code: str,
) -> None:
    edge_id = hash_id("IEDGE", source, target, edge_type, label, length=14)
    edges[edge_id] = {
        "edge_id": edge_id,
        "source_node_id": source,
        "target_node_id": target,
        "edge_type": edge_type,
        "label": label,
        "weight": round(float(weight), 4),
        "polarity": 1 if polarity >= 0 else -1,
        "delay": "hours" if edge_type in {"driver_activation", "industry_transmission"} else "days",
        "confidence": round(float(confidence), 4),
        "chain_codes": [chain_code],
        "review_status": "candidate",
    }


def _propagate_event(
    event: dict[str, Any],
    start_node: str,
    edges_by_source: dict[str, list[dict[str, Any]]],
    nodes: dict[str, dict[str, Any]],
    *,
    max_hops: int,
) -> list[dict[str, Any]]:
    base_signal = _clip(float(event.get("signal") or 0.0), -1.0, 1.0)
    base_confidence = _clip(float(event.get("confidence") or 0.5), 0.0, 1.0)
    paths: list[dict[str, Any]] = []
    queue: deque[tuple[str, list[dict[str, Any]], float, float]] = deque()
    queue.append((start_node, [], base_signal, base_confidence))
    paths.append(_path_row(event, start_node, [], base_signal, base_confidence, nodes))
    while queue:
        current, steps, signal, confidence = queue.popleft()
        if len(steps) >= max_hops:
            continue
        for edge in edges_by_source.get(current, []):
            target = clean_text(edge.get("target_node_id"))
            if not target or target in {step.get("target_node_id") for step in steps}:
                continue
            next_signal = signal * int(edge.get("polarity") or 1) * float(edge.get("weight") or 0.0)
            next_confidence = confidence * float(edge.get("confidence") or 0.0) * (0.82 ** len(steps))
            if abs(next_signal) < 0.015 or next_confidence < 0.05:
                continue
            step = {
                "edge_id": edge.get("edge_id"),
                "source_node_id": current,
                "target_node_id": target,
                "label": edge.get("label"),
                "edge_type": edge.get("edge_type"),
                "weight": edge.get("weight"),
                "polarity": edge.get("polarity"),
                "confidence": edge.get("confidence"),
            }
            next_steps = [*steps, step]
            paths.append(_path_row(event, target, next_steps, next_signal, next_confidence, nodes))
            queue.append((target, next_steps, next_signal, next_confidence))
    return paths


def _path_row(
    event: dict[str, Any],
    final_node: str,
    steps: list[dict[str, Any]],
    signal: float,
    confidence: float,
    nodes: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    final = nodes.get(final_node, {})
    path_id = hash_id("IPATH", event.get("event_id"), final_node, len(steps), round(signal, 5), length=14)
    return {
        "path_id": path_id,
        "event_id": event.get("event_id"),
        "event_summary": event.get("summary"),
        "start_node_id": steps[0]["source_node_id"] if steps else final_node,
        "final_node_id": final_node,
        "final_node_label": final.get("label") or final_node,
        "final_node_type": final.get("node_type"),
        "chain_codes": final.get("chain_codes") or [],
        "hop_count": len(steps),
        "path_signal": round(_clip(signal, -1.0, 1.0), 4),
        "path_confidence": round(_clip(confidence, 0.0, 1.0), 4),
        "steps": steps,
    }


def _aggregate_activations(paths: list[dict[str, Any]], nodes: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    weighted_signal: dict[str, float] = defaultdict(float)
    weight_sum: dict[str, float] = defaultdict(float)
    event_ids: dict[str, set[str]] = defaultdict(set)
    for path in paths:
        node_id = clean_text(path.get("final_node_id"))
        confidence = float(path.get("path_confidence") or 0.0)
        weighted_signal[node_id] += float(path.get("path_signal") or 0.0) * confidence
        weight_sum[node_id] += confidence
        if path.get("event_id"):
            event_ids[node_id].add(str(path["event_id"]))
    rows = []
    for node_id, total in weighted_signal.items():
        node = nodes.get(node_id, {})
        denominator = weight_sum[node_id] or 1.0
        score = total / denominator
        rows.append(
            {
                "node_id": node_id,
                "label": node.get("label") or node_id,
                "node_type": node.get("node_type"),
                "chain_codes": node.get("chain_codes") or [],
                "activation_score": round(_clip(score, -1.0, 1.0), 4),
                "confidence_weight": round(_clip(denominator / max(1, len(event_ids[node_id])), 0.0, 1.0), 4),
                "event_ids": sorted(event_ids[node_id]),
            }
        )
    return sorted(rows, key=lambda row: abs(float(row["activation_score"])), reverse=True)


def _event_start_node(event: dict[str, Any], graph: dict[str, Any]) -> str:
    code = clean_text(event.get("commodity_code")).upper()
    dimension_type = normalize_dimension_type(event.get("dimension_type"))
    for node in graph.get("nodes", []):
        if (
            isinstance(node, dict)
            and node.get("node_type") == "framework_dimension"
            and clean_text(node.get("commodity_code")).upper() == code
            and node.get("dimension_type") == dimension_type
        ):
            return clean_text(node.get("node_id"))
    for node in graph.get("nodes", []):
        if isinstance(node, dict) and node.get("node_type") == "asset" and clean_text(node.get("commodity_code")).upper() == code:
            return clean_text(node.get("node_id"))
    return ""


def _graph_stats(graph: dict[str, Any]) -> dict[str, Any]:
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    return {
        "chain_count": len(graph.get("chains", [])),
        "node_count": len(nodes),
        "edge_count": len(edges),
        "node_type_counts": dict(Counter(node.get("node_type") for node in nodes if isinstance(node, dict))),
        "edge_type_counts": dict(Counter(edge.get("edge_type") for edge in edges if isinstance(edge, dict))),
        "chain_node_counts": {
            chain["chain_code"]: sum(
                1 for node in nodes if isinstance(node, dict) and chain["chain_code"] in (node.get("chain_codes") or [])
            )
            for chain in graph.get("chains", [])
        },
    }


def _spec_for_chain(chain_code: str) -> dict[str, Any]:
    return next(spec for spec in CHAIN_SPECS if spec["chain_code"] == chain_code)


def _chain_name(chain_code: str) -> str:
    for spec in CHAIN_SPECS:
        if spec["chain_code"] == chain_code:
            return spec["chain_name"]
    return chain_code


def _dimension_type_label(dimension_type: str) -> str:
    return {
        "supply": "供给",
        "demand": "需求",
        "inventory": "库存",
        "cost": "成本利润",
        "policy": "政策",
        "macro": "宏观",
        "geopolitics": "地缘",
        "spread": "价差结构",
        "valuation": "估值情绪",
        "substitution": "替代关系",
        "weather": "天气",
        "logistics": "物流",
        "funding": "资金",
        "seasonality": "季节性",
        "disease": "疫病",
        "capacity_cycle": "产能周期",
        "supply_demand": "供需平衡",
    }.get(dimension_type, dimension_type)


def _dimension_layer(dimension_type: str) -> str:
    if dimension_type in {"supply", "cost", "inventory", "weather", "logistics", "disease", "capacity_cycle"}:
        return "upstream"
    if dimension_type in {"spread", "valuation", "macro", "policy", "geopolitics", "funding"}:
        return "supporting"
    if dimension_type in {"demand", "substitution"}:
        return "downstream"
    return "tracking"


def _dimension_weight(rows: list[dict[str, Any]]) -> float:
    if not rows:
        return 0.42
    values = []
    for row in rows:
        try:
            values.append(float(row.get("default_weight") or 0.1))
        except (TypeError, ValueError):
            values.append(0.1)
    return round(max(0.32, min(0.82, (sum(values) / len(values)) * 3.2)), 4)


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Build the initial commodity industry graph and run test propagation.")
    parser.add_argument("--quanta-root", help="Override GJ_QUANTA_DATA_ROOT.")
    parser.add_argument("--max-hops", type=int, default=4, help="Max hops for test propagation.")
    parser.add_argument("--no-test-events", action="store_true", help="Only build graph; skip test calculation.")
    args = parser.parse_args(argv)
    result = publish_commodity_industry_graph(
        args.quanta_root,
        max_hops=args.max_hops,
        include_test_events=not args.no_test_events,
    )
    stats = result["payload"]["stats"]
    calc_stats = result["payload"]["test_calculation"].get("stats", {})
    print(f"商品产业链图谱 → {result['graph']}")
    print(f"latest → {result['latest_graph']}")
    print(
        "chains={chain_count} nodes={node_count} edges={edge_count} test_paths={path_count}".format(
            path_count=calc_stats.get("path_count", 0),
            **stats,
        )
    )


if __name__ == "__main__":
    main()
