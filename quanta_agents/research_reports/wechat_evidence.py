from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from quanta_agents.core.analysis_framework import latest_analysis_framework_refs
from quanta_agents.core.config import quanta_data_root
from quanta_agents.core.frameworks import iter_leaf_nodes, load_framework_for_asset
from quanta_agents.core.io import dated_parts, read_json, relative_to_root, utc_now_iso, write_json
from quanta_agents.core.llm_client import chat, get_provider
from quanta_agents.core.llm_json import parse_json_object
from quanta_agents.core.taxonomy import AssetTaxonomy, load_asset_taxonomy
from quanta_agents.futures_daily.framework_alignment import _match_framework_node
from quanta_agents.opinion_radar.news_logic import _latest_logic_run


RAW_WECHAT_RELATIVE = "raw_objects/web_pages/hzzhqx_wechat"

FUNDAMENTAL_WORDS = (
    "基本面",
    "供应",
    "供给",
    "产量",
    "产能",
    "开工",
    "检修",
    "进口",
    "出口",
    "发运",
    "到港",
    "库存",
    "仓单",
    "需求",
    "消费",
    "成交",
    "订单",
    "终端",
    "地产",
    "基建",
    "汽车",
    "利润",
    "成本",
    "原料",
    "加工费",
    "压榨",
    "政策",
    "监管",
    "关税",
    "收储",
    "抛储",
    "天气",
    "降水",
    "干旱",
    "地缘",
    "制裁",
    "停火",
    "通航",
    "美联储",
    "美元",
    "利率",
    "通胀",
    "PMI",
    "社融",
)

MARKET_ONLY_WORDS = (
    "主力合约",
    "夜盘",
    "收盘",
    "收涨",
    "收跌",
    "涨幅",
    "跌幅",
    "成交",
    "持仓",
    "盘面",
    "技术面",
    "压力位",
    "支撑位",
    "K线",
    "均线",
)

BULLISH_PATTERNS = (
    r"偏多",
    r"利多",
    r"支撑",
    r"走强",
    r"反弹",
    r"去库",
    r"库存[^。；;，,]{0,18}(下降|减少|回落|去化|低位)",
    r"供应[^。；;，,]{0,18}(收缩|减少|下降|偏紧|受限)",
    r"供给[^。；;，,]{0,18}(收缩|减少|下降|偏紧|受限)",
    r"产量[^。；;，,]{0,18}(下降|减少)",
    r"减产",
    r"检修",
    r"停产",
    r"进口[^。；;，,]{0,18}(下降|减少|偏低)",
    r"出口[^。；;，,]{0,18}(增加|增长|恢复)",
    r"需求[^。；;，,]{0,18}(改善|回升|增加|增长|韧性|刚性)",
    r"消费[^。；;，,]{0,18}(改善|回升|增加|增长|韧性)",
    r"开工[^。；;，,]{0,18}(回升|提升|增加)",
    r"利润[^。；;，,]{0,18}(修复|改善)",
    r"降息",
    r"宽松",
    r"鸽派",
    r"美元[^。；;，,]{0,18}(走弱|回落|下跌)",
    r"收益率[^。；;，,]{0,18}(下行|回落)",
    r"风险溢价[^。；;，,]{0,18}(抬升|上升)",
    r"地缘[^。；;，,]{0,18}(升级|紧张|冲突)",
    r"天气[^。；;，,]{0,18}(炒作|不利)",
)

BEARISH_PATTERNS = (
    r"偏空",
    r"利空",
    r"压制",
    r"承压",
    r"走弱",
    r"回落",
    r"累库",
    r"库存[^。；;，,]{0,18}(上升|增加|累积|高位)",
    r"供应[^。；;，,]{0,18}(增加|恢复|宽松|过剩)",
    r"供给[^。；;，,]{0,18}(增加|恢复|宽松|过剩)",
    r"增产",
    r"复产",
    r"进口[^。；;，,]{0,18}(增加|增长|恢复)",
    r"出口[^。；;，,]{0,18}(下降|减少|疲软)",
    r"需求[^。；;，,]{0,18}(走弱|下降|减少|疲软|清淡|不足)",
    r"消费[^。；;，,]{0,18}(走弱|下降|减少|疲软|清淡|不足)",
    r"成交[^。；;，,]{0,18}(下降|减少|清淡)",
    r"加息",
    r"收紧",
    r"鹰派",
    r"美元[^。；;，,]{0,18}(走强|上涨|反弹)",
    r"收益率[^。；;，,]{0,18}(上行|走高)",
    r"停火",
    r"复航",
    r"通航恢复",
    r"地缘[^。；;，,]{0,18}(缓和|降温)",
    r"风险溢价[^。；;，,]{0,18}(回吐|下降)",
    r"供增需弱",
)

BOILERPLATE_PATTERNS = (
    "免责声明",
    "风险提示",
    "本报告中的信息均来源于",
    "本报告版权",
    "据此投资",
    "长按二维码",
    "扫码关注",
)

GENERIC_SECTION_HEADINGS = {
    "黑色系",
    "贵金属",
    "有色金属",
    "农产品",
    "能化",
    "能化板块",
    "能源化工",
    "金融期货",
    "股指期货",
    "期指",
    "油脂油料",
    "软商品",
}


def _hash_id(prefix: str, *parts: Any) -> str:
    raw = "||".join(str(part) for part in parts)
    return f"{prefix}-{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:16].upper()}"


def _clean_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _canonical_date(date: str) -> str:
    value = date.replace("-", "")
    if len(value) != 8 or not value.isdigit():
        raise ValueError("date must be YYYYMMDD or YYYY-MM-DD")
    return value


def _strip_boilerplate_tail(text: str) -> str:
    cut = len(text)
    for marker in BOILERPLATE_PATTERNS:
        idx = text.find(marker)
        if idx >= 0 and idx < cut:
            cut = idx
    return text[:cut].strip()


def _manifest_source(path: Path, manifest: dict[str, Any]) -> str:
    return str(
        manifest.get("source_account")
        or manifest.get("source_display_name")
        or manifest.get("source_name")
        or path.parents[4].name
    )


def load_wechat_articles(
    root: str | Path | None = None,
    *,
    date: str,
    min_text_chars: int = 80,
    article_limit: int | None = None,
) -> list[dict[str, Any]]:
    root_path = quanta_data_root(root)
    yyyy, mm, dd = dated_parts(_canonical_date(date))
    base = root_path / RAW_WECHAT_RELATIVE
    manifests = sorted(base.glob(f"*/{yyyy}/{mm}/{dd}/*/raw_manifest.json"))
    articles: list[dict[str, Any]] = []
    for manifest_path in manifests:
        manifest = read_json(manifest_path)
        if not isinstance(manifest, dict):
            continue
        text_path = manifest_path.with_name("extracted_text.txt")
        text = text_path.read_text(encoding="utf-8", errors="ignore") if text_path.exists() else ""
        text = _strip_boilerplate_tail(text)
        if len(_clean_spaces(text)) < min_text_chars:
            continue
        article = {
            "article_id": str(manifest.get("raw_id") or manifest_path.parent.name),
            "raw_id": str(manifest.get("raw_id") or manifest_path.parent.name),
            "title": str(manifest.get("title") or ""),
            "source_account": _manifest_source(manifest_path, manifest),
            "published_at": str(manifest.get("published_at") or manifest.get("source_time") or ""),
            "source_url": manifest.get("source_url") or manifest.get("canonical_url") or manifest.get("source_uri"),
            "raw_object_dir": str(manifest_path.parent),
            "raw_manifest": str(manifest_path),
            "text_path": str(text_path),
            "text_chars": len(text),
            "text": text,
        }
        articles.append(article)
        if article_limit and len(articles) >= article_limit:
            break
    return articles


def _asset_by_name(taxonomy: AssetTaxonomy, name: str) -> dict[str, Any]:
    return next(
        (item for item in taxonomy.assets if item.get("canonical_name") == name),
        {"canonical_name": name},
    )


def _variety_names(taxonomy: AssetTaxonomy, text: str) -> list[str]:
    names = [hit.label for hit in taxonomy.classify(text) if hit.kind == "variety"]
    return list(dict.fromkeys(names))


def _is_heading(line: str, taxonomy: AssetTaxonomy) -> tuple[bool, list[str]]:
    text = line.strip()
    if not text:
        return False, []
    compact = re.sub(r"\s+", "", text)
    if len(compact) > 52:
        return False, []
    hits = taxonomy.classify(text)
    variety_names = [hit.label for hit in hits if hit.kind == "variety"]
    generic = re.sub(r"^\d{1,2}[.、\s]*", "", compact).rstrip(":：")
    if text.startswith(("品种：", "品种:")):
        return True, list(dict.fromkeys(variety_names))
    if generic in GENERIC_SECTION_HEADINGS:
        return True, []
    if not hits and re.match(r"^\d{0,2}\s*[\u4e00-\u9fffA-Za-z0-9/（）() -]{1,24}[:：]$", text):
        return True, []
    if not hits:
        return False, []
    if variety_names and text.endswith((":", "：")) and len(compact) <= 30:
        return True, list(dict.fromkeys(variety_names))
    if text.startswith("【") and variety_names:
        return True, list(dict.fromkeys(variety_names))
    if any(punc in text for punc in "，,。；;：:") and not re.match(r"^\d{1,2}\s*[:：]?\s*\S+$", text):
        return False, []
    if len(compact) <= 24:
        return True, list(dict.fromkeys(variety_names))
    if re.match(r"^(0?\d+|[一二三四五六七八九十]+)[.、\s]+", text):
        return True, list(dict.fromkeys(variety_names))
    return False, []


def _split_article_sections(article: dict[str, Any], taxonomy: AssetTaxonomy) -> list[dict[str, Any]]:
    text = str(article.get("text") or "")
    lines = [line.strip() for line in re.split(r"[\r\n]+", text) if line.strip()]
    sections: list[dict[str, Any]] = []
    buffer: list[str] = []
    current_assets: list[str] = []
    current_heading = ""

    def flush() -> None:
        nonlocal buffer, current_assets, current_heading
        body = "\n".join(buffer).strip()
        if not body:
            buffer = []
            current_assets = []
            current_heading = ""
            return
        assets = current_assets or _variety_names(taxonomy, body)
        if assets:
            sections.append(
                {
                    "section_id": _hash_id("WSECT", article.get("article_id"), len(sections), current_heading, body[:120]),
                    "heading": current_heading,
                    "assets": assets[:8],
                    "asset_scope": "heading" if current_assets else "inferred_section",
                    "text": body,
                }
            )
        buffer = []
        current_assets = []
        current_heading = ""

    for line in lines:
        if any(marker in line for marker in BOILERPLATE_PATTERNS):
            break
        if re.fullmatch(r"\d{1,2}", line):
            continue
        is_heading, heading_assets = _is_heading(line, taxonomy)
        if is_heading:
            flush()
            current_heading = line
            current_assets = heading_assets
            buffer = [line]
            continue
        buffer.append(line)
    flush()
    if sections:
        return sections

    assets = _variety_names(taxonomy, text)
    return [
        {
            "section_id": _hash_id("WSECT", article.get("article_id"), "whole", text[:120]),
            "heading": "",
            "assets": assets[:8],
            "asset_scope": "inferred_section",
            "text": text,
        }
    ] if assets else []


def _sentence_parts(text: str) -> list[str]:
    parts: list[str] = []
    for raw in re.split(r"(?<=[。！？!?；;])\s+|[\r\n]+", text):
        raw = raw.strip()
        if not raw:
            continue
        pieces = re.split(r"(?<=[。！？!?；;])", raw)
        for piece in pieces:
            piece = piece.strip()
            if piece:
                parts.append(piece)
    return parts


def _chunk_section(section: dict[str, Any], *, max_chars: int = 900, min_chars: int = 24) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    current: list[str] = []
    current_len = 0
    for part in _sentence_parts(str(section.get("text") or "")):
        clean_part = _clean_spaces(part)
        if len(clean_part) >= min_chars and current_len == 0:
            chunks.append({"text": clean_part, "heading": section.get("heading") or ""})
            continue
        if len(clean_part) >= min_chars and current:
            chunk_text = _clean_spaces(" ".join(current))
            if len(chunk_text) >= min_chars:
                chunks.append({"text": chunk_text, "heading": section.get("heading") or ""})
            chunks.append({"text": clean_part, "heading": section.get("heading") or ""})
            current = []
            current_len = 0
            continue
        if current and current_len + len(part) > max_chars:
            chunk_text = _clean_spaces(" ".join(current))
            if len(chunk_text) >= min_chars:
                chunks.append({"text": chunk_text, "heading": section.get("heading") or ""})
            current = []
            current_len = 0
        current.append(part)
        current_len += len(part)
    if current:
        chunk_text = _clean_spaces(" ".join(current))
        if len(chunk_text) >= min_chars:
            chunks.append({"text": chunk_text, "heading": section.get("heading") or ""})
    return chunks


def _primary_chunk_assets(taxonomy: AssetTaxonomy, text: str, candidates: list[str]) -> list[str]:
    prefix = text[:64]
    prefix_assets = _variety_names(taxonomy, prefix)
    primary = [name for name in prefix_assets if name in candidates]
    return list(dict.fromkeys(primary))


def _is_market_observation(text: str) -> bool:
    fundamental_hits = sum(1 for word in FUNDAMENTAL_WORDS if word in text)
    market_hits = sum(1 for word in MARKET_ONLY_WORDS if word in text)
    has_price_numbers = bool(re.search(r"(涨|跌|收于|报收|元/吨|美元/桶|点|%)", text))
    return has_price_numbers and market_hits >= 1 and fundamental_hits <= 1


def _pattern_hits(patterns: tuple[str, ...], text: str) -> int:
    return sum(1 for pattern in patterns if re.search(pattern, text))


def _infer_direction(text: str, *, scoring_role: str) -> tuple[str, float, dict[str, Any]]:
    if scoring_role == "market_observation":
        return "neutral", 0.0, {"method": "market_observation_excluded"}
    bullish = _pattern_hits(BULLISH_PATTERNS, text)
    bearish = _pattern_hits(BEARISH_PATTERNS, text)
    if bullish == bearish:
        return "neutral", 0.0, {"method": "rule_patterns", "bullish_hits": bullish, "bearish_hits": bearish}
    if bullish > bearish:
        return "bullish", 1.0, {"method": "rule_patterns", "bullish_hits": bullish, "bearish_hits": bearish}
    return "bearish", -1.0, {"method": "rule_patterns", "bullish_hits": bullish, "bearish_hits": bearish}


def _dimension_label(label: str) -> str:
    text = str(label or "").strip()
    if "/" in text:
        text = text.rsplit("/", 1)[-1].strip()
    return text or "未归类"


def _asset_framework(asset: dict[str, Any], root: Path) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    framework = load_framework_for_asset(asset, root)
    leaves = iter_leaf_nodes(framework) if framework else []
    return framework, leaves


def _load_logic_context(root: Path, logic_run: str | Path | None) -> dict[str, Any]:
    run_path = Path(logic_run).expanduser() if logic_run else _latest_logic_run(root)
    if not run_path:
        return {"run_dir": "", "trade_thesis": {}, "dimension_scores": {}}
    return {
        "run_dir": str(run_path),
        "trade_thesis": read_json(run_path / "trade_thesis.json"),
        "dimension_scores": read_json(run_path / "dimension_scores.json"),
    }


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
    if event.get("scoring_role") == "market_observation":
        return {"status": "tracking", "reason": "盘面/技术观察不直接参与基本面结论更新"}
    direction = float(event.get("direction_score") or 0)
    if direction == 0:
        return {"status": "tracking", "reason": "研报片段方向中性，作为跟踪变量"}
    ctx = _asset_context(logic_context, str(event.get("asset") or ""))
    thesis = ctx["thesis"]
    if not thesis:
        return {"status": "new_signal", "reason": "该品种暂无上一期交易主线"}
    dim_label = (event.get("framework_node") or {}).get("dimension_label")
    dim_score = ctx["dimension_scores"].get(str(dim_label))
    dim_direction = float((dim_score or {}).get("direction_score") or 0)
    if dim_direction and dim_direction * direction < 0:
        return {"status": "dimension_conflict", "reason": f"研报证据方向与上一期{dim_label}维度相反"}
    prior_score = float(thesis.get("decision_score") or thesis.get("framework_score") or 0)
    if prior_score and prior_score * direction < 0:
        return {"status": "thesis_conflict", "reason": "研报证据方向与上一期品种主线相反"}
    return {"status": "supports_thesis", "reason": "研报证据方向与上一期主线或维度一致"}


def map_article_to_evidence(
    article: dict[str, Any],
    *,
    taxonomy: AssetTaxonomy,
    root: Path,
    logic_context: dict[str, Any],
    max_chunks_per_article: int = 120,
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    sections = _split_article_sections(article, taxonomy)
    framework_cache: dict[str, tuple[dict[str, Any] | None, list[dict[str, Any]]]] = {}
    chunk_count = 0
    for section in sections:
        chunks = _chunk_section(section)
        for chunk_index, chunk in enumerate(chunks):
            if chunk_count >= max_chunks_per_article:
                break
            text = str(chunk.get("text") or "")
            section_assets = list(section.get("assets") or [])
            chunk_assets = _variety_names(taxonomy, text)
            if section.get("asset_scope") == "heading" and 0 < len(section_assets) <= 2:
                assets = section_assets
            elif chunk_assets:
                primary_assets = _primary_chunk_assets(taxonomy, text, chunk_assets)
                if primary_assets:
                    assets = primary_assets
                elif len(chunk_assets) == 1:
                    assets = chunk_assets
                elif len(section_assets) == 1:
                    assets = section_assets
                else:
                    assets = []
            elif len(section_assets) == 1:
                assets = section_assets
            else:
                assets = []
            if not assets:
                continue
            for asset_name in assets[:6]:
                asset = _asset_by_name(taxonomy, asset_name)
                if asset_name not in framework_cache:
                    framework_cache[asset_name] = _asset_framework(asset, root)
                framework, leaves = framework_cache[asset_name]
                if leaves:
                    node_label, node_id, confidence, method = _match_framework_node(text, leaves)
                else:
                    node_label, node_id, confidence, method = "未归类", "default::未归类", 0.2, "no_framework"
                scoring_role = "market_observation" if _is_market_observation(text) else "fundamental_evidence"
                direction, direction_score, direction_meta = _infer_direction(text, scoring_role=scoring_role)
                event = {
                    "evidence_id": _hash_id(
                        "WREP",
                        article.get("article_id"),
                        section.get("section_id"),
                        chunk_index,
                        asset_name,
                        node_id,
                    ),
                    "article_id": article.get("article_id"),
                    "raw_id": article.get("raw_id"),
                    "source_account": article.get("source_account"),
                    "title": article.get("title"),
                    "published_at": article.get("published_at"),
                    "source_url": article.get("source_url"),
                    "raw_manifest": article.get("raw_manifest"),
                    "text_path": article.get("text_path"),
                    "asset": asset_name,
                    "asset_id": asset.get("asset_id"),
                    "section": {
                        "section_id": section.get("section_id"),
                        "heading": section.get("heading") or "",
                    },
                    "text": text,
                    "direction": direction,
                    "direction_score": direction_score,
                    "direction_inference": direction_meta,
                    "scoring_role": scoring_role,
                    "framework": {
                        "framework_id": framework.get("framework_id") if framework else "",
                        "asset_id": framework.get("asset_id") if framework else "",
                        "source_path": framework.get("_source_path") if framework else "",
                    },
                    "framework_node": {
                        "node_id": node_id,
                        "label": node_label,
                        "dimension_label": _dimension_label(node_label),
                    },
                    "match": {"method": method, "confidence": round(float(confidence), 3)},
                }
                event["consistency"] = _consistency(event, logic_context)
                event["heat"] = round(max(float(confidence), 0.2) * (1.0 if scoring_role == "fundamental_evidence" else 0.35), 3)
                evidence.append(event)
            chunk_count += 1
        if chunk_count >= max_chunks_per_article:
            break
    return evidence


def _dedupe_key(text: str) -> str:
    normalized = re.sub(r"\d+(\.\d+)?", "#", _clean_spaces(text))
    normalized = re.sub(r"[^\w\u4e00-\u9fff#]+", "", normalized.lower())
    return normalized[:160]


def _evidence_brief(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "evidence_id": event.get("evidence_id"),
        "source_account": event.get("source_account"),
        "title": event.get("title"),
        "published_at": event.get("published_at"),
        "text": str(event.get("text") or "")[:420],
        "direction": event.get("direction"),
        "direction_score": event.get("direction_score"),
        "scoring_role": event.get("scoring_role"),
        "match_confidence": (event.get("match") or {}).get("confidence"),
        "consistency": event.get("consistency"),
    }


def _dimension_weight_lookup(root: Path) -> dict[tuple[str, str], float]:
    path = root / "agent_workspace" / "candidates" / "analysis_framework" / "latest" / "analysis-framework-registry.json"
    if not path.exists():
        return {}
    registry = read_json(path)
    lookup: dict[tuple[str, str], float] = {}
    for row in registry.get("dimensions") or []:
        if not isinstance(row, dict):
            continue
        asset = str(row.get("asset") or "")
        weight = float(row.get("default_weight") or 0.08)
        for key in (
            str(row.get("dimension_id") or ""),
            str(row.get("dimension_name") or ""),
            _dimension_label(str(row.get("dimension_label") or "")),
        ):
            if key:
                lookup[(asset, key)] = weight
    return lookup


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


def _relation_to_prior(prior_score: float, evidence_score: float, consistency_counts: dict[str, int]) -> str:
    if not prior_score:
        return "new_signal" if abs(evidence_score) >= 1.5 else "tracking"
    if consistency_counts.get("thesis_conflict") or consistency_counts.get("dimension_conflict"):
        if prior_score * evidence_score < 0 and abs(evidence_score) >= 2.5:
            return "conflicts"
        return "mixed"
    if prior_score * evidence_score > 0 and abs(evidence_score) >= 1:
        return "supports"
    return "tracking"


def _aggregate_dimension(
    *,
    asset: str,
    label: str,
    events: list[dict[str, Any]],
    weight_lookup: dict[tuple[str, str], float],
) -> dict[str, Any]:
    deduped: dict[str, dict[str, Any]] = {}
    for event in events:
        key = _dedupe_key(str(event.get("text") or ""))
        group = deduped.setdefault(
            key,
            {
                "text": event.get("text"),
                "events": [],
                "sources": set(),
                "direction_score_sum": 0.0,
                "heat_sum": 0.0,
            },
        )
        group["events"].append(event)
        group["sources"].add(str(event.get("source_account") or ""))
        if event.get("scoring_role") == "fundamental_evidence":
            heat = float(event.get("heat") or 0)
            group["direction_score_sum"] += float(event.get("direction_score") or 0) * heat
            group["heat_sum"] += heat
    groups = []
    scored_heat = 0.0
    scored_direction = 0.0
    for group in deduped.values():
        group_events = group["events"]
        heat = float(group["heat_sum"] or 0)
        direction_score = group["direction_score_sum"] / heat if heat else 0.0
        source_count = len(group["sources"])
        support_level = "cross_source" if source_count >= 2 else "single_source"
        groups.append(
            {
                "text": group.get("text"),
                "merged_count": len(group_events),
                "source_count": source_count,
                "support_level": support_level,
                "direction_score": round(direction_score, 3),
                "sources": sorted(group["sources"]),
                "evidence_ids": [event.get("evidence_id") for event in group_events],
            }
        )
        if heat:
            multiplier = 1.2 if source_count >= 2 else 1.0
            scored_heat += heat * multiplier
            scored_direction += direction_score * heat * multiplier
    net_direction = scored_direction / scored_heat if scored_heat else 0.0
    node_id = str((events[0].get("framework_node") or {}).get("node_id") or "")
    weight = weight_lookup.get((asset, node_id)) or weight_lookup.get((asset, label)) or 0.08
    return {
        "dimension_label": label,
        "framework_node": events[0].get("framework_node") or {},
        "evidence_count": len(events),
        "deduped_evidence_count": len(groups),
        "source_count": len({str(event.get("source_account") or "") for event in events}),
        "fundamental_evidence_count": sum(1 for event in events if event.get("scoring_role") == "fundamental_evidence"),
        "market_observation_count": sum(1 for event in events if event.get("scoring_role") == "market_observation"),
        "direction_score": round(net_direction, 3),
        "direction": "bullish" if net_direction > 0.15 else "bearish" if net_direction < -0.15 else "neutral",
        "dynamic_weight": round(float(weight), 4),
        "weighted_signal": round(net_direction * float(weight) * 10, 3),
        "consistency_counts": dict(Counter(str((event.get("consistency") or {}).get("status")) for event in events)),
        "evidence_groups": sorted(groups, key=lambda item: (item["source_count"], abs(item["direction_score"]), item["merged_count"]), reverse=True)[:12],
        "top_evidence": [_evidence_brief(event) for event in sorted(events, key=lambda item: float(item.get("heat") or 0), reverse=True)[:8]],
    }


def _llm_available(provider: str) -> tuple[bool, str]:
    try:
        llm = get_provider(provider)
        if not llm.api_keys:
            return False, f"missing API key for {llm.name}; set {llm.api_key_hint}"
        return True, ""
    except Exception as exc:
        return False, str(exc)


def _llm_asset_assessment(asset: str, payload: dict[str, Any], *, provider: str) -> dict[str, Any]:
    thesis = payload.get("linked_trade_thesis") or {}

    def evidence_for_prompt(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        brief = []
        for row in rows[:3]:
            consistency = row.get("consistency") or {}
            brief.append(
                {
                    "source": row.get("source_account"),
                    "direction": row.get("direction"),
                    "consistency": consistency.get("status"),
                    "text": str(row.get("text") or "")[:260],
                }
            )
        return brief

    dimensions = [
        {
            "dimension_label": item.get("dimension_label"),
            "direction": item.get("direction"),
            "direction_score": item.get("direction_score"),
            "source_count": item.get("source_count"),
            "consistency_counts": item.get("consistency_counts"),
            "evidence_groups": [
                {
                    "text": str(group.get("text") or "")[:220],
                    "source_count": group.get("source_count"),
                    "direction_score": group.get("direction_score"),
                    "support_level": group.get("support_level"),
                }
                for group in (item.get("evidence_groups") or [])[:4]
            ],
            "top_evidence": evidence_for_prompt(item.get("top_evidence") or []),
        }
        for item in (payload.get("dimensions") or [])[:8]
    ]
    prompt = (
        "你是商品期货投研 Agent。请把微信研报证据和上一期投研结论进行对齐，更新交易主线。\n"
        "不要把行情涨跌本身当作基本面确认；只有供需、库存、成本、政策、宏观、地缘等逻辑证据可以改变结论。\n"
        "请合并重复证据，区分“进一步验证、削弱、反转、新变量、噪音跟踪”。\n"
        "严格输出 JSON，不要 Markdown，格式：\n"
        '{"relation_to_prior":"supports|conflicts|mixed|new_signal|tracking",'
        '"updated_bias":"偏多|小幅偏多|中性|小幅偏空|偏空|不确定",'
        '"main_logic_update":"一段通顺的主线更新",'
        '"score_adjustment":0.0,'
        '"dimension_updates":[{"dimension_label":"...","status":"confirmed|weakened|reversed|new|tracking",'
        '"importance_score":0.0,"net_direction":"bullish|bearish|neutral","merged_summary":"..."}],'
        '"new_framework_candidates":["需要新增/调整的维度或词条"],'
        '"tracking_points":["后续跟踪"],'
        '"quality_notes":["噪音或口径问题"]}\n\n'
        "硬性要求：main_logic_update 必须填写 120-500 个中文字符；"
        "dimension_updates 至少覆盖最重要的 3 个维度；tracking_points 至少 3 条；"
        "即使 relation_to_prior 是 tracking，也要解释为什么只是跟踪而不改变结论。\n\n"
        + json.dumps(
            {
                "asset": asset,
                "prior_conclusion": {
                    "recommendation": thesis.get("recommendation"),
                    "framework_score": thesis.get("framework_score"),
                    "decision_score": thesis.get("decision_score"),
                    "main_trade_thesis": thesis.get("main_trade_thesis"),
                },
                "rule_research_conclusion": payload.get("research_conclusion"),
                "dimensions": dimensions,
            },
            ensure_ascii=False,
        )
    )
    provider_name = str(provider or "").lower()
    max_tokens = 2800 if "m3" in provider_name or "minimax" in provider_name else 3600
    timeout = 90 if "m3" in provider_name or "minimax" in provider_name else 160
    last_error: Exception | None = None
    retry_prompt = prompt
    for attempt in range(2):
        try:
            parsed = parse_json_object(
                chat(retry_prompt, max_tokens=max_tokens, temperature=0.15, timeout=timeout, provider=provider)
            )
            relation = str(parsed.get("relation_to_prior") or "tracking")
            if relation not in {"supports", "conflicts", "mixed", "new_signal", "tracking"}:
                relation = "tracking"
            main_logic_update = str(parsed.get("main_logic_update") or "")
            dimension_updates = parsed.get("dimension_updates") if isinstance(parsed.get("dimension_updates"), list) else []
            tracking_points = parsed.get("tracking_points") if isinstance(parsed.get("tracking_points"), list) else []
            quality_notes = parsed.get("quality_notes") if isinstance(parsed.get("quality_notes"), list) else []
            if (not main_logic_update or not dimension_updates) and attempt == 0:
                retry_prompt = (
                    prompt
                    + "\n\n上一次输出缺少必填字段。请重新输出完整 JSON，必须包含非空 main_logic_update 和至少 3 条 dimension_updates。"
                )
                continue
            if not main_logic_update:
                quality_notes.append("LLM返回缺少 main_logic_update，需重跑或人工复核。")
            if not dimension_updates:
                quality_notes.append("LLM返回缺少 dimension_updates，需重跑或人工复核。")
            return {
                "method": "llm",
                "provider": provider,
                "relation_to_prior": relation,
                "updated_bias": str(parsed.get("updated_bias") or ""),
                "main_logic_update": main_logic_update,
                "score_adjustment": float(parsed.get("score_adjustment") or 0),
                "dimension_updates": dimension_updates,
                "new_framework_candidates": parsed.get("new_framework_candidates") if isinstance(parsed.get("new_framework_candidates"), list) else [],
                "tracking_points": tracking_points,
                "quality_notes": quality_notes,
            }
        except Exception as exc:
            last_error = exc
            continue
    return {
        "method": "failed",
        "provider": provider,
        "relation_to_prior": "tracking",
        "updated_bias": "",
        "main_logic_update": "",
        "score_adjustment": 0.0,
        "dimension_updates": [],
        "new_framework_candidates": [],
        "tracking_points": [],
        "quality_notes": [f"LLM研报结论更新失败：{last_error}"],
    }


def _aggregate_assets(
    events: list[dict[str, Any]],
    *,
    logic_context: dict[str, Any],
    root: Path,
    use_llm_assessment: bool,
    llm_provider: str,
    llm_asset_limit: int,
) -> dict[str, Any]:
    weight_lookup = _dimension_weight_lookup(root)
    by_asset_events: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_asset_events[str(event.get("asset") or "")].append(event)

    assets: dict[str, Any] = {}
    for asset, asset_events in by_asset_events.items():
        dimension_events: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for event in asset_events:
            label = str((event.get("framework_node") or {}).get("dimension_label") or "未归类")
            dimension_events[label].append(event)
        dimensions = [
            _aggregate_dimension(asset=asset, label=label, events=dim_events, weight_lookup=weight_lookup)
            for label, dim_events in dimension_events.items()
        ]
        dimensions = sorted(dimensions, key=lambda item: abs(float(item.get("weighted_signal") or 0)), reverse=True)
        weighted_sum = sum(float(item.get("weighted_signal") or 0) for item in dimensions)
        weight_sum = sum(abs(float(item.get("dynamic_weight") or 0)) for item in dimensions if item.get("fundamental_evidence_count"))
        evidence_score = max(-10.0, min(10.0, weighted_sum / max(weight_sum, 0.15)))
        ctx = _asset_context(logic_context, asset)
        thesis = ctx["thesis"]
        prior_score = float(thesis.get("decision_score") or thesis.get("framework_score") or 0)
        combined = evidence_score if not thesis else max(-10.0, min(10.0, prior_score * 0.65 + evidence_score * 0.35))
        consistency_counts = dict(Counter(str((event.get("consistency") or {}).get("status")) for event in asset_events))
        assets[asset] = {
            "asset": asset,
            "event_count": len(asset_events),
            "article_count": len({str(event.get("article_id") or "") for event in asset_events}),
            "source_count": len({str(event.get("source_account") or "") for event in asset_events}),
            "evidence_score": round(evidence_score, 2),
            "consistency_counts": consistency_counts,
            "linked_trade_thesis": thesis,
            "research_conclusion": {
                "prior_score": round(prior_score, 2),
                "evidence_score": round(evidence_score, 2),
                "combined_score": round(combined, 2),
                "prior_bias": _bias(prior_score),
                "evidence_bias": _bias(evidence_score),
                "updated_bias": _bias(combined),
                "relation_to_prior": _relation_to_prior(prior_score, evidence_score, consistency_counts),
            },
            "dimensions": dimensions,
        }

    if use_llm_assessment:
        available, reason = _llm_available(llm_provider)
        ranked_assets = sorted(assets.items(), key=lambda item: (item[1]["source_count"], item[1]["event_count"]), reverse=True)
        for index, (asset, payload) in enumerate(ranked_assets):
            if not available:
                payload["llm_assessment"] = {
                    "method": "skipped_missing_key",
                    "provider": llm_provider,
                    "relation_to_prior": "tracking",
                    "quality_notes": [reason],
                }
            elif index >= llm_asset_limit:
                payload["llm_assessment"] = {"method": "skipped_limit", "provider": llm_provider, "relation_to_prior": "tracking"}
            else:
                print(
                    f"LLM assessment {index + 1}/{min(len(ranked_assets), llm_asset_limit)} {asset} via {llm_provider}",
                    flush=True,
                )
                payload["llm_assessment"] = _llm_asset_assessment(asset, payload, provider=llm_provider)
    return dict(sorted(assets.items(), key=lambda item: (item[1]["source_count"], item[1]["event_count"]), reverse=True))


def _framework_update_candidates(events: list[dict[str, Any]], assets: dict[str, Any]) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    low_conf_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        confidence = float((event.get("match") or {}).get("confidence") or 0)
        node_id = str((event.get("framework_node") or {}).get("node_id") or "")
        if node_id == "default::未归类" or confidence < 0.45:
            low_conf_groups[(str(event.get("asset") or ""), str((event.get("framework_node") or {}).get("dimension_label") or "未归类"))].append(event)
    for (asset, label), group in low_conf_groups.items():
        if not group:
            continue
        sources = sorted({str(event.get("source_account") or "") for event in group})
        candidates.append(
            {
                "candidate_id": _hash_id("WFWK", asset, label, len(group), ",".join(sources)),
                "candidate_type": "review_unmapped_or_low_confidence_terms",
                "asset": asset,
                "current_dimension_label": label,
                "evidence_count": len(group),
                "source_count": len(sources),
                "sources": sources,
                "sample_evidence": [_evidence_brief(event) for event in group[:6]],
                "suggested_action": "请人工/LLM复核是否新增维度、补充指标/事件词条，或扩充现有维度别名。",
                "review_status": "candidate",
            }
        )

    for asset, payload in assets.items():
        conflict_dims = [
            dim
            for dim in payload.get("dimensions") or []
            if (dim.get("consistency_counts") or {}).get("dimension_conflict")
            or (dim.get("consistency_counts") or {}).get("thesis_conflict")
        ]
        for dim in conflict_dims[:5]:
            candidates.append(
                {
                    "candidate_id": _hash_id("WFWK", asset, dim.get("dimension_label"), "conflict"),
                    "candidate_type": "review_dynamic_weight_or_logic_reversal",
                    "asset": asset,
                    "dimension_label": dim.get("dimension_label"),
                    "evidence_count": dim.get("evidence_count"),
                    "source_count": dim.get("source_count"),
                    "direction_score": dim.get("direction_score"),
                    "consistency_counts": dim.get("consistency_counts"),
                    "suggested_action": "该维度出现与上一期结论相反的研报证据，需检查是否是逻辑反转、时间演化，或原框架权重过高。",
                    "review_status": "candidate",
                }
            )
    return {
        "schema_version": "wechat_framework_update_candidates.v1",
        "candidate_count": len(candidates),
        "candidates": sorted(candidates, key=lambda item: (item.get("source_count") or 0, item.get("evidence_count") or 0), reverse=True),
    }


def build_wechat_research_evidence(
    root: str | Path | None = None,
    *,
    date: str,
    logic_run: str | Path | None = None,
    article_limit: int | None = None,
    max_chunks_per_article: int = 120,
    use_llm_assessment: bool = False,
    llm_provider: str = "m3",
    llm_asset_limit: int = 30,
) -> dict[str, Any]:
    root_path = quanta_data_root(root)
    date_key = _canonical_date(date)
    taxonomy = load_asset_taxonomy(root_path, required=True)
    logic_context = _load_logic_context(root_path, logic_run)
    articles = load_wechat_articles(root_path, date=date_key, article_limit=article_limit)
    events: list[dict[str, Any]] = []
    for article in articles:
        events.extend(
            map_article_to_evidence(
                article,
                taxonomy=taxonomy,
                root=root_path,
                logic_context=logic_context,
                max_chunks_per_article=max_chunks_per_article,
            )
        )

    assets = _aggregate_assets(
        events,
        logic_context=logic_context,
        root=root_path,
        use_llm_assessment=use_llm_assessment,
        llm_provider=llm_provider,
        llm_asset_limit=llm_asset_limit,
    )
    llm_assessments = [
        payload.get("llm_assessment")
        for payload in assets.values()
        if isinstance(payload.get("llm_assessment"), dict)
    ]
    framework_candidates = _framework_update_candidates(events, assets)
    return {
        "schema_version": "wechat_research_evidence.v1",
        "status": "candidate",
        "generated_at": utc_now_iso(),
        "date": date_key,
        "source": {
            "name": "hzzhqx_wechat",
            "raw_root": RAW_WECHAT_RELATIVE,
        },
        "logic_context": {"run_dir": logic_context.get("run_dir", "")},
        "analysis_framework_ref": latest_analysis_framework_refs(root_path),
        "semantic_layer": {
            "sectioning": "article_lines_to_asset_sections",
            "mapping": "asset_taxonomy_plus_framework_dimension_match",
            "direction": "rule_patterns_with_market_observation_exclusion",
            "llm_assessment": use_llm_assessment,
            "llm_provider": llm_provider if use_llm_assessment else "",
            "llm_asset_limit": llm_asset_limit if use_llm_assessment else 0,
        },
        "stats": {
            "article_count": len(articles),
            "mapped_evidence_count": len(events),
            "asset_count": len(assets),
            "source_count": len({article.get("source_account") for article in articles}),
            "fundamental_evidence_count": sum(1 for event in events if event.get("scoring_role") == "fundamental_evidence"),
            "market_observation_count": sum(1 for event in events if event.get("scoring_role") == "market_observation"),
            "consistency_counts": dict(Counter(str((event.get("consistency") or {}).get("status")) for event in events)),
            "llm_assessment_count": sum(1 for item in llm_assessments if item.get("method") == "llm"),
            "llm_relation_counts": dict(Counter(str(item.get("relation_to_prior") or "") for item in llm_assessments)),
            "framework_update_candidate_count": framework_candidates["candidate_count"],
        },
        "articles": [
            {
                key: article.get(key)
                for key in (
                    "article_id",
                    "title",
                    "source_account",
                    "published_at",
                    "source_url",
                    "raw_manifest",
                    "text_path",
                    "text_chars",
                )
            }
            for article in articles
        ],
        "assets": assets,
        "evidence": sorted(events, key=lambda item: (str(item.get("published_at") or ""), float(item.get("heat") or 0)), reverse=True),
        "framework_update_candidates": framework_candidates,
    }


def publish_wechat_research_evidence(
    root: str | Path | None = None,
    *,
    date: str,
    logic_run: str | Path | None = None,
    article_limit: int | None = None,
    max_chunks_per_article: int = 120,
    use_llm_assessment: bool = False,
    llm_provider: str = "m3",
    llm_asset_limit: int = 30,
) -> dict[str, Any]:
    root_path = quanta_data_root(root)
    date_key = _canonical_date(date)
    yyyy, mm, dd = dated_parts(date_key)
    payload = build_wechat_research_evidence(
        root_path,
        date=date_key,
        logic_run=logic_run,
        article_limit=article_limit,
        max_chunks_per_article=max_chunks_per_article,
        use_llm_assessment=use_llm_assessment,
        llm_provider=llm_provider,
        llm_asset_limit=llm_asset_limit,
    )
    stamp = datetime.now().strftime("%H%M%S")
    base = root_path / "agent_workspace" / "candidates" / "research_reports" / "wechat_evidence"
    archive_path = base / yyyy / mm / dd / f"wechat-research-evidence-{stamp}.json"
    latest_path = base / "latest" / "wechat-research-evidence.json"
    write_json(archive_path, payload)
    write_json(latest_path, payload)
    return {
        "archive": str(archive_path),
        "latest": str(latest_path),
        "payload": payload,
        "paths": {
            "archive": relative_to_root(archive_path, root_path),
            "latest": relative_to_root(latest_path, root_path),
        },
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Build WeChat research-report evidence aligned to commodity frameworks.")
    parser.add_argument("--date", required=True, help="YYYYMMDD or YYYY-MM-DD")
    parser.add_argument("--quanta-root", help="Override quanta_data root.")
    parser.add_argument("--logic-run", help="Path to futures daily run dir with trade_thesis.json.")
    parser.add_argument("--article-limit", type=int, help="Limit source articles for testing.")
    parser.add_argument("--max-chunks-per-article", type=int, default=120)
    parser.add_argument("--llm-assessment", action="store_true", help="Use LLM for asset-level conclusion updates.")
    parser.add_argument("--llm-provider", default="m3", help="LLM provider alias, e.g. m3 or deepseek.")
    parser.add_argument("--llm-asset-limit", type=int, default=30)
    args = parser.parse_args(argv)
    result = publish_wechat_research_evidence(
        args.quanta_root,
        date=args.date,
        logic_run=args.logic_run,
        article_limit=args.article_limit,
        max_chunks_per_article=args.max_chunks_per_article,
        use_llm_assessment=args.llm_assessment,
        llm_provider=args.llm_provider,
        llm_asset_limit=args.llm_asset_limit,
    )
    stats = result["payload"]["stats"]
    print(f"微信研报证据池 → {result['latest']}")
    print(
        "articles={article_count} evidence={mapped_evidence_count} assets={asset_count} framework_candidates={framework_update_candidate_count}".format(
            **stats
        )
    )


if __name__ == "__main__":
    main()
