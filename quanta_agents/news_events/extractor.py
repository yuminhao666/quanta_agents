from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from quanta_agents.core.io import utc_now_iso
from quanta_agents.core.llm_client import chat
from quanta_agents.core.llm_json import parse_json_object

from .ids import mention_id


EVENT_TYPES = {"geopolitics", "policy", "macro", "supply", "demand", "inventory", "cost", "logistics", "market", "other"}
EVENT_STAGES = {"rumor", "proposal", "announced", "ongoing", "completed", "denial", "retraction", "update", "observation"}
TRUTH_STATUSES = {"unverified", "partially_confirmed", "confirmed", "disputed", "retracted"}
MODALITIES = {"reported", "official_statement", "forecast", "rumor", "market_observation"}

ENTITY_KEYWORDS = (
    "伊朗",
    "美国",
    "美联储",
    "FOMC",
    "OPEC",
    "OPEC+",
    "EIA",
    "API",
    "霍尔木兹",
    "霍尔木兹海峡",
    "中东",
    "俄罗斯",
    "乌克兰",
    "中国",
)
LOCATION_KEYWORDS = ("霍尔木兹", "霍尔木兹海峡", "中东", "伊朗", "美国", "俄罗斯", "乌克兰")


def _prompt_path(name: str) -> Path:
    return Path(__file__).resolve().parents[1] / "prompts" / name


def _news_payload(news_item: dict[str, Any]) -> dict[str, Any]:
    return {
        "news_id": news_item.get("news_id"),
        "publish_time": news_item.get("publish_time"),
        "title": news_item.get("title"),
        "content": news_item.get("content"),
        "normalized_text": news_item.get("normalized_text"),
        "important": news_item.get("important"),
        "channel": news_item.get("channel"),
    }


def _bounded_float(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return round(max(0.0, min(1.0, number)), 3)


def infer_event_type(text: str) -> str:
    rules = (
        ("geopolitics", ("霍尔木兹", "伊朗", "中东", "地缘", "冲突", "战争", "袭击", "制裁", "停火")),
        ("policy", ("政策", "监管", "关税", "法案", "财政", "央行", "降准", "LPR")),
        ("macro", ("美联储", "FOMC", "利率", "降息", "加息", "CPI", "PPI", "PMI", "GDP", "非农", "通胀")),
        ("inventory", ("库存", "EIA", "API", "仓单", "累库", "去库")),
        ("supply", ("供应", "供给", "产量", "减产", "增产", "停产", "复产", "检修", "出口")),
        ("demand", ("需求", "消费", "订单", "进口", "开工")),
        ("cost", ("成本", "利润", "加工费", "运费")),
        ("logistics", ("航运", "通航", "港口", "运河", "海峡", "物流")),
        ("market", ("上涨", "下跌", "回落", "拉升", "跳水", "收涨", "收跌")),
    )
    for event_type, words in rules:
        if any(word in text for word in words):
            return event_type
    return "other"


def infer_stage_truth_modality(text: str) -> tuple[str, str, str]:
    if "撤回" in text or "收回" in text or "更正" in text:
        return "retraction", "retracted", "official_statement"
    if "否认" in text or "驳斥" in text or "不属实" in text:
        return "denial", "confirmed", "official_statement" if "官方" in text else "reported"
    if "传闻" in text or "据悉" in text or "可能" in text or "考虑" in text:
        return "rumor", "unverified", "rumor"
    if "宣布" in text or "批准" in text or "发布" in text or "公布" in text:
        return "announced", "confirmed", "official_statement"
    if "更新" in text or "进展" in text or "继续" in text:
        return "update", "partially_confirmed", "reported"
    if re.search(r"上涨|下跌|回落|拉升|跳水|收涨|收跌", text):
        return "observation", "partially_confirmed", "market_observation"
    return "observation", "unverified", "reported"


def _entities(text: str) -> list[str]:
    found = [word for word in ENTITY_KEYWORDS if word in text]
    return list(dict.fromkeys(found))


def _locations(text: str) -> list[str]:
    found = [word for word in LOCATION_KEYWORDS if word in text]
    return list(dict.fromkeys(found))


def _action(text: str) -> str:
    for word in ("否认", "封锁", "撤回", "宣布", "批准", "公布", "袭击", "停火", "制裁", "减产", "增产", "库存变化", "上涨", "下跌", "回落"):
        if word in text:
            return word
    if "库存" in text:
        return "库存变化"
    if "可能" in text:
        return "可能发生"
    return "报道"


def _object_for_action(text: str, action: str) -> list[str]:
    if action in text:
        tail = text.split(action, 1)[1]
        tail = re.split(r"[，。；;,.]", tail, maxsplit=1)[0].strip()
        if tail:
            return [tail[:80]]
    if "霍尔木兹" in text:
        return ["霍尔木兹海峡通行"]
    return [text[:80]]


def _fallback_mentions(news_item: dict[str, Any], *, syndication_group_id: str | None = None) -> list[dict[str, Any]]:
    text = str(news_item.get("normalized_text") or "").strip()
    if not text:
        return []
    entities = _entities(text)
    locations = _locations(text)
    stage, truth_status, modality = infer_stage_truth_modality(text)
    action = _action(text)
    subject = entities[:1] or ([str(news_item.get("channel"))] if news_item.get("channel") else [])
    obj = _object_for_action(text, action)
    if action == "否认" and obj and not obj[0].startswith("封锁") and "封锁" in text:
        obj = ["封锁霍尔木兹海峡" if "霍尔木兹" in text else obj[0]]
    event_type = infer_event_type(text)
    summary_parts = ["/".join(subject), action, "/".join(obj)]
    canonical_summary = "".join(part for part in summary_parts if part).strip() or text[:160]
    mention = {
        "mention_id": mention_id(news_item["news_id"], canonical_summary),
        "source_news_id": news_item["news_id"],
        "subject": subject,
        "action": action,
        "object": obj,
        "location": locations,
        "event_type": event_type,
        "event_stage": stage,
        "modality": modality,
        "event_time": news_item.get("publish_time"),
        "entities": entities,
        "canonical_summary": canonical_summary[:260],
        "truth_status": truth_status,
        "market_attention": round(0.35 + min(int(news_item.get("important") or 0), 1) * 0.25, 3),
        "extraction_confidence": 0.35,
        "evidence_quote": text[:120],
        "created_at": utc_now_iso(),
        "extraction_method": "rule_fallback",
        "syndication_group_id": syndication_group_id,
    }
    mentions = []
    if stage == "denial" and obj:
        denied_object = obj[0]
        denied_action = "封锁" if "封锁" in denied_object or "关闭" in denied_object else "发生"
        denied_summary = f"被否认传闻：{denied_object}"
        mentions.append(
            {
                **mention,
                "mention_id": mention_id(news_item["news_id"], f"denied_claim::{denied_object}"),
                "subject": subject,
                "action": denied_action,
                "object": [denied_object],
                "event_stage": "rumor",
                "modality": "reported",
                "canonical_summary": denied_summary[:260],
                "truth_status": "disputed",
                "market_attention": 0.25,
                "extraction_confidence": 0.25,
                "evidence_quote": mention["evidence_quote"],
                "extraction_method": "rule_fallback_denied_claim",
            }
        )
    mentions.append(mention)
    if re.search(r"油价|金价|铜价|价格|主力合约", text) and re.search(r"回落|上涨|下跌|拉升|跳水", text):
        market_summary = re.search(r"([^。；;，,]*(?:油价|金价|铜价|价格|主力合约)[^。；;]*)", text)
        phrase = market_summary.group(1).strip() if market_summary else text[:120]
        if phrase and phrase != mention["canonical_summary"]:
            mentions.append(
                {
                    **mention,
                    "mention_id": mention_id(news_item["news_id"], f"market::{phrase}"),
                    "subject": [],
                    "action": "市场观察",
                    "object": [phrase[:80]],
                    "event_type": "market",
                    "event_stage": "observation",
                    "modality": "market_observation",
                    "canonical_summary": phrase[:260],
                    "truth_status": "partially_confirmed",
                    "extraction_confidence": 0.3,
                }
            )
    return mentions


def _normalize_llm_mention(raw: dict[str, Any], news_item: dict[str, Any], *, syndication_group_id: str | None) -> dict[str, Any]:
    text = str(news_item.get("normalized_text") or "")
    summary = str(raw.get("canonical_summary") or raw.get("summary") or text[:160]).strip()
    event_type = str(raw.get("event_type") or infer_event_type(summary)).strip()
    stage = str(raw.get("event_stage") or "observation").strip()
    modality = str(raw.get("modality") or "reported").strip()
    truth_status = str(raw.get("truth_status") or "unverified").strip()
    event_type = event_type if event_type in EVENT_TYPES else "other"
    stage = stage if stage in EVENT_STAGES else "observation"
    modality = modality if modality in MODALITIES else "reported"
    truth_status = truth_status if truth_status in TRUTH_STATUSES else "unverified"
    evidence_quote = str(raw.get("evidence_quote") or text[:120]).strip()
    if evidence_quote and evidence_quote not in text:
        evidence_quote = text[:120]
    return {
        "mention_id": mention_id(news_item["news_id"], summary),
        "source_news_id": news_item["news_id"],
        "subject": raw.get("subject") if isinstance(raw.get("subject"), list) else [],
        "action": str(raw.get("action") or _action(summary)),
        "object": raw.get("object") if isinstance(raw.get("object"), list) else [],
        "location": raw.get("location") if isinstance(raw.get("location"), list) else _locations(summary),
        "event_type": event_type,
        "event_stage": stage,
        "modality": modality,
        "event_time": raw.get("event_time") or news_item.get("publish_time"),
        "entities": raw.get("entities") if isinstance(raw.get("entities"), list) else _entities(summary),
        "canonical_summary": summary[:260],
        "truth_status": truth_status,
        "market_attention": _bounded_float(raw.get("market_attention"), 0.45),
        "extraction_confidence": _bounded_float(raw.get("extraction_confidence"), 0.55),
        "evidence_quote": evidence_quote[:160],
        "created_at": utc_now_iso(),
        "extraction_method": "llm",
        "syndication_group_id": syndication_group_id,
    }


def extract_event_mentions(
    news_item: dict[str, Any],
    *,
    use_llm: bool = True,
    syndication_group_id: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    errors: list[dict[str, Any]] = []
    if use_llm:
        try:
            prompt = _prompt_path("news_event_mention_extraction.v1.txt").read_text(encoding="utf-8")
            parsed = parse_json_object(
                chat(
                    f"{prompt}\n\n新闻对象：\n{json.dumps(_news_payload(news_item), ensure_ascii=False)}",
                    max_tokens=1800,
                    temperature=0.1,
                    timeout=80,
                )
            )
            raw_mentions = parsed.get("mentions") if isinstance(parsed.get("mentions"), list) else []
            return [
                _normalize_llm_mention(row, news_item, syndication_group_id=syndication_group_id)
                for row in raw_mentions
                if isinstance(row, dict)
            ], errors
        except Exception as exc:
            errors.append(
                {
                    "stage": "event_mention_extraction",
                    "news_id": news_item.get("news_id"),
                    "error": str(exc),
                    "fallback": "rule_fallback",
                }
            )
    return _fallback_mentions(news_item, syndication_group_id=syndication_group_id), errors


def _extract_event_mentions_chunk(
    prompt: str,
    chunk: list[dict[str, Any]],
    syndication_group_ids: dict[str, str] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    errors: list[dict[str, Any]] = []
    mentions: list[dict[str, Any]] = []
    item_by_id = {item["news_id"]: item for item in chunk}
    try:
        payload = [_news_payload(item) for item in chunk]
        parsed = parse_json_object(
            chat(
                f"{prompt}\n\n新闻对象数组：\n{json.dumps(payload, ensure_ascii=False)}",
                max_tokens=min(10000, 900 + 1000 * len(chunk)),
                temperature=0.1,
                timeout=120,
            )
        )
        rows = parsed.get("items") if isinstance(parsed.get("items"), list) else None
        if rows is None and parsed.get("news_id") and isinstance(parsed.get("mentions"), list):
            rows = [parsed]
        if rows is None and isinstance(parsed.get("mentions"), list):
            grouped: dict[str, list[dict[str, Any]]] = {}
            for raw in parsed["mentions"]:
                if isinstance(raw, dict):
                    grouped.setdefault(str(raw.get("source_news_id") or raw.get("news_id") or ""), []).append(raw)
            rows = [{"news_id": news_id, "mentions": raw_mentions} for news_id, raw_mentions in grouped.items()]
        rows = rows or []
        seen_news_ids: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            news_id = str(row.get("news_id") or "")
            item = item_by_id.get(news_id)
            if item is None:
                continue
            seen_news_ids.add(news_id)
            raw_mentions = row.get("mentions") if isinstance(row.get("mentions"), list) else []
            mentions.extend(
                _normalize_llm_mention(
                    raw,
                    item,
                    syndication_group_id=(syndication_group_ids or {}).get(news_id),
                )
                for raw in raw_mentions
                if isinstance(raw, dict)
            )
        for news_id, item in item_by_id.items():
            if news_id in seen_news_ids:
                continue
            errors.append(
                {
                    "stage": "event_mention_batch_extraction",
                    "news_id": news_id,
                    "error": "missing item in model response",
                    "fallback": "rule_fallback",
                }
            )
            mentions.extend(
                _fallback_mentions(
                    item,
                    syndication_group_id=(syndication_group_ids or {}).get(news_id),
                )
            )
    except Exception as exc:
        for item in chunk:
            news_id = item["news_id"]
            errors.append(
                {
                    "stage": "event_mention_batch_extraction",
                    "news_id": news_id,
                    "error": str(exc),
                    "fallback": "rule_fallback",
                }
            )
            mentions.extend(
                _fallback_mentions(
                    item,
                    syndication_group_id=(syndication_group_ids or {}).get(news_id),
                )
            )
    return mentions, errors


def extract_event_mentions_batch(
    news_items: list[dict[str, Any]],
    *,
    use_llm: bool = True,
    syndication_group_ids: dict[str, str] | None = None,
    batch_size: int = 8,
    max_workers: int = 1,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    errors: list[dict[str, Any]] = []
    mentions: list[dict[str, Any]] = []
    if not use_llm:
        for item in news_items:
            item_mentions, item_errors = extract_event_mentions(
                item,
                use_llm=False,
                syndication_group_id=(syndication_group_ids or {}).get(item["news_id"]),
            )
            mentions.extend(item_mentions)
            errors.extend(item_errors)
        return mentions, errors

    prompt = _prompt_path("news_event_mention_batch_extraction.v1.txt").read_text(encoding="utf-8")
    size = max(1, batch_size)
    chunks = [news_items[start : start + size] for start in range(0, len(news_items), size)]
    workers = max(1, max_workers)
    if workers == 1 or len(chunks) <= 1:
        for chunk in chunks:
            chunk_mentions, chunk_errors = _extract_event_mentions_chunk(prompt, chunk, syndication_group_ids)
            mentions.extend(chunk_mentions)
            errors.extend(chunk_errors)
        return mentions, errors

    ordered_results: dict[int, tuple[list[dict[str, Any]], list[dict[str, Any]]]] = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(_extract_event_mentions_chunk, prompt, chunk, syndication_group_ids): index
            for index, chunk in enumerate(chunks)
        }
        for future in as_completed(futures):
            ordered_results[futures[future]] = future.result()
    for index in sorted(ordered_results):
        chunk_mentions, chunk_errors = ordered_results[index]
        mentions.extend(chunk_mentions)
        errors.extend(chunk_errors)
    return mentions, errors
