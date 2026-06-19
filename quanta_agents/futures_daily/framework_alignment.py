from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from quanta_agents.core.config import quanta_data_root
from quanta_agents.core.analysis_framework import latest_analysis_framework_refs
from quanta_agents.core.frameworks import iter_leaf_nodes, load_framework_for_asset
from quanta_agents.core.io import dated_parts, read_json, relative_to_root, utc_now_iso, write_json
from quanta_agents.core.llm_client import chat
from quanta_agents.core.llm_json import parse_json_object
from quanta_agents.core.taxonomy import AssetTaxonomy, load_asset_taxonomy


FACTOR_FIELDS = {
    "bullish_factors": "bullish",
    "bearish_factors": "bearish",
    "key_data": "neutral",
    "key_events": "event",
    "supply_demand": "neutral",
    "price_forecast": "forecast",
}

DEFAULT_DIMENSIONS = [
    ("宏观", ["美元", "利率", "通胀", "美联储", "PMI", "社融", "政策", "汇率"]),
    ("供给", ["供应", "产量", "减产", "增产", "检修", "进口", "出口", "库存", "开工"]),
    ("需求", ["需求", "消费", "订单", "补库", "去库", "终端", "地产", "基建"]),
    ("成本", ["成本", "利润", "煤", "电", "原油", "运费", "加工费"]),
    ("政策与事件", ["政策", "关税", "制裁", "地缘", "事故", "罢工", "停火", "战争"]),
    ("价格与交易", ["价格", "基差", "价差", "震荡", "趋势", "压力", "支撑"]),
]

DIMENSION_HINTS = {
    "supply": ["供给", "供应", "产量", "产能", "开工", "发运", "进口", "出口", "减产", "增产", "复产", "停产", "到港", "种植面积", "单产", "矿山", "油井", "钻机"],
    "demand": ["需求", "消费", "成交", "下游", "终端", "补货", "订单", "地产", "基建", "汽车", "家电", "饲料", "出行", "炼厂投料", "表需"],
    "inventory": ["库存", "累库", "去库", "仓单", "港口库存", "社会库存", "油厂库存", "EIA", "API", "浮仓", "库容"],
    "cost_profit": ["成本", "利润", "加工费", "冶炼", "压榨", "运费", "原料", "价差", "基差", "升贴水", "比价", "窗口"],
    "feedstock": ["原料", "成本", "进口利润", "压榨利润", "CNF", "运费", "汇率", "基差"],
    "geopolitics": ["地缘", "中东", "制裁", "海峡", "霍尔木兹", "冲突", "战争", "停火", "通航", "OPEC", "风险溢价"],
    "policy": ["政策", "监管", "关税", "国储", "收储", "抛储", "限产", "环保", "安监", "能耗", "财政", "专项债"],
    "policy_regulation": ["政策", "监管", "财政", "产业政策", "资本市场", "改革", "规则", "专项债"],
    "macro": ["宏观", "美元", "利率", "美联储", "通胀", "PMI", "社融", "M2", "汇率", "流动性", "风险偏好", "经济"],
    "monetary_liquidity": ["货币", "流动性", "利率", "央行", "降准", "降息", "社融", "M2", "MLF", "LPR", "SHIBOR", "DR007"],
    "macro_growth": ["经济", "增长", "GDP", "PMI", "工业增加值", "消费", "投资", "复苏", "衰退", "企业利润"],
    "valuation_sentiment": ["估值", "情绪", "风险偏好", "两融", "资金", "成交", "贴水", "升水", "ETF", "北向", "上涨", "下跌", "指数", "合约", "点位"],
    "global_capital_flow": ["美联储", "美元", "外资", "全球资金", "Risk-On", "Risk-Off", "地缘", "中美利差", "汇率"],
    "term_credit_structure": ["期限", "信用", "收益率", "曲线", "利差", "基差", "倒挂", "陡峭"],
    "spread": ["价差", "基差", "月差", "期限结构", "裂解", "EFS", "contango", "backwardation", "升贴水"],
    "weather": ["天气", "降水", "温度", "干旱", "洪涝", "霜冻", "墒情", "厄尔尼诺", "拉尼娜"],
    "cross_chain": ["产业链", "联动", "替代", "比价", "上下游", "利润转移", "传导"],
}


def _tokens(text: str) -> set[str]:
    chunks = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9_+-]{1,}", text)
    return {chunk.lower() for chunk in chunks if len(chunk.strip()) >= 2}


def _hash_id(prefix: str, *parts: str) -> str:
    raw = "||".join(parts)
    return f"{prefix}-{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:16].upper()}"


def _leaf_text(leaf: dict[str, Any]) -> str:
    values = [
        leaf.get("node_id"),
        leaf.get("name"),
        leaf.get("display"),
        leaf.get("dimension_type"),
        leaf.get("description"),
        leaf.get("prompt_hint"),
        "/".join(str(item) for item in leaf.get("path", []) if item),
    ]
    values.extend(leaf.get("typical_indicators", []) or [])
    values.extend(leaf.get("typical_events", []) or [])
    values.extend(leaf.get("graph_node_refs", []) or [])
    for indicator in leaf.get("indicators", []) or []:
        if isinstance(indicator, dict):
            values.extend([indicator.get("key"), indicator.get("display")])
            values.extend(indicator.get("variants", []) or [])
    for claim in leaf.get("claims", []) or []:
        if isinstance(claim, dict):
            values.append(claim.get("statement"))
    return " ".join(str(value) for value in values if value)


def _leaf_hint_keywords(leaf: dict[str, Any]) -> list[str]:
    keys = [
        str(leaf.get("dimension_type") or "").lower(),
        str(leaf.get("name") or "").lower(),
        str(leaf.get("display") or "").lower(),
        str(leaf.get("node_id") or "").lower(),
    ]
    hints: list[str] = []
    for key in keys:
        for hint_key, values in DIMENSION_HINTS.items():
            if hint_key in key or key in hint_key or any(str(value).lower() in key for value in values):
                hints.extend(values)
    hints.extend(str(item) for item in leaf.get("typical_indicators", []) or [])
    hints.extend(str(item) for item in leaf.get("typical_events", []) or [])
    return [item for item in dict.fromkeys(hints) if item]


def _dimension_hint_score(text: str, leaf: dict[str, Any]) -> float:
    low = text.lower()
    hits = 0
    for keyword in _leaf_hint_keywords(leaf):
        word = str(keyword).strip()
        if not word:
            continue
        if word in text or word.lower() in low:
            hits += 1
    if not hits:
        return 0.0
    return min(0.55, 0.16 + hits * 0.11)


def _default_node_match(text: str) -> tuple[str, str, float]:
    low = text.lower()
    best = ("未归类", "default::未归类", 0.2)
    for name, keywords in DEFAULT_DIMENSIONS:
        score = sum(1 for keyword in keywords if keyword.lower() in low)
        if score and score / max(len(keywords), 1) > best[2]:
            best = (name, f"default::{name}", min(0.65, 0.35 + score * 0.08))
    return best


def _framework_dimension_label(label: str) -> str:
    text = str(label or "").strip()
    if "/" in text:
        text = text.rsplit("/", 1)[-1].strip()
    return text or "未归类"


def _framework_leaf_for_default(name: str, leaves: list[dict[str, Any]]) -> dict[str, Any] | None:
    name_norm = _tokens(name)
    best_leaf = None
    best_score = 0
    for leaf in leaves:
        leaf_name = " ".join(
            str(value)
            for value in [leaf.get("name"), leaf.get("display"), leaf.get("dimension_type")]
            if value
        )
        score = len(name_norm & _tokens(leaf_name))
        if str(leaf.get("name") or "") == name:
            score += 2
        if score > best_score:
            best_leaf = leaf
            best_score = score
    return best_leaf


def _match_framework_node(text: str, leaves: list[dict[str, Any]]) -> tuple[str, str, float, str]:
    if not leaves:
        name, node_id, confidence = _default_node_match(text)
        return name, node_id, confidence, "default_dimension"

    factor_tokens = _tokens(text)
    best_leaf: dict[str, Any] | None = None
    best_score = 0.0
    for leaf in leaves:
        leaf_tokens = _tokens(_leaf_text(leaf))
        if not leaf_tokens:
            continue
        overlap = len(factor_tokens & leaf_tokens)
        lexical_score = overlap / max(min(len(factor_tokens), len(leaf_tokens)), 1)
        hint_score = _dimension_hint_score(text, leaf)
        score = lexical_score + hint_score
        if score > best_score:
            best_score = score
            best_leaf = leaf

    if best_leaf and best_score >= 0.08:
        node_id = str(best_leaf.get("node_id") or "")
        path = best_leaf.get("path")
        display = "/".join(path) if isinstance(path, list) else str(best_leaf.get("name") or node_id)
        confidence = min(0.92, 0.45 + best_score)
        method = "framework_dimension" if _dimension_hint_score(text, best_leaf) else "framework_leaf"
        return display, node_id, confidence, method

    name, _, confidence = _default_node_match(text)
    default_leaf = _framework_leaf_for_default(name, leaves)
    if default_leaf:
        node_id = str(default_leaf.get("node_id") or "")
        path = default_leaf.get("path")
        display = "/".join(path) if isinstance(path, list) else str(default_leaf.get("name") or node_id)
        return display, node_id, max(0.35, confidence), "framework_dimension_default"

    if not best_leaf:
        name, node_id, confidence = _default_node_match(text)
        return name, node_id, confidence, "default_dimension"

    node_id = str(best_leaf.get("node_id") or "")
    path = best_leaf.get("path")
    display = "/".join(path) if isinstance(path, list) else str(best_leaf.get("name") or node_id)
    confidence = min(0.92, 0.45 + best_score)
    return display, node_id, confidence, "framework_leaf"


def _leaf_label(leaf: dict[str, Any]) -> str:
    path = leaf.get("path")
    if isinstance(path, list) and path:
        return "/".join(str(item) for item in path if item)
    return str(leaf.get("name") or leaf.get("display") or leaf.get("node_id") or "")


def _leaf_prompt_row(leaf: dict[str, Any]) -> dict[str, Any]:
    return {
        "node_id": leaf.get("node_id"),
        "label": _leaf_label(leaf),
        "dimension_type": leaf.get("dimension_type"),
        "description": leaf.get("description"),
        "prompt_hint": leaf.get("prompt_hint"),
        "typical_indicators": (leaf.get("typical_indicators") or [])[:12],
        "typical_events": (leaf.get("typical_events") or [])[:12],
    }


def _llm_refine_asset_links(asset: str, links: list[dict[str, Any]], leaves: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not links or not leaves:
        return links
    leaf_by_id = {str(leaf.get("node_id") or ""): leaf for leaf in leaves if leaf.get("node_id")}
    factors = [
        {
            "factor_id": link.get("factor_id"),
            "source_field": link.get("source_field"),
            "extracted_direction": link.get("direction"),
            "text": link.get("text"),
            "rule_node_id": (link.get("framework_node") or {}).get("node_id"),
            "rule_label": (link.get("framework_node") or {}).get("label"),
            "rule_confidence": (link.get("match") or {}).get("confidence"),
        }
        for link in links
    ]
    prompt = (
        "你是商品期货研究框架映射 Agent。请基于品种分析框架，把研报抽取因素映射到最合适的框架维度。\n"
        "关键词匹配只是候选，请你综合语义、因果链、指标口径和字段含义做判断。\n"
        "严格输出 JSON，不要 Markdown，格式：\n"
        '{"mappings":[{"factor_id":"...","node_id":"框架node_id或default::未归类",'
        '"confidence":0.0,"impact_direction":"bullish|bearish|neutral|event|forecast",'
        '"reason":"一句话说明为什么这样映射"}]}\n\n'
        "规则：\n"
        "1. node_id 必须来自 framework_dimensions；如果都不合适，使用 default::未归类。\n"
        "2. confidence 表示“映射到该维度”的置信度，不是事实真伪置信度。\n"
        "3. 行情/技术位/盘面表现若没有基本面含义，应映射到价格与交易或 default::未归类，不要误映射到供需。\n"
        "4. key_data/supply_demand 中的中性文本也要判断其对基本面的实际影响方向，写入 impact_direction。\n\n"
        + json.dumps(
            {
                "asset": asset,
                "framework_dimensions": [_leaf_prompt_row(leaf) for leaf in leaves],
                "factors": factors,
            },
            ensure_ascii=False,
        )
    )
    try:
        parsed = parse_json_object(chat(prompt, max_tokens=3200, temperature=0.15, timeout=120))
    except Exception as exc:
        for link in links:
            link["llm_refinement"] = {"status": "failed", "error": str(exc)}
        return links

    mapping_rows = parsed.get("mappings") if isinstance(parsed.get("mappings"), list) else []
    by_factor = {
        str(item.get("factor_id")): item
        for item in mapping_rows
        if isinstance(item, dict) and item.get("factor_id")
    }
    for link in links:
        item = by_factor.get(str(link.get("factor_id")))
        if not item:
            continue
        previous_match = link.get("match") or {}
        node_id = str(item.get("node_id") or "").strip()
        try:
            confidence = max(0.0, min(0.98, float(item.get("confidence"))))
        except (TypeError, ValueError):
            confidence = float(previous_match.get("confidence") or 0.2)
        if node_id in leaf_by_id:
            leaf = leaf_by_id[node_id]
            node_label = _leaf_label(leaf)
            method = "llm_framework_dimension"
        else:
            node_id = "default::未归类"
            node_label = "未归类"
            method = "llm_unmapped"
            confidence = min(confidence, 0.45)
        impact = str(item.get("impact_direction") or "").strip()
        if impact in {"bullish", "bearish", "neutral", "event", "forecast"}:
            link["llm_impact_direction"] = impact
        link["framework_node"] = {"node_id": node_id, "label": node_label}
        link["match"] = {
            "method": method,
            "confidence": round(confidence, 3),
            "rule_method": previous_match.get("method"),
            "rule_confidence": previous_match.get("confidence"),
        }
        link["llm_refinement"] = {
            "status": "succeeded",
            "reason": str(item.get("reason") or ""),
        }
        link["status"] = "auto_linked" if confidence >= 0.7 else "pending_review"
    return links


def _factor_cluster_key(
    text: str,
    taxonomy: AssetTaxonomy,
    *,
    framework_node: tuple[str, str] | None = None,
    match_method: str = "",
) -> tuple[str, str]:
    hits = [hit for hit in taxonomy.classify(text) if hit.kind == "macro"]
    if hits:
        return hits[0].key, hits[0].label
    if framework_node and match_method.startswith("framework"):
        node_id, label = framework_node
        dimension_label = _framework_dimension_label(label)
        if dimension_label != "未归类":
            return f"D::{dimension_label}", dimension_label
    name, _, _ = _default_node_match(text)
    return f"D::{name}", name


def build_framework_alignment(
    summary: dict[str, Any],
    root: str | Path | None = None,
    *,
    use_llm_refinement: bool = False,
) -> dict[str, Any]:
    taxonomy = load_asset_taxonomy(root)
    report_date = str(summary.get("date") or "")
    details = summary.get("detailed_analysis") or {}
    alignments: list[dict[str, Any]] = []
    links_by_asset: dict[str, list[dict[str, Any]]] = defaultdict(list)
    leaves_by_asset: dict[str, list[dict[str, Any]]] = {}

    for commodity, payload in details.items():
        if commodity == "_metadata" or not isinstance(payload, dict):
            continue
        asset_name = taxonomy.canonical_name(str(commodity)) or str(commodity)
        asset = next(
            (item for item in taxonomy.assets if item.get("canonical_name") == asset_name),
            {"canonical_name": asset_name},
        )
        framework = load_framework_for_asset(asset, root)
        leaves = iter_leaf_nodes(framework) if framework else []
        framework_ref = {
            "framework_id": framework.get("framework_id") if framework else "",
            "asset_id": framework.get("asset_id") if framework else "",
            "source_path": framework.get("_source_path") if framework else "",
        }
        leaves_by_asset[asset_name] = leaves

        for field, direction in FACTOR_FIELDS.items():
            values = payload.get(field) or []
            if not isinstance(values, list):
                continue
            for raw in values:
                text = str(raw).strip()
                if not text:
                    continue
                node_label, node_id, confidence, method = _match_framework_node(text, leaves)
                factor_id = _hash_id("FACTOR", asset_name, field, text)
                effect = "support" if direction == "bullish" else "refute" if direction == "bearish" else "neutral"
                link = {
                    "factor_id": factor_id,
                    "asset": asset_name,
                    "asset_id": asset.get("asset_id"),
                    "source_field": field,
                    "direction": direction,
                    "text": text,
                    "framework_node": {"node_id": node_id, "label": node_label},
                    "framework": framework_ref,
                    "effect": effect,
                    "match": {"method": method, "confidence": round(confidence, 3)},
                    "status": "auto_linked" if confidence >= 0.7 else "pending_review",
                }
                links_by_asset[asset_name].append(link)

    for asset_name, links in sorted(links_by_asset.items()):
        final_links = (
            _llm_refine_asset_links(asset_name, links, leaves_by_asset.get(asset_name, []))
            if use_llm_refinement
            else links
        )
        alignments.extend(final_links)

    clusters: dict[str, dict[str, Any]] = {}
    timeline_events: list[dict[str, Any]] = []
    for link in alignments:
        asset_name = str(link.get("asset") or "")
        direction = str(link.get("llm_impact_direction") or link.get("direction") or "neutral")
        text = str(link.get("text") or "")
        node = link.get("framework_node") or {}
        match = link.get("match") or {}
        cluster_key, cluster_label = _factor_cluster_key(
            text,
            taxonomy,
            framework_node=(str(node.get("node_id") or ""), str(node.get("label") or "")),
            match_method=str(match.get("method") or ""),
        )
        cluster = clusters.setdefault(
            cluster_key,
            {
                "cluster_id": _hash_id("FCL", cluster_key),
                "label": cluster_label,
                "kind": "macro" if cluster_key.startswith("M::") else "dimension",
                "factors": [],
                "assets": {},
            },
        )
        factor_id = str(link.get("factor_id") or "")
        cluster["factors"].append(factor_id)
        cluster["assets"].setdefault(asset_name, {"bullish": 0, "bearish": 0, "neutral": 0, "event": 0, "forecast": 0})
        cluster["assets"][asset_name][direction] = cluster["assets"][asset_name].get(direction, 0) + 1
        timeline_events.append(
            {
                "date": report_date,
                "event_id": _hash_id("LOGIC", report_date, factor_id),
                "cluster_id": cluster["cluster_id"],
                "asset": asset_name,
                "direction": direction,
                "text": text,
                "source": "commodity_summary",
            }
        )

    cluster_list = []
    for cluster in clusters.values():
        assets = cluster.pop("assets")
        exposures = []
        for asset, counts in sorted(assets.items()):
            net = counts["bullish"] - counts["bearish"]
            exposures.append({"asset": asset, "net_direction": net, "counts": counts})
        cluster["asset_exposures"] = exposures
        longs = [item["asset"] for item in exposures if item["net_direction"] > 0]
        shorts = [item["asset"] for item in exposures if item["net_direction"] < 0]
        cluster["hedge_candidates"] = [
            {"long": long_asset, "short": short_asset, "rationale": "same_factor_opposite_exposure"}
            for long_asset in longs[:5]
            for short_asset in shorts[:5]
        ]
        cluster_list.append(cluster)

    frameworks_used = {}
    for link in alignments:
        framework = link.get("framework") or {}
        asset_name = str(link.get("asset") or "")
        if not asset_name or not framework.get("framework_id"):
            continue
        frameworks_used[asset_name] = framework

    return {
        "schema_version": "framework_alignment.v1",
        "status": "candidate",
        "report_date": report_date,
        "generated_at": utc_now_iso(),
        "taxonomy_source": str(taxonomy.source_path) if taxonomy.source_path else "",
        "analysis_framework_ref": latest_analysis_framework_refs(root),
        "summary_ref": {
            "title": summary.get("title"),
            "analysis_date": summary.get("analysis_date"),
            "source_report_hash": summary.get("source_report_hash"),
        },
        "mapping_method": "rule_candidates_plus_llm_refinement" if use_llm_refinement else "rule_candidates",
        "frameworks_used": frameworks_used,
        "alignments": alignments,
        "factor_clusters": sorted(cluster_list, key=lambda item: len(item["factors"]), reverse=True),
        "logic_timeline_events": timeline_events,
    }


def publish_framework_alignment(
    summary_path: str | Path,
    root: str | Path | None = None,
    *,
    use_llm_refinement: bool = True,
) -> dict[str, str]:
    root_path = quanta_data_root(root)
    summary_file = Path(summary_path).expanduser()
    summary = read_json(summary_file)
    result = build_framework_alignment(summary, root_path, use_llm_refinement=use_llm_refinement)
    date_key = str(result.get("report_date") or "")
    yyyy, mm, dd = dated_parts(date_key)
    base = root_path / "agent_workspace" / "candidates"
    alignment_dir = base / "framework_alignment" / yyyy / mm / dd
    cluster_dir = base / "factor_clusters" / yyyy / mm / dd
    timeline_dir = base / "logic_timeline" / yyyy / mm / dd
    alignment_path = alignment_dir / f"ALIGN-{date_key}-COMMODITY.json"
    cluster_path = cluster_dir / f"FCL-{date_key}-COMMODITY.json"
    timeline_path = timeline_dir / f"LOGIC-TIMELINE-{date_key}-COMMODITY.json"
    write_json(alignment_path, result)
    write_json(
        cluster_path,
        {
            "schema_version": "factor_clusters.v1",
            "status": "candidate",
            "report_date": date_key,
            "generated_at": result["generated_at"],
            "clusters": result["factor_clusters"],
        },
    )
    write_json(
        timeline_path,
        {
            "schema_version": "logic_timeline_events.v1",
            "status": "candidate",
            "report_date": date_key,
            "generated_at": result["generated_at"],
            "events": result["logic_timeline_events"],
        },
    )
    return {
        "alignment": relative_to_root(alignment_path, root_path),
        "factor_clusters": relative_to_root(cluster_path, root_path),
        "logic_timeline": relative_to_root(timeline_path, root_path),
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Align futures daily extraction to frameworks.")
    parser.add_argument("--summary", required=True, help="Path to *_commodity_summary.json")
    parser.add_argument("--quanta-root", help="Override GJ_QUANTA_DATA_ROOT.")
    parser.add_argument("--no-llm-refinement", action="store_true", help="Use rule-only framework mapping.")
    args = parser.parse_args(argv)
    paths = publish_framework_alignment(
        args.summary,
        args.quanta_root,
        use_llm_refinement=not args.no_llm_refinement,
    )
    print("框架对齐候选已生成：")
    for role, path in paths.items():
        print(f"{role} → {path}")


if __name__ == "__main__":
    main()
