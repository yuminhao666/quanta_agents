from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from quanta_agents.core.analysis_framework import load_latest_analysis_framework_registry
from quanta_agents.core.config import first_env, quanta_data_root
from quanta_agents.core.io import dated_parts, read_json, relative_to_root, utc_now_iso, write_json
from quanta_agents.core.llm_client import chat
from quanta_agents.core.llm_json import parse_json_object


def _hash_id(prefix: str, *parts: Any) -> str:
    raw = "||".join(str(part) for part in parts)
    return f"{prefix}-{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:16].upper()}"


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _date_from_run(run_dir: Path) -> str:
    manifest = run_dir / "manifest.json"
    if manifest.exists():
        payload = read_json(manifest)
        if isinstance(payload, dict) and payload.get("date"):
            return str(payload["date"])
    name = run_dir.name
    if name.startswith("RUN-") and len(name) >= 12 and name[4:12].isdigit():
        return name[4:12]
    return ""


def _all_runs(root: Path) -> list[Path]:
    base = root / "agent_workspace" / "candidates" / "futures_daily_raw_runs"
    return sorted(
        [
            path
            for path in base.glob("*/*/*/RUN-*-RAW-DAILY")
            if path.is_dir() and (path / "dimension_scores.json").exists() and (path / "trade_thesis.json").exists()
        ],
        key=lambda path: path.stat().st_mtime,
    )


def _selected_runs(root: Path, current_run: Path, history_limit: int) -> list[Path]:
    current_resolved = current_run.resolve()
    runs = _all_runs(root)
    current_mtime = current_run.stat().st_mtime if current_run.exists() else datetime.now().timestamp()
    previous = [
        path
        for path in runs
        if path.resolve() != current_resolved and path.stat().st_mtime <= current_mtime
    ]
    return (previous[-max(history_limit - 1, 0) :] + [current_run])[-history_limit:]


def _load_run(run_dir: Path) -> dict[str, Any]:
    return {
        "run_dir": str(run_dir),
        "date": _date_from_run(run_dir),
        "dimension_scores": read_json(run_dir / "dimension_scores.json"),
        "trade_thesis": read_json(run_dir / "trade_thesis.json"),
    }


def _dimension_registry(root: Path) -> dict[tuple[str, str], dict[str, Any]]:
    registry = load_latest_analysis_framework_registry(root) or {}
    rows = {}
    for row in registry.get("dimensions") or []:
        if not isinstance(row, dict):
            continue
        rows[(str(row.get("asset") or ""), str(row.get("dimension_id") or ""))] = row
        rows[(str(row.get("asset") or ""), str(row.get("dimension_label") or ""))] = row
    return rows


def _asset_rows(run: dict[str, Any], asset: str) -> list[dict[str, Any]]:
    payload = ((run.get("dimension_scores") or {}).get("assets") or {}).get(asset) or {}
    return [row for row in payload.get("dimensions") or [] if isinstance(row, dict)]


def _dimension_key(row: dict[str, Any]) -> str:
    return str(row.get("dimension_id") or row.get("dimension_label") or "未归类")


def _sign(value: Any, threshold: float = 0.35) -> int:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0
    if score > threshold:
        return 1
    if score < -threshold:
        return -1
    return 0


def _build_history_stats(runs: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    history: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for run in runs:
        assets = (run.get("dimension_scores") or {}).get("assets") or {}
        for asset in assets:
            for row in _asset_rows(run, asset):
                key = (asset, _dimension_key(row))
                history[key].append(
                    {
                        "date": run.get("date"),
                        "direction_score": row.get("direction_score"),
                        "weighted_contribution": row.get("weighted_contribution"),
                        "conflict_level": row.get("conflict_level"),
                        "conflict_ratio": row.get("conflict_ratio"),
                        "directional_consensus": row.get("directional_consensus"),
                        "evidence_count": row.get("scored_evidence_count") or row.get("evidence_count"),
                    }
                )

    stats: dict[tuple[str, str], dict[str, Any]] = {}
    for key, rows in history.items():
        activations = len(rows)
        reversals = 0
        continuations = 0
        previous_sign = 0
        for row in rows:
            current_sign = _sign(row.get("direction_score"))
            if previous_sign and current_sign:
                if previous_sign == current_sign:
                    continuations += 1
                else:
                    reversals += 1
            if current_sign:
                previous_sign = current_sign
        high_conflicts = sum(1 for row in rows if row.get("conflict_level") in {"high", "medium"})
        avg_abs_contribution = sum(abs(float(row.get("weighted_contribution") or 0.0)) for row in rows) / max(activations, 1)
        avg_consensus = sum(float(row.get("directional_consensus") or 0.0) for row in rows) / max(activations, 1)
        validation_rate = continuations / max(continuations + reversals, 1)
        reversal_rate = reversals / max(continuations + reversals, 1)
        conflict_rate = high_conflicts / max(activations, 1)
        experience_multiplier = _clip(1.0 + 0.2 * validation_rate - 0.3 * reversal_rate - 0.25 * conflict_rate, 0.55, 1.35)
        stats[key] = {
            "activation_count": activations,
            "continuation_count": continuations,
            "reversal_count": reversals,
            "validation_rate": round(validation_rate, 3),
            "reversal_rate": round(reversal_rate, 3),
            "conflict_rate": round(conflict_rate, 3),
            "avg_abs_contribution": round(avg_abs_contribution, 3),
            "avg_directional_consensus": round(avg_consensus, 3),
            "experience_multiplier": round(experience_multiplier, 3),
        }
    return stats


def _current_dynamic_multiplier(row: dict[str, Any], divergence_status: str) -> float:
    consensus = float(row.get("directional_consensus") or 0.0)
    conflict_ratio = float(row.get("conflict_ratio") or 0.0)
    evidence_count = int(row.get("scored_evidence_count") or row.get("evidence_count") or 0)
    multiplier = 0.75 + 0.35 * consensus + min(0.25, evidence_count * 0.025) - 0.45 * conflict_ratio
    if row.get("conflict_level") in {"high", "medium"}:
        multiplier -= 0.15
    if divergence_status == "opposite_direction":
        multiplier -= 0.12
    return round(_clip(multiplier, 0.35, 1.45), 3)


def _proposal_action(row: dict[str, Any], hist: dict[str, Any], divergence_status: str) -> str:
    if row.get("dimension_label") == "未归类":
        return "add_or_map_dimension"
    if row.get("conflict_level") in {"high", "medium"} and int(row.get("scored_evidence_count") or 0) >= 3:
        return "split_dimension_or_lower_dynamic_weight"
    if hist.get("reversal_rate", 0) >= 0.4:
        return "lower_base_weight"
    if hist.get("validation_rate", 0) >= 0.65 and hist.get("conflict_rate", 0) <= 0.2:
        return "raise_base_weight"
    if divergence_status == "opposite_direction":
        return "review_scoring_polarity"
    return "maintain_with_dynamic_adjustment"


def _candidate_reason(row: dict[str, Any], hist: dict[str, Any], action: str) -> str:
    label = row.get("dimension_label")
    if action == "split_dimension_or_lower_dynamic_weight":
        return f"{label}维度内多空证据冲突较高，建议拆分子维度或降低当日动态权重。"
    if action == "raise_base_weight":
        return f"{label}历史验证率较高且冲突率低，可考虑提高经验基准权重。"
    if action == "lower_base_weight":
        return f"{label}历史反转率偏高，建议降低经验基准权重并复核映射口径。"
    if action == "review_scoring_polarity":
        return f"{label}与原始研报方向存在明显分歧，建议复核指标方向和证据归类。"
    if action == "add_or_map_dimension":
        return "存在未归类证据，建议补充框架维度或指标/事件词条。"
    return f"{label}按历史经验和当日证据做动态权重调整，暂不改变长期基准。"


def _candidate_row(
    *,
    asset: str,
    row: dict[str, Any],
    registry_row: dict[str, Any],
    hist: dict[str, Any],
    divergence_status: str,
    current_run: str,
) -> dict[str, Any]:
    current_weight = float(row.get("effective_weight") or 0.0)
    base_weight = float(registry_row.get("default_weight") or current_weight or 0.05)
    dynamic_multiplier = _current_dynamic_multiplier(row, divergence_status)
    experience_multiplier = float(hist.get("experience_multiplier") or 1.0)
    suggested_base_weight = round(_clip(base_weight * experience_multiplier, 0.01, 0.35), 4)
    suggested_dynamic_weight = round(_clip(current_weight * dynamic_multiplier, 0.0, 0.4), 4)
    action = _proposal_action(row, hist, divergence_status)
    return {
        "candidate_id": _hash_id("FWKWGT", current_run, asset, row.get("dimension_id"), action),
        "schema_version": "framework_weight_optimization_candidate.v1",
        "artifact_type": "framework_weight_optimization_candidate",
        "asset": asset,
        "asset_id": registry_row.get("asset_id"),
        "framework_id": registry_row.get("framework_id"),
        "dimension_id": row.get("dimension_id"),
        "dimension_label": row.get("dimension_label"),
        "dimension_type": registry_row.get("dimension_type"),
        "action": action,
        "proposal_summary": _candidate_reason(row, hist, action),
        "current_metrics": {
            "framework_direction_score": row.get("direction_score"),
            "current_effective_weight": row.get("effective_weight"),
            "weighted_contribution": row.get("weighted_contribution"),
            "directional_consensus": row.get("directional_consensus"),
            "conflict_level": row.get("conflict_level"),
            "conflict_ratio": row.get("conflict_ratio"),
            "scored_evidence_count": row.get("scored_evidence_count"),
        },
        "historical_experience": hist,
        "recommended_adjustment": {
            "current_base_weight": round(base_weight, 4),
            "suggested_base_weight": suggested_base_weight,
            "experience_multiplier": round(experience_multiplier, 3),
            "dynamic_multiplier": dynamic_multiplier,
            "suggested_dynamic_weight_hint": suggested_dynamic_weight,
        },
        "review_status": "pending_review",
        "status": "candidate",
    }


def _llm_review_candidates(candidates: list[dict[str, Any]], *, provider: str, limit: int) -> dict[str, Any]:
    sample = [
        {
            "candidate_id": item.get("candidate_id"),
            "asset": item.get("asset"),
            "dimension_label": item.get("dimension_label"),
            "action": item.get("action"),
            "proposal_summary": item.get("proposal_summary"),
            "current_metrics": item.get("current_metrics"),
            "historical_experience": item.get("historical_experience"),
            "recommended_adjustment": item.get("recommended_adjustment"),
        }
        for item in candidates[:limit]
    ]
    prompt = (
        "你是商品期货分析框架优化官。请审阅框架权重和维度结构调整候选。\n"
        "目标是提升研究逻辑自洽、次日验证率和框架可维护性，不要追涨杀跌。\n"
        "严格输出 JSON，不要 Markdown，格式：\n"
        '{"overall_assessment":"总体评价",'
        '"accepted":["candidate_id"],'
        '"needs_human_review":["candidate_id"],'
        '"rejected":["candidate_id"],'
        '"priority_notes":[{"candidate_id":"...","note":"..."}],'
        '"framework_level_suggestions":["建议"]}\n\n'
        + json.dumps({"candidates": sample}, ensure_ascii=False)
    )
    try:
        parsed = parse_json_object(
            chat(
                prompt,
                max_tokens=2600,
                temperature=0.15,
                timeout=180,
                provider=provider,
                env_prefix="QUANTA_FRAMEWORK_OPTIMIZER",
            )
        )
        return {
            "method": provider,
            "status": "succeeded",
            "overall_assessment": str(parsed.get("overall_assessment") or ""),
            "accepted": parsed.get("accepted") if isinstance(parsed.get("accepted"), list) else [],
            "needs_human_review": parsed.get("needs_human_review") if isinstance(parsed.get("needs_human_review"), list) else [],
            "rejected": parsed.get("rejected") if isinstance(parsed.get("rejected"), list) else [],
            "priority_notes": parsed.get("priority_notes") if isinstance(parsed.get("priority_notes"), list) else [],
            "framework_level_suggestions": parsed.get("framework_level_suggestions") if isinstance(parsed.get("framework_level_suggestions"), list) else [],
        }
    except Exception as exc:
        return {
            "method": provider,
            "status": "failed",
            "error": str(exc),
            "overall_assessment": "",
            "accepted": [],
            "needs_human_review": [],
            "rejected": [],
            "priority_notes": [],
            "framework_level_suggestions": [],
        }


def build_framework_weight_optimization(
    current_run: str | Path,
    root: str | Path | None = None,
    *,
    history_limit: int = 20,
    use_llm: bool = False,
    provider: str | None = None,
    llm_candidate_limit: int = 80,
) -> dict[str, Any]:
    root_path = quanta_data_root(root)
    current_path = Path(current_run).expanduser()
    run_paths = _selected_runs(root_path, current_path, history_limit)
    runs = [_load_run(path) for path in run_paths]
    current = _load_run(current_path)
    hist_stats = _build_history_stats(runs)
    registry = _dimension_registry(root_path)
    candidates: list[dict[str, Any]] = []
    action_counts: Counter[str] = Counter()
    current_assets = (current.get("dimension_scores") or {}).get("assets") or {}
    current_theses = ((current.get("trade_thesis") or {}).get("assets") or {})

    for asset, payload in sorted(current_assets.items()):
        divergence_status = ((current_theses.get(asset) or {}).get("score_divergence") or {}).get("status", "")
        for row in payload.get("dimensions") or []:
            if not isinstance(row, dict):
                continue
            dimension_id = _dimension_key(row)
            hist = hist_stats.get((asset, dimension_id), {})
            registry_row = registry.get((asset, str(row.get("dimension_id") or ""))) or registry.get((asset, str(row.get("dimension_label") or ""))) or {}
            candidate = _candidate_row(
                asset=asset,
                row=row,
                registry_row=registry_row,
                hist=hist,
                divergence_status=str(divergence_status),
                current_run=str(current_path),
            )
            action_counts[candidate["action"]] += 1
            candidates.append(candidate)

    candidates.sort(
        key=lambda item: (
            item["action"] not in {"split_dimension_or_lower_dynamic_weight", "review_scoring_polarity", "lower_base_weight"},
            -abs(float((item.get("current_metrics") or {}).get("weighted_contribution") or 0.0)),
        )
    )
    selected_provider = provider or first_env("QUANTA_FRAMEWORK_OPTIMIZER_PROVIDER", default="m3")
    llm_review = (
        _llm_review_candidates(candidates, provider=selected_provider, limit=llm_candidate_limit)
        if use_llm and candidates
        else {"method": "none", "status": "skipped", "provider": selected_provider}
    )
    return {
        "schema_version": "framework_weight_optimization.v1",
        "status": "candidate",
        "generated_at": utc_now_iso(),
        "current_run": str(current_path),
        "history_runs": [str(path) for path in run_paths],
        "optimization_policy": {
            "base_weight": "历史经验和人工框架先验，只生成候选，不自动写回。",
            "dynamic_weight": "按当日证据强度、冲突率、方向共识、原始分歧动态调整。",
            "write_back": "human_review_required",
            "model_role": "m3/minimax 仅做候选复核和框架优化建议，不直接修改框架。",
        },
        "stats": {
            "asset_count": len(current_assets),
            "candidate_count": len(candidates),
            "action_counts": dict(action_counts),
            "history_run_count": len(run_paths),
            "llm_enabled": use_llm,
            "llm_provider": selected_provider,
        },
        "llm_review": llm_review,
        "candidates": candidates,
    }


def publish_framework_weight_optimization(
    current_run: str | Path,
    root: str | Path | None = None,
    *,
    history_limit: int = 20,
    use_llm: bool = False,
    provider: str | None = None,
    llm_candidate_limit: int = 80,
) -> dict[str, Any]:
    root_path = quanta_data_root(root)
    payload = build_framework_weight_optimization(
        current_run,
        root_path,
        history_limit=history_limit,
        use_llm=use_llm,
        provider=provider,
        llm_candidate_limit=llm_candidate_limit,
    )
    now = datetime.now()
    yyyy, mm, dd = dated_parts(now.strftime("%Y%m%d"))
    base = root_path / "agent_workspace" / "candidates" / "framework_optimization" / yyyy / mm / dd
    latest = root_path / "agent_workspace" / "candidates" / "framework_optimization" / "latest"
    output_path = base / f"framework-weight-optimization-{now.strftime('%H%M%S')}.json"
    latest_path = latest / "framework-weight-optimization.json"
    write_json(output_path, payload)
    write_json(latest_path, payload)
    return {
        "optimization": relative_to_root(output_path, root_path),
        "latest": relative_to_root(latest_path, root_path),
        "payload": payload,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Build framework weight optimization candidates from daily logic runs.")
    parser.add_argument("--current-run", required=True, help="Current RUN-*-RAW-DAILY directory.")
    parser.add_argument("--quanta-root", help="Override GJ_QUANTA_DATA_ROOT.")
    parser.add_argument("--history-limit", type=int, default=20)
    parser.add_argument("--llm", action="store_true", help="Use m3/minimax to review candidates.")
    parser.add_argument("--provider", default=None, help="LLM provider for review, default QUANTA_FRAMEWORK_OPTIMIZER_PROVIDER or m3.")
    parser.add_argument("--llm-candidate-limit", type=int, default=80)
    args = parser.parse_args(argv)
    result = publish_framework_weight_optimization(
        args.current_run,
        args.quanta_root,
        history_limit=args.history_limit,
        use_llm=args.llm,
        provider=args.provider,
        llm_candidate_limit=args.llm_candidate_limit,
    )
    stats = result["payload"]["stats"]
    print(f"框架权重优化候选 → {result['optimization']}")
    print(
        "assets={asset_count} candidates={candidate_count} history_runs={history_run_count} llm={llm_enabled}".format(
            **stats
        )
    )


if __name__ == "__main__":
    main()
