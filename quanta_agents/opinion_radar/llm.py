from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from quanta_agents.core.llm_client import chat_messages

from . import config


_LLM_CACHE = config.CACHE_DIR / "llm_themes.json"


def _load_cache() -> dict[str, Any]:
    if not _LLM_CACHE.exists():
        return {}
    try:
        return json.loads(_LLM_CACHE.read_text(encoding="utf-8"))
    except ValueError:
        return {}


def _save_cache(cache: dict[str, Any]) -> None:
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _LLM_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def _cache_key(label: str, titles: list[str]) -> str:
    raw = "v2||" + label + "||" + "|".join(sorted(titles))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _chat(messages: list[dict[str, str]], max_tokens: int) -> str:
    return chat_messages(
        messages,
        max_tokens=max_tokens,
        temperature=0.2,
        timeout=40,
        provider=config.RADAR_LLM_PROVIDER,
        env_prefix="RADAR_LLM",
    )


def synthesize(briefs: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    if not config.LLM_ENABLED or len(briefs) < 2:
        return None
    listing = "\n".join(
        f"{i + 1}. [{b['label']}] {b.get('theme') or b['label']}：{b.get('summary', '')}"
        for i, b in enumerate(briefs)
    )
    cache = _load_cache()
    cache_key = "SYNTH::" + hashlib.sha1(listing.encode("utf-8")).hexdigest()
    if cache_key in cache:
        return cache[cache_key]

    prompt = (
        "下面是商品期货市场新闻按品种/宏观分桶后的市场主题，存在语义重复。\n"
        "请合并成 5-8 个互不重复的核心市场主题，每个主题给一个简洁名称(≤14字)"
        "和一句话概括(≤45字)，并列出归属条目序号。\n"
        '严格只输出 JSON：{"themes":[{"title":"...","summary":"...","members":[1,3,5]}]}\n\n'
        + listing
    )
    try:
        content = _strip_fences(_chat([{"role": "user", "content": prompt}], 700))
        parsed = json.loads(content)
        themes = parsed.get("themes") if isinstance(parsed, dict) else parsed
        result = [
            {
                "title": str(item.get("title", "")).strip(),
                "summary": str(item.get("summary", "")).strip(),
                "members": [
                    int(member)
                    for member in item.get("members", [])
                    if isinstance(member, (int, float, str)) and str(member).strip().isdigit()
                ],
            }
            for item in (themes or [])
            if item.get("title")
        ]
        if result:
            cache[cache_key] = result
            _save_cache(cache)
        return result or None
    except Exception:
        return None


def _call_llm(label: str, titles: list[str]) -> dict[str, str] | None:
    prompt = (
        f"你是商品期货舆情分析师。下面是同属「{label}」的一批新闻快讯，"
        f"请提炼当前最主要的 1 个市场主题，并说明其驱动逻辑。\n"
        f"严格只输出 JSON：{{\"theme\":\"不超过12字的主题名\","
        f"\"summary\":\"不超过40字概括市场在交易什么\","
        f"\"logic\":\"不超过60字的驱动逻辑：为何重要、影响哪些品种、传导路径\"}}\n\n"
        + "\n".join(f"- {title}" for title in titles[:30])
    )
    content = _strip_fences(_chat([{"role": "user", "content": prompt}], 320))
    parsed = json.loads(content)
    return {
        "theme": str(parsed.get("theme", "")).strip(),
        "summary": str(parsed.get("summary", "")).strip(),
        "logic": str(parsed.get("logic", "")).strip(),
    }


def name_bucket(label: str, titles: list[str], top_title: str) -> dict[str, str]:
    fallback = {"theme": label, "summary": top_title[:40], "logic": top_title[:60], "source": "rule"}
    if not config.LLM_ENABLED or not titles:
        return fallback
    cache = _load_cache()
    key = _cache_key(label, titles)
    if key in cache:
        return {**cache[key], "source": "llm_cached"}
    try:
        result = _call_llm(label, titles)
        if not result or not result.get("theme"):
            return fallback
        cache[key] = result
        _save_cache(cache)
        return {**result, "source": "llm"}
    except Exception:
        return fallback
