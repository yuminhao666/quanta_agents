from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quanta_agents.core.config import quanta_data_root
from quanta_agents.core.io import dated_parts, read_json, relative_to_root, utc_now_iso, write_json
from quanta_agents.signal_mapping.mapper import (
    _asset_ref,
    _clean_text,
    _date_key,
    _hash_id,
    _source_ref,
    build_polymarket_event_definition_signal,
    validate_signal_theme_objects,
)
from quanta_agents.signal_mapping.theme_anchor_matcher import (
    EMPTY_THEME_ANCHOR_INDEX,
    ThemeAnchorIndex,
    load_theme_anchor_index,
)


POLYMARKET_SIGNAL_MAPPER_VERSION = "polymarket_signal_mapper.v1"
DEFAULT_WORK_ORDER_ID = "WO-DEV-20260620-011"

DEFAULT_HOTSPOTS_REL = "agent_workspace/candidates/polymarket_daily/latest/hotspots.json"
DEFAULT_MANIFEST_REL = "agent_workspace/candidates/polymarket_daily/latest/manifest.json"


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _liquidity_label(value: Any) -> str:
    liquidity = _as_float(value)
    if liquidity is None:
        return "unknown"
    if liquidity >= 100_000:
        return "high"
    if liquidity >= 10_000:
        return "medium"
    if liquidity > 0:
        return "low"
    return "unknown"


def _isoish(value: Any) -> str | None:
    text = _clean_text(value)
    if not text:
        return None
    if re.fullmatch(r"\d{8}", text):
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return text


def _market_time_window(market: dict[str, Any], hotspots: dict[str, Any]) -> dict[str, Any]:
    source = hotspots.get("source") if isinstance(hotspots.get("source"), dict) else {}
    start = _isoish(source.get("collected_at") or hotspots.get("generated_at") or market.get("updated_at"))
    end = _isoish(market.get("end_date") or start)
    return {
        "start": start,
        "end": end,
        "horizon": "event_window",
    }


def _market_price(market: dict[str, Any]) -> float | None:
    # Polymarket names this field probability in the source payload, but in
    # research_signal we record it only as observed contract price.
    for key in ("primary_probability", "last_trade_price"):
        price = _as_float(market.get(key))
        if price is not None:
            return price
    prices = market.get("outcome_prices")
    if isinstance(prices, list):
        for item in prices:
            if isinstance(item, dict):
                price = _as_float(item.get("price"))
                if price is not None:
                    return price
    return None


def _market_spread(market: dict[str, Any]) -> float | None:
    spread = _as_float(market.get("spread"))
    if spread is not None:
        return spread
    bid = _as_float(market.get("best_bid"))
    ask = _as_float(market.get("best_ask"))
    if bid is None or ask is None or ask < bid:
        return None
    return round(ask - bid, 6)


def _question_text(market: dict[str, Any]) -> str:
    zh = _clean_text(market.get("question_zh"), limit=220)
    raw = _clean_text(market.get("question"), limit=220)
    if zh and raw and zh != raw:
        return f"{zh} / {raw}"
    return zh or raw or "Polymarket event definition"


def _event_title_text(market: dict[str, Any]) -> str:
    event = market.get("event") if isinstance(market.get("event"), dict) else {}
    values = [
        market.get("category_zh"),
        market.get("category"),
        event.get("title_zh"),
        event.get("title"),
        *(market.get("hotspot_reasons") or []),
    ]
    return " ".join(_clean_text(value) for value in values if _clean_text(value))


def _settlement_rule(market: dict[str, Any]) -> str:
    question = _question_text(market)
    outcome = _clean_text(market.get("primary_outcome") or market.get("primary_outcome_zh"))
    end = _clean_text(market.get("end_date"))
    url = _clean_text(market.get("url"))
    parts = [
        f"Use Polymarket's published resolution criteria for: {question}",
        f"Observed outcome contract: {outcome}" if outcome else "",
        f"Market end date: {end}" if end else "",
        f"Official market URL: {url}" if url else "",
    ]
    return "; ".join(part for part in parts if part)


ASSET_KEYWORD_RULES: tuple[tuple[tuple[str, ...], tuple[str, str]], ...] = (
    (("bitcoin", "btc", "比特币"), ("CRYPTO-BTC", "Bitcoin")),
    (("ethereum", "eth", "以太坊"), ("CRYPTO-ETH", "Ethereum")),
    (("gold", "黄金", "xau"), ("FUT-AU", "黄金")),
    (("silver", "白银", "xag"), ("FUT-AG", "白银")),
    (("copper", "铜"), ("FUT-CU", "铜")),
    (("crude", "oil", "wti", "brent", "opec", "hormuz", "iran", "伊朗", "霍尔木兹", "原油"), ("FUT-SC", "原油")),
    (("fed", "fomc", "interest rate", "rates", "美联储", "利率"), ("MACRO-RATES", "美元利率")),
)


def infer_market_asset_refs(market: dict[str, Any]) -> list[dict[str, Any]]:
    text = " ".join(
        [
            _question_text(market),
            _event_title_text(market),
            _clean_text(market.get("slug")),
            _clean_text(market.get("url")),
        ]
    ).lower()
    refs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for keywords, (asset_id, label) in ASSET_KEYWORD_RULES:
        if not any(keyword.lower() in text for keyword in keywords):
            continue
        if asset_id in seen:
            continue
        refs.append(_asset_ref(label, asset_id))
        seen.add(asset_id)
    return refs


def _theme_ref_from_match(match: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(match.get("theme_anchor_id") or ""),
        "label": str(match.get("title") or match.get("theme_anchor_id") or ""),
        "ref_type": "theme",
    }


def _match_theme_anchor(
    index: ThemeAnchorIndex,
    market: dict[str, Any],
    *,
    asset_refs: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if index.anchor_count <= 0:
        return None
    text = " ".join(
        part
        for part in (
            _question_text(market),
            _event_title_text(market),
            _settlement_rule(market),
        )
        if part
    )
    text = _expand_match_synonyms(text)
    return index.best_match(
        text,
        asset_labels=[str(ref.get("label") or "") for ref in asset_refs],
        min_score=0.52,
    )


def _expand_match_synonyms(text: str) -> str:
    expanded = [text]
    lowered = text.lower()
    if any(token in lowered for token in ("us x iran", "u.s. x iran", "us-iran", "u.s.-iran")) or any(
        token in text for token in ("美国与伊朗", "美伊", "伊朗外交")
    ):
        expanded.append("美伊 伊朗 中东 地缘 霍尔木兹 原油")
    if any(token in lowered for token in ("fomc", "fed", "interest rate")) or "美联储" in text:
        expanded.append("美联储 利率 加息 降息 美债 国债期货 股指")
    return " ".join(expanded)


def _source_path(path: str | Path | None, root: Path) -> str:
    if not path:
        return ""
    return relative_to_root(Path(path), root)


def polymarket_market_to_research_signal(
    market: dict[str, Any],
    *,
    hotspots: dict[str, Any] | None = None,
    source_path: str = "",
    created_at: str | None = None,
    theme_index: ThemeAnchorIndex = EMPTY_THEME_ANCHOR_INDEX,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    hotspots = hotspots or {}
    market_id = _clean_text(market.get("market_id") or market.get("id") or market.get("condition_id"))
    if not market_id:
        market_id = _hash_id("POLY", market.get("question"), market.get("url"))
    asset_refs = infer_market_asset_refs(market)
    match = _match_theme_anchor(theme_index, market, asset_refs=asset_refs)
    theme_refs = [_theme_ref_from_match(match)] if match else []
    anchoring_note = (
        f"theme_anchor_status=anchored; theme_anchor_id={match.get('theme_anchor_id')}; "
        f"match_method={match.get('match_method')}; match_score={match.get('match_score')}"
        if match
        else "theme_anchor_status=unanchored_theme_candidate; no conservative theme_anchor match was found."
    )
    question = _question_text(market)
    signal = build_polymarket_event_definition_signal(
        market_id=market_id,
        question=question,
        price=_market_price(market),
        spread=_market_spread(market),
        liquidity=_as_float(market.get("liquidity")),
        liquidity_label=_liquidity_label(market.get("liquidity")),
        settlement_rule=_settlement_rule(market),
        time_window=_market_time_window(market, hotspots),
        source_path=source_path,
        source_url=_clean_text(market.get("url")) or None,
        created_at=created_at,
        theme_refs=theme_refs,
        asset_refs=asset_refs,
        mapped_by=POLYMARKET_SIGNAL_MAPPER_VERSION,
        mapping_method="polymarket_hotspot_to_research_signal.v1",
        notes=(
            f"Polymarket observed contract price is stored as market_observation.price only, "
            f"not as truth probability. {anchoring_note}"
        ),
    )
    return signal, match


def map_polymarket_hotspots_to_signals(
    hotspots: dict[str, Any],
    *,
    source_path: str = "",
    created_at: str | None = None,
    theme_index: ThemeAnchorIndex = EMPTY_THEME_ANCHOR_INDEX,
    max_markets: int = 25,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    created = created_at or utc_now_iso()
    signals: list[dict[str, Any]] = []
    matches: list[dict[str, Any]] = []
    for market in (hotspots.get("hotspots") or [])[:max_markets]:
        if not isinstance(market, dict):
            continue
        signal, match = polymarket_market_to_research_signal(
            market,
            hotspots=hotspots,
            source_path=source_path,
            created_at=created,
            theme_index=theme_index,
        )
        signals.append(signal)
        matches.append(
            {
                "signal_id": signal["signal_id"],
                "market_id": signal["source_ref"].get("id"),
                "question": _question_text(market),
                "anchoring_status": "anchored" if match else "unanchored_theme_candidate",
                "theme_anchor_id": (match or {}).get("theme_anchor_id"),
                "theme_title": (match or {}).get("title"),
                "match_method": (match or {}).get("match_method"),
                "match_score": (match or {}).get("match_score"),
                "matched_terms": (match or {}).get("matched_terms") or [],
            }
        )
    return signals, matches


def _resolve_input_path(root: Path, value: str | Path | None, default_rel: str) -> Path:
    path = Path(value).expanduser() if value else root / default_rel
    return path if path.is_absolute() else root / path


def _signal_candidate_dir(root: Path, date_key: str, stamp: str) -> Path:
    yyyy, mm, dd = dated_parts(date_key)
    return root / "agent_workspace/candidates/signal_map" / yyyy / mm / dd / f"CAND-SIGNAL-MAP-{date_key}-{stamp}"


def _run_dir(root: Path, date_key: str, run_id: str) -> Path:
    yyyy, mm, dd = dated_parts(date_key)
    return root / "agent_workspace/runs/signal_mapping" / yyyy / mm / dd / run_id


def run_polymarket_signal_mapping(
    root: str | Path | None = None,
    *,
    hotspots_path: str | Path | None = None,
    manifest_path: str | Path | None = None,
    theme_anchor_path: str | Path | None = None,
    report_date: str | None = None,
    work_order_id: str = DEFAULT_WORK_ORDER_ID,
    max_markets: int = 25,
    validate: bool = True,
    write_outputs: bool = True,
) -> dict[str, Any]:
    root_path = quanta_data_root(root)
    hotspots_file = _resolve_input_path(root_path, hotspots_path, DEFAULT_HOTSPOTS_REL)
    manifest_file = _resolve_input_path(root_path, manifest_path, DEFAULT_MANIFEST_REL)
    hotspots = read_json(hotspots_file)
    manifest = read_json(manifest_file) if manifest_file.exists() else {}
    if not isinstance(hotspots, dict):
        raise ValueError(f"Polymarket hotspots payload is not an object: {hotspots_file}")
    if not isinstance(manifest, dict):
        manifest = {}

    created_at = utc_now_iso()
    date_key = _date_key(report_date or manifest.get("date") or hotspots.get("generated_at"))
    stamp = datetime.now(timezone.utc).strftime("%H%M%S%f")
    run_id = f"RUN-{re.sub(r'[^A-Z0-9_-]+', '-', work_order_id.upper())}-POLYMARKET-SIGNAL-{stamp}"

    theme_index = load_theme_anchor_index(
        root_path,
        candidate_path=theme_anchor_path,
        date_key=date_key if theme_anchor_path is None else None,
    )
    signals, theme_matches = map_polymarket_hotspots_to_signals(
        hotspots,
        source_path=_source_path(hotspots_file, root_path),
        created_at=created_at,
        theme_index=theme_index,
        max_markets=max_markets,
    )
    anchored_count = sum(1 for item in theme_matches if item["anchoring_status"] == "anchored")
    unanchored_count = len(theme_matches) - anchored_count

    validation_result: dict[str, Any] = {"schema_validation": "not_run"}
    if validate:
        validation_result = validate_signal_theme_objects(root_path, signals=signals, themes=[])

    input_refs = [
        _source_ref("candidate", "polymarket_daily", path=hotspots_file, root=root_path, artifact_id=manifest.get("candidate_id")),
    ]
    if manifest_file.exists():
        input_refs.append(
            _source_ref("candidate", "polymarket_daily_manifest", path=manifest_file, root=root_path, artifact_id=manifest.get("candidate_id"))
        )
    for source_path in theme_index.source_paths:
        input_refs.append(_source_ref("candidate", "theme_anchor", path=source_path, root=root_path, artifact_id=Path(source_path).parent.name))

    paths: dict[str, str] = {}
    output_refs: list[dict[str, Any]] = []
    if write_outputs:
        signal_dir = _signal_candidate_dir(root_path, date_key, stamp)
        run_path = _run_dir(root_path, date_key, run_id)
        signal_file = signal_dir / "research_signals.json"
        signal_manifest = signal_dir / "manifest.json"
        run_manifest = run_path / "run_manifest.json"

        signal_payload = {
            "schema_version": "research_signal_candidate_set.v1",
            "status": "candidate",
            "candidate_id": signal_dir.name,
            "generated_at": created_at,
            "work_order_id": work_order_id,
            "mapper_version": POLYMARKET_SIGNAL_MAPPER_VERSION,
            "source_workflow": "polymarket_daily",
            "source_candidate_id": manifest.get("candidate_id"),
            "theme_anchor_candidate_ids": theme_index.candidate_ids,
            "mapping_summary": {
                "source_role": "web_info",
                "signal_kind": "event_definition",
                "confidence_label": "low_confidence_signal",
                "price_semantics": "market_observation.price is the observed Polymarket contract price; market_observation.probability is always null.",
                "anchored_count": anchored_count,
                "unanchored_count": unanchored_count,
            },
            "theme_anchor_matches": theme_matches,
            "signals": signals,
        }
        write_json(signal_file, signal_payload)
        write_json(
            signal_manifest,
            {
                "schema_version": "signal_map_candidate_manifest.v1",
                "status": "candidate",
                "candidate_id": signal_dir.name,
                "run_id": run_id,
                "date": date_key,
                "generated_at": created_at,
                "work_order_id": work_order_id,
                "source_workflow": "polymarket_daily",
                "source_candidate_id": manifest.get("candidate_id"),
                "schema_validated": validation_result.get("schema_validation") == "passed",
                "outputs": {"research_signals": relative_to_root(signal_file, root_path)},
                "signal_count": len(signals),
                "anchored_count": anchored_count,
                "unanchored_count": unanchored_count,
                "requires_review": True,
            },
        )
        output_refs = [
            _source_ref("candidate", "signal_map", artifact_id=signal_dir.name, path=signal_manifest, root=root_path)
        ]
        write_json(
            run_manifest,
            {
                "schema_version": "polymarket_signal_mapping_run.v1",
                "status": "succeeded",
                "run_id": run_id,
                "run_type": "polymarket_signal_mapper",
                "date": date_key,
                "generated_at": created_at,
                "work_order_id": work_order_id,
                "mapper_version": POLYMARKET_SIGNAL_MAPPER_VERSION,
                "input_refs": input_refs,
                "output_refs": output_refs,
                "signal_count": len(signals),
                "theme_anchor_count": theme_index.anchor_count,
                "anchored_count": anchored_count,
                "unanchored_count": unanchored_count,
                "schema_validation": validation_result,
                "human_review_required": True,
                "notes": [
                    "Polymarket enters Research Memory only as web_info/event_definition/low_confidence_signal.",
                    "Observed contract prices are stored as market_observation.price, never as truth probability.",
                    "Unmatched markets remain unanchored_theme_candidate observations for review.",
                ],
            },
        )
        paths = {
            "signal_candidate_dir": relative_to_root(signal_dir, root_path),
            "signal_manifest": relative_to_root(signal_manifest, root_path),
            "research_signals": relative_to_root(signal_file, root_path),
            "run_manifest": relative_to_root(run_manifest, root_path),
        }

    return {
        "status": "succeeded",
        "run_id": run_id,
        "date": date_key,
        "signal_count": len(signals),
        "anchored_count": anchored_count,
        "unanchored_count": unanchored_count,
        "schema_validation": validation_result,
        "paths": paths,
        "input_refs": input_refs,
        "output_refs": output_refs,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Map Polymarket hotspots into low-confidence research_signal candidates.")
    parser.add_argument("--root", default="", help="quanta_data root. Defaults to GJ_QUANTA_DATA_ROOT discovery.")
    parser.add_argument("--hotspots", default="", help="Polymarket hotspots.json path. Defaults to latest.")
    parser.add_argument("--manifest", default="", help="Polymarket manifest.json path. Defaults to latest.")
    parser.add_argument("--theme-anchor-path", default="", help="theme_anchors.json or its candidate directory.")
    parser.add_argument("--report-date", default="", help="YYYYMMDD/YYY-MM-DD report date override.")
    parser.add_argument("--work-order-id", default=DEFAULT_WORK_ORDER_ID)
    parser.add_argument("--max-markets", type=int, default=25)
    parser.add_argument("--no-validate", action="store_true", help="Skip JSON Schema validation.")
    parser.add_argument("--dry-run", action="store_true", help="Build and validate without writing candidates.")
    args = parser.parse_args(argv)

    result = run_polymarket_signal_mapping(
        args.root or None,
        hotspots_path=args.hotspots or None,
        manifest_path=args.manifest or None,
        theme_anchor_path=args.theme_anchor_path or None,
        report_date=args.report_date or None,
        work_order_id=args.work_order_id,
        max_markets=args.max_markets,
        validate=not args.no_validate,
        write_outputs=not args.dry_run,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
