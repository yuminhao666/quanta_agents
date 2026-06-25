from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from quanta_agents.core.config import quanta_data_root
from quanta_agents.core.io import atomic_write_text, dated_parts, read_json, relative_to_root, utc_now_iso, write_json
from quanta_agents.core.llm_client import chat, get_provider
from quanta_agents.core.llm_json import parse_json_object, strip_fences


SCHEMA_VERSION = "market_theme_state.v1"
AGENT_VERSION = "0.2.0"
DEFAULT_TOP_THEMES = 18
LLM_EXTRACTION_VERSION = "market_theme_llm_extractor.v1"


CONCEPT_RULES: list[dict[str, Any]] = [
    {
        "id": "US_IRAN_HORMUZ",
        "title": "美伊谈判与霍尔木兹通行不确定",
        "driver": "geopolitics",
        "keywords": ["美伊", "伊朗", "霍尔木兹", "瑞士", "铀浓缩", "黎巴嫩", "真主党", "以色列", "停火"],
        "required_any": ["美伊", "伊朗", "霍尔木兹", "真主党", "以色列"],
        "priority": 20,
    },
    {
        "id": "FED_RATE_PATH",
        "title": "美联储利率路径与美元估值扰动",
        "driver": "monetary_policy",
        "keywords": ["美联储", "FOMC", "点阵图", "加息", "降息", "联邦基金利率", "沃什", "美元指数"],
        "required_any": ["美联储", "FOMC", "点阵图", "联邦基金利率"],
        "priority": 20,
    },
    {
        "id": "CHINA_LIQUIDITY_POLICY",
        "title": "国内流动性与利率走廊调整",
        "driver": "liquidity",
        "keywords": ["央行", "逆回购", "DR001", "DR007", "利率走廊", "流动性", "资金面", "FIMARMBRepo"],
        "required_any": ["央行", "逆回购", "DR001", "DR007", "利率走廊", "FIMARMBRepo"],
        "priority": 18,
    },
    {
        "id": "WEATHER_CROP_RISK",
        "title": "天气扰动与农产品供给预期",
        "driver": "supply",
        "keywords": ["厄尔尼诺", "天气", "季风", "干旱", "降雨", "产量扰动", "减产预期"],
        "required_any": ["厄尔尼诺", "天气", "季风", "干旱", "降雨"],
        "priority": 18,
    },
    {
        "id": "BIOFUEL_POLICY",
        "title": "生柴政策与油脂需求预期",
        "driver": "demand",
        "keywords": ["B50", "B15", "生柴", "生物柴油", "生物燃料"],
        "required_any": ["B50", "B15", "生柴", "生物柴油", "生物燃料"],
        "priority": 18,
    },
    {
        "id": "OPEC_CRUDE_SUPPLY",
        "title": "OPEC增产与能源供应预期",
        "driver": "supply",
        "keywords": ["OPEC", "OPEC+", "增产", "减产", "原油供应", "桶/日", "产量配额", "减产执行率"],
        "required_any": ["OPEC", "OPEC+", "桶/日"],
        "exclude_any": ["厄尔尼诺", "天气", "季风", "干旱", "降雨", "B50", "B15", "生柴"],
        "priority": 18,
    },
    {
        "id": "COAL_SAFETY_SUPPLY",
        "title": "山西煤矿安监与黑色原料供应",
        "driver": "supply",
        "keywords": ["山西", "安监", "炼焦煤", "焦煤", "停产", "复产", "煤矿"],
        "required_any": ["山西", "安监", "炼焦煤", "焦煤", "煤矿"],
        "priority": 16,
    },
    {
        "id": "TRADE_TARIFF_POLICY",
        "title": "关税与贸易政策扰动",
        "driver": "trade_policy",
        "keywords": ["关税", "反倾销", "贸易政策", "出口限制", "进口限制", "贸易关系", "制裁", "豁免", "232调查"],
        "required_any": ["关税", "反倾销", "出口限制", "进口限制", "贸易关系", "制裁", "豁免", "232调查"],
        "exclude_any": ["巴西压榨", "新旧榨季", "印度出口政策"],
        "priority": 16,
    },
    {
        "id": "INVENTORY_WAREHOUSE",
        "title": "库存与仓单变化",
        "driver": "inventory",
        "keywords": [
            "仓单",
            "去库",
            "累库",
            "库容",
            "社库",
            "港口库存",
            "库存高位",
            "库存累积",
            "库存创新高",
            "库存下降",
            "库存减少",
            "全球库存",
        ],
        "required_any": [
            "仓单",
            "去库",
            "累库",
            "港口库存",
            "库存高位",
            "库存累积",
            "库存创新高",
            "库存下降",
            "库存减少",
            "全球库存",
        ],
        "exclude_any": ["采购传闻", "采购协议", "出口强劲", "到港高峰"],
        "priority": 14,
    },
    {
        "id": "PRODUCTION_CAPACITY",
        "title": "开工检修与产能变化",
        "driver": "production",
        "keywords": ["开工", "检修", "复产", "投产", "产能", "装置", "停机"],
        "required_any": ["开工", "检修", "复产", "投产", "产能", "装置", "停机"],
        "exclude_any": ["OPEC", "OPEC+", "桶/日", "厄尔尼诺", "天气"],
        "priority": 12,
    },
    {
        "id": "SUPPLY_SURPLUS_PRESSURE",
        "title": "供应宽松与产量回升压力",
        "driver": "supply",
        "keywords": ["供应维持宽松", "供应宽松", "供应压力", "产量回升", "供应边际回升", "供应过剩"],
        "required_any": ["供应维持宽松", "供应宽松", "供应压力", "产量回升", "供应边际回升", "供应过剩"],
        "priority": 12,
    },
    {
        "id": "PORT_LABOR_DISRUPTION",
        "title": "港口与劳资扰动",
        "driver": "logistics",
        "keywords": ["港工会", "停工授权", "黑德兰港", "港口", "航运"],
        "required_any": ["港工会", "停工授权", "黑德兰港", "航运"],
        "priority": 12,
    },
    {
        "id": "DEMAND_SEASONALITY",
        "title": "需求季节性与终端成交",
        "driver": "demand",
        "keywords": ["淡季", "旺季", "需求", "消费", "成交", "终端", "订单"],
        "required_any": ["淡季", "旺季", "需求", "消费", "成交", "终端", "订单"],
        "exclude_any": ["厄尔尼诺", "天气", "季风", "降雨"],
        "priority": 8,
    },
    {
        "id": "LITHIUM_IRON_PHOSPHATE",
        "title": "磷酸铁锂价格与新能源链条",
        "driver": "demand",
        "keywords": ["磷酸铁锂", "碳酸锂", "新能源", "电池"],
        "required_any": ["磷酸铁锂", "碳酸锂", "新能源", "电池"],
        "priority": 10,
    },
    {
        "id": "UK_POLITICS",
        "title": "英国政局传闻与风险情绪",
        "driver": "politics",
        "keywords": ["英国首相", "斯塔默", "辞职", "内阁"],
        "required_any": ["英国首相", "斯塔默", "内阁"],
        "priority": 12,
    },
]


MACRO_CONCEPTS = {
    "US_IRAN_HORMUZ",
    "FED_RATE_PATH",
    "CHINA_LIQUIDITY_POLICY",
    "TRADE_TARIFF_POLICY",
    "UK_POLITICS",
}


DRIVER_LABELS = {
    "geopolitics": "地缘风险",
    "monetary_policy": "货币政策",
    "liquidity": "流动性",
    "inventory": "库存仓单",
    "supply": "供应扰动",
    "demand": "需求变化",
    "trade_policy": "贸易政策",
    "market_theme": "综合主线",
    "logistics": "物流扰动",
    "politics": "政治风险",
    "production": "生产开工",
}


def _clean_text(value: Any, limit: int | None = None) -> str:
    text = str(value or "").strip()
    text = re.sub(r"<[^>]+>", "", text).replace("&nbsp;", " ")
    text = re.sub(r"\s+", " ", text).strip()
    if limit and len(text) > limit:
        return text[: max(limit - 1, 0)].rstrip() + "…"
    return text


def _hash_id(prefix: str, *parts: Any, size: int = 12) -> str:
    raw = "||".join(str(part) for part in parts)
    return f"{prefix}-{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:size].upper()}"


def _relative(path: Path, root: Path) -> str:
    return relative_to_root(path, root)


def _driver_from_text(text: str) -> str:
    lowered = text.lower()
    if any(word in text for word in ("伊朗", "霍尔木兹", "战争", "停火", "以色列", "真主党")):
        return "geopolitics"
    if any(word in text for word in ("美联储", "利率", "降息", "加息", "美元", "FOMC")):
        return "monetary_policy"
    if any(word in text for word in ("库存", "仓单", "去库", "累库")):
        return "inventory"
    if any(word in text for word in ("开工", "检修", "复产", "投产", "产能", "OPEC", "煤矿", "停产")):
        return "supply"
    if any(word in text for word in ("需求", "消费", "成交", "订单", "淡季", "旺季")):
        return "demand"
    if any(word in text for word in ("关税", "反倾销", "制裁", "出口", "贸易")):
        return "trade_policy"
    if "liquidity" in lowered or any(word in text for word in ("央行", "资金面", "流动性")):
        return "liquidity"
    return "market_theme"


def _keyword_hits(text: str, keywords: list[str]) -> list[str]:
    return [keyword for keyword in keywords if keyword and keyword in text]


def _rule_match_score(text: str, rule: dict[str, Any]) -> tuple[float, list[str]] | None:
    exclude_hits = _keyword_hits(text, list(rule.get("exclude_any") or []))
    if exclude_hits:
        return None

    required_any = list(rule.get("required_any") or [])
    required_hits = _keyword_hits(text, required_any)
    if required_any and not required_hits:
        return None

    required_all = list(rule.get("required_all") or [])
    if required_all and len(_keyword_hits(text, required_all)) < len(required_all):
        return None

    keyword_hits = _keyword_hits(text, list(rule.get("keywords") or []))
    min_hits = int(rule.get("min_hits") or 1)
    if len(keyword_hits) < min_hits:
        return None

    # Prefer concepts where the text contains both the required anchor and
    # additional evidence terms. This prevents broad terms such as "库存" or
    # "产量" from stealing clauses whose main variable is weather, trade, etc.
    score = float(rule.get("priority") or 0) + len(keyword_hits) + 1.5 * len(required_hits)
    return score, keyword_hits


def _concept_from_text(text: str, *, fallback_asset: str = "") -> tuple[str, str, str]:
    best: tuple[float, dict[str, Any], list[str]] | None = None
    for rule in CONCEPT_RULES:
        matched = _rule_match_score(text, rule)
        if not matched:
            continue
        score, hits = matched
        if best is None or score > best[0]:
            best = (score, rule, hits)
    if best:
        _, rule, _ = best
        return str(rule["id"]), str(rule["title"]), str(rule["driver"])

    driver = _driver_from_text(text)
    seed = fallback_asset or _clean_text(text, 24) or driver
    slug = hashlib.sha1(f"{driver}::{seed}".encode("utf-8")).hexdigest()[:10].upper()
    driver_label = DRIVER_LABELS.get(driver, driver)
    title = f"{fallback_asset}{driver_label}" if fallback_asset else _clean_text(text, 18) or driver_label
    return f"GENERIC_{driver.upper()}_{slug}", title, driver


def _theme_id(concept_id: str, asset: str = "") -> str:
    if concept_id in MACRO_CONCEPTS or not asset:
        return f"MKT-THEME-MACRO-{concept_id}"
    asset_part = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", asset).strip("-").upper()
    return f"MKT-THEME-{asset_part}-{concept_id}"


def _latest_futures_daily_run_dir(root: Path, date_key: str | None = None) -> Path | None:
    base = root / "agent_workspace" / "candidates" / "futures_daily_raw_runs"
    manifests = list(base.glob("*/*/*/RUN-*/manifest.json"))
    if date_key:
        clean = date_key.replace("-", "")
        manifests = [path for path in manifests if "".join(path.relative_to(base).parts[:3]) == clean]
    candidates: list[tuple[str, Path]] = []
    for manifest_path in manifests:
        try:
            manifest = read_json(manifest_path)
        except Exception:
            continue
        outputs = manifest.get("outputs") if isinstance(manifest.get("outputs"), dict) else {}
        if str(manifest.get("status") or "").startswith("aborted"):
            continue
        if {"market_review", "logic_summary"} <= set(outputs):
            candidates.append((str(manifest.get("generated_at") or ""), manifest_path.parent))
    return sorted(candidates)[-1][1] if candidates else None


def _manifest_output_path(run_dir: Path, key: str, fallback_glob: str) -> Path | None:
    manifest_path = run_dir / "manifest.json"
    if manifest_path.exists():
        try:
            outputs = read_json(manifest_path).get("outputs") or {}
            relative = outputs.get(key)
            if relative and (run_dir / str(relative)).exists():
                return run_dir / str(relative)
        except Exception:
            pass
    matches = sorted(run_dir.glob(fallback_glob))
    return matches[-1] if matches else None


def _summary_path(run_dir: Path) -> Path | None:
    return _manifest_output_path(run_dir, "summary", "*_commodity_summary.json")


def _market_review_path(run_dir: Path) -> Path | None:
    return _manifest_output_path(run_dir, "market_review", "*_commodity_marketreview.json")


def _load_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = read_json(path)
    return payload if isinstance(payload, dict) else {}


def _add_evidence(bucket: dict[str, Any], evidence: dict[str, Any]) -> None:
    bucket["evidence_items"].append(evidence)
    bucket["source_types"].add(evidence["source_type"])
    bucket["driver_refs"].add(evidence["driver_ref"])
    for asset in evidence.get("asset_refs") or []:
        if asset:
            bucket["asset_refs"].add(str(asset))
    bucket["source_scores"][evidence["source_type"]] += float(evidence.get("weight") or 0)


def _ensure_theme_bucket(themes: dict[str, dict[str, Any]], theme_id: str, title: str) -> dict[str, Any]:
    return themes.setdefault(
        theme_id,
        {
            "theme_id": theme_id,
            "title_candidates": Counter(),
            "asset_refs": set(),
            "driver_refs": set(),
            "source_types": set(),
            "source_scores": defaultdict(float),
            "evidence_items": [],
        },
    )


def _summary_evidence_candidates(
    *,
    root: Path,
    source_path: Path,
    source_field: str,
    source_role: str,
    text: Any,
    weight: float,
) -> list[dict[str, Any]]:
    candidates = []
    for index, segment in enumerate(_summary_segments(text)):
        candidates.append(
            {
                "evidence_id": _hash_id("MTHEV", source_role, source_field, index, segment),
                "source_type": "futures_daily",
                "source_role": source_role,
                "source_path": _relative(source_path, root),
                "source_field": source_field,
                "asset_refs": [],
                "summary": segment,
                "weight": weight,
            }
        )
    return candidates


def _add_rule_candidate_evidence(
    *,
    themes: dict[str, dict[str, Any]],
    candidate: dict[str, Any],
) -> None:
    concept_id, title, driver = _concept_from_text(str(candidate.get("summary") or ""))
    bucket = _ensure_theme_bucket(themes, _theme_id(concept_id), title)
    bucket["title_candidates"][title] += 1
    _add_evidence(bucket, {**candidate, "driver_ref": driver, "classification_method": "rule_fallback"})


def _llm_available(provider: str | None) -> tuple[bool, str]:
    try:
        llm = get_provider(provider)
    except Exception as exc:  # pragma: no cover - defensive env failure path.
        return False, str(exc)
    if not llm.api_keys:
        return False, f"missing API key for {llm.name}; set {llm.api_key_hint}"
    return True, f"{llm.name}:{llm.model}"


def _bounded_float(value: Any, *, default: float = 0.5) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(0.0, min(1.0, number))


def _llm_cache_dir(root: Path) -> Path:
    return root / "agent_workspace" / "cache" / "market_theme_state" / LLM_EXTRACTION_VERSION


def _llm_cache_key(candidates: list[dict[str, Any]]) -> str:
    slim = [
        {
            "id": item.get("evidence_id"),
            "role": item.get("source_role"),
            "field": item.get("source_field"),
            "summary": item.get("summary"),
        }
        for item in candidates
    ]
    return _hash_id("MTHEME-LLM", json.dumps(slim, ensure_ascii=False, sort_keys=True), size=16)


def _llm_prompt(candidates: list[dict[str, Any]]) -> str:
    payload = {
        "task": "market_theme_extraction_and_evidence_classification",
        "schema_version": LLM_EXTRACTION_VERSION,
        "evidence_candidates": [
            {
                "evidence_id": item.get("evidence_id"),
                "source_role": item.get("source_role"),
                "source_field": item.get("source_field"),
                "summary": item.get("summary"),
            }
            for item in candidates
        ],
        "allowed_drivers": sorted(DRIVER_LABELS),
    }
    return f"""你是 Quanta 商品期货市场主题状态 Agent。

任务：基于给定 evidence_candidates，抽取今天市场正在交易的稳定主题，并把每条证据归到最相关的主题。

硬约束：
- 只能使用 evidence_candidates 中的信息，不能补充外部事实。
- 每个主题必须引用 evidence_id；没有证据的主题不要输出。
- 主题 ID 目标是稳定概念 ID，不是自然语言标题；请用英文大写蛇形，如 US_IRAN_HORMUZ、FED_RATE_PATH、TRADE_TARIFF_POLICY。
- 方向、强弱、时间窗口不要写进 concept_id 或标题，放在 reason/状态字段里。
- 不要把顺带出现的词当主因：例如“强厄尔尼诺导致农产品产量扰动”不是 OPEC；“印度出口政策”不等同关税贸易扰动；“采购传闻/到港高峰”不等同库存仓单变化。
- 输出目标是“市场主题状态”，不是品种行情卡片；优先抽取宏观、政策、地缘、跨资产、产业链共性和重要供需扰动。
- 单品种主题只有在证据明确属于期市速递重要事件、无法合理泛化为上层主题、且对市场有持续跟踪价值时才保留；否则把品种只放在 reason 或 asset_refs，不要放大成独立主题。
- 如果一个品种表现只是同一宏观主题的下游影响，例如“贵金属受美联储压制”，优先并入上层“美联储利率路径”主题，不要重复建主题。
- 可以把同一证据归入多个主题，但每个主题 evidence_ids 尽量控制在 1-6 条，优先选择最能代表该主题的证据。
- 如果某条证据只是品种涨跌榜单或过长混合句，请只归类其中清楚的共性因子。

输出严格 JSON，不要 markdown，不要解释。格式：
{{
  "themes": [
    {{
      "concept_id": "STABLE_CONCEPT_ID",
      "title": "中文主题名",
      "driver": "geopolitics|monetary_policy|liquidity|inventory|supply|demand|trade_policy|production|logistics|politics|market_theme",
      "scope": "macro|cross_asset|asset",
      "asset_refs": [],
      "evidence_ids": ["MTHEV-..."],
      "confidence": 0.0,
      "reason": "为什么这些证据属于同一主题"
    }}
  ],
  "discarded_evidence": [
    {{"evidence_id": "MTHEV-...", "reason": "不构成稳定主题或证据主因不清"}}
  ],
  "quality_notes": []
}}

输入：
```json
{json.dumps(payload, ensure_ascii=False, indent=2)}
```
"""


def _parse_llm_theme_response(text: str) -> dict[str, Any]:
    stripped = strip_fences(text)
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        parsed = parse_json_object(stripped)
    if isinstance(parsed, list):
        parsed = {"themes": parsed}
    if not isinstance(parsed, dict):
        raise ValueError("LLM theme output is not a JSON object")
    if not isinstance(parsed.get("themes"), list):
        raise ValueError("LLM theme output missing themes list")
    return parsed


def _concept_id_from_llm(raw: Any, title: str) -> str:
    value = str(raw or "").strip().upper()
    value = value.removeprefix("MKT-THEME-MACRO-").removeprefix("MKT-THEME-")
    value = re.sub(r"[^A-Z0-9_]+", "_", value).strip("_")
    if re.fullmatch(r"[A-Z][A-Z0-9_]{2,80}", value):
        return value
    return f"LLM_{hashlib.sha1(title.encode('utf-8')).hexdigest()[:10].upper()}"


def _apply_llm_themes(
    *,
    themes: dict[str, dict[str, Any]],
    candidates: list[dict[str, Any]],
    parsed: dict[str, Any],
    provider_status: str,
) -> dict[str, Any]:
    candidates_by_id = {str(item["evidence_id"]): item for item in candidates}
    accepted_theme_count = 0
    accepted_evidence_count = 0
    invalid_ref_count = 0
    empty_theme_count = 0
    quality_notes = [
        _clean_text(item, 180)
        for item in (parsed.get("quality_notes") or [])
        if _clean_text(item, 180)
    ][:8]

    for raw_theme in parsed.get("themes") or []:
        if not isinstance(raw_theme, dict):
            continue
        title = _clean_text(raw_theme.get("title") or raw_theme.get("canonical_title_zh"), 80)
        evidence_ids = [str(item) for item in (raw_theme.get("evidence_ids") or []) if item]
        evidence_items = [candidates_by_id[item] for item in evidence_ids if item in candidates_by_id]
        invalid_ref_count += len([item for item in evidence_ids if item not in candidates_by_id])
        if not title:
            title = _clean_text(raw_theme.get("concept_id"), 80) or "未命名市场主题"
        if not evidence_items:
            empty_theme_count += 1
            continue

        driver = str(raw_theme.get("driver") or "").strip()
        if driver not in DRIVER_LABELS:
            driver = _driver_from_text(" ".join([title, *[str(item.get("summary") or "") for item in evidence_items]]))
        concept_id = _concept_id_from_llm(raw_theme.get("concept_id") or raw_theme.get("theme_id"), title)
        theme_key = _theme_id(concept_id)
        bucket = _ensure_theme_bucket(themes, theme_key, title)
        bucket["title_candidates"][title] += 2
        confidence = _bounded_float(raw_theme.get("confidence"), default=0.65)
        asset_refs = [str(item).strip() for item in (raw_theme.get("asset_refs") or []) if str(item).strip()]
        for asset in asset_refs:
            bucket["asset_refs"].add(asset)
        for evidence in evidence_items:
            _add_evidence(
                bucket,
                {
                    **evidence,
                    "asset_refs": sorted({*asset_refs, *[str(item) for item in evidence.get("asset_refs") or [] if item]}),
                    "driver_ref": driver,
                    "classification_method": "llm",
                    "llm_extraction": {
                        "version": LLM_EXTRACTION_VERSION,
                        "provider": provider_status,
                        "concept_id": concept_id,
                        "confidence": round(confidence, 3),
                        "reason": _clean_text(raw_theme.get("reason"), 220),
                    },
                },
            )
            accepted_evidence_count += 1
        accepted_theme_count += 1

    return {
        "accepted_theme_count": accepted_theme_count,
        "accepted_evidence_count": accepted_evidence_count,
        "invalid_ref_count": invalid_ref_count,
        "empty_theme_count": empty_theme_count,
        "discarded_evidence_count": len(parsed.get("discarded_evidence") or []),
        "quality_notes": quality_notes,
    }


def _run_llm_theme_extraction(
    *,
    root: Path,
    themes: dict[str, dict[str, Any]],
    candidates: list[dict[str, Any]],
    provider: str | None,
    timeout: int,
    require_llm: bool,
    use_cache: bool,
) -> dict[str, Any]:
    available, provider_status = _llm_available(provider)
    if not available:
        if require_llm:
            raise RuntimeError(f"LLM theme extraction required but unavailable: {provider_status}")
        return {
            "enabled": False,
            "method": "rule_fallback",
            "status": "unavailable",
            "reason": provider_status,
            "candidate_count": len(candidates),
        }

    cache_dir = _llm_cache_dir(root)
    cache_key = _llm_cache_key(candidates)
    cache_path = cache_dir / f"{cache_key}.json"
    cache_hit = False
    try:
        if use_cache and cache_path.exists():
            parsed = read_json(cache_path)
            cache_hit = True
        else:
            response = chat(
                _llm_prompt(candidates),
                provider=provider,
                max_tokens=3600,
                temperature=0.1,
                timeout=timeout,
            )
            parsed = _parse_llm_theme_response(response)
            cache_dir.mkdir(parents=True, exist_ok=True)
            write_json(cache_path, parsed)
        apply_stats = _apply_llm_themes(
            themes=themes,
            candidates=candidates,
            parsed=parsed,
            provider_status=provider_status,
        )
        if apply_stats["accepted_theme_count"] <= 0:
            raise ValueError("LLM returned no themes with valid evidence_ids")
        return {
            "enabled": True,
            "method": "llm",
            "status": "succeeded",
            "provider": provider_status,
            "version": LLM_EXTRACTION_VERSION,
            "candidate_count": len(candidates),
            "cache_key": cache_key,
            "cache_hit": cache_hit,
            **apply_stats,
        }
    except Exception as exc:
        if require_llm:
            raise RuntimeError(f"LLM theme extraction failed: {str(exc)[:300]}") from exc
        return {
            "enabled": True,
            "method": "rule_fallback",
            "status": "failed",
            "provider": provider_status,
            "version": LLM_EXTRACTION_VERSION,
            "candidate_count": len(candidates),
            "error": str(exc)[:300],
        }


def _summary_segments(text: Any) -> list[str]:
    clean = _clean_text(text, 2000)
    if not clean:
        return []
    clean = re.sub(r"\s+(\d+[.、])", r"\n\1", clean)
    parts = re.split(r"\n+|；|。", clean)
    out = []
    for part in parts:
        item = re.sub(r"^\d+[.、]\s*", "", part).strip(" ：:，,")
        # The source summaries may start with asset score prefixes. Keep the
        # market variable but drop the asset-specific lead so theme IDs stay generic.
        item = re.sub(r"^[\u4e00-\u9fffA-Za-z0-9（）()]+?\([-+]?\d+(?:\.\d+)?\)[：:]\s*", "", item)
        if len(item) < 8:
            continue
        common_match = re.search(r"(共性(?:支撑|压力|扰动|利多|利空)来自)(.+)$", item)
        if common_match:
            prefix = common_match.group(1)
            for factor in re.split(r"、|；|;", common_match.group(2)):
                factor = factor.strip(" ：:，,。")
                if len(factor) >= 3:
                    out.append(_clean_text(f"{prefix}{factor}", 220))
            continue
        out.append(_clean_text(item, 360))
    return out[:24]


def _add_summary_text_evidence(
    *,
    root: Path,
    themes: dict[str, dict[str, Any]],
    source_path: Path,
    source_field: str,
    source_role: str,
    text: Any,
    weight: float,
) -> int:
    count = 0
    for index, segment in enumerate(_summary_segments(text)):
        concept_id, title, driver = _concept_from_text(segment)
        bucket = _ensure_theme_bucket(themes, _theme_id(concept_id), title)
        bucket["title_candidates"][title] += 1
        _add_evidence(
            bucket,
            {
                "evidence_id": _hash_id("MTHEV", source_role, source_field, index, segment),
                "source_type": "futures_daily",
                "source_role": source_role,
                "source_path": _relative(source_path, root),
                "source_field": source_field,
                "asset_refs": [],
                "driver_ref": driver,
                "summary": segment,
                "weight": weight,
            },
        )
        count += 1
    return count


def _collect_futures_evidence(
    *,
    root: Path,
    run_dir: Path,
    themes: dict[str, dict[str, Any]],
    use_llm: bool = True,
    llm_provider: str | None = None,
    llm_timeout: int = 120,
    require_llm: bool = False,
    use_llm_cache: bool = True,
) -> dict[str, Any]:
    market_review_path = _market_review_path(run_dir)
    market_review = _load_json_if_exists(market_review_path) if market_review_path else {}
    logic_summary_path = run_dir / "logic_summary.json"
    logic_summary = _load_json_if_exists(logic_summary_path)

    extracted_counts = {}
    candidates: list[dict[str, Any]] = []
    if market_review_path:
        field_candidates = _summary_evidence_candidates(
            root=root,
            source_path=market_review_path,
            source_field="market_events_summary",
            source_role="market_review_events",
            text=market_review.get("market_events_summary"),
            weight=0.20,
        )
        candidates.extend(field_candidates)
        extracted_counts["market_events_summary"] = len(field_candidates)

        field_candidates = _summary_evidence_candidates(
            root=root,
            source_path=market_review_path,
            source_field="market_logic_summary",
            source_role="market_review_logic",
            text=market_review.get("market_logic_summary"),
            weight=0.18,
        )
        candidates.extend(field_candidates)
        extracted_counts["market_logic_summary"] = len(field_candidates)

    if logic_summary_path.exists():
        field_candidates = _summary_evidence_candidates(
            root=root,
            source_path=logic_summary_path,
            source_field="important_events_summary",
            source_role="logic_summary_events",
            text=logic_summary.get("important_events_summary"),
            weight=0.22,
        )
        candidates.extend(field_candidates)
        extracted_counts["important_events_summary"] = len(field_candidates)

        field_candidates = _summary_evidence_candidates(
            root=root,
            source_path=logic_summary_path,
            source_field="important_logic_summary",
            source_role="logic_summary_logic",
            text=logic_summary.get("important_logic_summary"),
            weight=0.20,
        )
        candidates.extend(field_candidates)
        extracted_counts["important_logic_summary"] = len(field_candidates)

    theme_extraction = {
        "enabled": False,
        "method": "rule_fallback",
        "status": "disabled",
        "candidate_count": len(candidates),
    }
    if candidates and use_llm:
        theme_extraction = _run_llm_theme_extraction(
            root=root,
            themes=themes,
            candidates=candidates,
            provider=llm_provider,
            timeout=llm_timeout,
            require_llm=require_llm,
            use_cache=use_llm_cache,
        )

    if not candidates:
        theme_extraction = {**theme_extraction, "status": "no_candidates"}
    elif theme_extraction.get("method") != "llm":
        for candidate in candidates:
            _add_rule_candidate_evidence(themes=themes, candidate=candidate)

    return {
        "run_dir": _relative(run_dir, root),
        "source_mode": "futures_summary_only",
        "market_review": _relative(market_review_path, root) if market_review_path else "",
        "logic_summary": _relative(logic_summary_path, root) if logic_summary_path.exists() else "",
        "fields": {
            "market_review": ["market_events_summary", "market_logic_summary"],
            "logic_summary": ["important_events_summary", "important_logic_summary"],
        },
        "extracted_counts": extracted_counts,
        "theme_extraction": theme_extraction,
    }


def _collect_radar_evidence(root: Path, radar_path: Path, themes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    radar = _load_json_if_exists(radar_path)
    default_window = str(radar.get("default_window") or "24")
    snapshot = (radar.get("windows") or {}).get(default_window) or {}
    radar_themes = snapshot.get("themes") if isinstance(snapshot.get("themes"), list) else []
    max_heat = max([float(item.get("heat") or 0) for item in radar_themes] or [1.0])

    for item in radar_themes:
        text = _clean_text(
            " ".join(
                [
                    str(item.get("theme") or ""),
                    str(item.get("label") or ""),
                    str(item.get("summary") or ""),
                    " ".join(str(flash.get("text") or "") for flash in (item.get("top_flashes") or [])[:3]),
                ]
            ),
            500,
        )
        if not text:
            continue
        asset = str(item.get("label") or "") if item.get("kind") == "variety" else ""
        concept_id, title, driver = _concept_from_text(text, fallback_asset=asset)
        theme_key = _theme_id(concept_id, "" if concept_id in MACRO_CONCEPTS else asset)
        bucket = _ensure_theme_bucket(themes, theme_key, title)
        bucket["title_candidates"][title] += 1
        if asset:
            bucket["asset_refs"].add(asset)
        _add_evidence(
            bucket,
            {
                "evidence_id": _hash_id("MTHEV", "radar_theme", item.get("key"), item.get("theme"), text),
                "source_type": "opinion_radar",
                "source_role": "radar_theme",
                "source_path": _relative(radar_path, root),
                "source_field": f"windows.{default_window}.themes.{item.get('key')}",
                "asset_refs": [asset] if asset else list(item.get("varieties") or []),
                "driver_ref": driver,
                "summary": _clean_text(item.get("summary") or item.get("theme") or item.get("label"), 260),
                "weight": 0.20 * (float(item.get("heat") or 0) / max(max_heat, 1e-6)),
                "radar_key": item.get("key") or "",
                "heat": item.get("heat") or 0,
                "important_count": item.get("important_count") or 0,
                "top_flashes": [
                    {
                        "text": _clean_text(flash.get("text"), 120),
                        "url": flash.get("url") or "",
                        "publish_time": flash.get("publish_time") or "",
                        "important": int(flash.get("important") or 0),
                    }
                    for flash in (item.get("top_flashes") or [])[:3]
                ],
            },
        )

    return {
        "path": _relative(radar_path, root),
        "generated_at": radar.get("generated_at") or "",
        "default_window": default_window,
        "theme_count": len(radar_themes),
        "synthesis_count": len(snapshot.get("synthesis") or []),
    }


def _collect_news_brief_evidence(root: Path, brief_path: Path, themes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    brief = _load_json_if_exists(brief_path)
    narrative = brief.get("llm_brief") if isinstance(brief.get("llm_brief"), dict) else {}
    fields = {
        "key_news": 0.34,
        "watch_items": 0.24,
        "asset_notes": 0.20,
        "overview": 0.18,
    }
    for field, weight in fields.items():
        values = narrative.get(field) if isinstance(narrative.get(field), list) else []
        for index, item in enumerate(values):
            if not isinstance(item, dict):
                continue
            text = _clean_text(item.get("text"), 500)
            if not text:
                continue
            asset = str(item.get("asset") or "")
            concept_id, title, driver = _concept_from_text(text, fallback_asset=asset)
            theme_key = _theme_id(concept_id, "" if concept_id in MACRO_CONCEPTS else asset)
            bucket = _ensure_theme_bucket(themes, theme_key, title)
            bucket["title_candidates"][title] += 2 if field == "key_news" else 1
            _add_evidence(
                bucket,
                {
                    "evidence_id": _hash_id("MTHEV", "half_day_news", field, index, text),
                    "source_type": "half_day_news_brief",
                    "source_role": field,
                    "source_path": _relative(brief_path, root),
                    "source_field": f"llm_brief.{field}[{index}]",
                    "asset_refs": [asset] if asset else [],
                    "driver_ref": driver,
                    "summary": _clean_text(text, 300),
                    "weight": weight,
                    "source_refs": item.get("source_refs") or [],
                },
            )

    return {
        "path": _relative(brief_path, root),
        "generated_at": brief.get("generated_at") or "",
        "title": brief.get("title") or "",
        "stats": brief.get("stats") or {},
    }


def _previous_state(root: Path, path: str | Path | None = None) -> dict[str, Any]:
    state_path = Path(path).expanduser() if path else root / "agent_workspace" / "candidates" / "market_themes" / "latest" / "market-theme-state.json"
    return _load_json_if_exists(state_path)


def _score_theme(bucket: dict[str, Any], previous_theme: dict[str, Any] | None) -> dict[str, Any]:
    source_scores = bucket["source_scores"]
    futures_score = min(1.0, source_scores.get("futures_daily", 0.0))
    news_score = min(1.0, source_scores.get("half_day_news_brief", 0.0))
    radar_score = min(1.0, source_scores.get("opinion_radar", 0.0))
    previous_strength = float((previous_theme or {}).get("strength") or 0)
    strength = min(1.0, 0.45 * futures_score + 0.35 * news_score + 0.20 * radar_score + 0.70 * previous_strength)
    source_count = len(bucket["source_types"])
    confidence = min(
        1.0,
        0.30 + 0.14 * source_count + 0.025 * min(len(bucket["evidence_items"]), 8) + 0.03 * min(len(bucket["asset_refs"]), 6),
    )
    if strength >= 0.55:
        status = "active"
    elif strength >= 0.20:
        status = "emerging"
    else:
        status = "cooling" if previous_theme else "emerging"

    if not previous_theme:
        trend = "strengthening"
    else:
        diff = strength - previous_strength
        if diff > 0.08:
            trend = "strengthening"
        elif diff < -0.08:
            trend = "weakening"
        else:
            trend = "stable"
    return {
        "strength": round(strength, 3),
        "confidence": round(confidence, 3),
        "status": status,
        "trend": trend,
        "source_scores": {
            "futures_daily": round(futures_score, 3),
            "half_day_news_brief": round(news_score, 3),
            "opinion_radar": round(radar_score, 3),
            "previous_decay": round(0.70 * previous_strength, 3),
        },
    }


def _build_theme_rows(
    themes: dict[str, dict[str, Any]],
    previous: dict[str, Any],
    *,
    generated_at: str,
    top: int,
) -> list[dict[str, Any]]:
    previous_by_id = {
        str(theme.get("theme_id")): theme
        for theme in (previous.get("themes") or [])
        if isinstance(theme, dict) and theme.get("theme_id")
    }
    rows: list[dict[str, Any]] = []
    for theme_id, bucket in themes.items():
        previous_theme = previous_by_id.get(theme_id)
        score = _score_theme(bucket, previous_theme)
        evidence_items = sorted(
            bucket["evidence_items"],
            key=lambda item: (float(item.get("weight") or 0), item.get("source_type") or ""),
            reverse=True,
        )
        if "GENERIC_" in theme_id and len(evidence_items) < 2:
            continue
        title = bucket["title_candidates"].most_common(1)[0][0] if bucket["title_candidates"] else theme_id
        first_seen = (previous_theme or {}).get("first_seen") or generated_at
        last_seen = generated_at
        asset_refs = sorted(bucket["asset_refs"])
        row = {
            "theme_id": theme_id,
            "title": title,
            "scope": "cross_asset" if len(asset_refs) > 1 else ("asset" if asset_refs else "macro"),
            "asset_refs": asset_refs[:20],
            "driver_refs": sorted(bucket["driver_refs"]),
            "status": score["status"],
            "strength": score["strength"],
            "confidence": score["confidence"],
            "trend": score["trend"],
            "first_seen": first_seen,
            "last_seen": last_seen,
            "source_scores": score["source_scores"],
            "evidence": {
                "items": evidence_items[:12],
                "source_type_counts": dict(Counter(item["source_type"] for item in evidence_items)),
            },
            "change_explanation": _change_explanation(score, evidence_items, previous_theme),
            "watch_items": _watch_items(evidence_items),
        }
        rows.append(row)

    rows.sort(key=lambda item: (item["strength"], item["confidence"], len(item["evidence"]["items"])), reverse=True)
    return rows[:top]


def _change_explanation(
    score: dict[str, Any],
    evidence_items: list[dict[str, Any]],
    previous_theme: dict[str, Any] | None,
) -> str:
    source_counts = Counter(item["source_type"] for item in evidence_items)
    parts = []
    if source_counts.get("futures_daily"):
        parts.append(f"期市速递提供 {source_counts['futures_daily']} 条基准事件/主线证据")
    if source_counts.get("half_day_news_brief"):
        parts.append(f"半日新闻提供 {source_counts['half_day_news_brief']} 条事件链或观察项")
    if source_counts.get("opinion_radar"):
        parts.append(f"舆情雷达提供 {source_counts['opinion_radar']} 个热度主题")
    if not parts:
        parts.append("今日缺少新增证据")
    if previous_theme:
        parts.append(f"较上一状态趋势为 {score['trend']}")
    else:
        parts.append("今日进入主题状态池")
    return "；".join(parts) + "。"


def _watch_items(evidence_items: list[dict[str, Any]]) -> list[str]:
    items = []
    for evidence in evidence_items:
        if evidence.get("source_role") == "watch_items":
            items.append(_clean_text(evidence.get("summary"), 160))
    if items:
        return items[:5]
    for evidence in evidence_items:
        if evidence.get("source_type") == "half_day_news_brief":
            items.append(f"跟踪：{_clean_text(evidence.get('summary'), 140)}")
    return items[:3]


def _changes(themes: list[dict[str, Any]], previous: dict[str, Any]) -> dict[str, list[str]]:
    previous_by_id = {
        str(theme.get("theme_id")): theme
        for theme in (previous.get("themes") or [])
        if isinstance(theme, dict) and theme.get("theme_id")
    }
    new_themes = []
    reinforced = []
    weakened = []
    conflict = []
    archive = []
    for theme in themes:
        theme_id = theme["theme_id"]
        prev = previous_by_id.get(theme_id)
        if not prev:
            new_themes.append(theme_id)
            continue
        diff = float(theme.get("strength") or 0) - float(prev.get("strength") or 0)
        if diff > 0.08:
            reinforced.append(theme_id)
        elif diff < -0.08:
            weakened.append(theme_id)
        if len(theme.get("driver_refs") or []) > 2 and len((theme.get("evidence") or {}).get("items") or []) > 5:
            conflict.append(theme_id)
        if theme.get("status") == "cooling":
            archive.append(theme_id)
    return {
        "new_themes": new_themes[:12],
        "reinforced_themes": reinforced[:12],
        "weakened_themes": weakened[:12],
        "conflict_themes": conflict[:12],
        "archive_candidates": archive[:12],
    }


def build_market_theme_state(
    *,
    root: str | Path | None = None,
    date_key: str | None = None,
    futures_run_dir: str | Path | None = None,
    radar_path: str | Path | None = None,
    half_day_brief_path: str | Path | None = None,
    previous_state_path: str | Path | None = None,
    include_radar: bool = False,
    include_half_day_news: bool = False,
    use_llm: bool = True,
    llm_provider: str | None = None,
    llm_timeout: int = 120,
    require_llm: bool = False,
    use_llm_cache: bool = True,
    top: int = DEFAULT_TOP_THEMES,
    now: datetime | None = None,
) -> dict[str, Any]:
    root_path = quanta_data_root(root)
    generated_dt = now or datetime.now()
    generated_at = generated_dt.isoformat(sep=" ")
    as_of_date = date_key or generated_dt.strftime("%Y%m%d")
    run_dir = Path(futures_run_dir).expanduser() if futures_run_dir else _latest_futures_daily_run_dir(root_path, date_key)
    if not run_dir:
        raise FileNotFoundError("未找到可用期市速递 run")

    radar = Path(radar_path).expanduser() if radar_path else root_path / "agent_workspace" / "candidates" / "opinion_radar" / "latest" / "radar.json"
    half_day = (
        Path(half_day_brief_path).expanduser()
        if half_day_brief_path
        else root_path / "agent_workspace" / "candidates" / "news_brief" / "half_day" / "latest" / "half-day-news-brief.json"
    )
    previous = _previous_state(root_path, previous_state_path)
    if previous_state_path is None and str(previous.get("as_of_date") or "") == str(as_of_date):
        previous = {}
    theme_buckets: dict[str, dict[str, Any]] = {}

    futures_source = _collect_futures_evidence(
        root=root_path,
        run_dir=run_dir,
        themes=theme_buckets,
        use_llm=use_llm,
        llm_provider=llm_provider,
        llm_timeout=llm_timeout,
        require_llm=require_llm,
        use_llm_cache=use_llm_cache,
    )
    radar_source = (
        _collect_radar_evidence(root_path, radar, theme_buckets)
        if include_radar
        else {"path": _relative(radar, root_path), "included": False}
    )
    news_source = (
        _collect_news_brief_evidence(root_path, half_day, theme_buckets)
        if include_half_day_news
        else {"path": _relative(half_day, root_path), "included": False}
    )
    themes = _build_theme_rows(theme_buckets, previous, generated_at=generated_at, top=top)

    return {
        "schema_version": SCHEMA_VERSION,
        "status": "candidate",
        "as_of_date": as_of_date,
        "generated_at": generated_at,
        "agent": {"name": "market_theme_state", "version": AGENT_VERSION},
        "source_refs": {
            "futures_daily": futures_source,
            "opinion_radar": radar_source,
            "half_day_news_brief": news_source,
            "previous_state": previous.get("source_refs", {}).get("self_path", "") if previous else "",
        },
        "stats": {
            "raw_theme_bucket_count": len(theme_buckets),
            "theme_count": len(themes),
            "theme_extraction": futures_source.get("theme_extraction") or {},
            "source_type_counts": dict(
                Counter(
                    item["source_type"]
                    for bucket in theme_buckets.values()
                    for item in bucket["evidence_items"]
                )
            ),
        },
        "themes": themes,
        "changes": _changes(themes, previous),
        "quality_notes": _quality_notes(themes, futures_source, radar_source, news_source),
    }


def _quality_notes(
    themes: list[dict[str, Any]],
    futures_source: dict[str, Any],
    radar_source: dict[str, Any],
    news_source: dict[str, Any],
) -> list[str]:
    notes = []
    extraction = futures_source.get("theme_extraction") or {}
    if extraction.get("method") != "llm":
        reason = extraction.get("reason") or extraction.get("error") or extraction.get("status") or "unknown"
        notes.append(f"市场主题抽取使用规则兜底，LLM 主路径未生效：{_clean_text(reason, 180)}。")
    for note in extraction.get("quality_notes") or []:
        notes.append(_clean_text(note, 220))
    if radar_source.get("included", True) and not radar_source.get("theme_count"):
        notes.append("opinion_radar latest 缺少 themes，本轮主题热度层覆盖不足。")
    if news_source.get("included", True) and not news_source.get("generated_at"):
        notes.append("news_brief/half_day latest 缺失或未生成，本轮新闻半日聚类层覆盖不足。")
    if not themes:
        notes.append("未生成有效市场主题，需要检查期市速递、舆情雷达和半日新闻简报输入。")
    return notes


def render_markdown(payload: dict[str, Any]) -> str:
    extraction = (payload.get("stats") or {}).get("theme_extraction") or {}
    lines = [
        "# 市场主题日更状态",
        "",
        f"- 生成时间: {payload.get('generated_at')}",
        f"- 日期: {payload.get('as_of_date')}",
        f"- 主题数: {payload.get('stats', {}).get('theme_count')}",
        f"- 主题抽取: {extraction.get('method') or 'unknown'} / {extraction.get('status') or 'unknown'}",
        "",
        "## Top Themes",
        "",
    ]
    for index, theme in enumerate(payload.get("themes") or [], 1):
        assets = "、".join(theme.get("asset_refs") or []) or "宏观/跨资产"
        drivers = "、".join(theme.get("driver_refs") or []) or "-"
        lines.extend(
            [
                f"### {index}. {theme.get('title')}",
                "",
                f"- theme_id: `{theme.get('theme_id')}`",
                f"- 状态: {theme.get('status')} / {theme.get('trend')} / strength {theme.get('strength')} / confidence {theme.get('confidence')}",
                f"- 资产: {assets}",
                f"- driver: {drivers}",
                f"- 变化说明: {theme.get('change_explanation')}",
                "- 关键证据:",
            ]
        )
        for evidence in (theme.get("evidence") or {}).get("items", [])[:4]:
            lines.append(
                f"  - [{evidence.get('source_type')}] {evidence.get('source_role')}: {_clean_text(evidence.get('summary'), 160)}"
            )
        watch = theme.get("watch_items") or []
        if watch:
            lines.append("- 观察项:")
            for item in watch[:3]:
                lines.append(f"  - {_clean_text(item, 160)}")
        lines.append("")

    notes = payload.get("quality_notes") or []
    if notes:
        lines.extend(["## Quality Notes", ""])
        for note in notes:
            lines.append(f"- {note}")
        lines.append("")
    return "\n".join(lines)


def publish_market_theme_state(
    *,
    root: str | Path | None = None,
    date_key: str | None = None,
    futures_run_dir: str | Path | None = None,
    radar_path: str | Path | None = None,
    half_day_brief_path: str | Path | None = None,
    previous_state_path: str | Path | None = None,
    include_radar: bool = False,
    include_half_day_news: bool = False,
    use_llm: bool = True,
    llm_provider: str | None = None,
    llm_timeout: int = 120,
    require_llm: bool = False,
    use_llm_cache: bool = True,
    top: int = DEFAULT_TOP_THEMES,
    now: datetime | None = None,
) -> dict[str, Any]:
    root_path = quanta_data_root(root)
    generated_dt = now or datetime.now()
    payload = build_market_theme_state(
        root=root_path,
        date_key=date_key,
        futures_run_dir=futures_run_dir,
        radar_path=radar_path,
        half_day_brief_path=half_day_brief_path,
        previous_state_path=previous_state_path,
        include_radar=include_radar,
        include_half_day_news=include_half_day_news,
        use_llm=use_llm,
        llm_provider=llm_provider,
        llm_timeout=llm_timeout,
        require_llm=require_llm,
        use_llm_cache=use_llm_cache,
        top=top,
        now=generated_dt,
    )
    date_for_path = str(payload.get("as_of_date") or generated_dt.strftime("%Y%m%d")).replace("-", "")[:8]
    yyyy, mm, dd = dated_parts(date_for_path)
    stamp = generated_dt.strftime("%H%M%S")
    run_id = f"RUN-MARKET-THEME-{date_for_path}-{stamp}"
    base = root_path / "agent_workspace" / "candidates" / "market_themes"
    archive_dir = base / yyyy / mm / dd
    latest_dir = base / "latest"
    run_dir = root_path / "agent_workspace" / "runs" / "market_themes" / yyyy / mm / dd / run_id
    archive_json = archive_dir / "market-theme-state.json"
    archive_md = archive_dir / "market-theme-state.md"
    latest_json = latest_dir / "market-theme-state.json"
    latest_md = latest_dir / "market-theme-state.md"
    markdown = render_markdown(payload)

    payload.setdefault("source_refs", {})["self_path"] = _relative(latest_json, root_path)
    write_json(archive_json, payload)
    atomic_write_text(archive_md, markdown)
    latest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(archive_json, latest_json)
    shutil.copy2(archive_md, latest_md)

    manifest = {
        "schema_version": "market_theme_state_run_manifest.v1",
        "status": "succeeded",
        "run_id": run_id,
        "run_type": "market_theme_state",
        "started_at": utc_now_iso(),
        "finished_at": utc_now_iso(),
        "inputs": payload.get("source_refs"),
        "outputs": {
            "archive_json": _relative(archive_json, root_path),
            "archive_markdown": _relative(archive_md, root_path),
            "latest_json": _relative(latest_json, root_path),
            "latest_markdown": _relative(latest_md, root_path),
        },
        "requires_review": True,
        "generator": {
            "project": "quanta_agents",
            "module": "quanta_agents.market_themes.state",
            "version": AGENT_VERSION,
        },
        "model_refs": [
            {
                "task": "market_theme_extraction_and_evidence_classification",
                "method": (payload.get("stats") or {}).get("theme_extraction", {}).get("method"),
                "status": (payload.get("stats") or {}).get("theme_extraction", {}).get("status"),
                "provider": (payload.get("stats") or {}).get("theme_extraction", {}).get("provider", ""),
                "version": LLM_EXTRACTION_VERSION,
            }
        ],
    }
    write_json(run_dir / "manifest.json", manifest)
    return {
        "run_id": run_id,
        "payload": payload,
        "latest_json": str(latest_json),
        "latest_markdown": str(latest_md),
        "archive_json": str(archive_json),
        "archive_markdown": str(archive_md),
        "run_manifest": str(run_dir / "manifest.json"),
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Build daily market theme state from existing Quanta artifacts.")
    parser.add_argument("--quanta-root", help="Override GJ_QUANTA_DATA_ROOT.")
    parser.add_argument("--date", help="YYYYMMDD; used for output partition and futures daily run selection.")
    parser.add_argument("--futures-run-dir", help="Explicit futures_daily_raw_runs RUN directory.")
    parser.add_argument("--radar-path", help="Explicit opinion radar json path.")
    parser.add_argument("--half-day-brief-path", help="Explicit half-day news brief json path.")
    parser.add_argument("--previous-state-path", help="Explicit previous market-theme-state json path.")
    parser.add_argument("--include-radar", action="store_true", help="Also mix opinion radar themes into the state.")
    parser.add_argument("--include-half-day-news", action="store_true", help="Also mix half-day news brief into the state.")
    parser.add_argument("--llm-provider", help="LLM provider, e.g. m3 or deepseek; default reads QUANTA_AGENT_LLM_PROVIDER.")
    parser.add_argument("--llm-timeout", type=int, default=120)
    parser.add_argument("--require-llm", action="store_true", help="Fail instead of falling back to deterministic rules.")
    parser.add_argument("--no-llm", action="store_true", help="Disable model extraction and use deterministic fallback rules.")
    parser.add_argument("--no-llm-cache", action="store_true", help="Do not reuse cached LLM extraction responses.")
    parser.add_argument("--top", type=int, default=DEFAULT_TOP_THEMES)
    args = parser.parse_args(argv)

    result = publish_market_theme_state(
        root=args.quanta_root,
        date_key=args.date,
        futures_run_dir=args.futures_run_dir,
        radar_path=args.radar_path,
        half_day_brief_path=args.half_day_brief_path,
        previous_state_path=args.previous_state_path,
        include_radar=args.include_radar,
        include_half_day_news=args.include_half_day_news,
        use_llm=not args.no_llm,
        llm_provider=args.llm_provider,
        llm_timeout=args.llm_timeout,
        require_llm=args.require_llm,
        use_llm_cache=not args.no_llm_cache,
        top=args.top,
    )
    payload = result["payload"]
    print(f"run_id: {result['run_id']}")
    print(f"latest_json: {result['latest_json']}")
    print(f"latest_markdown: {result['latest_markdown']}")
    print(f"theme_count: {payload['stats']['theme_count']}")
    extraction = payload["stats"].get("theme_extraction") or {}
    print(f"theme_extraction: {extraction.get('method')} / {extraction.get('status')}")
    for theme in payload.get("themes", [])[:8]:
        print(
            f"- {theme['title']} | {theme['status']} {theme['trend']} "
            f"strength={theme['strength']} evidence={len(theme['evidence']['items'])}"
        )


if __name__ == "__main__":
    main()
