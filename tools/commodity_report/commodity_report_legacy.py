from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

from quanta_agents.core.config import quanta_data_root
from quanta_agents.core.io import dated_parts, sha256_bytes, utc_now_iso, write_json
from quanta_agents.core.llm_client import chat
from quanta_agents.core.llm_json import parse_json_object
from quanta_agents.core.taxonomy import load_asset_taxonomy

from quanta_agents.futures_daily.evidence_framework import (
    apply_dimension_summaries_to_trade_theses,
    build_asset_dimension_summaries,
    build_evidence_nodes_from_reports,
)
from quanta_agents.futures_daily.framework_alignment import build_framework_alignment
from quanta_agents.futures_daily.logic_chain import (
    build_logic_chain_bundle,
    build_logic_chains,
    build_logic_summary,
)


def _chat(prompt: str, *, max_tokens: int = 4000, temperature: float = 0.3, timeout: int = 180) -> str:
    return chat(prompt, max_tokens=max_tokens, temperature=temperature, timeout=timeout)


def _load_rows(raw_folder: Path) -> list[dict[str, Any]]:
    rows_path = raw_folder / "rows.jsonl"
    if not rows_path.exists():
        raise FileNotFoundError(f"日报原文目录缺少 rows.jsonl：{rows_path}")
    rows = []
    for line in rows_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            item = json.loads(line)
            if isinstance(item, dict):
                rows.append(item)
    return rows


def _text(row: dict[str, Any]) -> str:
    return f"{row.get('title') or ''}\n{row.get('content') or ''}".strip()


def _text_sha(row: dict[str, Any]) -> str:
    return sha256_bytes(_text(row).encode("utf-8"))


def _safe_name(value: str, fallback: str = "report") -> str:
    text = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", str(value or "").strip())
    text = text.strip("._")
    return (text or fallback)[:90]


def _bounded_score(value: Any, *, integer: bool = False) -> float | int:
    try:
        score = float(str(value).strip())
    except (TypeError, ValueError):
        score = 0.0
    score = max(-10.0, min(10.0, score))
    if integer:
        return int(round(score))
    return round(score, 4)


def _bounded_weight(value: Any, default: float = 0.5) -> float:
    try:
        weight = float(str(value).strip())
    except (TypeError, ValueError):
        weight = default
    return round(max(0.0, min(1.0, weight)), 4)


def _as_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item or "").strip()]


def _dedupe(values: list[str], limit: int | None = None) -> list[str]:
    seen = set()
    result = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
        if limit and len(result) >= limit:
            break
    return result


def _truncate_data(data: Any, max_items: int = 5, max_length: int = 80) -> list[str]:
    return [(item[:max_length] + "..." if len(item) > max_length else item) for item in _as_list(data)[:max_items]]


def _chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def _json_chat(prompt: str, *, max_tokens: int, temperature: float = 0.3, timeout: int = 180) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            return parse_json_object(_chat(prompt, max_tokens=max_tokens, temperature=temperature, timeout=timeout))
        except Exception as exc:
            last_error = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(str(last_error) if last_error else "unknown llm json error")


def _normalize_report_chunk(
    row: dict[str, Any],
    assets: list[str],
    payload: dict[str, Any],
) -> tuple[dict[str, int], dict[str, dict[str, Any]]]:
    scores_raw = payload.get("sentiment_scores") or {}
    details_raw = payload.get("detailed_analysis") or {}
    if not isinstance(scores_raw, dict):
        scores_raw = {}
    if not isinstance(details_raw, dict):
        details_raw = {}

    scores: dict[str, int] = {}
    details: dict[str, dict[str, Any]] = {}
    report_text = _text(row)
    for asset in assets:
        detail = details_raw.get(asset)
        if not isinstance(detail, dict):
            detail = {}
        score = _bounded_score(detail.get("sentiment_score", scores_raw.get(asset, 0)), integer=True)
        scores[asset] = int(score)
        details[asset] = {
            "item": asset,
            "commodity": asset,
            "bullish_factors": _as_list(detail.get("bullish_factors")),
            "bearish_factors": _as_list(detail.get("bearish_factors")),
            "key_data": _as_list(detail.get("key_data")),
            "key_events": _as_list(detail.get("key_events")),
            "supply_demand": _as_list(detail.get("supply_demand")),
            "price_forecast": _as_list(detail.get("price_forecast")),
            "sentiment_score": int(score),
            "influence_score": _bounded_weight(detail.get("influence_score"), default=0.5),
            "source_report_hash": sha256_bytes(report_text.encode("utf-8")),
            "timestamp": datetime.now().isoformat(),
        }
    return scores, details


def _analyze_report_chunk(
    row: dict[str, Any],
    assets: list[str],
    *,
    taxonomy_prompt: str,
) -> tuple[dict[str, int], dict[str, dict[str, Any]]]:
    assets_json = json.dumps(assets, ensure_ascii=False)
    prompt = f"""你是旧版 commodity_report 的期货品种研报分析器。请对「单篇研报」进行分析。

这一步对应旧代码 ReportAnalyzer：
1. 对标准化后的品种逐一给出 sentiment_score；
2. 对每个品种提取 detailed_analysis；
3. 输出结构必须能被后续 ReportProcessor 按品种合并。

本篇研报已由本地 taxonomy 识别出这些标准品种，只分析这些品种，不要新增品种：
{assets_json}

参考标准品种表：
{taxonomy_prompt}

严格输出 JSON，不要 Markdown：
{{
  "sentiment_scores": {{"品种": 0}},
  "detailed_analysis": {{
    "品种": {{
      "commodity": "品种",
      "bullish_factors": ["利多因素"],
      "bearish_factors": ["利空因素"],
      "key_data": ["重要数据"],
      "key_events": ["重要事件"],
      "supply_demand": ["供需情况分析"],
      "price_forecast": ["价格预测"],
      "sentiment_score": 0,
      "influence_score": 0.5
    }}
  }}
}}

要求：
1. sentiment_score 是整数，范围 -10 到 10，10 表示强烈看多，-10 表示强烈看空。
2. 打分根据本篇研报里的宏观和基本面论据判断，不要被短期价格波动、技术位、主力合约涨跌单独影响。
3. detailed_analysis 必须覆盖输入品种列表里的每个品种；如果只是顺带提及或没有论据，也要保留该品种并给空数组/0分。
4. influence_score 取 0 到 1，表示本篇研报对该品种结论的影响力；直接专题、高质量数据、论据充分则高，泛泛提及或仅行情信息则低。
5. 尽可能详尽地罗列本篇研报中的论据，但不要编造；每个数组建议 0 到 5 条。

研报信息：
row_id={row.get("row_id") or ""}
机构={row.get("org_name") or ""}
标题={row.get("title") or ""}

研报全文：
{_text(row)}
"""
    max_tokens = min(14000, max(3200, 1200 + len(assets) * 520))
    payload = _json_chat(prompt, max_tokens=max_tokens, temperature=0.3, timeout=220)
    return _normalize_report_chunk(row, assets, payload)


def _empty_report_result(row: dict[str, Any], assets: list[str], error: str = "") -> dict[str, Any]:
    detailed = {}
    scores = {}
    for asset in assets:
        scores[asset] = 0
        detailed[asset] = {
            "item": asset,
            "commodity": asset,
            "bullish_factors": [],
            "bearish_factors": [],
            "key_data": [],
            "key_events": [],
            "supply_demand": [],
            "price_forecast": [],
            "sentiment_score": 0,
            "influence_score": 0.0,
            "analysis_error": error,
            "source_report_hash": _text_sha(row),
            "timestamp": datetime.now().isoformat(),
        }
    return {
        "title": row.get("title"),
        "org_name": row.get("org_name"),
        "date": row.get("report_date") or row.get("date"),
        "sentiment_scores": scores,
        "detailed_analysis": detailed,
        "source_report_hash": _text_sha(row),
        "analysis_date": datetime.now().isoformat(),
        "analyzer_type": "commodity",
        "_metadata": {"analysis_error": error, "row_id": row.get("row_id")},
    }


def _analyze_single_report(row: dict[str, Any], assets: list[str], taxonomy_prompt: str) -> dict[str, Any]:
    all_scores: dict[str, int] = {}
    all_details: dict[str, dict[str, Any]] = {}
    errors = []
    for chunk in _chunks(assets, 12):
        try:
            scores, details = _analyze_report_chunk(row, chunk, taxonomy_prompt=taxonomy_prompt)
            all_scores.update(scores)
            all_details.update(details)
        except Exception as exc:
            errors.append(str(exc))
            empty = _empty_report_result(row, chunk, str(exc))
            all_scores.update(empty["sentiment_scores"])
            all_details.update(empty["detailed_analysis"])

    return {
        "title": row.get("title"),
        "org_name": row.get("org_name"),
        "date": row.get("report_date") or row.get("date"),
        "sentiment_scores": all_scores,
        "detailed_analysis": all_details,
        "source_report_hash": _text_sha(row),
        "analysis_date": datetime.now().isoformat(),
        "analyzer_type": "commodity",
        "_metadata": {
            "row_id": row.get("row_id"),
            "raw_text_sha256": _text_sha(row),
            "detected_assets": assets,
            "raw": row.get("raw") or {},
            "analysis_errors": errors,
            "logic": "tmp_code.commodity_report.ReportAnalyzer compatible single-report JSON",
        },
    }


def _report_output_path(run_dir: Path, row: dict[str, Any]) -> Path:
    org = _safe_name(str(row.get("org_name") or "unknown"), "org")
    title = _safe_name(str(row.get("title") or "report"), "report")
    date = str(row.get("report_date") or row.get("date") or "unknown").replace("-", "")[:8]
    row_id = _safe_name(str(row.get("row_id") or ""), "row")
    return run_dir / "per_report" / org / f"{date}_{org}_{row_id}_{title}.json"


def _single_report_cache_dir(root_path: Path, yyyy: str, mm: str, dd: str) -> Path:
    return (
        root_path
        / "agent_workspace"
        / "candidates"
        / "futures_daily_single_report_analysis"
        / yyyy
        / mm
        / dd
        / "COMMODITY-LEGACY"
    )


def _load_cached_report(path: Path, row: dict[str, Any], assets: list[str]) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(cached, dict):
        return None
    metadata = cached.get("_metadata") if isinstance(cached.get("_metadata"), dict) else {}
    if str(metadata.get("row_id") or "") != str(row.get("row_id") or ""):
        return None
    cached_text_sha = metadata.get("raw_text_sha256") or cached.get("source_report_hash")
    if cached_text_sha != _text_sha(row):
        return None
    if metadata.get("analysis_errors"):
        return None
    details = cached.get("detailed_analysis") if isinstance(cached.get("detailed_analysis"), dict) else {}
    cached_assets = metadata.get("detected_assets") or list(details)
    if sorted(str(asset) for asset in cached_assets) != sorted(str(asset) for asset in assets):
        return None
    if any(asset not in details for asset in assets):
        return None
    return cached


def _merge_prompt(asset: str, processed_reports: list[dict[str, Any]]) -> str:
    input_data = {"commodity": asset, "reports": processed_reports}
    return f"""请按照旧版 commodity_report 的 ReportProcessor 逻辑，汇总多篇研报对同一期货品种的分析。

返回严格 JSON：
{{
  "commodity": "{asset}",
  "bullish_factors": ["合并后的利多因素1", "利多因素2"],
  "bearish_factors": ["合并后的利空因素1", "利空因素2"],
  "key_data": ["重要数据1", "重要数据2"],
  "key_events": ["重要事件1", "重要事件2"],
  "supply_demand": ["供需分析1", "供需分析2"],
  "price_forecast": ["价格预测1", "价格预测2"]
}}

重要要求：
1. JSON 必须完整闭合，所有字符串用双引号包裹。
2. 内容要判断是否和「{asset}」相关，不相关的要去除。
3. 合并重复内容，优先保留重要数据、关键事件、方向性强的基本面论据。
4. 不要把报告级审计理由直接拼进去，要写成面向日报读者的正常研报总结。
5. 不要在任何值中使用换行符。

reports_json:
{json.dumps(input_data, ensure_ascii=False)}
"""


def _fallback_merge(asset: str, reports: list[tuple[dict[str, Any], str]]) -> dict[str, Any]:
    report_data = [item[0] for item in reports]
    return {
        "commodity": asset,
        "bullish_factors": _dedupe([text for report in report_data for text in _as_list(report.get("bullish_factors"))], 8),
        "bearish_factors": _dedupe([text for report in report_data for text in _as_list(report.get("bearish_factors"))], 8),
        "key_data": _dedupe([text for report in report_data for text in _as_list(report.get("key_data"))], 8),
        "key_events": _dedupe([text for report in report_data for text in _as_list(report.get("key_events"))], 8),
        "supply_demand": _dedupe([text for report in report_data for text in _as_list(report.get("supply_demand"))], 8),
        "price_forecast": _dedupe([text for report in report_data for text in _as_list(report.get("price_forecast"))], 8),
    }


def _merge_asset_reports(asset: str, reports: list[tuple[dict[str, Any], str]]) -> dict[str, Any]:
    report_data = [item[0] for item in reports]
    file_paths = [item[1] for item in reports]
    influence_scores = [_bounded_weight(report.get("influence_score"), default=0.5) for report in report_data]
    original_scores = [float(_bounded_score(report.get("sentiment_score"), integer=False)) for report in report_data]
    total_weight = sum(influence_scores)
    weighted_score = (
        sum(score * weight for score, weight in zip(original_scores, influence_scores)) / total_weight
        if total_weight > 0
        else 0.0
    )

    if len(report_data) == 1:
        merged = {
            **report_data[0],
            "commodity": asset,
        }
    else:
        processed_reports = []
        for report in report_data:
            processed_reports.append(
                {
                    "bullish_factors": _truncate_data(report.get("bullish_factors"), 3, 80),
                    "bearish_factors": _truncate_data(report.get("bearish_factors"), 2, 80),
                    "key_data": _truncate_data(report.get("key_data"), 3, 50),
                    "key_events": _truncate_data(report.get("key_events"), 2, 80),
                    "supply_demand": _truncate_data(report.get("supply_demand"), 2, 80),
                    "price_forecast": _truncate_data(report.get("price_forecast"), 2, 80),
                    "sentiment_score": report.get("sentiment_score", 0),
                    "influence_score": report.get("influence_score", 0.5),
                }
            )
        try:
            merged = _json_chat(_merge_prompt(asset, processed_reports), max_tokens=3600, temperature=0.25, timeout=160)
        except Exception as exc:
            merged = _fallback_merge(asset, reports)
            merged["merge_error"] = str(exc)

    merged["commodity"] = asset
    merged["item"] = asset
    merged["sentiment_score"] = weighted_score
    merged["source_report_hash"] = sha256_bytes(json.dumps(report_data, ensure_ascii=False, default=str).encode("utf-8"))
    merged["timestamp"] = datetime.now().isoformat()
    merged["original_sources"] = file_paths
    merged["original_scores"] = original_scores
    merged["influence_scores"] = influence_scores
    merged["weighted_sentiment_score"] = weighted_score
    return merged


def _market_review_prompt(summary: dict[str, Any]) -> str:
    details = summary.get("detailed_analysis") or {}
    events_data = {
        asset: details.get(asset, {}).get("key_events", [])
        for asset in details
        if asset != "_metadata"
    }
    logic_data = {
        asset: {
            "bullish_factors": details.get(asset, {}).get("bullish_factors", []),
            "bearish_factors": details.get(asset, {}).get("bearish_factors", []),
            "sentiment_score": details.get(asset, {}).get("sentiment_score", 0),
        }
        for asset in details
        if asset != "_metadata"
    }
    return f"""你是 ModelQuanta 期货市场每日观察的主编。请根据商品品种汇总 JSON，生成旧版 commodity_report 风格的市场总览。

严格输出 JSON：
{{
  "market_events_summary": "1. **主题**：内容...",
  "market_logic_summary": "当前市场核心逻辑..."
}}

要求：
1. market_events_summary 总结“今天发生了什么”，按 4-6 条主题归纳，不要逐品种流水账，不要拼接原始因素。
2. market_logic_summary 总结“市场在交易什么”，按宏观、能源化工、黑色、有色/贵金属、农产品、新能源/金融等主线组织。
3. 语言要像旧版日报样例：有标题、有归纳、有品种映射，重点突出，避免把所有细节都堆进去。
4. 如果多个品种反映同一逻辑，合并成主题；只保留最重要的数据或事件。

所有品种事件：
{json.dumps(events_data, ensure_ascii=False)}

所有品种逻辑：
{json.dumps(logic_data, ensure_ascii=False)}
"""


def _market_review(summary: dict[str, Any]) -> dict[str, Any]:
    try:
        payload = _json_chat(_market_review_prompt(summary), max_tokens=3600, temperature=0.35, timeout=180)
        return {
            "market_events_summary": str(payload.get("market_events_summary") or ""),
            "market_logic_summary": str(payload.get("market_logic_summary") or ""),
            "timestamp": datetime.now().isoformat(),
            "analysis_method": "m3_commodity_report_legacy_market_review",
        }
    except Exception as exc:
        details = summary.get("detailed_analysis") or {}
        top = sorted(
            details.values(),
            key=lambda item: abs(float(item.get("sentiment_score") or 0)),
            reverse=True,
        )[:12]
        events = _dedupe([text for item in top for text in _as_list(item.get("key_events"))], 8)
        logic = []
        for item in top[:8]:
            asset = item.get("commodity") or item.get("item")
            score = float(item.get("sentiment_score") or 0)
            factors = item.get("bullish_factors") if score >= 0 else item.get("bearish_factors")
            first = (_as_list(factors) or ["暂无核心因素"])[0]
            logic.append(f"{asset}({score:.2f})：{first}")
        return {
            "market_events_summary": "\n".join(f"{idx}. {text}" for idx, text in enumerate(events, 1)),
            "market_logic_summary": "\n".join(logic),
            "timestamp": datetime.now().isoformat(),
            "analysis_method": "fallback_commodity_report_legacy_market_review",
            "error": str(exc),
        }


def run_legacy_from_raw_folder(
    raw_folder: str | Path,
    root: str | Path | None = None,
    *,
    use_llm: bool = True,
    max_assets: int | None = None,
    max_workers: int = 1,
    use_llm_postprocess: bool | None = None,
) -> dict[str, Any]:
    if not use_llm:
        raise RuntimeError("commodity_report legacy runner requires LLM; use raw_run with --no-llm for rule fallback")
    raw_path = Path(raw_folder).expanduser()
    manifest = json.loads((raw_path / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("status") != "available":
        raise RuntimeError(f"日报原文不可用：{raw_path} status={manifest.get('status')}")

    date_key = str(manifest["date"]).replace("-", "")
    root_path = quanta_data_root(root)
    yyyy, mm, dd = dated_parts(date_key)
    run_id = f"RUN-{date_key}-{datetime.now().strftime('%H%M%S')}-COMMODITY-LEGACY"
    run_dir = root_path / "agent_workspace" / "candidates" / "futures_daily_raw_runs" / yyyy / mm / dd / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = _single_report_cache_dir(root_path, yyyy, mm, dd)

    taxonomy = load_asset_taxonomy(root_path, required=True)
    taxonomy_prompt = taxonomy.prompt_asset_list()
    rows = _load_rows(raw_path)
    report_jobs = []
    asset_report_map: dict[str, list[tuple[dict[str, Any], str]]] = {}

    for index, source_row in enumerate(rows, 1):
        row = {**source_row}
        row_id = str(row.get("row_id") or f"row-{index:04d}")
        row["row_id"] = row_id
        text = _text(row)
        assets = list(dict.fromkeys(hit.label for hit in taxonomy.classify(text) if hit.kind == "variety"))
        if max_assets and max_assets > 0:
            allowed = set(taxonomy.names()[:max_assets])
            assets = [asset for asset in assets if asset in allowed]
        if assets:
            report_jobs.append((row, assets))

    print(f"commodity_report legacy single-report analysis: {len(report_jobs)} source reports", flush=True)

    def analyze_job(job: tuple[dict[str, Any], list[str]]) -> tuple[dict[str, Any], Path, bool]:
        row, assets = job
        run_report_path = _report_output_path(run_dir, row)
        cache_report_path = _report_output_path(cache_dir, row)
        run_report_path.parent.mkdir(parents=True, exist_ok=True)
        cached = _load_cached_report(cache_report_path, row, assets)
        if cached is not None:
            report_json = {**cached}
            metadata = dict(report_json.get("_metadata") or {})
            metadata["cache_status"] = "hit"
            metadata["cache_source_path"] = str(cache_report_path)
            report_json["_metadata"] = metadata
            write_json(run_report_path, report_json)
            return report_json, run_report_path, True

        report_json = _analyze_single_report(row, assets, taxonomy_prompt)
        metadata = dict(report_json.get("_metadata") or {})
        metadata["cache_status"] = "miss_generated"
        metadata["cache_path"] = str(cache_report_path)
        report_json["_metadata"] = metadata
        write_json(cache_report_path, report_json)
        write_json(run_report_path, report_json)
        return report_json, run_report_path, False

    report_results: list[tuple[dict[str, Any], Path]] = []
    cached_report_count = 0
    generated_report_count = 0
    if max_workers > 1 and len(report_jobs) > 1:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(analyze_job, job) for job in report_jobs]
            for completed, future in enumerate(as_completed(futures), 1):
                report_json, report_path, from_cache = future.result()
                report_results.append((report_json, report_path))
                cached_report_count += 1 if from_cache else 0
                generated_report_count += 0 if from_cache else 1
                if completed % 5 == 0 or completed == len(futures):
                    print(
                        "commodity_report legacy single-report analysis: "
                        f"{completed}/{len(futures)} "
                        f"(cache hit {cached_report_count}, generated {generated_report_count})",
                        flush=True,
                    )
    else:
        for completed, job in enumerate(report_jobs, 1):
            report_json, report_path, from_cache = analyze_job(job)
            report_results.append((report_json, report_path))
            cached_report_count += 1 if from_cache else 0
            generated_report_count += 0 if from_cache else 1
            if completed % 5 == 0 or completed == len(report_jobs):
                print(
                    "commodity_report legacy single-report analysis: "
                    f"{completed}/{len(report_jobs)} "
                    f"(cache hit {cached_report_count}, generated {generated_report_count})",
                    flush=True,
                )

    for report_json, report_path in report_results:
        for asset, detail in (report_json.get("detailed_analysis") or {}).items():
            if isinstance(detail, dict):
                asset_report_map.setdefault(asset, []).append((detail, str(report_path)))

    ordered_assets = sorted(asset_report_map, key=lambda name: len(asset_report_map[name]), reverse=True)
    if max_assets and max_assets > 0:
        ordered_assets = ordered_assets[:max_assets]

    print(f"commodity_report legacy merge: {len(ordered_assets)} assets", flush=True)

    def merge_job(asset: str) -> tuple[str, dict[str, Any]]:
        return asset, _merge_asset_reports(asset, asset_report_map[asset])

    merged_results: dict[str, dict[str, Any]] = {}
    if max_workers > 1 and len(ordered_assets) > 1:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(merge_job, asset) for asset in ordered_assets]
            for completed, future in enumerate(as_completed(futures), 1):
                asset, merged = future.result()
                merged_results[asset] = merged
                if completed % 5 == 0 or completed == len(futures):
                    print(f"commodity_report legacy merge: {completed}/{len(futures)}", flush=True)
    else:
        for completed, asset in enumerate(ordered_assets, 1):
            asset, merged = merge_job(asset)
            merged_results[asset] = merged
            if completed % 5 == 0 or completed == len(ordered_assets):
                print(f"commodity_report legacy merge: {completed}/{len(ordered_assets)}", flush=True)

    detailed = {asset: merged_results[asset] for asset in ordered_assets}
    sentiment_scores = {
        asset: detailed[asset].get("sentiment_score", 0)
        for asset in ordered_assets
        if isinstance(detailed.get(asset), dict)
    }
    summary = {
        "title": "综合commodity分析报告",
        "org_name": "合并报告",
        "date": date_key,
        "sentiment_scores": sentiment_scores,
        "detailed_analysis": detailed,
        "source_report_hash": sha256_bytes((raw_path / "rows.jsonl").read_bytes()),
        "analysis_date": datetime.now().isoformat(),
        "analyzer_type": "commodity",
        "_metadata": {
            "processing_date": datetime.now().isoformat(),
            "source_date": date_key,
            "raw_folder": str(raw_path),
            "raw_manifest": manifest,
            "taxonomy_source": str(taxonomy.source_path) if taxonomy.source_path else "",
            "single_report_cache_dir": str(cache_dir),
            "single_report_cache_hits": cached_report_count,
            "single_report_generated": generated_report_count,
            "source_file_count": len(report_results),
            "total_commodities": len(detailed),
            "single_report_commodities": sum(1 for asset in ordered_assets if len(asset_report_map[asset]) == 1),
            "merged_commodities": sum(1 for asset in ordered_assets if len(asset_report_map[asset]) > 1),
            "total_assets_detected": len(asset_report_map),
            "assets_returned": len(detailed),
            "logic": "tmp_code.commodity_report adapted to quanta_data raw_objects; M3 direct runner",
            "scoring_method": "per_report_integer_score_then_influence_weighted_merge",
            "max_workers": max_workers,
        },
    }

    market_review = _market_review(summary)
    postprocess_llm = use_llm if use_llm_postprocess is None else use_llm_postprocess
    # Full-run LLM refinement in framework_alignment is per asset and too slow for
    # daily production. Keep deterministic mapping here; use M3 for report merge
    # and all-day logic synthesis, then review low-confidence mappings separately.
    alignment = build_framework_alignment(summary, root_path, use_llm_refinement=False)
    evidence_nodes = build_evidence_nodes_from_reports(
        report_results,
        root_path,
        use_llm_refinement=False,
    )
    asset_dimension_summaries = build_asset_dimension_summaries(evidence_nodes)
    logic_bundle = build_logic_chain_bundle(
        summary,
        alignment,
        use_llm_for_thesis=False,
        use_llm_for_summary=False,
    )
    logic_bundle["trade_thesis"] = apply_dimension_summaries_to_trade_theses(
        logic_bundle["trade_thesis"],
        asset_dimension_summaries,
    )
    logic_bundle["logic_chains"] = build_logic_chains(
        summary,
        alignment,
        logic_bundle["dimension_scores"],
        logic_bundle["trade_thesis"],
    )
    logic_bundle["logic_summary"] = build_logic_summary(
        summary,
        alignment,
        logic_bundle["dimension_scores"],
        logic_bundle["trade_thesis"],
        logic_bundle["logic_chains"],
        use_llm=postprocess_llm,
    )
    logic_bundle["logic_summary"]["evidence_enhancement"] = {
        "evidence_nodes": "evidence_nodes.json",
        "asset_dimension_summaries": "asset_dimension_summaries.json",
        "method": "per_report_evidence_nodes_to_dimension_summary",
    }

    write_json(run_dir / f"{date_key}_commodity_summary.json", summary)
    write_json(run_dir / f"{date_key}_commodity_marketreview.json", market_review)
    write_json(run_dir / "framework_alignment.json", alignment)
    write_json(run_dir / "evidence_nodes.json", evidence_nodes)
    write_json(run_dir / "asset_dimension_summaries.json", asset_dimension_summaries)
    write_json(run_dir / "dimension_scores.json", logic_bundle["dimension_scores"])
    write_json(run_dir / "trade_thesis.json", logic_bundle["trade_thesis"])
    write_json(run_dir / "logic_chains.json", logic_bundle["logic_chains"])
    write_json(run_dir / "logic_summary.json", logic_bundle["logic_summary"])
    write_json(run_dir / "framework_update_candidates.json", logic_bundle["framework_update_candidates"])
    write_json(run_dir / "framework_format_review.json", logic_bundle["framework_format_review"])
    write_json(
        run_dir / "manifest.json",
        {
            "schema_version": "futures_daily_raw_run_manifest.v1",
            "status": "candidate",
            "run_id": run_id,
            "date": date_key,
            "raw_folder": str(raw_path),
            "generated_at": utc_now_iso(),
            "logic": "commodity_report_legacy",
            "outputs": {
                "summary": f"{date_key}_commodity_summary.json",
                "market_review": f"{date_key}_commodity_marketreview.json",
                "framework_alignment": "framework_alignment.json",
                "evidence_nodes": "evidence_nodes.json",
                "asset_dimension_summaries": "asset_dimension_summaries.json",
                "dimension_scores": "dimension_scores.json",
                "trade_thesis": "trade_thesis.json",
                "logic_chains": "logic_chains.json",
                "logic_summary": "logic_summary.json",
                "framework_update_candidates": "framework_update_candidates.json",
                "framework_format_review": "framework_format_review.json",
            },
        },
    )

    return {
        "run_dir": str(run_dir),
        "summary": summary,
        "market_review": market_review,
        "alignment": alignment,
        "evidence_nodes": evidence_nodes,
        "asset_dimension_summaries": asset_dimension_summaries,
        "logic_bundle": logic_bundle,
    }
