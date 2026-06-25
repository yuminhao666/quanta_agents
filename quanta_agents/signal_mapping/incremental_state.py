from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quanta_agents.core.config import quanta_data_root
from quanta_agents.core.io import dated_parts, read_json, relative_to_root, utc_now_iso, write_json


STATE_SCHEMA_VERSION = "incremental_research_state.v1"
STATE_UPDATER_VERSION = "incremental_state_updater.v1"
DEFAULT_WORK_ORDER_ID = "WO-DEV-20260620-012"


def _hash_id(prefix: str, *parts: Any) -> str:
    raw = "||".join(str(part or "") for part in parts)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16].upper()
    return f"{prefix}-{digest}"


def _clean(value: Any, *, limit: int | None = None) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if limit is not None and len(text) > limit:
        return text[:limit].rstrip(" ,，;；") + "..."
    return text


def _norm(value: Any) -> str:
    text = _clean(value).lower()
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", text)


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _signal_ref(signal: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(signal.get("signal_id") or ""),
        "label": _clean(signal.get("notes") or signal.get("signal_kind") or signal.get("signal_id"), limit=96),
        "ref_type": "signal",
    }


def _theme_ref(theme_id: str, title: str) -> dict[str, Any]:
    return {"id": theme_id, "label": title, "ref_type": "theme_state"}


def _dedupe_refs(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    out: list[dict[str, Any]] = []
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        key = (
            str(ref.get("ref_type") or ""),
            str(ref.get("id") or ""),
            str(ref.get("path") or ref.get("url") or ref.get("label") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(ref)
    return out


def _merge_ref_lists(*values: Any) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for value in values:
        refs.extend(item for item in _list(value) if isinstance(item, dict))
    return _dedupe_refs(refs)


def _date_from_signal(signal: dict[str, Any], fallback: str) -> str:
    window = _dict(signal.get("time_window"))
    for key in ("start", "end"):
        text = _clean(window.get(key))
        if text:
            return text[:10]
    created = _clean(signal.get("created_at") or signal.get("updated_at"))
    return created[:10] if created else fallback


def _direction_value(direction: Any) -> float:
    value = str(direction or "").strip().lower()
    if value in {"bullish", "risk_up", "positive", "support"}:
        return 1.0
    if value in {"bearish", "risk_down", "negative", "pressure"}:
        return -1.0
    return 0.0


def _signal_weight(signal: dict[str, Any]) -> float:
    try:
        strength = float(signal.get("strength"))
    except (TypeError, ValueError):
        strength = 0.5
    try:
        confidence = float(signal.get("confidence"))
    except (TypeError, ValueError):
        confidence = 0.45
    return max(0.01, min(1.0, abs(strength) * max(0.01, confidence)))


def _is_conflict(signal: dict[str, Any]) -> bool:
    if signal.get("signal_kind") == "thesis_conflict":
        return True
    return bool(signal.get("conflict_refs"))


def _phase(support_count: int, conflict_count: int, observation_count: int) -> str:
    if conflict_count > 0:
        return "conflicted"
    if support_count >= 2:
        return "tracking"
    if observation_count > support_count:
        return "watching"
    return "new"


def _normalization_payload(obj: dict[str, Any]) -> dict[str, Any]:
    """Return upstream LLM normalization fields when an information processor supplied them."""
    for key in ("llm_normalization", "normalization", "semantic_normalization"):
        payload = obj.get(key)
        if isinstance(payload, dict):
            return payload
    mapping = obj.get("mapping")
    if isinstance(mapping, dict) and isinstance(mapping.get("normalization"), dict):
        return mapping["normalization"]
    return {}


def _asset_key(refs: list[dict[str, Any]]) -> str:
    ids = sorted(_clean(ref.get("id") or ref.get("label")) for ref in refs if _clean(ref.get("id") or ref.get("label")))
    return "|".join(ids)


def _theme_title(theme: dict[str, Any], signals: list[dict[str, Any]] | None = None) -> str:
    norm = _normalization_payload(theme)
    for key in ("canonical_title_zh", "canonical_title", "event_title_zh", "title_zh"):
        text = _clean(norm.get(key), limit=96)
        if text:
            return text
    title = _clean(theme.get("title"), limit=96)
    if title:
        return title
    for signal in signals or []:
        for ref in _list(signal.get("theme_refs")):
            text = _clean(ref.get("label"), limit=96)
            if text:
                return text
    return "未命名主题"


def _theme_type(theme: dict[str, Any], signals: list[dict[str, Any]] | None = None) -> str:
    norm = _normalization_payload(theme)
    theme_type = _clean(norm.get("theme_type") or theme.get("theme_type"))
    if theme_type:
        return theme_type
    texts = [_theme_title(theme, signals)]
    for signal in signals or []:
        texts.append(_clean(_dict(signal.get("event_definition_layers")).get("fact_layer")))
        texts.append(_clean(signal.get("notes")))
    joined = " ".join(texts)
    if any(word in joined for word in ("伊朗", "中东", "霍尔木兹", "地缘", "制裁", "冲突")):
        return "geopolitics"
    if any(word in joined for word in ("美联储", "利率", "通胀", "美元", "社融")):
        return "macro"
    if any(word in joined for word in ("库存", "仓单", "去库", "累库")):
        return "inventory"
    if any(word in joined for word in ("供给", "供应", "产量", "减产", "增产")):
        return "supply"
    if any(word in joined for word in ("需求", "消费", "订单", "终端")):
        return "demand"
    return "other"


def _theme_identity_candidates(theme: dict[str, Any]) -> list[str]:
    norm = _normalization_payload(theme)
    ids = [
        norm.get("canonical_theme_id"),
        norm.get("theme_state_id"),
        theme.get("theme_state_id"),
        theme.get("canonical_theme_id"),
    ]
    return [_clean(item) for item in ids if _clean(item)]


def _theme_match_key(title: str, theme_type: str, asset_refs: list[dict[str, Any]]) -> str:
    return f"{_norm(title)}|{theme_type}|{_asset_key(asset_refs)}"


def _theme_state_id(
    theme: dict[str, Any],
    state: dict[str, Any],
    *,
    signals: list[dict[str, Any]] | None = None,
) -> str:
    for candidate in _theme_identity_candidates(theme):
        if candidate in _dict(state.get("themes")):
            return candidate
        if candidate:
            return candidate

    title = _theme_title(theme, signals)
    theme_type = _theme_type(theme, signals)
    asset_refs = _merge_ref_lists(theme.get("asset_refs"), *(signal.get("asset_refs") for signal in signals or []))
    match_key = _theme_match_key(title, theme_type, asset_refs)
    for theme_id, existing in _dict(state.get("themes")).items():
        keys = set(_list(existing.get("match_keys")))
        aliases = {_norm(alias) for alias in _list(existing.get("aliases"))}
        if match_key in keys or _norm(title) in aliases:
            return str(theme_id)
    return _hash_id("THSTATE", match_key)


def _event_key(signal: dict[str, Any]) -> str:
    norm = _normalization_payload(signal)
    for key in ("canonical_event_id", "event_state_id", "event_key"):
        value = _clean(norm.get(key) or signal.get(key))
        if value:
            return value
    layers = _dict(signal.get("event_definition_layers"))
    fact = _clean(layers.get("fact_layer") or signal.get("notes"), limit=360)
    settlement = _clean(layers.get("settlement_rule_layer"), limit=240)
    assets = _asset_key(_list(signal.get("asset_refs")))
    if signal.get("signal_kind") == "event_definition":
        return _hash_id("EVSTATE", fact, settlement, assets)
    window = _dict(signal.get("time_window"))
    horizon = _clean(window.get("horizon"))
    return _hash_id("EVSTATE", fact, assets, horizon)


def _dimension_key(signal: dict[str, Any], theme_id: str) -> str | None:
    framework_refs = [ref for ref in _list(signal.get("framework_node_refs")) if isinstance(ref, dict)]
    if not framework_refs:
        return None
    framework = framework_refs[0]
    assets = _asset_key(_list(signal.get("asset_refs")))
    node = _clean(framework.get("id") or framework.get("label"))
    if not node:
        return None
    return _hash_id("DIMSTATE", assets, node, theme_id)


def _empty_state(*, generated_at: str | None = None) -> dict[str, Any]:
    now = generated_at or utc_now_iso()
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "status": "candidate",
        "state_id": _hash_id("IRSTATE", now),
        "generated_at": now,
        "updated_at": now,
        "updater_version": STATE_UPDATER_VERSION,
        "processing_policy": {
            "mode": "incremental_merge",
            "llm_role": (
                "LLM runs in the information-processing layer to emit canonical ids, titles, "
                "event definitions, and dimension refs; this state updater performs auditable merges."
            ),
            "fallback": "deterministic_title_asset_theme_type_matching",
        },
        "stats": {
            "theme_count": 0,
            "event_count": 0,
            "dimension_count": 0,
            "signal_count": 0,
        },
        "themes": {},
        "events": {},
        "dimensions": {},
        "processed_signal_ids": [],
        "run_history": [],
    }


def _copy_state(previous_state: dict[str, Any] | None, *, generated_at: str | None = None) -> dict[str, Any]:
    if not previous_state:
        return _empty_state(generated_at=generated_at)
    # JSON round-trip keeps the updater side-effect free for callers.
    state = json.loads(json.dumps(previous_state, ensure_ascii=False))
    state["schema_version"] = STATE_SCHEMA_VERSION
    state["updated_at"] = generated_at or utc_now_iso()
    state.setdefault("themes", {})
    state.setdefault("events", {})
    state.setdefault("dimensions", {})
    state.setdefault("processed_signal_ids", [])
    state.setdefault("run_history", [])
    return state


def _ensure_theme(
    state: dict[str, Any],
    theme_id: str,
    theme: dict[str, Any],
    *,
    signals: list[dict[str, Any]],
    observed_at: str,
) -> dict[str, Any]:
    title = _theme_title(theme, signals)
    theme_type = _theme_type(theme, signals)
    asset_refs = _merge_ref_lists(theme.get("asset_refs"), *(signal.get("asset_refs") for signal in signals))
    source_refs = _merge_ref_lists(theme.get("source_refs"), *(signal.get("source_ref") for signal in signals))
    framework_refs = _merge_ref_lists(
        theme.get("framework_node_refs"), *(signal.get("framework_node_refs") for signal in signals)
    )
    match_key = _theme_match_key(title, theme_type, asset_refs)
    themes = state["themes"]
    item = themes.setdefault(
        theme_id,
        {
            "theme_state_id": theme_id,
            "title": title,
            "theme_type": theme_type,
            "aliases": [],
            "asset_refs": [],
            "framework_node_refs": [],
            "source_refs": [],
            "signal_refs": [],
            "event_refs": [],
            "dimension_refs": [],
            "first_seen_at": observed_at,
            "last_seen_at": observed_at,
            "support_count": 0,
            "conflict_count": 0,
            "observation_count": 0,
            "current_phase": "new",
            "match_keys": [],
            "llm_normalization_refs": [],
        },
    )
    item["title"] = title if item.get("title") == "未命名主题" else item.get("title", title)
    item["theme_type"] = item.get("theme_type") or theme_type
    item["aliases"] = sorted({_clean(alias) for alias in [*item.get("aliases", []), title, theme.get("title")] if _clean(alias)})
    item["asset_refs"] = _merge_ref_lists(item.get("asset_refs"), asset_refs)
    item["framework_node_refs"] = _merge_ref_lists(item.get("framework_node_refs"), framework_refs)
    item["source_refs"] = _merge_ref_lists(item.get("source_refs"), source_refs)
    item["first_seen_at"] = min(str(item.get("first_seen_at") or observed_at), observed_at)
    item["last_seen_at"] = max(str(item.get("last_seen_at") or observed_at), observed_at)
    item["match_keys"] = sorted({*item.get("match_keys", []), match_key})
    normalization = _normalization_payload(theme)
    if normalization:
        item["llm_normalization_refs"] = _merge_ref_lists(
            item.get("llm_normalization_refs"),
            [
                {
                    "ref_type": "llm_normalization",
                    "id": _clean(normalization.get("normalization_id") or normalization.get("canonical_theme_id") or theme_id),
                    "label": _clean(normalization.get("canonical_title_zh") or normalization.get("canonical_title") or title),
                }
            ],
        )
    return item


def _update_event(
    state: dict[str, Any],
    signal: dict[str, Any],
    *,
    theme_id: str,
    theme_title: str,
    observed_at: str,
) -> dict[str, Any]:
    key = _event_key(signal)
    layers = _dict(signal.get("event_definition_layers"))
    event = state["events"].setdefault(
        key,
        {
            "event_state_id": key,
            "fact_layer": _clean(layers.get("fact_layer") or signal.get("notes"), limit=420),
            "political_layer": _clean(layers.get("political_layer"), limit=260) or None,
            "settlement_rule_layer": _clean(layers.get("settlement_rule_layer"), limit=360) or None,
            "asset_refs": [],
            "theme_refs": [],
            "source_refs": [],
            "signal_refs": [],
            "first_seen_at": observed_at,
            "last_seen_at": observed_at,
            "observation_count": 0,
        },
    )
    event["asset_refs"] = _merge_ref_lists(event.get("asset_refs"), signal.get("asset_refs"))
    event["theme_refs"] = _merge_ref_lists(event.get("theme_refs"), [_theme_ref(theme_id, theme_title)])
    event["source_refs"] = _merge_ref_lists(event.get("source_refs"), [signal.get("source_ref")])
    event["signal_refs"] = _merge_ref_lists(event.get("signal_refs"), [_signal_ref(signal)])
    event["first_seen_at"] = min(str(event.get("first_seen_at") or observed_at), observed_at)
    event["last_seen_at"] = max(str(event.get("last_seen_at") or observed_at), observed_at)
    event["observation_count"] = int(event.get("observation_count") or 0) + 1
    return event


def _update_dimension(
    state: dict[str, Any],
    signal: dict[str, Any],
    *,
    theme_id: str,
    theme_title: str,
    observed_at: str,
) -> dict[str, Any] | None:
    key = _dimension_key(signal, theme_id)
    if key is None:
        return None
    framework = _list(signal.get("framework_node_refs"))[0]
    weight = _signal_weight(signal)
    signed = _direction_value(signal.get("direction")) * weight
    dimension = state["dimensions"].setdefault(
        key,
        {
            "dimension_state_id": key,
            "asset_refs": [],
            "framework_node_ref": framework,
            "theme_refs": [],
            "signal_refs": [],
            "first_seen_at": observed_at,
            "last_seen_at": observed_at,
            "signal_count": 0,
            "support_count": 0,
            "conflict_count": 0,
            "weighted_direction_sum": 0.0,
            "total_weight": 0.0,
            "direction_score": 0.0,
        },
    )
    dimension["asset_refs"] = _merge_ref_lists(dimension.get("asset_refs"), signal.get("asset_refs"))
    dimension["theme_refs"] = _merge_ref_lists(dimension.get("theme_refs"), [_theme_ref(theme_id, theme_title)])
    dimension["signal_refs"] = _merge_ref_lists(dimension.get("signal_refs"), [_signal_ref(signal)])
    dimension["first_seen_at"] = min(str(dimension.get("first_seen_at") or observed_at), observed_at)
    dimension["last_seen_at"] = max(str(dimension.get("last_seen_at") or observed_at), observed_at)
    dimension["signal_count"] = int(dimension.get("signal_count") or 0) + 1
    if _is_conflict(signal):
        dimension["conflict_count"] = int(dimension.get("conflict_count") or 0) + 1
    else:
        dimension["support_count"] = int(dimension.get("support_count") or 0) + 1
    dimension["weighted_direction_sum"] = round(float(dimension.get("weighted_direction_sum") or 0.0) + signed, 6)
    dimension["total_weight"] = round(float(dimension.get("total_weight") or 0.0) + weight, 6)
    if dimension["total_weight"] > 0:
        dimension["direction_score"] = round(dimension["weighted_direction_sum"] / dimension["total_weight"], 4)
    return dimension


def _signals_by_theme(signals: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for signal in signals:
        refs = [ref for ref in _list(signal.get("theme_refs")) if isinstance(ref, dict)]
        if not refs:
            grouped.setdefault("", []).append(signal)
            continue
        for ref in refs:
            grouped.setdefault(str(ref.get("id") or ""), []).append(signal)
    return grouped


def apply_incremental_update(
    previous_state: dict[str, Any] | None,
    *,
    signals: list[dict[str, Any]],
    themes: list[dict[str, Any]],
    run_id: str | None = None,
    generated_at: str | None = None,
    input_refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Merge new signal/theme candidates into historical theme, event, and dimension state."""
    now = generated_at or utc_now_iso()
    state = _copy_state(previous_state, generated_at=now)
    processed = set(str(item) for item in _list(state.get("processed_signal_ids")))
    grouped = _signals_by_theme(signals)
    theme_by_id = {str(theme.get("theme_anchor_id") or ""): theme for theme in themes}
    emitted_signal_ids: set[str] = set()
    update_counter: Counter[str] = Counter()

    all_theme_keys = set(theme_by_id) | set(grouped)
    for source_theme_id in sorted(all_theme_keys):
        related_signals = grouped.get(source_theme_id, [])
        theme = theme_by_id.get(source_theme_id) or {
            "title": (related_signals[0].get("theme_refs") or [{}])[0].get("label")
            if related_signals
            else "未命名主题",
            "asset_refs": [ref for signal in related_signals for ref in _list(signal.get("asset_refs"))],
            "theme_type": "",
        }
        observed_at = min(
            [_date_from_signal(signal, now[:10]) for signal in related_signals] or [now[:10]]
        )
        theme_id = _theme_state_id(theme, state, signals=related_signals)
        theme_state = _ensure_theme(state, theme_id, theme, signals=related_signals, observed_at=observed_at)

        for signal in related_signals:
            signal_id = str(signal.get("signal_id") or "")
            if not signal_id or signal_id in processed or signal_id in emitted_signal_ids:
                continue
            emitted_signal_ids.add(signal_id)
            observed = _date_from_signal(signal, observed_at)
            signal_ref = _signal_ref(signal)
            event = _update_event(
                state,
                signal,
                theme_id=theme_id,
                theme_title=str(theme_state.get("title") or ""),
                observed_at=observed,
            )
            dimension = _update_dimension(
                state,
                signal,
                theme_id=theme_id,
                theme_title=str(theme_state.get("title") or ""),
                observed_at=observed,
            )
            theme_state["signal_refs"] = _merge_ref_lists(theme_state.get("signal_refs"), [signal_ref])
            theme_state["event_refs"] = _merge_ref_lists(
                theme_state.get("event_refs"),
                [{"id": event["event_state_id"], "label": event["fact_layer"], "ref_type": "event_state"}],
            )
            if dimension:
                theme_state["dimension_refs"] = _merge_ref_lists(
                    theme_state.get("dimension_refs"),
                    [
                        {
                            "id": dimension["dimension_state_id"],
                            "label": _clean(_dict(dimension.get("framework_node_ref")).get("label")),
                            "ref_type": "dimension_state",
                        }
                    ],
                )
            if _is_conflict(signal):
                theme_state["conflict_count"] = int(theme_state.get("conflict_count") or 0) + 1
                update_counter["conflict_signals"] += 1
            elif signal.get("signal_kind") == "event_definition":
                theme_state["observation_count"] = int(theme_state.get("observation_count") or 0) + 1
                update_counter["event_definition_signals"] += 1
            else:
                theme_state["support_count"] = int(theme_state.get("support_count") or 0) + 1
                update_counter["support_signals"] += 1
            theme_state["last_seen_at"] = max(str(theme_state.get("last_seen_at") or observed), observed)
            theme_state["current_phase"] = _phase(
                int(theme_state.get("support_count") or 0),
                int(theme_state.get("conflict_count") or 0),
                int(theme_state.get("observation_count") or 0),
            )

    state["processed_signal_ids"] = sorted(processed | emitted_signal_ids)
    state["stats"] = {
        "theme_count": len(state.get("themes") or {}),
        "event_count": len(state.get("events") or {}),
        "dimension_count": len(state.get("dimensions") or {}),
        "signal_count": len(state.get("processed_signal_ids") or []),
        "new_signal_count": len(emitted_signal_ids),
        **dict(update_counter),
    }
    state["run_history"].append(
        {
            "run_id": run_id or _hash_id("RUN-INCREMENTAL", now),
            "updated_at": now,
            "new_signal_count": len(emitted_signal_ids),
            "input_refs": input_refs or [],
        }
    )
    return state


def _load_candidate_payload(path: str | Path) -> dict[str, Any]:
    payload = read_json(Path(path).expanduser())
    if not isinstance(payload, dict):
        raise ValueError(f"candidate payload must be an object: {path}")
    return payload


def load_signal_theme_candidates(
    *,
    signal_paths: list[str | Path] | None = None,
    theme_paths: list[str | Path] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    signals: list[dict[str, Any]] = []
    themes: list[dict[str, Any]] = []
    input_refs: list[dict[str, Any]] = []
    for path in signal_paths or []:
        payload = _load_candidate_payload(path)
        signals.extend(item for item in _list(payload.get("signals")) if isinstance(item, dict))
        input_refs.append({"ref_type": "candidate", "source_name": "research_signals", "path": str(path)})
    for path in theme_paths or []:
        payload = _load_candidate_payload(path)
        themes.extend(item for item in _list(payload.get("theme_anchors")) if isinstance(item, dict))
        input_refs.append({"ref_type": "candidate", "source_name": "theme_anchors", "path": str(path)})
    return signals, themes, input_refs


def _latest_state_path(root: Path) -> Path:
    return root / "agent_workspace" / "candidates" / "incremental_state" / "latest" / "research_state.json"


def _read_previous_state(root: Path, previous_state_path: str | Path | None) -> dict[str, Any] | None:
    path = Path(previous_state_path).expanduser() if previous_state_path else _latest_state_path(root)
    if not path.exists():
        return None
    payload = read_json(path)
    return payload if isinstance(payload, dict) else None


def publish_incremental_state(
    root: str | Path | None = None,
    *,
    signal_paths: list[str | Path] | None = None,
    theme_paths: list[str | Path] | None = None,
    previous_state_path: str | Path | None = None,
    date_key: str | None = None,
    work_order_id: str = DEFAULT_WORK_ORDER_ID,
    write_latest: bool = False,
    write_outputs: bool = True,
) -> dict[str, Any]:
    root_path = quanta_data_root(root)
    generated_at = utc_now_iso()
    date_value = (date_key or datetime.now(timezone.utc).strftime("%Y%m%d")).replace("-", "")
    yyyy, mm, dd = dated_parts(date_value)
    stamp = datetime.now(timezone.utc).strftime("%H%M%S%f")
    run_id = f"RUN-{re.sub(r'[^A-Z0-9_-]+', '-', work_order_id.upper())}-INCREMENTAL-STATE-{stamp}"
    signals, themes, input_refs = load_signal_theme_candidates(
        signal_paths=signal_paths,
        theme_paths=theme_paths,
    )
    previous = _read_previous_state(root_path, previous_state_path)
    state = apply_incremental_update(
        previous,
        signals=signals,
        themes=themes,
        run_id=run_id,
        generated_at=generated_at,
        input_refs=input_refs,
    )
    state["previous_state_ref"] = (
        relative_to_root(Path(previous_state_path).expanduser(), root_path)
        if previous_state_path
        else relative_to_root(_latest_state_path(root_path), root_path)
        if previous
        else ""
    )
    state["work_order_id"] = work_order_id

    paths: dict[str, str] = {}
    if write_outputs:
        candidate_dir = (
            root_path
            / "agent_workspace"
            / "candidates"
            / "incremental_state"
            / yyyy
            / mm
            / dd
            / f"CAND-INCREMENTAL-STATE-{date_value}-{stamp}"
        )
        run_dir = root_path / "agent_workspace" / "runs" / "incremental_state" / yyyy / mm / dd / run_id
        state_path = candidate_dir / "research_state.json"
        manifest_path = candidate_dir / "manifest.json"
        run_manifest_path = run_dir / "run_manifest.json"
        write_json(state_path, state)
        manifest = {
            "schema_version": "incremental_state_candidate_manifest.v1",
            "status": "candidate",
            "candidate_id": candidate_dir.name,
            "run_id": run_id,
            "date": date_value,
            "generated_at": generated_at,
            "work_order_id": work_order_id,
            "input_refs": input_refs,
            "outputs": {"research_state": relative_to_root(state_path, root_path)},
            "stats": state["stats"],
            "write_latest": write_latest,
            "requires_review": True,
        }
        write_json(manifest_path, manifest)
        write_json(
            run_manifest_path,
            {
                "schema_version": "incremental_state_run.v1",
                "status": "succeeded",
                "run_id": run_id,
                "run_type": "incremental_state_update",
                "date": date_value,
                "generated_at": generated_at,
                "work_order_id": work_order_id,
                "input_refs": input_refs,
                "output_refs": [
                    {
                        "ref_type": "candidate",
                        "source_name": "incremental_state",
                        "id": candidate_dir.name,
                        "path": relative_to_root(manifest_path, root_path),
                    }
                ],
                "stats": state["stats"],
                "human_review_required": True,
            },
        )
        paths = {
            "candidate_dir": relative_to_root(candidate_dir, root_path),
            "manifest": relative_to_root(manifest_path, root_path),
            "research_state": relative_to_root(state_path, root_path),
            "run_manifest": relative_to_root(run_manifest_path, root_path),
        }
        if write_latest:
            latest = _latest_state_path(root_path)
            write_json(latest, state)
            paths["latest"] = relative_to_root(latest, root_path)

    return {
        "status": "succeeded",
        "run_id": run_id,
        "date": date_value,
        "state": state,
        "stats": state["stats"],
        "paths": paths,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Incrementally merge research_signal/theme_anchor candidates into research state."
    )
    parser.add_argument("--root", default="", help="quanta_data root. Defaults to discovery.")
    parser.add_argument("--signal-set", action="append", default=[], help="research_signals.json path.")
    parser.add_argument("--theme-set", action="append", default=[], help="theme_anchors.json path.")
    parser.add_argument("--previous-state", default="", help="Previous research_state.json path.")
    parser.add_argument("--date", default="", help="YYYYMMDD update date.")
    parser.add_argument("--work-order-id", default=DEFAULT_WORK_ORDER_ID)
    parser.add_argument("--write-latest", action="store_true", help="Also update incremental_state/latest.")
    parser.add_argument("--dry-run", action="store_true", help="Build state without writing candidate files.")
    args = parser.parse_args(argv)
    result = publish_incremental_state(
        args.root or None,
        signal_paths=args.signal_set,
        theme_paths=args.theme_set,
        previous_state_path=args.previous_state or None,
        date_key=args.date or None,
        work_order_id=args.work_order_id,
        write_latest=args.write_latest,
        write_outputs=not args.dry_run,
    )
    printable = {key: value for key, value in result.items() if key != "state"}
    print(json.dumps(printable, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
