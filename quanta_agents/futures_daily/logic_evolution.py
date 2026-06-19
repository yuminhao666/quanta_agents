from __future__ import annotations

import argparse
import hashlib
from collections import Counter
from pathlib import Path
from typing import Any

from quanta_agents.core.config import quanta_data_root
from quanta_agents.core.io import read_json, utc_now_iso, write_json
from quanta_agents.core.llm_client import chat
from quanta_agents.core.llm_json import parse_json_object


def _hash_id(prefix: str, *parts: Any) -> str:
    raw = "||".join(str(part) for part in parts)
    return f"{prefix}-{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:16].upper()}"


def _date_from_run(run_dir: Path) -> str:
    manifest = run_dir / "manifest.json"
    if manifest.exists():
        payload = read_json(manifest)
        if isinstance(payload, dict) and payload.get("date"):
            return str(payload["date"])
    for part in run_dir.parts:
        if part.startswith("RUN-") and len(part) >= 12:
            date_key = part[4:12]
            if date_key.isdigit():
                return date_key
    return ""


def _all_logic_runs(root: Path) -> list[Path]:
    base = root / "agent_workspace" / "candidates" / "futures_daily_raw_runs"
    candidates = [
        path
        for path in base.glob("*/*/*/RUN-*-RAW-DAILY")
        if path.is_dir() and (path / "dimension_scores.json").exists() and (path / "trade_thesis.json").exists()
    ]
    return sorted(candidates, key=lambda path: (path.as_posix(), path.stat().st_mtime))


def find_previous_logic_run(current_run: str | Path, root: str | Path | None = None) -> Path | None:
    root_path = quanta_data_root(root)
    current = Path(current_run).expanduser()
    runs = _all_logic_runs(root_path)
    current_resolved = current.resolve()
    before = [path for path in runs if path.resolve() != current_resolved and path.as_posix() < current.as_posix()]
    return before[-1] if before else None


def _load_run(run_dir: str | Path) -> dict[str, Any]:
    path = Path(run_dir).expanduser()
    return {
        "run_dir": str(path),
        "date": _date_from_run(path),
        "dimension_scores": read_json(path / "dimension_scores.json"),
        "trade_thesis": read_json(path / "trade_thesis.json"),
        "logic_chains": read_json(path / "logic_chains.json") if (path / "logic_chains.json").exists() else {},
    }


def _sign(score: float, threshold: float = 0.35) -> int:
    if score > threshold:
        return 1
    if score < -threshold:
        return -1
    return 0


def _dimension_key(row: dict[str, Any]) -> str:
    return str(row.get("dimension_id") or row.get("dimension_label") or "未归类")


def _dimension_map(run: dict[str, Any], asset: str) -> dict[str, dict[str, Any]]:
    asset_payload = ((run.get("dimension_scores") or {}).get("assets") or {}).get(asset) or {}
    return {
        _dimension_key(row): row
        for row in asset_payload.get("dimensions") or []
        if isinstance(row, dict)
    }


def _asset_theses(run: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return ((run.get("trade_thesis") or {}).get("assets") or {})


def _status_for_scores(previous_score: float | None, current_score: float | None) -> str:
    if previous_score is None and current_score is None:
        return "missing"
    if previous_score is None:
        return "new_logic"
    if current_score is None:
        return "faded"
    prev_sign = _sign(previous_score)
    curr_sign = _sign(current_score)
    if prev_sign and curr_sign and prev_sign != curr_sign:
        return "reversal"
    if not prev_sign and curr_sign:
        return "new_logic"
    if prev_sign and not curr_sign:
        return "weakened"
    if curr_sign and abs(current_score) >= abs(previous_score) * 1.2:
        return "further_validated"
    if curr_sign and abs(current_score) <= abs(previous_score) * 0.65:
        return "weakened"
    return "continues"


def _dimension_evolution(asset: str, dimension_id: str, prev: dict[str, Any] | None, curr: dict[str, Any] | None) -> dict[str, Any]:
    prev_score = float(prev.get("direction_score")) if prev and prev.get("direction_score") is not None else None
    curr_score = float(curr.get("direction_score")) if curr and curr.get("direction_score") is not None else None
    label = str((curr or prev or {}).get("dimension_label") or dimension_id)
    status = _status_for_scores(prev_score, curr_score)
    prev_evidence = int((prev or {}).get("deduped_evidence_count") or (prev or {}).get("evidence_count") or 0)
    curr_evidence = int((curr or {}).get("deduped_evidence_count") or (curr or {}).get("evidence_count") or 0)
    return {
        "logic_node_id": _hash_id("LNODE", asset, dimension_id),
        "asset": asset,
        "dimension_id": dimension_id,
        "dimension_label": label,
        "evolution_status": status,
        "previous_direction_score": prev_score,
        "current_direction_score": curr_score,
        "score_delta": round((curr_score or 0.0) - (prev_score or 0.0), 3),
        "previous_evidence_count": prev_evidence,
        "current_evidence_count": curr_evidence,
        "evidence_delta": curr_evidence - prev_evidence,
        "previous_synthesis": (prev or {}).get("dimension_synthesis"),
        "current_synthesis": (curr or {}).get("dimension_synthesis"),
        "tracking_question": f"{asset}/{label}逻辑是否继续被研报、新闻和数据库验证，或出现反向证据。",
    }


def _rule_asset_summary(asset: str, row: dict[str, Any]) -> str:
    status_counts = Counter(item["evolution_status"] for item in row.get("dimension_evolution") or [])
    score_delta = row.get("score_delta")
    dominant = status_counts.most_common(1)[0][0] if status_counts else "missing"
    return (
        f"{asset}框架分从{row.get('previous_score')}变为{row.get('current_score')}，"
        f"变化{score_delta}；维度演化以{dominant}为主。"
    )


def _llm_asset_summary(asset: str, row: dict[str, Any]) -> dict[str, Any]:
    prompt = (
        "你是商品期货逻辑演化复盘 Agent。请比较前后两天模型分析结果，判断交易逻辑如何变化。\n"
        "严格输出 JSON，不要 Markdown，格式：\n"
        '{"evolution_summary":"一句自然中文总结",'
        '"overall_status":"further_validated|continues|weakened|reversal|new_logic|mixed",'
        '"confirmed_logic":["被验证的逻辑"],'
        '"reversed_logic":["反转的逻辑"],'
        '"new_tracking_points":["新增跟踪点"],'
        '"self_optimization_notes":["模型/框架需要改进处"]}\n\n'
        "输入：\n"
        + str(
            {
                "asset": asset,
                "previous_score": row.get("previous_score"),
                "current_score": row.get("current_score"),
                "previous_thesis": row.get("previous_thesis"),
                "current_thesis": row.get("current_thesis"),
                "dimension_evolution": row.get("dimension_evolution"),
            }
        )
    )
    try:
        parsed = parse_json_object(chat(prompt, max_tokens=1600, temperature=0.2, timeout=120))
        status = str(parsed.get("overall_status") or "mixed")
        if status not in {"further_validated", "continues", "weakened", "reversal", "new_logic", "mixed"}:
            status = "mixed"
        return {
            "method": "llm",
            "evolution_summary": str(parsed.get("evolution_summary") or ""),
            "overall_status": status,
            "confirmed_logic": parsed.get("confirmed_logic") if isinstance(parsed.get("confirmed_logic"), list) else [],
            "reversed_logic": parsed.get("reversed_logic") if isinstance(parsed.get("reversed_logic"), list) else [],
            "new_tracking_points": parsed.get("new_tracking_points") if isinstance(parsed.get("new_tracking_points"), list) else [],
            "self_optimization_notes": parsed.get("self_optimization_notes") if isinstance(parsed.get("self_optimization_notes"), list) else [],
        }
    except Exception as exc:
        return {
            "method": "rule_fallback",
            "evolution_summary": _rule_asset_summary(asset, row),
            "overall_status": "mixed",
            "confirmed_logic": [],
            "reversed_logic": [],
            "new_tracking_points": [],
            "self_optimization_notes": [f"LLM演化复盘失败，已用规则兜底：{exc}"],
        }


def build_logic_evolution(
    current_run: str | Path,
    previous_run: str | Path | None = None,
    root: str | Path | None = None,
    *,
    use_llm: bool = False,
) -> dict[str, Any]:
    root_path = quanta_data_root(root)
    current_path = Path(current_run).expanduser()
    previous_path = Path(previous_run).expanduser() if previous_run else find_previous_logic_run(current_path, root_path)
    if not previous_path:
        raise FileNotFoundError("未找到可比较的上一期 futures daily run，请显式传入 --previous-run。")
    previous = _load_run(previous_path)
    current = _load_run(current_path)
    previous_theses = _asset_theses(previous)
    current_theses = _asset_theses(current)
    assets = sorted(set(previous_theses) | set(current_theses))
    rows: list[dict[str, Any]] = []
    timeline_events: list[dict[str, Any]] = []

    for asset in assets:
        prev_thesis = previous_theses.get(asset) or {}
        curr_thesis = current_theses.get(asset) or {}
        prev_score = float(prev_thesis.get("framework_score")) if prev_thesis.get("framework_score") is not None else None
        curr_score = float(curr_thesis.get("framework_score")) if curr_thesis.get("framework_score") is not None else None
        prev_dims = _dimension_map(previous, asset)
        curr_dims = _dimension_map(current, asset)
        dimension_rows = [
            _dimension_evolution(asset, dimension_id, prev_dims.get(dimension_id), curr_dims.get(dimension_id))
            for dimension_id in sorted(set(prev_dims) | set(curr_dims))
        ]
        row = {
            "asset": asset,
            "previous_score": prev_score,
            "current_score": curr_score,
            "score_delta": round((curr_score or 0.0) - (prev_score or 0.0), 3),
            "overall_status": _status_for_scores(prev_score, curr_score),
            "previous_recommendation": prev_thesis.get("recommendation"),
            "current_recommendation": curr_thesis.get("recommendation"),
            "previous_thesis": prev_thesis.get("main_trade_thesis"),
            "current_thesis": curr_thesis.get("main_trade_thesis"),
            "dimension_evolution": sorted(
                dimension_rows,
                key=lambda item: (item["evolution_status"] == "continues", -abs(float(item.get("score_delta") or 0))),
            ),
        }
        row["evolution_assessment"] = _llm_asset_summary(asset, row) if use_llm else {
            "method": "rule",
            "evolution_summary": _rule_asset_summary(asset, row),
            "overall_status": row["overall_status"],
            "confirmed_logic": [
                item["dimension_label"]
                for item in dimension_rows
                if item["evolution_status"] == "further_validated"
            ],
            "reversed_logic": [
                item["dimension_label"]
                for item in dimension_rows
                if item["evolution_status"] == "reversal"
            ],
            "new_tracking_points": [
                item["tracking_question"]
                for item in dimension_rows
                if item["evolution_status"] in {"new_logic", "reversal"}
            ][:5],
            "self_optimization_notes": [],
        }
        rows.append(row)
        for item in dimension_rows:
            timeline_events.append(
                {
                    "event_id": _hash_id("LEVOL", current.get("date"), item["logic_node_id"]),
                    "logic_node_id": item["logic_node_id"],
                    "asset": asset,
                    "dimension_id": item["dimension_id"],
                    "dimension_label": item["dimension_label"],
                    "from_date": previous.get("date"),
                    "to_date": current.get("date"),
                    "evolution_status": item["evolution_status"],
                    "score_delta": item["score_delta"],
                    "source": "daily_report_comparison",
                }
            )

    status_counts = Counter(row["overall_status"] for row in rows)
    dimension_status_counts = Counter(item["evolution_status"] for item in timeline_events)
    return {
        "schema_version": "logic_evolution.v1",
        "status": "candidate",
        "generated_at": utc_now_iso(),
        "previous_run": previous["run_dir"],
        "current_run": current["run_dir"],
        "from_date": previous.get("date"),
        "to_date": current.get("date"),
        "stats": {
            "asset_count": len(rows),
            "asset_status_counts": dict(status_counts),
            "dimension_event_count": len(timeline_events),
            "dimension_status_counts": dict(dimension_status_counts),
            "llm_enabled": use_llm,
        },
        "assets": rows,
        "timeline_events": timeline_events,
    }


def publish_logic_evolution(
    current_run: str | Path,
    previous_run: str | Path | None = None,
    root: str | Path | None = None,
    *,
    use_llm: bool = False,
) -> dict[str, Any]:
    payload = build_logic_evolution(current_run, previous_run, root, use_llm=use_llm)
    current_path = Path(current_run).expanduser()
    output_path = current_path / "logic_evolution.json"
    write_json(output_path, payload)
    return {"logic_evolution": str(output_path), "payload": payload}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Compare futures daily logic runs and build logic evolution timeline.")
    parser.add_argument("--current-run", required=True, help="Current RUN-*-RAW-DAILY directory.")
    parser.add_argument("--previous-run", help="Previous RUN-*-RAW-DAILY directory. Auto-detected if omitted.")
    parser.add_argument("--quanta-root", help="Override GJ_QUANTA_DATA_ROOT.")
    parser.add_argument("--llm", action="store_true", help="Use LLM to write asset-level evolution summaries.")
    args = parser.parse_args(argv)
    result = publish_logic_evolution(
        args.current_run,
        args.previous_run,
        args.quanta_root,
        use_llm=args.llm,
    )
    stats = result["payload"]["stats"]
    print(f"逻辑演化结果 → {result['logic_evolution']}")
    print(
        "assets={asset_count} dimension_events={dimension_event_count} llm={llm_enabled}".format(
            **stats
        )
    )


if __name__ == "__main__":
    main()
