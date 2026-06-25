from __future__ import annotations

import argparse
import hashlib
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from quanta_agents.core.config import quanta_data_root
from quanta_agents.core.io import dated_parts, read_json, relative_to_root, utc_now_iso, write_json
from quanta_agents.core.taxonomy import AssetTaxonomy, load_asset_taxonomy
from quanta_agents.research_reports.wechat_evidence import (
    RAW_WECHAT_RELATIVE,
    _canonical_date,
    _chunk_section,
    _clean_spaces,
    _hash_id,
    _infer_direction,
    _is_market_observation,
    _primary_chunk_assets,
    _split_article_sections,
    _variety_names,
    load_wechat_articles,
)


STORE_VERSION = "wechat_single_report_profile.v1"
EXTRACTION_RULESET = "rule_first_title_fallback.v1"

KEY_DATA_PATTERN = re.compile(
    r"(\d{1,4}(?:\.\d+)?\s*(?:%|万吨|吨|万桶|万手|亿元|美元/桶|美元/吨|元/吨|元/斤|GW|TEU|点|BP|个基点))",
    re.IGNORECASE,
)
EVENT_WORDS = (
    "会议",
    "政策",
    "关税",
    "制裁",
    "冲突",
    "停火",
    "封锁",
    "通航",
    "检修",
    "复产",
    "减产",
    "增产",
    "收储",
    "抛储",
    "发布",
    "公布",
    "上调",
    "下调",
    "美联储",
    "央行",
    "天气",
    "干旱",
)
SUPPLY_DEMAND_WORDS = (
    "供应",
    "供给",
    "产量",
    "产能",
    "开工",
    "检修",
    "进口",
    "出口",
    "到港",
    "发运",
    "库存",
    "仓单",
    "需求",
    "消费",
    "成交",
    "订单",
    "终端",
    "利润",
    "成本",
)
FORECAST_WORDS = (
    "预计",
    "预期",
    "后市",
    "短期",
    "中期",
    "日内观点",
    "中期观点",
    "参考策略",
    "运行区间",
    "震荡",
    "偏强",
    "偏弱",
    "看多",
    "看空",
    "逢低",
    "逢高",
    "压力",
    "支撑",
)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _date_range(start_date: str, end_date: str) -> list[str]:
    start_key = _canonical_date(start_date)
    end_key = _canonical_date(end_date)
    start = datetime.strptime(start_key, "%Y%m%d")
    end = datetime.strptime(end_key, "%Y%m%d")
    if start > end:
        raise ValueError("start date must be before or equal to end date")
    values: list[str] = []
    current = start
    while current <= end:
        values.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)
    return values


def _asset_ref(taxonomy: AssetTaxonomy, name: str) -> dict[str, Any]:
    asset = next(
        (item for item in taxonomy.assets if item.get("canonical_name") == name),
        {"canonical_name": name},
    )
    return {
        "name": str(asset.get("canonical_name") or name),
        "asset_id": asset.get("asset_id"),
        "commodity_code": asset.get("commodity_code") or asset.get("exchange_code"),
        "category": asset.get("category"),
        "sector": asset.get("sector"),
    }


def _profile_id(article: dict[str, Any], text_hash: str) -> str:
    return _hash_id(
        "RREP-PROFILE",
        article.get("raw_id") or article.get("article_id"),
        text_hash,
        STORE_VERSION,
    )


def _profile_output_path(root: Path, date_key: str, profile_id: str) -> Path:
    yyyy, mm, dd = dated_parts(date_key)
    return (
        root
        / "canonical_documents"
        / "research_reports"
        / "structured_profiles"
        / "hzzhqx_wechat"
        / yyyy
        / mm
        / dd
        / f"{profile_id}.json"
    )


def _safe_name(value: str, fallback: str = "report") -> str:
    text = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", str(value or "").strip())
    text = text.strip("._")
    return (text or fallback)[:90]


def _run_dir(root: Path, end_date: str, run_id: str) -> Path:
    yyyy, mm, dd = dated_parts(end_date)
    return root / "agent_workspace" / "runs" / "research_reports" / "single_report_store" / yyyy / mm / dd / run_id


def _run_id(start_date: str, end_date: str, work_order_id: str | None = None) -> str:
    label = re.sub(r"[^A-Z0-9_-]+", "-", str(work_order_id or "WECHAT-SREPORT").upper())
    return f"RUN-{label}-{start_date}-{end_date}-{datetime.now().strftime('%H%M%S')}"


def _legacy_candidate_dir(root: Path, date_key: str, run_label: str) -> Path:
    yyyy, mm, dd = dated_parts(date_key)
    return (
        root
        / "agent_workspace"
        / "candidates"
        / "futures_daily_single_report_analysis"
        / yyyy
        / mm
        / dd
        / _safe_name(run_label, "WECHAT-PROFILE-V1")
    )


def _legacy_report_output_path(base_dir: Path, profile: dict[str, Any], date_key: str) -> Path:
    metadata = profile.get("metadata") if isinstance(profile.get("metadata"), dict) else {}
    org = _safe_name(str(metadata.get("source_account") or "unknown"), "org")
    title = _safe_name(str(metadata.get("title") or "report"), "report")
    row_id = _safe_name(str(metadata.get("raw_id") or metadata.get("article_id") or ""), "row")
    return base_dir / "per_report" / org / f"{date_key}_{org}_{row_id}_{title}.json"


def _legacy_influence_score(detail: dict[str, Any]) -> float:
    evidence_count = int(detail.get("fundamental_evidence_count") or detail.get("evidence_count") or 0)
    if evidence_count <= 0:
        return 0.25
    return round(min(0.9, 0.35 + evidence_count * 0.04), 2)


def _legacy_report_payload(profile: dict[str, Any], date_key: str) -> dict[str, Any]:
    metadata = profile.get("metadata") if isinstance(profile.get("metadata"), dict) else {}
    details: dict[str, Any] = {}
    for asset, raw_detail in (profile.get("detailed_analysis") or {}).items():
        if not isinstance(raw_detail, dict):
            continue
        detail = {
            "item": asset,
            "commodity": asset,
            "bullish_factors": raw_detail.get("bullish_factors") or [],
            "bearish_factors": raw_detail.get("bearish_factors") or [],
            "key_data": raw_detail.get("key_data") or [],
            "key_events": raw_detail.get("key_events") or [],
            "supply_demand": raw_detail.get("supply_demand") or [],
            "price_forecast": raw_detail.get("price_forecast") or [],
            "sentiment_score": raw_detail.get("sentiment_score") or 0,
            "influence_score": _legacy_influence_score(raw_detail),
            "source_report_hash": profile.get("content_hash"),
            "timestamp": profile.get("generated_at"),
        }
        details[str(asset)] = detail
    return {
        "title": metadata.get("title") or "",
        "org_name": metadata.get("source_account") or "",
        "date": date_key,
        "sentiment_scores": profile.get("sentiment_scores") or {},
        "detailed_analysis": details,
        "source_report_hash": profile.get("content_hash"),
        "analysis_date": profile.get("generated_at"),
        "analyzer_type": "commodity",
        "_metadata": {
            "row_id": metadata.get("raw_id") or metadata.get("article_id"),
            "raw_text_sha256": profile.get("content_hash"),
            "detected_assets": list(details),
            "raw": {
                "source_system": profile.get("source_system"),
                "raw_manifest": metadata.get("raw_manifest"),
                "text_path": metadata.get("text_path"),
                "source_url": metadata.get("source_url"),
            },
            "analysis_errors": [],
            "cache_status": "profile_export",
            "profile_id": profile.get("profile_id"),
            "profile_schema_version": profile.get("schema_version"),
            "profile_extraction_ruleset": (profile.get("semantic_layer") or {}).get("extraction_ruleset"),
            "logic": "quanta_agents.research_reports.single_report_store legacy-compatible export",
        },
    }


def _direction_for_profile(text: str, *, scoring_role: str) -> tuple[str, float, dict[str, Any]]:
    direction, score, meta = _infer_direction(text, scoring_role=scoring_role)
    if direction != "neutral":
        return direction, score, meta
    bullish = bool(re.search(r"偏强|走强|上涨|上行|反弹|做多|逢低|蓄力上涨", text))
    bearish = bool(re.search(r"偏弱|走弱|下跌|下行|承压|做空|逢高|弱势", text))
    if bullish == bearish:
        return direction, score, meta
    if bullish:
        return "bullish", 0.7, {"method": "profile_outlook_terms"}
    return "bearish", -0.7, {"method": "profile_outlook_terms"}


def _field_tags(text: str, direction: str) -> list[str]:
    tags: list[str] = []
    if direction == "bullish":
        tags.append("bullish_factors")
    elif direction == "bearish":
        tags.append("bearish_factors")
    if KEY_DATA_PATTERN.search(text):
        tags.append("key_data")
    if any(word in text for word in EVENT_WORDS):
        tags.append("key_events")
    if any(word in text for word in SUPPLY_DEMAND_WORDS):
        tags.append("supply_demand")
    if any(word in text for word in FORECAST_WORDS):
        tags.append("price_forecast")
    return list(dict.fromkeys(tags))


def _append_unique(target: list[str], value: str, *, limit: int = 12) -> None:
    clean = _clean_spaces(value)[:260]
    if not clean or clean in target:
        return
    target.append(clean)
    if len(target) > limit:
        del target[limit:]


def _chunk_assets(taxonomy: AssetTaxonomy, section: dict[str, Any], text: str) -> list[str]:
    section_assets = list(section.get("assets") or [])
    chunk_assets = _variety_names(taxonomy, text)
    if section.get("asset_scope") == "heading" and 0 < len(section_assets) <= 2:
        return section_assets
    if section.get("asset_scope") == "title_fallback" and 0 < len(section_assets) <= 4:
        return chunk_assets or section_assets
    if chunk_assets:
        primary_assets = _primary_chunk_assets(taxonomy, text, chunk_assets)
        if primary_assets:
            return primary_assets
        if len(chunk_assets) <= 2:
            return chunk_assets
    if len(section_assets) == 1:
        return section_assets
    return []


def _bias(score: float) -> str:
    if score >= 5:
        return "偏多"
    if score >= 1.5:
        return "小幅偏多"
    if score <= -5:
        return "偏空"
    if score <= -1.5:
        return "小幅偏空"
    return "中性"


def _build_asset_analysis(
    *,
    asset_name: str,
    taxonomy: AssetTaxonomy,
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    fields: dict[str, list[str]] = {
        "bullish_factors": [],
        "bearish_factors": [],
        "key_data": [],
        "key_events": [],
        "supply_demand": [],
        "price_forecast": [],
    }
    weighted_sum = 0.0
    weight_sum = 0.0
    for item in evidence:
        text = str(item.get("text") or "")
        for tag in item.get("field_tags") or []:
            if tag in fields:
                _append_unique(fields[tag], text)
        direction_score = float(item.get("direction_score") or 0.0)
        if not direction_score:
            continue
        weight = 1.0 if item.get("scoring_role") == "fundamental_evidence" else 0.35
        weighted_sum += direction_score * weight
        weight_sum += weight
    sentiment_score = round(
        max(-10.0, min(10.0, (weighted_sum / weight_sum) * 10 if weight_sum else 0.0)),
        1,
    )
    return {
        "asset": _asset_ref(taxonomy, asset_name),
        "commodity": asset_name,
        "sentiment_score": sentiment_score,
        "sentiment_label": _bias(sentiment_score),
        "evidence_count": len(evidence),
        "fundamental_evidence_count": sum(
            1 for item in evidence if item.get("scoring_role") == "fundamental_evidence"
        ),
        "market_observation_count": sum(
            1 for item in evidence if item.get("scoring_role") == "market_observation"
        ),
        "direction_counts": dict(Counter(str(item.get("direction") or "neutral") for item in evidence)),
        "bullish_factors": fields["bullish_factors"],
        "bearish_factors": fields["bearish_factors"],
        "key_data": fields["key_data"],
        "key_events": fields["key_events"],
        "supply_demand": fields["supply_demand"],
        "price_forecast": fields["price_forecast"],
        "source_sections": list(
            {
                str((item.get("section") or {}).get("section_id") or "")
                for item in evidence
                if (item.get("section") or {}).get("section_id")
            }
        ),
        "evidence_refs": [
            {
                "evidence_id": item.get("evidence_id"),
                "direction": item.get("direction"),
                "field_tags": item.get("field_tags") or [],
                "section_id": (item.get("section") or {}).get("section_id"),
            }
            for item in evidence[:80]
        ],
    }


def build_single_report_profile(
    article: dict[str, Any],
    *,
    taxonomy: AssetTaxonomy,
    root: str | Path | None = None,
    max_chunks_per_article: int = 160,
) -> dict[str, Any]:
    root_path = quanta_data_root(root)
    text = str(article.get("text") or "")
    text_hash = _sha256_text(text)
    report_id = _profile_id(article, text_hash)
    sections = _split_article_sections(article, taxonomy)
    if not sections:
        title_assets = _variety_names(taxonomy, str(article.get("title") or ""))
        if title_assets:
            sections = [
                {
                    "section_id": _hash_id(
                        "WSECT",
                        article.get("article_id"),
                        "title_fallback",
                        str(article.get("title") or ""),
                        text_hash[:16],
                    ),
                    "heading": str(article.get("title") or ""),
                    "assets": title_assets[:4],
                    "asset_scope": "title_fallback",
                    "text": text,
                }
            ]
    profile_sections: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    by_asset: dict[str, list[dict[str, Any]]] = defaultdict(list)
    chunk_count = 0

    for section in sections:
        section_text = str(section.get("text") or "")
        profile_sections.append(
            {
                "section_id": section.get("section_id"),
                "heading": section.get("heading") or "",
                "assets": [_asset_ref(taxonomy, name) for name in section.get("assets") or []],
                "asset_scope": section.get("asset_scope"),
                "text_hash": _sha256_text(section_text),
                "text_excerpt": _clean_spaces(section_text)[:500],
            }
        )
        for chunk_index, chunk in enumerate(_chunk_section(section, min_chars=18)):
            if chunk_count >= max_chunks_per_article:
                break
            chunk_text = _clean_spaces(str(chunk.get("text") or ""))
            assets = _chunk_assets(taxonomy, section, chunk_text)
            if not assets:
                continue
            scoring_role = "market_observation" if _is_market_observation(chunk_text) else "fundamental_evidence"
            direction, direction_score, direction_meta = _direction_for_profile(
                chunk_text,
                scoring_role=scoring_role,
            )
            tags = _field_tags(chunk_text, direction)
            for asset_name in assets[:6]:
                item = {
                    "evidence_id": _hash_id(
                        "RREP-EVID",
                        article.get("raw_id") or article.get("article_id"),
                        section.get("section_id"),
                        chunk_index,
                        asset_name,
                        _sha256_text(chunk_text)[:16],
                    ),
                    "asset": asset_name,
                    "section": {
                        "section_id": section.get("section_id"),
                        "heading": section.get("heading") or "",
                        "asset_scope": section.get("asset_scope") or "",
                    },
                    "text": chunk_text[:900],
                    "direction": direction,
                    "direction_score": direction_score,
                    "direction_inference": direction_meta,
                    "scoring_role": scoring_role,
                    "field_tags": tags,
                }
                evidence.append(item)
                by_asset[asset_name].append(item)
            chunk_count += 1
        if chunk_count >= max_chunks_per_article:
            break

    detailed_analysis = {
        asset: _build_asset_analysis(asset_name=asset, taxonomy=taxonomy, evidence=items)
        for asset, items in sorted(by_asset.items(), key=lambda row: len(row[1]), reverse=True)
    }
    sentiment_scores = {
        asset: payload["sentiment_score"]
        for asset, payload in detailed_analysis.items()
    }
    return {
        "schema_version": STORE_VERSION,
        "artifact_type": "single_research_report_profile",
        "profile_id": report_id,
        "status": "structured" if detailed_analysis else "no_asset_evidence",
        "generated_at": utc_now_iso(),
        "analysis_version": STORE_VERSION,
        "source_system": "hzzhqx_wechat",
        "content_hash": text_hash,
        "metadata": {
            "article_id": article.get("article_id"),
            "raw_id": article.get("raw_id"),
            "title": article.get("title") or "",
            "source_account": article.get("source_account") or "",
            "published_at": article.get("published_at") or "",
            "source_url": article.get("source_url"),
            "raw_manifest": relative_to_root(Path(str(article.get("raw_manifest") or "")), root_path),
            "text_path": relative_to_root(Path(str(article.get("text_path") or "")), root_path),
            "text_chars": article.get("text_chars") or len(text),
        },
        "semantic_layer": {
            "sectioning": "wechat_article_sections",
            "asset_detection": "asset_taxonomy_aliases",
            "field_extraction": "commodity_report_fields_rule_first",
            "extraction_ruleset": EXTRACTION_RULESET,
            "sentiment_scoring": "directional_evidence_score_-10_to_10",
            "llm_enrichment": "not_run",
        },
        "lineage": {
            "input_refs": [
                {
                    "ref_type": "raw_manifest",
                    "path": relative_to_root(Path(str(article.get("raw_manifest") or "")), root_path),
                    "id": article.get("raw_id"),
                },
                {
                    "ref_type": "raw_text",
                    "path": relative_to_root(Path(str(article.get("text_path") or "")), root_path),
                    "hash": text_hash,
                },
            ],
            "raw_root": RAW_WECHAT_RELATIVE,
        },
        "stats": {
            "section_count": len(profile_sections),
            "evidence_count": len(evidence),
            "asset_count": len(detailed_analysis),
            "chunk_count": chunk_count,
            "field_tag_counts": dict(
                Counter(tag for item in evidence for tag in item.get("field_tags") or [])
            ),
            "direction_counts": dict(
                Counter(str(item.get("direction") or "neutral") for item in evidence)
            ),
        },
        "asset_mentions": [
            {
                "asset": _asset_ref(taxonomy, asset),
                "evidence_count": len(items),
                "sentiment_score": detailed_analysis[asset]["sentiment_score"],
                "sentiment_label": detailed_analysis[asset]["sentiment_label"],
            }
            for asset, items in sorted(by_asset.items(), key=lambda row: len(row[1]), reverse=True)
        ],
        "sentiment_scores": sentiment_scores,
        "detailed_analysis": detailed_analysis,
        "sections": profile_sections,
        "evidence": evidence[:240],
    }


def _profile_needs_write(path: Path, profile: dict[str, Any], *, force: bool) -> bool:
    if force or not path.exists():
        return True
    try:
        existing = read_json(path)
    except Exception:
        return True
    return not (
        isinstance(existing, dict)
        and existing.get("schema_version") == STORE_VERSION
        and existing.get("content_hash") == profile.get("content_hash")
        and (existing.get("semantic_layer") or {}).get("extraction_ruleset") == EXTRACTION_RULESET
    )


def run_wechat_single_report_store(
    root: str | Path | None = None,
    *,
    start_date: str,
    end_date: str,
    article_limit: int | None = None,
    per_date_limit: int | None = None,
    min_text_chars: int = 80,
    max_chunks_per_article: int = 160,
    force: bool = False,
    dry_run: bool = False,
    write_legacy_candidate_cache: bool = False,
    legacy_run_id: str = "WECHAT-PROFILE-V1",
    work_order_id: str = "WECHAT-SREPORT",
) -> dict[str, Any]:
    root_path = quanta_data_root(root)
    start_key = _canonical_date(start_date)
    end_key = _canonical_date(end_date)
    run_id = _run_id(start_key, end_key, work_order_id)
    output_dir = _run_dir(root_path, end_key, run_id)
    taxonomy = load_asset_taxonomy(root_path, required=True)
    created_at = utc_now_iso()

    profile_refs: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    asset_counter: Counter[str] = Counter()
    source_counter: Counter[str] = Counter()
    date_counter: Counter[str] = Counter()
    stats = Counter()
    legacy_refs: list[dict[str, Any]] = []

    for date_key in _date_range(start_key, end_key):
        remaining = None if article_limit is None else max(article_limit - stats["source_article_count"], 0)
        if remaining == 0:
            break
        effective_limit = per_date_limit
        if remaining is not None:
            effective_limit = min(remaining, per_date_limit) if per_date_limit else remaining
        try:
            articles = load_wechat_articles(
                root_path,
                date=date_key,
                min_text_chars=min_text_chars,
                article_limit=effective_limit,
            )
        except Exception as exc:
            errors.append({"date": date_key, "code": exc.__class__.__name__, "message": str(exc)})
            continue
        stats["source_article_count"] += len(articles)
        date_counter[date_key] += len(articles)
        for article in articles:
            source_counter[str(article.get("source_account") or "")] += 1
            try:
                profile = build_single_report_profile(
                    article,
                    taxonomy=taxonomy,
                    root=root_path,
                    max_chunks_per_article=max_chunks_per_article,
                )
                profile_path = _profile_output_path(root_path, date_key, str(profile["profile_id"]))
                needs_write = _profile_needs_write(profile_path, profile, force=force)
                if needs_write:
                    stats["profile_created_count"] += 1
                    if not dry_run:
                        write_json(profile_path, profile)
                else:
                    stats["profile_reused_count"] += 1
                if profile.get("status") == "no_asset_evidence":
                    stats["no_asset_evidence_count"] += 1
                legacy_rel = ""
                if write_legacy_candidate_cache:
                    legacy_base = _legacy_candidate_dir(root_path, date_key, legacy_run_id)
                    legacy_path = _legacy_report_output_path(legacy_base, profile, date_key)
                    legacy_payload = _legacy_report_payload(profile, date_key)
                    legacy_rel = relative_to_root(legacy_path, root_path)
                    stats["legacy_candidate_cache_count"] += 1
                    if not dry_run:
                        write_json(legacy_path, legacy_payload)
                    legacy_refs.append(
                        {
                            "profile_id": profile.get("profile_id"),
                            "date": date_key,
                            "path": legacy_rel,
                            "title": (profile.get("metadata") or {}).get("title"),
                            "source_account": (profile.get("metadata") or {}).get("source_account"),
                            "asset_count": (profile.get("stats") or {}).get("asset_count"),
                        }
                    )
                for item in profile.get("asset_mentions") or []:
                    asset_name = str(((item.get("asset") or {}).get("name")) or "")
                    if asset_name:
                        asset_counter[asset_name] += 1
                profile_refs.append(
                    {
                        "profile_id": profile.get("profile_id"),
                        "status": profile.get("status"),
                        "date": date_key,
                        "path": relative_to_root(profile_path, root_path),
                        "content_hash": profile.get("content_hash"),
                        "title": (profile.get("metadata") or {}).get("title"),
                        "source_account": (profile.get("metadata") or {}).get("source_account"),
                        "published_at": (profile.get("metadata") or {}).get("published_at"),
                        "asset_count": (profile.get("stats") or {}).get("asset_count"),
                        "evidence_count": (profile.get("stats") or {}).get("evidence_count"),
                        "write_action": "created" if needs_write else "reused",
                        "legacy_candidate_cache_path": legacy_rel,
                    }
                )
            except Exception as exc:
                stats["error_count"] += 1
                errors.append(
                    {
                        "date": date_key,
                        "raw_id": article.get("raw_id"),
                        "code": exc.__class__.__name__,
                        "message": str(exc),
                    }
                )

    index_payload = {
        "schema_version": "wechat_single_report_profile_index.v1",
        "run_id": run_id,
        "status": "candidate",
        "generated_at": utc_now_iso(),
        "date_range": {"start_date": start_key, "end_date": end_key},
        "analysis_version": STORE_VERSION,
        "source": {"name": "hzzhqx_wechat", "raw_root": RAW_WECHAT_RELATIVE},
        "stats": {
            "source_article_count": stats["source_article_count"],
            "profile_count": len(profile_refs),
            "profile_created_count": stats["profile_created_count"],
            "profile_reused_count": stats["profile_reused_count"],
            "no_asset_evidence_count": stats["no_asset_evidence_count"],
            "error_count": stats["error_count"],
            "asset_count": len(asset_counter),
            "source_count": len(source_counter),
            "date_count": len(date_counter),
            "legacy_candidate_cache_count": stats["legacy_candidate_cache_count"],
        },
        "asset_summary": [
            {"asset": asset, "profile_count": count}
            for asset, count in asset_counter.most_common(80)
        ],
        "source_summary": [
            {"source_account": source, "profile_count": count}
            for source, count in source_counter.most_common(40)
        ],
        "date_summary": [
            {"date": date, "article_count": count}
            for date, count in sorted(date_counter.items())
        ],
        "profile_refs": profile_refs,
        "legacy_candidate_cache": {
            "enabled": write_legacy_candidate_cache,
            "run_id": legacy_run_id if write_legacy_candidate_cache else "",
            "refs": legacy_refs,
        },
        "errors": errors,
    }
    run_manifest = {
        "schema_version": "agent_run_manifest.v1",
        "run_id": run_id,
        "run_type": "single_report_structuring",
        "status": "succeeded" if profile_refs and not errors else "partial" if profile_refs else "failed",
        "created_at": created_at,
        "completed_at": utc_now_iso(),
        "owner_project": "quanta_agents",
        "agent": {
            "name": "wechat_single_report_store",
            "version": STORE_VERSION,
            "runtime": "python",
        },
        "date_range": {"start_date": start_key, "end_date": end_key},
        "input_refs": [{"ref_type": "raw_root", "path": RAW_WECHAT_RELATIVE, "id": "hzzhqx_wechat"}],
        "output_refs": [
            {
                "ref_type": "single_report_profile",
                "id": item.get("profile_id"),
                "path": item.get("path"),
                "hash": item.get("content_hash"),
            }
            for item in profile_refs
        ],
        "candidate_refs": [
            {
                "ref_type": "profile_index",
                "path": relative_to_root(output_dir / "profile_index.json", root_path),
                "id": run_id,
            }
        ]
        + [
            {
                "ref_type": "legacy_compatible_single_report_analysis",
                "id": item.get("profile_id"),
                "path": item.get("path"),
            }
            for item in legacy_refs
        ],
        "human_review_required": True,
        "promotion_target": "candidate",
        "errors": errors,
    }
    if not dry_run:
        write_json(output_dir / "profile_index.json", index_payload)
        write_json(output_dir / "run_manifest.json", run_manifest)
        if write_legacy_candidate_cache:
            refs_by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for ref in legacy_refs:
                refs_by_date[str(ref.get("date") or "")].append(ref)
            for legacy_date, refs in refs_by_date.items():
                legacy_base = _legacy_candidate_dir(root_path, legacy_date, legacy_run_id)
                write_json(
                    legacy_base / "profile_index.json",
                    {
                        "schema_version": "legacy_compatible_single_report_profile_index.v1",
                        "status": "candidate",
                        "generated_at": utc_now_iso(),
                        "date": legacy_date,
                        "run_id": legacy_run_id,
                        "source_run_id": run_id,
                        "analysis_version": STORE_VERSION,
                        "profile_count": len(refs),
                        "profile_refs": refs,
                    },
                )
    return {
        "run_id": run_id,
        "status": run_manifest["status"],
        "run_dir": str(output_dir),
        "paths": {
            "run_manifest": relative_to_root(output_dir / "run_manifest.json", root_path),
            "profile_index": relative_to_root(output_dir / "profile_index.json", root_path),
        },
        "stats": index_payload["stats"],
        "profile_refs": profile_refs,
        "legacy_candidate_cache_refs": legacy_refs,
        "errors": errors,
        "dry_run": dry_run,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Build reusable structured profiles for hzzhqx WeChat research reports."
    )
    parser.add_argument("--quanta-root", help="Override quanta_data root.")
    parser.add_argument("--date", help="Single date, YYYYMMDD or YYYY-MM-DD.")
    parser.add_argument("--start-date", help="Start date, YYYYMMDD or YYYY-MM-DD.")
    parser.add_argument("--end-date", help="End date, YYYYMMDD or YYYY-MM-DD.")
    parser.add_argument("--article-limit", type=int, help="Total article limit across the date range.")
    parser.add_argument("--per-date-limit", type=int, help="Per-day article limit.")
    parser.add_argument("--min-text-chars", type=int, default=80)
    parser.add_argument("--max-chunks-per-article", type=int, default=160)
    parser.add_argument("--force", action="store_true", help="Rewrite existing profiles.")
    parser.add_argument("--dry-run", action="store_true", help="Build profiles without writing files.")
    parser.add_argument(
        "--write-legacy-candidate-cache",
        action="store_true",
        help="Also write legacy-compatible per_report JSONs under futures_daily_single_report_analysis.",
    )
    parser.add_argument(
        "--legacy-run-id",
        default="WECHAT-PROFILE-V1",
        help="Run label under futures_daily_single_report_analysis/YYYY/MM/DD/.",
    )
    parser.add_argument("--work-order-id", default="WECHAT-SREPORT")
    args = parser.parse_args(argv)

    start_date = args.start_date or args.date
    end_date = args.end_date or args.date or args.start_date
    if not start_date or not end_date:
        parser.error("provide --date or both --start-date/--end-date")
    result = run_wechat_single_report_store(
        args.quanta_root,
        start_date=start_date,
        end_date=end_date,
        article_limit=args.article_limit,
        per_date_limit=args.per_date_limit,
        min_text_chars=args.min_text_chars,
        max_chunks_per_article=args.max_chunks_per_article,
        force=args.force,
        dry_run=args.dry_run,
        write_legacy_candidate_cache=args.write_legacy_candidate_cache,
        legacy_run_id=args.legacy_run_id,
        work_order_id=args.work_order_id,
    )
    print(f"run_dir={result['run_dir']}")
    print(f"profile_index={result['paths']['profile_index']}")
    print(
        "status={status} articles={source_article_count} profiles={profile_count} "
        "created={profile_created_count} reused={profile_reused_count} "
        "legacy_cache={legacy_candidate_cache_count} "
        "no_asset={no_asset_evidence_count} errors={error_count}".format(
            status=result["status"],
            **result["stats"],
        )
    )


if __name__ == "__main__":
    main()
