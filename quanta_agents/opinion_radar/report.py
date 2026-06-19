from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
from pathlib import Path
import re
from typing import Any

from quanta_agents.core.config import quanta_data_root
from quanta_agents.core.io import (
    atomic_write_text,
    dated_parts,
    read_json,
    relative_to_root,
    utc_now_iso,
    write_json,
)


RELATION_LABELS = {
    "supports": "验证/强化",
    "conflicts": "冲突/证伪",
    "mixed": "多空分歧",
    "new_signal": "新增信号",
    "tracking": "继续跟踪",
}

STATUS_LABELS = {
    "validated": "主线被验证",
    "conflict": "主线需修正",
    "mixed": "主线分歧扩大",
    "new_signal": "新增主线候选",
    "tracking": "跟踪未定性",
}

ACTION_LABELS = {
    "reinforce_thesis": "强化原主线",
    "revise_or_downgrade_thesis": "修正/降级主线",
    "conditional_thesis": "收敛为条件性主线",
    "open_new_logic_candidate": "新增主线候选",
    "maintain_watch": "维持跟踪",
}

STAGE_LABELS = {
    "further_validated": "强化",
    "challenged": "分歧",
    "candidate": "新增",
    "tracking": "跟踪",
}

_ROUNDUP_RE = re.compile(r"金十数据整理|每日.+要闻|重要新闻汇总|市场要闻速递|动态汇总|一览")
_HIGH_SIGNAL_WORDS = (
    "点阵图",
    "政策声明",
    "美联储",
    "FOMC",
    "沃什",
    "鲍威尔",
    "鹰派",
    "鸽派",
    "加息",
    "降息",
    "利率",
    "EIA",
    "API",
    "库存",
    "OPEC",
    "减产",
    "增产",
    "进口",
    "出口",
    "产量",
    "开工",
    "协议",
    "制裁",
    "关税",
    "复航",
    "封锁",
)


def _compact(text: Any, limit: int = 140) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return value[:limit].rstrip("，,；;。 ") + "..."


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _latest_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = read_json(path)
    return payload if isinstance(payload, dict) else {}


def _default_snapshot(radar_payload: dict[str, Any]) -> dict[str, Any]:
    windows = radar_payload.get("windows") or {}
    default_window = str(radar_payload.get("default_window") or "")
    snapshot = windows.get(default_window)
    if isinstance(snapshot, dict):
        return snapshot
    for candidate in windows.values():
        if isinstance(candidate, dict):
            return candidate
    return {}


def _top_market_themes(snapshot: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    source = snapshot.get("synthesis") or snapshot.get("themes") or []
    themes = []
    for item in source:
        if not isinstance(item, dict):
            continue
        title = item.get("title") or item.get("theme") or item.get("label") or "未命名主题"
        varieties = item.get("varieties") if isinstance(item.get("varieties"), list) else []
        themes.append(
            {
                "title": str(title),
                "summary": _compact(item.get("summary") or item.get("logic") or "", 180),
                "heat": _safe_float(item.get("heat")),
                "flash_count": int(item.get("flash_count") or 0),
                "important_count": int(item.get("important_count") or 0),
                "varieties": varieties,
                "top_flashes": [
                    {
                        "publish_time": flash.get("publish_time"),
                        "text": _compact(flash.get("text"), 180),
                        "url": flash.get("url"),
                    }
                    for flash in (item.get("top_flashes") or [])[:3]
                    if isinstance(flash, dict)
                ],
            }
        )
    return sorted(themes, key=lambda item: item["heat"], reverse=True)[:limit]


def _rule_relation(consistency_counts: dict[str, Any]) -> str:
    thesis_conflicts = int(consistency_counts.get("thesis_conflict") or 0)
    dimension_conflicts = int(consistency_counts.get("dimension_conflict") or 0)
    supports = int(consistency_counts.get("supports_thesis") or 0)
    new_signals = int(consistency_counts.get("new_signal") or 0)
    conflicts = thesis_conflicts + dimension_conflicts
    if conflicts and supports:
        return "mixed"
    if conflicts:
        return "conflicts"
    if supports and new_signals:
        return "mixed"
    if supports:
        return "supports"
    if new_signals:
        return "new_signal"
    return "tracking"


def _asset_relation(payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    assessment = payload.get("llm_assessment")
    if not isinstance(assessment, dict):
        assessment = {}
    relation = str(assessment.get("relation_to_report") or "").strip()
    if relation in RELATION_LABELS:
        return relation, assessment
    return _rule_relation(payload.get("consistency_counts") or {}), assessment


def _status_for_relation(relation: str) -> str:
    return {
        "supports": "validated",
        "conflicts": "conflict",
        "mixed": "mixed",
        "new_signal": "new_signal",
        "tracking": "tracking",
    }.get(relation, "tracking")


def _action_for_relation(relation: str) -> str:
    return {
        "supports": "reinforce_thesis",
        "conflicts": "revise_or_downgrade_thesis",
        "mixed": "conditional_thesis",
        "new_signal": "open_new_logic_candidate",
        "tracking": "maintain_watch",
    }.get(relation, "maintain_watch")


def _stage_for_relation(relation: str) -> str:
    return {
        "supports": "further_validated",
        "conflicts": "challenged",
        "mixed": "challenged",
        "new_signal": "candidate",
        "tracking": "tracking",
    }.get(relation, "tracking")


def _bias_from_score(score: Any) -> str:
    value = _safe_float(score)
    if value >= 3:
        return "偏多"
    if value <= -3:
        return "偏空"
    if value > 0.8:
        return "小幅偏多"
    if value < -0.8:
        return "小幅偏空"
    return "中性"


def _dimension_briefs(payload: dict[str, Any], limit: int = 6) -> list[dict[str, Any]]:
    dimensions = []
    for item in payload.get("dimensions") or []:
        if not isinstance(item, dict):
            continue
        top_events = []
        for event in (item.get("top_events") or [])[:3]:
            if not isinstance(event, dict):
                continue
            top_events.append(
                {
                    "event_id": event.get("event_id"),
                    "publish_time": event.get("publish_time"),
                    "text": _compact(event.get("text"), 220),
                    "heat": _safe_float(event.get("heat")),
                    "direction_score": _safe_float(event.get("direction_score")),
                    "consistency": event.get("consistency") or {},
                }
            )
        dimensions.append(
            {
                "dimension_label": item.get("dimension_label") or "未归类",
                "event_count": int(item.get("event_count") or 0),
                "important_count": int(item.get("important_count") or 0),
                "heat": _safe_float(item.get("heat")),
                "news_direction_score": _safe_float(item.get("news_direction_score")),
                "consistency_counts": item.get("consistency_counts") or {},
                "top_events": top_events,
            }
        )
    return sorted(dimensions, key=lambda item: item["heat"], reverse=True)[:limit]


def _event_signal_score(text: str) -> int:
    return sum(1 for word in _HIGH_SIGNAL_WORDS if word in text)


def _event_rank(event: dict[str, Any], relation: str | None = None) -> tuple[int, int, int, float, str]:
    text = str(event.get("text") or "")
    status = str((event.get("consistency") or {}).get("status") or "")
    if relation in {"conflicts", "mixed"}:
        preferred = status in {"thesis_conflict", "dimension_conflict"}
    elif relation == "supports":
        preferred = status == "supports_thesis"
    else:
        preferred = status != "tracking"
    return (
        1 if preferred else 0,
        0 if _ROUNDUP_RE.search(text) else 1,
        _event_signal_score(text),
        _safe_float(event.get("heat")),
        str(event.get("publish_time") or ""),
    )


def _evidence_events(
    dimensions: list[dict[str, Any]],
    limit: int = 6,
    relation: str | None = None,
) -> list[dict[str, Any]]:
    events = []
    for dimension in dimensions:
        for event in dimension.get("top_events") or []:
            events.append(
                {
                    "dimension_label": dimension.get("dimension_label"),
                    "publish_time": event.get("publish_time"),
                    "text": event.get("text"),
                    "heat": event.get("heat"),
                    "consistency": event.get("consistency") or {},
                }
            )
    return sorted(events, key=lambda item: _event_rank(item, relation), reverse=True)[:limit]


def _status_events(events: list[dict[str, Any]], statuses: set[str], limit: int = 3) -> list[dict[str, Any]]:
    return [
        event
        for event in events
        if str((event.get("consistency") or {}).get("status") or "") in statuses
    ][:limit]


def _event_phrase(events: list[dict[str, Any]], limit: int = 2) -> str:
    parts = []
    for event in events[:limit]:
        dimension = str(event.get("dimension_label") or "")
        text = _compact(event.get("text"), 130)
        if not text:
            continue
        parts.append(f"{dimension}：{text}" if dimension else text)
    return "；".join(parts)


def _rule_news_logic_impact(
    asset: str,
    relation: str,
    events: list[dict[str, Any]],
) -> str:
    conflicts = _status_events(events, {"thesis_conflict", "dimension_conflict"})
    supports = _status_events(events, {"supports_thesis"})
    tracking = _status_events(events, {"tracking"})
    conflict_text = _event_phrase(conflicts)
    support_text = _event_phrase(supports)
    tracking_text = _event_phrase(tracking)
    if relation == "supports":
        detail = support_text or tracking_text
        return f"新闻层继续验证{asset}原主线：{detail}" if detail else f"新闻层继续验证{asset}原主线"
    if relation == "conflicts":
        detail = conflict_text or tracking_text
        return f"新闻层反向证据占优，{asset}原主线需要降级或重估：{detail}" if detail else f"{asset}原主线需要降级或重估"
    if relation == "mixed":
        parts = []
        if conflict_text:
            parts.append(f"反向证据：{conflict_text}")
        if support_text:
            parts.append(f"支撑证据：{support_text}")
        if not parts and tracking_text:
            parts.append(f"跟踪证据：{tracking_text}")
        detail = "；".join(parts)
        if detail:
            return f"新闻层已对{asset}旧主线形成再验证，{detail}。主线降为条件性判断。"
        return f"新闻层对{asset}旧主线形成多空分歧，主线降为条件性判断。"
    if relation == "new_signal":
        detail = support_text or conflict_text or tracking_text
        return f"{asset}出现研报外新增变量：{detail}" if detail else f"{asset}出现研报外新增变量"
    detail = tracking_text or support_text or conflict_text
    return f"新闻层暂无明确方向性验证，继续跟踪：{detail}" if detail else "新闻层暂无明确方向性验证，继续跟踪。"


def _updated_logic(
    asset: str,
    relation: str,
    thesis: dict[str, Any],
    assessment: dict[str, Any],
    events: list[dict[str, Any]],
) -> str:
    impact = str(assessment.get("logic_impact") or "").strip()
    summary = str(assessment.get("news_summary") or "").strip()
    if impact or summary:
        return impact or summary
    return _rule_news_logic_impact(asset, relation, events)


def _asset_logic_rows(news_logic_payload: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    rows = []
    for asset, payload in (news_logic_payload.get("assets") or {}).items():
        if not isinstance(payload, dict):
            continue
        relation, assessment = _asset_relation(payload)
        thesis = payload.get("linked_trade_thesis") or {}
        status = _status_for_relation(relation)
        action = _action_for_relation(relation)
        stage = _stage_for_relation(relation)
        dimensions = _dimension_briefs(payload)
        evidence = _evidence_events(dimensions, relation=relation)
        report_score = thesis.get("decision_score", thesis.get("framework_score"))
        updated_bias = str(assessment.get("updated_bias") or "").strip() or _bias_from_score(
            payload.get("news_direction_score", report_score)
        )
        rows.append(
            {
                "asset": asset,
                "heat": _safe_float(payload.get("heat")),
                "event_count": int(payload.get("event_count") or 0),
                "important_count": int(payload.get("important_count") or 0),
                "relation_to_report": relation,
                "relation_label": RELATION_LABELS[relation],
                "verification_status": status,
                "verification_label": STATUS_LABELS[status],
                "logic_development_stage": stage,
                "logic_development_label": STAGE_LABELS[stage],
                "revision_action": action,
                "revision_label": ACTION_LABELS[action],
                "current_recommendation": thesis.get("recommendation"),
                "current_report_thesis": thesis.get("main_trade_thesis") or "",
                "report_score": report_score,
                "news_direction_score": payload.get("news_direction_score"),
                "updated_bias": updated_bias,
                "updated_logic": _updated_logic(asset, relation, thesis, assessment, evidence),
                "logic_impact": assessment.get("logic_impact") or "",
                "news_summary": assessment.get("news_summary") or "",
                "key_confirmations": assessment.get("key_confirmations")
                if isinstance(assessment.get("key_confirmations"), list)
                else [],
                "key_conflicts": assessment.get("key_conflicts")
                if isinstance(assessment.get("key_conflicts"), list)
                else [],
                "new_variables": assessment.get("new_variables")
                if isinstance(assessment.get("new_variables"), list)
                else [],
                "tracking_points": assessment.get("tracking_points")
                if isinstance(assessment.get("tracking_points"), list)
                else [],
                "quality_notes": assessment.get("quality_notes")
                if isinstance(assessment.get("quality_notes"), list)
                else [],
                "consistency_counts": payload.get("consistency_counts") or {},
                "dimensions": dimensions,
                "evidence_events": evidence,
            }
        )
    return sorted(rows, key=lambda item: item["heat"], reverse=True)[:limit]


def _logic_updates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    updates = []
    for row in rows:
        if row["revision_action"] == "maintain_watch":
            continue
        updates.append(
            {
                "asset": row["asset"],
                "action": row["revision_action"],
                "action_label": row["revision_label"],
                "relation_to_report": row["relation_to_report"],
                "from_thesis": row["current_report_thesis"],
                "updated_bias": row["updated_bias"],
                "updated_logic": row["updated_logic"],
                "evidence_events": row["evidence_events"][:3],
                "tracking_points": row["tracking_points"],
            }
        )
    action_priority = {
        "revise_or_downgrade_thesis": 0,
        "conditional_thesis": 1,
        "open_new_logic_candidate": 2,
        "reinforce_thesis": 3,
    }
    return sorted(updates, key=lambda item: action_priority.get(item["action"], 9))


def _alerts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    alerts = []
    for row in rows:
        if row["relation_to_report"] == "conflicts":
            severity = "high"
        elif row["relation_to_report"] == "mixed":
            severity = "medium"
        elif row["relation_to_report"] == "new_signal":
            severity = "medium"
        else:
            continue
        alerts.append(
            {
                "severity": severity,
                "asset": row["asset"],
                "title": f"{row['asset']}：{row['revision_label']}",
                "message": row["updated_logic"],
                "evidence_events": row["evidence_events"][:2],
            }
        )
    priority = {"high": 0, "medium": 1, "low": 2}
    return sorted(alerts, key=lambda item: priority.get(item["severity"], 9))


def build_market_radar_report(
    radar_payload: dict[str, Any] | None = None,
    news_logic_payload: dict[str, Any] | None = None,
    *,
    generated_at: str | None = None,
    top_assets: int = 20,
    top_themes: int = 8,
) -> dict[str, Any]:
    radar = radar_payload or {}
    news_logic = news_logic_payload or {}
    snapshot = _default_snapshot(radar)
    themes = _top_market_themes(snapshot, top_themes)
    rows = _asset_logic_rows(news_logic, top_assets)
    status_counts = Counter(row["verification_status"] for row in rows)
    action_counts = Counter(row["revision_action"] for row in rows)
    relation_counts = Counter(row["relation_to_report"] for row in rows)
    updates = _logic_updates(rows)
    alerts = _alerts(rows)
    generated = generated_at or utc_now_iso()

    summary = {
        "theme_count": len(themes),
        "asset_count": len(rows),
        "alert_count": len(alerts),
        "logic_update_count": len(updates),
        "top_theme": themes[0]["title"] if themes else "",
        "status_counts": dict(status_counts),
        "relation_counts": dict(relation_counts),
        "action_counts": dict(action_counts),
    }
    return {
        "schema_version": "market_sentiment_radar_report.v1",
        "status": "candidate",
        "generated_at": generated,
        "source_refs": {
            "radar_generated_at": radar.get("generated_at"),
            "news_logic_generated_at": news_logic.get("generated_at"),
            "logic_run": (news_logic.get("logic_context") or {}).get("run_dir", ""),
            "analysis_framework_ref": news_logic.get("analysis_framework_ref") or {},
        },
        "summary": summary,
        "market_pulse": {
            "window": snapshot.get("window") or {},
            "stats": snapshot.get("stats") or {},
            "top_themes": themes,
            "important_stream": snapshot.get("important_stream") or [],
        },
        "logic_validation": {
            "assets": rows,
            "status_counts": dict(status_counts),
            "relation_counts": dict(relation_counts),
        },
        "investment_logic_updates": updates,
        "alerts": alerts,
    }


def _md_escape(value: Any) -> str:
    text = _compact(value, 160)
    return text.replace("|", "\\|")


def render_market_radar_markdown(report: dict[str, Any]) -> str:
    source_refs = report.get("source_refs") or {}
    summary = report.get("summary") or {}
    lines = [
        "# 市场舆情雷达报告",
        "",
        f"- 生成时间：{report.get('generated_at')}",
        f"- 关联研报逻辑 run：{source_refs.get('logic_run') or '未关联'}",
        (
            f"- 覆盖资产：{summary.get('asset_count', 0)}，"
            f"预警：{summary.get('alert_count', 0)}"
        ),
        "",
        "## 市场脉冲",
    ]
    themes = ((report.get("market_pulse") or {}).get("top_themes") or [])[:8]
    if themes:
        for theme in themes:
            lines.append(
                f"- {theme.get('title')}：热度 {theme.get('heat')}，"
                f"快讯 {theme.get('flash_count')} 条。{theme.get('summary')}"
            )
    else:
        lines.append("- 暂无可用主题。")

    lines.extend(
        [
            "",
            "## 研报主线验证",
            "| 资产 | 状态 | 修正动作 | 舆情偏向 | 逻辑影响 |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    rows = ((report.get("logic_validation") or {}).get("assets") or [])[:20]
    if rows:
        for row in rows:
            lines.append(
                "| {asset} | {status} | {action} | {bias} | {impact} |".format(
                    asset=_md_escape(row.get("asset")),
                    status=_md_escape(row.get("verification_label")),
                    action=_md_escape(row.get("revision_label")),
                    bias=_md_escape(row.get("updated_bias")),
                    impact=_md_escape(row.get("updated_logic")),
                )
            )
    else:
        lines.append("| - | - | - | - | 暂无研报主线验证结果 |")

    lines.extend(["", "## 投资逻辑修正清单"])
    updates = report.get("investment_logic_updates") or []
    if updates:
        for item in updates[:12]:
            evidence = item.get("evidence_events") or []
            evidence_text = evidence[0].get("text") if evidence else ""
            lines.append(
                f"- **{item.get('asset')}** [{item.get('action_label')}] "
                f"{item.get('updated_logic')} 证据：{_compact(evidence_text, 180)}"
            )
    else:
        lines.append("- 暂无需要修正或强化的投资逻辑。")

    lines.extend(["", "## 重点预警"])
    alerts = report.get("alerts") or []
    if alerts:
        for alert in alerts[:10]:
            lines.append(
                f"- [{alert.get('severity')}] **{alert.get('asset')}**：{alert.get('message')}"
            )
    else:
        lines.append("- 暂无冲突或新增信号预警。")

    return "\n".join(lines).rstrip() + "\n"


def publish_market_radar_report(
    root: str | Path | None = None,
    *,
    radar_path: str | Path | None = None,
    news_logic_path: str | Path | None = None,
    radar_payload: dict[str, Any] | None = None,
    news_logic_payload: dict[str, Any] | None = None,
    top_assets: int = 20,
    top_themes: int = 8,
    now: datetime | None = None,
) -> dict[str, Any]:
    root_path = quanta_data_root(root)
    radar_source = Path(radar_path).expanduser() if radar_path else (
        root_path / "agent_workspace" / "candidates" / "opinion_radar" / "latest" / "radar.json"
    )
    news_logic_source = Path(news_logic_path).expanduser() if news_logic_path else (
        root_path
        / "agent_workspace"
        / "candidates"
        / "opinion_radar"
        / "news_logic"
        / "latest"
        / "news-logic.json"
    )
    radar = radar_payload if radar_payload is not None else _latest_json(radar_source)
    news_logic = (
        news_logic_payload if news_logic_payload is not None else _latest_json(news_logic_source)
    )
    generated_at = (now or datetime.now()).isoformat(sep=" ")
    payload = build_market_radar_report(
        radar,
        news_logic,
        generated_at=generated_at,
        top_assets=top_assets,
        top_themes=top_themes,
    )
    markdown = render_market_radar_markdown(payload)

    date_key = (now or datetime.now()).strftime("%Y%m%d")
    yyyy, mm, dd = dated_parts(date_key)
    stamp = (now or datetime.now()).strftime("%H%M%S")
    base = root_path / "agent_workspace" / "candidates" / "opinion_radar" / "reports"
    latest_json = base / "latest" / "market-radar-report.json"
    latest_md = base / "latest" / "market-radar-report.md"
    archive_json = base / yyyy / mm / dd / f"market-radar-report-{stamp}.json"
    archive_md = base / yyyy / mm / dd / f"market-radar-report-{stamp}.md"
    write_json(latest_json, payload)
    write_json(archive_json, payload)
    atomic_write_text(latest_md, markdown)
    atomic_write_text(archive_md, markdown)
    return {
        "latest_json": str(latest_json),
        "latest_markdown": str(latest_md),
        "archive_json": str(archive_json),
        "archive_markdown": str(archive_md),
        "payload": payload,
        "markdown": markdown,
        "source_paths": {
            "radar": relative_to_root(radar_source, root_path),
            "news_logic": relative_to_root(news_logic_source, root_path),
        },
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Generate a market sentiment radar report.")
    parser.add_argument("--quanta-root", help="Override GJ_QUANTA_DATA_ROOT.")
    parser.add_argument("--radar-path", help="Path to radar.json. Defaults to latest radar.")
    parser.add_argument(
        "--news-logic-path",
        help="Path to news-logic.json. Defaults to latest news logic.",
    )
    parser.add_argument("--top-assets", type=int, default=20)
    parser.add_argument("--top-themes", type=int, default=8)
    parser.add_argument(
        "--print-markdown",
        action="store_true",
        help="Print report Markdown to stdout.",
    )
    args = parser.parse_args(argv)

    result = publish_market_radar_report(
        args.quanta_root,
        radar_path=args.radar_path,
        news_logic_path=args.news_logic_path,
        top_assets=args.top_assets,
        top_themes=args.top_themes,
    )
    print(f"市场舆情雷达报告 JSON → {result['latest_json']}")
    print(f"市场舆情雷达报告 Markdown → {result['latest_markdown']}")
    if args.print_markdown:
        print("-" * 56)
        print(result["markdown"])


if __name__ == "__main__":
    main()
