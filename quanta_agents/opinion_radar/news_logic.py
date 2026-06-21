from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from quanta_agents.core.analysis_framework import latest_analysis_framework_refs
from quanta_agents.core.config import quanta_data_root
from quanta_agents.core.frameworks import iter_leaf_nodes, load_framework_for_asset
from quanta_agents.core.io import dated_parts, read_json, utc_now_iso, write_json
from quanta_agents.core.llm_client import chat
from quanta_agents.core.llm_json import parse_json_object
from quanta_agents.core.taxonomy import load_asset_taxonomy
from quanta_agents.adapters import register_payload_if_enabled
from quanta_agents.futures_daily.framework_alignment import _match_framework_node
from quanta_agents.signal_mapping.theme_anchor_matcher import (
    ThemeAnchorIndex,
    load_theme_anchor_index,
    theme_anchor_ref,
)

from . import db, filters


POSITIVE_WORDS = (
    "上涨",
    "涨超",
    "走强",
    "提振",
    "支撑",
    "减产",
    "去库",
    "短缺",
    "封锁",
    "中断",
    "制裁",
    "限产",
    "降息",
    "宽松",
    "鸽派",
    "收益率下行",
    "美元走弱",
    "美元指数回落",
    "安全检查趋严",
    "复产偏慢",
    "供应收缩",
)

NEGATIVE_WORDS = (
    "下跌",
    "跌超",
    "走弱",
    "压制",
    "累库",
    "增产",
    "过剩",
    "疲弱",
    "复航",
    "重开",
    "恢复通行",
    "停火",
    "加息",
    "收紧",
    "鹰派",
    "收益率走高",
    "收益率上行",
    "美元走强",
    "美元指数拉升",
    "加息赔率回升",
    "加息预期",
    "风险溢价回吐",
    "局势缓和",
    "供应担忧下降",
)


def _hash_id(prefix: str, *parts: str) -> str:
    raw = "||".join(str(part) for part in parts)
    return f"{prefix}-{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:16].upper()}"


def _display_text(flash: dict[str, Any]) -> str:
    text = f"{flash.get('title') or ''} {flash.get('content') or ''}".strip()
    text = re.sub(r"<[^>]+>", "", text).replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", text).strip()


def _flash_time(flash: dict[str, Any]) -> str:
    value = flash.get("publish_time") or flash.get("time") or flash.get("created_at") or ""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    return str(value)


def _flash_key(flash: dict[str, Any], text: str) -> str:
    return str(flash.get("flash_id") or flash.get("id") or _hash_id("NEWS", _flash_time(flash), text))


def _news_direction(text: str) -> float:
    positive = sum(1 for word in POSITIVE_WORDS if word in text)
    negative = sum(1 for word in NEGATIVE_WORDS if word in text)
    if positive == negative:
        return 0.0
    return 1.0 if positive > negative else -1.0


def _dimension_label(label: str) -> str:
    text = str(label or "").strip()
    if "/" in text:
        return text.rsplit("/", 1)[-1].strip()
    return text or "未归类"


def _latest_logic_run(root: Path, date_key: str | None = None) -> Path | None:
    base = root / "agent_workspace" / "candidates" / "futures_daily_raw_runs"
    if date_key:
        yyyy, mm, dd = dated_parts(date_key)
        candidates = sorted((base / yyyy / mm / dd).glob("RUN-*-RAW-DAILY"))
    else:
        candidates = sorted(base.glob("*/*/*/RUN-*-RAW-DAILY"))
    candidates = [
        path
        for path in candidates
        if path.is_dir() and (path / "trade_thesis.json").exists() and (path / "dimension_scores.json").exists()
    ]
    return candidates[-1] if candidates else None


def _load_logic_context(root: Path, logic_run: str | Path | None, date_key: str | None) -> dict[str, Any]:
    run_path = Path(logic_run).expanduser() if logic_run else _latest_logic_run(root, date_key)
    if not run_path:
        return {"run_dir": "", "trade_thesis": {}, "dimension_scores": {}}
    return {
        "run_dir": str(run_path),
        "trade_thesis": read_json(run_path / "trade_thesis.json"),
        "dimension_scores": read_json(run_path / "dimension_scores.json"),
    }


def _asset_framework_map(text: str, root: str | Path | None = None) -> list[dict[str, Any]]:
    taxonomy = load_asset_taxonomy(root)
    variety_hits = [hit for hit in taxonomy.classify(text) if hit.kind == "variety"]
    out = []
    for hit in variety_hits:
        asset = next(
            (item for item in taxonomy.assets if item.get("canonical_name") == hit.label),
            {"canonical_name": hit.label},
        )
        framework = load_framework_for_asset(asset, root)
        leaves = iter_leaf_nodes(framework) if framework else []
        if leaves:
            node_label, node_id, confidence, method = _match_framework_node(text, leaves)
        else:
            node_label, node_id, confidence, method = "未归类", "default::未归类", 0.2, "no_framework"
        out.append(
            {
                "asset": hit.label,
                "asset_id": hit.asset_id,
                "framework": {
                    "framework_id": framework.get("framework_id") if framework else "",
                    "asset_id": framework.get("asset_id") if framework else "",
                    "source_path": framework.get("_source_path") if framework else "",
                },
                "framework_node": {"node_id": node_id, "label": node_label, "dimension_label": _dimension_label(node_label)},
                "match": {"method": method, "confidence": round(confidence, 3)},
            }
        )
    return out


def _event_theme_anchor(
    text: str,
    mapped: dict[str, Any],
    theme_index: ThemeAnchorIndex,
) -> dict[str, Any]:
    framework_node = mapped.get("framework_node") or {}
    match = theme_index.best_match(
        " ".join(
            str(item or "")
            for item in [
                mapped.get("asset"),
                mapped.get("asset_id"),
                framework_node.get("dimension_label"),
                framework_node.get("label"),
                text,
            ]
        ),
        asset_labels=[
            str(mapped.get("asset") or ""),
            str(mapped.get("asset_id") or ""),
            str(framework_node.get("dimension_label") or ""),
        ],
    )
    if not match:
        return {
            "theme_anchor_refs": [],
            "theme_anchor_match_method": "no_theme_anchor_match",
            "anchoring_status": "unanchored_theme_candidate",
            "theme_type": "",
            "matched_theme_title": "",
        }
    return {
        "theme_anchor_refs": [theme_anchor_ref(match)],
        "theme_anchor_match_method": match["match_method"],
        "anchoring_status": "anchored_theme",
        "theme_type": match.get("theme_type") or "",
        "matched_theme_title": match.get("title") or "",
    }


def _dedupe_theme_refs(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    refs = []
    for event in events:
        for ref in event.get("theme_anchor_refs") or []:
            if not isinstance(ref, dict):
                continue
            ref_id = str(ref.get("id") or "")
            if not ref_id or ref_id in seen:
                continue
            seen.add(ref_id)
            refs.append(ref)
    return refs


def _anchoring_status(events: list[dict[str, Any]]) -> str:
    return "anchored_theme" if _dedupe_theme_refs(events) else "unanchored_theme_candidate"


def map_flash_to_events(
    flash: dict[str, Any],
    root: str | Path | None = None,
    *,
    theme_anchor_index: ThemeAnchorIndex | None = None,
) -> list[dict[str, Any]]:
    text = _display_text(flash)
    if not text:
        return []
    direction = _news_direction(text)
    important = int(flash.get("important") or 0)
    flash_id = _flash_key(flash, text)
    events = []
    theme_index = theme_anchor_index or load_theme_anchor_index(root)
    for mapped in _asset_framework_map(text, root):
        confidence = float(mapped["match"]["confidence"])
        anchor = _event_theme_anchor(text, mapped, theme_index)
        events.append(
            {
                "event_id": _hash_id("NLOGIC", flash_id, mapped["asset"], mapped["framework_node"]["node_id"]),
                "flash_id": flash_id,
                "asset": mapped["asset"],
                "asset_id": mapped.get("asset_id"),
                "publish_time": _flash_time(flash),
                "text": text,
                "url": flash.get("url"),
                "channel": flash.get("channel"),
                "important": important,
                "direction_score": direction,
                "heat": round((1.0 + important) * max(confidence, 0.25), 3),
                "framework": mapped["framework"],
                "framework_node": mapped["framework_node"],
                "match": mapped["match"],
                "theme_anchor_refs": anchor["theme_anchor_refs"],
                "theme_anchor_match_method": anchor["theme_anchor_match_method"],
                "anchoring_status": anchor["anchoring_status"],
                "theme_type": anchor["theme_type"],
                "matched_theme_title": anchor["matched_theme_title"],
            }
        )
    return events


def _asset_context(logic_context: dict[str, Any], asset: str) -> dict[str, Any]:
    thesis = ((logic_context.get("trade_thesis") or {}).get("assets") or {}).get(asset) or {}
    scores = ((logic_context.get("dimension_scores") or {}).get("assets") or {}).get(asset) or {}
    dim_scores = {
        str(row.get("dimension_label")): row
        for row in scores.get("dimensions") or []
        if isinstance(row, dict)
    }
    return {"thesis": thesis, "scores": scores, "dimension_scores": dim_scores}


def _consistency(event: dict[str, Any], logic_context: dict[str, Any]) -> dict[str, Any]:
    ctx = _asset_context(logic_context, event["asset"])
    thesis = ctx["thesis"]
    if not thesis:
        return {"status": "new_signal", "reason": "该品种暂无研报交易主线"}
    framework_score = float(thesis.get("framework_score") or 0)
    news_score = float(event.get("direction_score") or 0)
    dim_label = event["framework_node"]["dimension_label"]
    dim_score = ctx["dimension_scores"].get(dim_label)
    dim_direction = float((dim_score or {}).get("direction_score") or 0)
    if news_score == 0:
        return {"status": "tracking", "reason": "新闻方向中性，用于跟踪事件进展"}
    if dim_direction and dim_direction * news_score < 0:
        return {"status": "dimension_conflict", "reason": f"新闻方向与研报{dim_label}维度相反"}
    if framework_score and framework_score * news_score < 0:
        return {"status": "thesis_conflict", "reason": "新闻方向与研报主线相反"}
    return {"status": "supports_thesis", "reason": "新闻方向与研报主线或维度方向一致"}


def _event_brief(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": event.get("event_id"),
        "publish_time": event.get("publish_time"),
        "dimension": (event.get("framework_node") or {}).get("dimension_label"),
        "text": str(event.get("text") or "")[:260],
        "rule_direction_score": event.get("direction_score"),
        "heat": event.get("heat"),
        "rule_consistency": event.get("consistency"),
    }


def _llm_asset_news_assessment(asset: str, payload: dict[str, Any]) -> dict[str, Any]:
    thesis = payload.get("linked_trade_thesis") or {}
    dimensions = [
        {
            "dimension_label": item.get("dimension_label"),
            "event_count": item.get("event_count"),
            "news_direction_score": item.get("news_direction_score"),
            "consistency_counts": item.get("consistency_counts"),
            "top_events": [_event_brief(event) for event in (item.get("top_events") or [])[:4]],
        }
        for item in (payload.get("dimensions") or [])[:8]
    ]
    prompt = (
        "你是商品期货舆情雷达 Agent。请基于新闻快讯和研报主线，判断新闻对该品种逻辑的影响。\n"
        "关键词方向和规则一致性只是参考，请以新闻语义、框架维度、研报主线为准。\n"
        "严格输出 JSON，不要 Markdown，格式：\n"
        '{"relation_to_report":"supports|conflicts|mixed|new_signal|tracking",'
        '"news_summary":"新闻在交易什么",'
        '"logic_impact":"对研报主线的影响",'
        '"updated_bias":"偏多|偏空|中性|不确定",'
        '"key_confirmations":["确认点"],'
        '"key_conflicts":["冲突点"],'
        '"new_variables":["新增变量"],'
        '"tracking_points":["后续跟踪"],'
        '"quality_notes":["口径/噪音/需复核点"]}\n\n'
        "要求：\n"
        "1. 不要把行情涨跌本身当作基本面确认，除非新闻给出供需、政策、库存、利率等原因。\n"
        "2. 如果新闻只是复述盘面，relation_to_report 用 tracking。\n"
        "3. 如果研报没有该品种主线，但新闻明显重要，用 new_signal。\n"
        "4. 对同一事件的多条快讯要合并看，不要重复计数。\n\n"
        + json.dumps(
            {
                "asset": asset,
                "report_thesis": {
                    "recommendation": thesis.get("recommendation"),
                    "framework_score": thesis.get("framework_score"),
                    "main_trade_thesis": thesis.get("main_trade_thesis"),
                    "core_drivers": (thesis.get("thesis_generation") or {}).get("core_drivers"),
                    "constraints": (thesis.get("thesis_generation") or {}).get("constraints"),
                    "tracking_points": (thesis.get("thesis_generation") or {}).get("tracking_points"),
                },
                "news_stats": {
                    "event_count": payload.get("event_count"),
                    "heat": payload.get("heat"),
                    "news_direction_score": payload.get("news_direction_score"),
                    "rule_consistency_counts": payload.get("consistency_counts"),
                },
                "news_dimensions": dimensions,
            },
            ensure_ascii=False,
        )
    )
    try:
        parsed = parse_json_object(chat(prompt, max_tokens=2200, temperature=0.2, timeout=120))
        relation = str(parsed.get("relation_to_report") or "tracking")
        if relation not in {"supports", "conflicts", "mixed", "new_signal", "tracking"}:
            relation = "tracking"
        return {
            "method": "llm",
            "relation_to_report": relation,
            "news_summary": str(parsed.get("news_summary") or ""),
            "logic_impact": str(parsed.get("logic_impact") or ""),
            "updated_bias": str(parsed.get("updated_bias") or ""),
            "key_confirmations": parsed.get("key_confirmations") if isinstance(parsed.get("key_confirmations"), list) else [],
            "key_conflicts": parsed.get("key_conflicts") if isinstance(parsed.get("key_conflicts"), list) else [],
            "new_variables": parsed.get("new_variables") if isinstance(parsed.get("new_variables"), list) else [],
            "tracking_points": parsed.get("tracking_points") if isinstance(parsed.get("tracking_points"), list) else [],
            "quality_notes": parsed.get("quality_notes") if isinstance(parsed.get("quality_notes"), list) else [],
        }
    except Exception as exc:
        return {
            "method": "failed",
            "relation_to_report": "tracking",
            "news_summary": "",
            "logic_impact": "",
            "updated_bias": "",
            "key_confirmations": [],
            "key_conflicts": [],
            "new_variables": [],
            "tracking_points": [],
            "quality_notes": [f"LLM新闻研判失败：{exc}"],
        }


def build_news_logic_radar(
    flashes: list[dict[str, Any]],
    root: str | Path | None = None,
    *,
    logic_run: str | Path | None = None,
    date_key: str | None = None,
    theme_anchor_path: str | Path | None = None,
    use_llm_assessment: bool = False,
    llm_asset_limit: int = 80,
) -> dict[str, Any]:
    root_path = quanta_data_root(root)
    logic_context = _load_logic_context(root_path, logic_run, date_key)
    theme_index = load_theme_anchor_index(root_path, candidate_path=theme_anchor_path, date_key=date_key)
    events = []
    for flash in flashes:
        if not filters.keep_flash(flash):
            continue
        for event in map_flash_to_events(flash, root_path, theme_anchor_index=theme_index):
            event["consistency"] = _consistency(event, logic_context)
            events.append(event)

    by_asset: dict[str, Any] = {}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        grouped[event["asset"]].append(event)

    for asset, asset_events in grouped.items():
        dimension_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for event in asset_events:
            dimension_groups[event["framework_node"]["dimension_label"]].append(event)
        dimensions = []
        for label, dim_events in dimension_groups.items():
            heat = sum(float(event["heat"]) for event in dim_events)
            directional = sum(float(event["direction_score"]) * float(event["heat"]) for event in dim_events)
            dimensions.append(
                {
                    "dimension_label": label,
                    "event_count": len(dim_events),
                    "important_count": sum(int(event["important"]) for event in dim_events),
                    "heat": round(heat, 3),
                    "news_direction_score": round(directional / max(heat, 1e-6), 3),
                    "consistency_counts": dict(Counter(event["consistency"]["status"] for event in dim_events)),
                    "theme_anchor_refs": _dedupe_theme_refs(dim_events),
                    "anchoring_status": _anchoring_status(dim_events),
                    "theme_anchor_match_method": "event_theme_anchor_rollup",
                    "top_events": sorted(dim_events, key=lambda item: item["heat"], reverse=True)[:8],
                }
            )
        heat = sum(float(event["heat"]) for event in asset_events)
        by_asset[asset] = {
            "asset": asset,
            "event_count": len(asset_events),
            "important_count": sum(int(event["important"]) for event in asset_events),
            "heat": round(heat, 3),
            "news_direction_score": round(
                sum(float(event["direction_score"]) * float(event["heat"]) for event in asset_events) / max(heat, 1e-6),
                3,
            ),
            "consistency_counts": dict(Counter(event["consistency"]["status"] for event in asset_events)),
            "theme_anchor_refs": _dedupe_theme_refs(asset_events),
            "anchoring_status": _anchoring_status(asset_events),
            "theme_anchor_match_method": "event_theme_anchor_rollup",
            "linked_trade_thesis": _asset_context(logic_context, asset)["thesis"],
            "dimensions": sorted(dimensions, key=lambda item: item["heat"], reverse=True),
        }

    if use_llm_assessment:
        ranked_assets = sorted(by_asset.items(), key=lambda item: float(item[1].get("heat") or 0), reverse=True)
        for index, (asset, payload) in enumerate(ranked_assets):
            if index >= llm_asset_limit:
                payload["llm_assessment"] = {"method": "skipped_limit", "relation_to_report": "tracking"}
                continue
            payload["llm_assessment"] = _llm_asset_news_assessment(asset, payload)

    llm_assessments = [
        payload.get("llm_assessment")
        for payload in by_asset.values()
        if isinstance(payload.get("llm_assessment"), dict)
    ]
    return {
        "schema_version": "news_logic_radar.v1",
        "status": "candidate",
        "generated_at": utc_now_iso(),
        "logic_context": {"run_dir": logic_context.get("run_dir", "")},
        "analysis_framework_ref": latest_analysis_framework_refs(root_path),
        "theme_anchor_context": {
            "candidate_ids": theme_index.candidate_ids,
            "source_paths": theme_index.source_paths,
            "anchor_count": theme_index.anchor_count,
            "policy": "candidate_theme_anchors_are_review_required_not_gold",
        },
        "semantic_layer": {
            "asset_assessment_method": "llm" if use_llm_assessment else "rule_only",
            "llm_asset_limit": llm_asset_limit if use_llm_assessment else 0,
            "theme_anchor_matching": "theme_anchor_first_then_unanchored_fallback",
        },
        "stats": {
            "flash_count": len(flashes),
            "kept_flash_count": sum(1 for flash in flashes if filters.keep_flash(flash)),
            "mapped_event_count": len(events),
            "asset_count": len(by_asset),
            "framework_mapped_count": sum(1 for event in events if event["framework"].get("framework_id")),
            "theme_anchor_source_count": theme_index.anchor_count,
            "theme_anchor_match_count": sum(1 for event in events if event.get("theme_anchor_refs")),
            "unanchored_event_count": sum(1 for event in events if not event.get("theme_anchor_refs")),
            "consistency_counts": dict(Counter(event["consistency"]["status"] for event in events)),
            "llm_assessment_count": sum(1 for item in llm_assessments if item.get("method") == "llm"),
            "llm_relation_counts": dict(Counter(str(item.get("relation_to_report") or "") for item in llm_assessments)),
        },
        "assets": dict(sorted(by_asset.items(), key=lambda item: item[1]["heat"], reverse=True)),
        "events": sorted(events, key=lambda item: (item.get("publish_time") or "", item["heat"]), reverse=True),
    }


def fetch_flashes_for_window(date: str | None = None, hours: int = 24) -> list[dict[str, Any]]:
    if date:
        start = datetime.strptime(date, "%Y-%m-%d")
        end = start + timedelta(days=1)
    else:
        end = db.latest_time() or datetime.now()
        start = end - timedelta(hours=hours)
    return db.fetch_flashes(start, end)


def publish_news_logic_radar(
    root: str | Path | None = None,
    *,
    date: str | None = None,
    hours: int = 24,
    logic_run: str | Path | None = None,
    theme_anchor_path: str | Path | None = None,
    flashes: list[dict[str, Any]] | None = None,
    use_llm_assessment: bool = True,
    llm_asset_limit: int = 80,
) -> dict[str, Any]:
    root_path = quanta_data_root(root)
    date_key = (date or datetime.now().strftime("%Y-%m-%d")).replace("-", "")
    yyyy, mm, dd = dated_parts(date_key)
    raw_flashes = flashes if flashes is not None else fetch_flashes_for_window(date, hours)
    logic_date_key = date_key if date else None
    payload = build_news_logic_radar(
        raw_flashes,
        root_path,
        logic_run=logic_run,
        date_key=logic_date_key,
        theme_anchor_path=theme_anchor_path,
        use_llm_assessment=use_llm_assessment,
        llm_asset_limit=llm_asset_limit,
    )
    stamp = datetime.now().strftime("%H%M%S")
    base = root_path / "agent_workspace" / "candidates" / "opinion_radar" / "news_logic"
    archive_path = base / yyyy / mm / dd / f"news-logic-{stamp}.json"
    latest_path = base / "latest" / "news-logic.json"
    write_json(archive_path, payload)
    write_json(latest_path, payload)
    register_payload_if_enabled(payload, kind="news_logic", root=root_path, source_path=latest_path)
    return {"archive": str(archive_path), "latest": str(latest_path), "payload": payload}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Map news flashes into commodity frameworks and compare with report logic chains.")
    parser.add_argument("--date", help="YYYY-MM-DD. If omitted, use latest rolling window.")
    parser.add_argument("--hours", type=int, default=24, help="Rolling window hours when --date is omitted.")
    parser.add_argument("--logic-run", help="Path to futures daily run dir with trade_thesis.json.")
    parser.add_argument("--theme-anchor-path", help="Path to a theme_anchor candidate set or candidate directory.")
    parser.add_argument("--quanta-root", help="Override GJ_QUANTA_DATA_ROOT.")
    parser.add_argument("--no-llm-assessment", action="store_true", help="Skip LLM asset-level news/thesis assessment.")
    parser.add_argument("--llm-asset-limit", type=int, default=80, help="Maximum assets assessed by LLM.")
    args = parser.parse_args(argv)
    result = publish_news_logic_radar(
        args.quanta_root,
        date=args.date,
        hours=args.hours,
        logic_run=args.logic_run,
        theme_anchor_path=args.theme_anchor_path,
        use_llm_assessment=not args.no_llm_assessment,
        llm_asset_limit=args.llm_asset_limit,
    )
    stats = result["payload"]["stats"]
    print(f"新闻逻辑雷达 → {result['latest']}")
    print(
        "flashes={kept_flash_count}/{flash_count} mapped={mapped_event_count} assets={asset_count}".format(
            **stats
        )
    )


if __name__ == "__main__":
    main()
