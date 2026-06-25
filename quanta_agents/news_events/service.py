from __future__ import annotations

import json
import math
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from quanta_agents.core.config import quanta_data_root
from quanta_agents.core.io import atomic_write_text, dated_parts, relative_to_root, utc_now_iso, write_json
from quanta_agents.opinion_radar import db

from .asset_mapping import map_event_assets_and_frameworks
from .dedup import build_syndication_groups
from .event_clustering import create_event_from_mention, create_related_event, merge_mention_into_event
from .event_matching import choose_event_match, load_policy
from .event_retrieval import retrieve_candidate_events
from .extractor import extract_event_mentions_batch
from .legacy_adapter import build_news_logic_compatible_view
from .normalization import normalize_and_filter
from .repository import NewsEventRepository
from .state_updater import build_topic_state_snapshots
from .topic_mapping import map_event_to_topic


def _date_key(value: str | None = None) -> str:
    if value:
        return value.replace("-", "")
    return datetime.now().strftime("%Y%m%d")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    text = "".join(json.dumps(row, ensure_ascii=False, sort_keys=False, default=str) + "\n" for row in rows)
    atomic_write_text(path, text)


def fetch_flashes_for_batch(date: str | None = None, hours: int = 24) -> list[dict[str, Any]]:
    if date:
        start = datetime.strptime(date, "%Y-%m-%d")
        end = start.replace(hour=23, minute=59, second=59)
    else:
        latest = db.latest_time() or datetime.now()
        from datetime import timedelta

        end = latest
        start = latest - timedelta(hours=hours)
    return db.fetch_flashes(start, end)


def _member_row(event: dict[str, Any], mention: dict[str, Any], relation: str) -> dict[str, Any]:
    return {
        "event_id": event["event_id"],
        "mention_id": mention["mention_id"],
        "relation_to_event": relation,
        "source_news_id": mention["source_news_id"],
        "syndication_group_id": mention.get("syndication_group_id"),
    }


def _parse_time_for_review(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _review_item(
    *,
    review_type: str,
    object_type: str,
    object_id: str,
    severity: str,
    reason: str,
    source_news_id: str | None = None,
    agent_run_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "review_type": review_type,
        "object_type": object_type,
        "object_id": object_id,
        "severity": severity,
        "reason": reason,
        "source_news_id": source_news_id,
        "agent_run_id": agent_run_id,
        "status": "open",
        "metadata": metadata or {},
    }


def _mention_review_items(mention: dict[str, Any], news_by_id: dict[str, dict[str, Any]], run_id: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    source_news_id = str(mention.get("source_news_id") or "")
    mention_id = str(mention.get("mention_id") or "")
    confidence = float(mention.get("extraction_confidence") or 0.0)
    if confidence <= 0.2:
        items.append(
            _review_item(
                review_type="low_extraction_confidence",
                object_type="event_mention",
                object_id=mention_id,
                severity="medium",
                reason="Event mention extraction confidence is too low for unattended promotion.",
                source_news_id=source_news_id,
                agent_run_id=run_id,
                metadata={"extraction_confidence": confidence, "extraction_method": mention.get("extraction_method")},
            )
        )

    event_time_raw = mention.get("event_time")
    event_time = _parse_time_for_review(event_time_raw)
    if event_time_raw and event_time is None:
        items.append(
            _review_item(
                review_type="unparseable_event_time",
                object_type="event_mention",
                object_id=mention_id,
                severity="medium",
                reason="Event time is not a fully parseable timestamp; split reported_at and target_time before state use.",
                source_news_id=source_news_id,
                agent_run_id=run_id,
                metadata={"event_time": event_time_raw},
            )
        )

    publish_time = _parse_time_for_review((news_by_id.get(source_news_id) or {}).get("publish_time"))
    if event_time and publish_time and event_time - publish_time > timedelta(days=1):
        items.append(
            _review_item(
                review_type="future_event_time",
                object_type="event_mention",
                object_id=mention_id,
                severity="medium",
                reason="Event time is materially after publish_time; likely target_time or forward guidance, not observed event_time.",
                source_news_id=source_news_id,
                agent_run_id=run_id,
                metadata={
                    "event_time": event_time.isoformat(timespec="seconds"),
                    "publish_time": publish_time.isoformat(timespec="seconds"),
                },
            )
        )

    text = " ".join(
        str(mention.get(key) or "")
        for key in ("canonical_summary", "evidence_quote", "action")
    ).lower()
    if "十分钟后" in text or "in ten minutes" in text:
        items.append(
            _review_item(
                review_type="calendar_notice_needs_release_time",
                object_type="event_mention",
                object_id=mention_id,
                severity="low",
                reason="Calendar notice should preserve release_time and should not be treated as a policy event itself.",
                source_news_id=source_news_id,
                agent_run_id=run_id,
                metadata={"event_stage": mention.get("event_stage"), "modality": mention.get("modality")},
            )
        )
    return items


def _audit_markdown(result: dict[str, Any]) -> str:
    lines = [
        "# News Event Audit Samples",
        "",
        f"run_id: {result['run_id']}",
        "",
    ]
    news_by_id = {item["news_id"]: item for item in result["normalized_news"]}
    mentions_by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for member in result["canonical_event_members"]:
        mention = next((row for row in result["event_mentions"] if row["mention_id"] == member["mention_id"]), None)
        if mention:
            mentions_by_event[member["event_id"]].append(mention)
    topic_by_event = {row["object_id"]: row for row in result["topic_memberships"]}
    assets_by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for link in result["event_asset_links"]:
        assets_by_event[link["event_id"]].append(link)
    relations_by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for relation in result["event_relations"]:
        relations_by_event[relation["source_event_id"]].append(relation)
        relations_by_event[relation["target_event_id"]].append(relation)

    for index, event in enumerate(result["canonical_events"][:10], start=1):
        lines.extend([f"## CanonicalEvent {index}: {event['event_id']}", ""])
        lines.append(f"- Summary: {event.get('canonical_summary')}")
        lines.append(f"- Type/stage: {event.get('event_type')} / {(event.get('core_signature') or {}).get('event_stage')}")
        lines.append(f"- Topic: {(topic_by_event.get(event['event_id']) or {}).get('topic_id', '')}")
        lines.append(
            "- Assets: "
            + ", ".join(f"{link.get('asset_label')}({link.get('relation')})" for link in assets_by_event.get(event["event_id"], []))
        )
        if relations_by_event.get(event["event_id"]):
            lines.append("- Relations: " + "; ".join(f"{r['source_event_id']} {r['relation']} {r['target_event_id']}" for r in relations_by_event[event["event_id"]]))
        for mention in mentions_by_event.get(event["event_id"], [])[:3]:
            news = news_by_id.get(mention["source_news_id"]) or {}
            lines.append(f"- Raw news: {news.get('publish_time')} {news.get('title') or news.get('normalized_text')}")
            lines.append(f"- EventMention: {mention.get('canonical_summary')} [{mention.get('extraction_method')}]")
        lines.append("")
    return "\n".join(lines)


def _write_artifacts(result: dict[str, Any], root: Path, run_dir: Path) -> dict[str, str]:
    run_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "run_manifest": run_dir / "run_manifest.json",
        "normalized_news": run_dir / "normalized_news.jsonl",
        "filtered_news": run_dir / "filtered_news.jsonl",
        "syndication_groups": run_dir / "syndication_groups.jsonl",
        "event_mentions": run_dir / "event_mentions.jsonl",
        "canonical_events": run_dir / "canonical_events.jsonl",
        "event_relations": run_dir / "event_relations.jsonl",
        "event_asset_links": run_dir / "event_asset_links.jsonl",
        "event_framework_node_links": run_dir / "event_framework_node_links.jsonl",
        "topic_memberships": run_dir / "topic_memberships.jsonl",
        "topic_state_snapshots": run_dir / "topic_state_snapshots.jsonl",
        "review_queue": run_dir / "review_queue.jsonl",
        "metrics": run_dir / "metrics.json",
        "errors": run_dir / "errors.jsonl",
        "audit_samples": run_dir / "audit_samples.md",
        "legacy_news_logic_events": run_dir / "legacy_news_logic_events.jsonl",
    }
    _write_jsonl(files["normalized_news"], result["normalized_news"])
    _write_jsonl(files["filtered_news"], result["filtered_news"])
    _write_jsonl(files["syndication_groups"], result["syndication_groups"])
    _write_jsonl(files["event_mentions"], result["event_mentions"])
    _write_jsonl(files["canonical_events"], result["canonical_events"])
    _write_jsonl(files["event_relations"], result["event_relations"])
    _write_jsonl(files["event_asset_links"], result["event_asset_links"])
    _write_jsonl(files["event_framework_node_links"], result["event_framework_node_links"])
    _write_jsonl(files["topic_memberships"], result["topic_memberships"])
    _write_jsonl(files["topic_state_snapshots"], result["topic_state_snapshots"])
    _write_jsonl(files["review_queue"], result["review_queue"])
    _write_jsonl(files["errors"], result["errors"])
    _write_jsonl(files["legacy_news_logic_events"], result["legacy_news_logic_events"])
    write_json(files["metrics"], result["metrics"])
    write_json(files["run_manifest"], result["run_manifest"])
    atomic_write_text(files["audit_samples"], _audit_markdown(result))
    return {key: relative_to_root(path, root) for key, path in files.items()}


def run_news_event_batch(
    *,
    root: str | Path | None = None,
    flashes: list[dict[str, Any]] | None = None,
    date: str | None = None,
    hours: int = 24,
    limit: int | None = None,
    use_llm: bool = True,
    dry_run: bool = False,
    catalog_path: str | Path | None = None,
    run_id: str | None = None,
    llm_batch_size: int = 8,
    llm_workers: int = 1,
) -> dict[str, Any]:
    root_path = quanta_data_root(root)
    start_monotonic = time.perf_counter()
    started_at = utc_now_iso()
    date_key = _date_key(date)
    yyyy, mm, dd = dated_parts(date_key)
    stamp = datetime.now().strftime("%H%M%S")
    run_id = run_id or f"RUN-NEWS-EVENT-{date_key}-{stamp}"
    raw_flashes = flashes if flashes is not None else fetch_flashes_for_batch(date=date, hours=hours)
    if limit is not None:
        raw_flashes = raw_flashes[:limit]

    repo = NewsEventRepository(root_path, db_path=catalog_path, dry_run=dry_run)
    policy = load_policy(root_path)
    normalized_news = normalize_and_filter(raw_flashes, root=str(root_path))
    candidate_news = [
        item
        for item in normalized_news
        if (item.get("filter_decision") or {}).get("decision") != "filtered_out"
    ]
    groups, syndication_members, news_to_group = build_syndication_groups(candidate_news)
    news_by_id = {item["news_id"]: item for item in normalized_news}

    event_mentions, errors = extract_event_mentions_batch(
        candidate_news,
        use_llm=use_llm,
        syndication_group_ids=news_to_group,
        batch_size=llm_batch_size,
        max_workers=llm_workers,
    )

    events_by_id: dict[str, dict[str, Any]] = {event["event_id"]: event for event in repo.list_canonical_events(limit=5000)}
    created_or_updated_event_ids: set[str] = set()
    canonical_members: list[dict[str, Any]] = []
    event_relations: list[dict[str, Any]] = []
    review_queue: list[dict[str, Any]] = []
    relation_counts: Counter[str] = Counter()

    for mention in event_mentions:
        review_queue.extend(_mention_review_items(mention, news_by_id, run_id))
        candidates = retrieve_candidate_events(mention, list(events_by_id.values()), policy=policy)
        decision = choose_event_match(mention, candidates, policy=policy, use_llm=use_llm)
        relation_counts[decision["decision"]] += 1
        if decision["decision"] == "SAME_EVENT" and decision.get("target_event_id") in events_by_id:
            event = merge_mention_into_event(events_by_id[decision["target_event_id"]], mention)
            events_by_id[event["event_id"]] = event
            created_or_updated_event_ids.add(event["event_id"])
            canonical_members.append(_member_row(event, mention, "SAME_EVENT"))
        elif decision["decision"] in {"UPDATE", "CONFIRM", "CONTRADICT", "RETRACT", "CAUSES"} and decision.get("target_event_id"):
            event, relation = create_related_event(
                mention,
                relation=decision["decision"],
                target_event_id=decision["target_event_id"],
                confidence=float(decision.get("score") or decision.get("confidence") or 0.0),
                reason=decision.get("reason") or "",
            )
            if event["event_id"] in events_by_id:
                event = merge_mention_into_event(events_by_id[event["event_id"]], mention)
            events_by_id[event["event_id"]] = event
            created_or_updated_event_ids.add(event["event_id"])
            event_relations.append(relation)
            canonical_members.append(_member_row(event, mention, "SEED"))
        else:
            event = create_event_from_mention(mention)
            if event["event_id"] in events_by_id:
                event = merge_mention_into_event(events_by_id[event["event_id"]], mention)
            events_by_id[event["event_id"]] = event
            created_or_updated_event_ids.add(event["event_id"])
            canonical_members.append(_member_row(event, mention, "SEED"))
            if decision.get("score", 0.0) >= float((policy.get("thresholds") or {}).get("llm_pair_judge") or 0.65):
                review_queue.append({"mention_id": mention["mention_id"], "decision": decision, "reason": "mid_score_no_merge"})

    batch_events = [events_by_id[event_id] for event_id in sorted(created_or_updated_event_ids)]
    event_asset_links: list[dict[str, Any]] = []
    event_framework_links: list[dict[str, Any]] = []
    for event in batch_events:
        asset_links, framework_links = map_event_assets_and_frameworks(event, root_path)
        event_asset_links.extend(asset_links)
        event_framework_links.extend(framework_links)

    existing_topics = {topic["topic_id"]: topic for topic in repo.list_topics(limit=5000)}
    persistent_topics: dict[str, dict[str, Any]] = dict(existing_topics)
    topic_memberships: list[dict[str, Any]] = []
    topic_aliases: list[dict[str, Any]] = []
    assets_by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    frameworks_by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for link in event_asset_links:
        assets_by_event[link["event_id"]].append(link)
    for link in event_framework_links:
        frameworks_by_event[link["event_id"]].append(link)
    for event in batch_events:
        topic, membership, aliases = map_event_to_topic(
            event,
            list(persistent_topics.values()),
            asset_links=assets_by_event.get(event["event_id"], []),
            framework_links=frameworks_by_event.get(event["event_id"], []),
            agent_run_id=run_id,
            thresholds=policy.get("thresholds") or {},
            use_llm=use_llm,
        )
        persistent_topics[topic["topic_id"]] = topic
        topic_memberships.append(membership)
        topic_aliases.extend(aliases)
        if membership.get("assigned_by") == "rule" and membership.get("relation_to_topic") == "creates":
            review_queue.append(
                _review_item(
                    review_type="rule_created_topic_candidate",
                    object_type="topic_membership",
                    object_id=membership["membership_id"],
                    severity="low",
                    reason="Rule fallback created a new topic candidate; keep visible confidence capped until reviewed.",
                    agent_run_id=run_id,
                    metadata={
                        "event_id": event["event_id"],
                        "topic_id": membership["topic_id"],
                        "membership_score": membership.get("membership_score"),
                    },
                )
            )

    legacy_rows = build_news_logic_compatible_view(batch_events, event_asset_links, event_framework_links, news_by_id=news_by_id)
    current_topics = [
        topic
        for topic_id, topic in persistent_topics.items()
        if topic_id in {membership["topic_id"] for membership in topic_memberships}
    ]
    topic_state_snapshots = build_topic_state_snapshots(current_topics)
    exact_duplicate_count = sum(1 for member in syndication_members if member.get("duplicate_type") == "exact_duplicate")
    multi_asset_event_count = sum(1 for event_id, links in assets_by_event.items() if len(links) > 1)
    metrics = {
        "raw_news_count": len(raw_flashes),
        "filtered_news_count": len(candidate_news),
        "filtered_out_count": len(normalized_news) - len(candidate_news),
        "exact_duplicate_count": exact_duplicate_count,
        "syndication_group_count": len(groups),
        "event_mention_count": len(event_mentions),
        "canonical_event_count": len(batch_events),
        "same_event_count": relation_counts.get("SAME_EVENT", 0),
        "update_count": relation_counts.get("UPDATE", 0),
        "confirm_count": relation_counts.get("CONFIRM", 0),
        "contradict_count": relation_counts.get("CONTRADICT", 0),
        "multi_asset_event_count": multi_asset_event_count,
        "topic_count": len({membership["topic_id"] for membership in topic_memberships}),
        "review_queue_count": len(review_queue),
        "llm_call_failure_count": len(errors),
        "llm_extraction_batch_size": llm_batch_size if use_llm else 0,
        "llm_extraction_batch_count": math.ceil(len(candidate_news) / max(llm_batch_size, 1)) if use_llm else 0,
        "llm_extraction_worker_count": llm_workers if use_llm else 0,
        "average_item_seconds": round(
            (time.perf_counter() - start_monotonic) / max(len(raw_flashes), 1),
            4,
        ),
        "llm_extracted_mention_count": sum(1 for mention in event_mentions if mention.get("extraction_method") == "llm"),
    }
    run_dir = root_path / "agent_workspace" / "candidates" / "news_events" / yyyy / mm / dd / run_id
    run_manifest = {
        "run_id": run_id,
        "run_type": "news_event_batch",
        "status": "succeeded",
        "started_at": started_at,
        "finished_at": utc_now_iso(),
        "inputs": [
            {
                "source_type": "mysql" if flashes is None else "provided_flashes",
                "table": "jin10_flash" if flashes is None else "",
                "date": date,
                "hours": hours,
                "limit": limit,
                "llm_batch_size": llm_batch_size if use_llm else None,
                "llm_workers": llm_workers if use_llm else None,
            }
        ],
        "outputs": [],
        "metrics": metrics,
        "requires_review": True,
        "generator": {"project": "quanta_agents", "module": "quanta_agents.news_events"},
        "catalog_path": str(repo.db_path),
        "dry_run": dry_run,
    }
    result = {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "normalized_news": normalized_news,
        "filtered_news": [{"news_id": item["news_id"], **(item.get("filter_decision") or {})} for item in normalized_news],
        "syndication_groups": groups,
        "syndication_members": syndication_members,
        "event_mentions": event_mentions,
        "canonical_events": batch_events,
        "canonical_event_members": canonical_members,
        "event_relations": event_relations,
        "event_asset_links": event_asset_links,
        "event_framework_node_links": event_framework_links,
        "persistent_topics": current_topics,
        "topic_aliases": topic_aliases,
        "topic_memberships": topic_memberships,
        "topic_state_snapshots": topic_state_snapshots,
        "review_queue": review_queue,
        "legacy_news_logic_events": legacy_rows,
        "metrics": metrics,
        "errors": errors,
        "run_manifest": run_manifest,
    }
    paths = _write_artifacts(result, root_path, run_dir)
    result["paths"] = paths
    result["run_manifest"]["outputs"] = [
        {"artifact_type": key, "path": value}
        for key, value in paths.items()
        if key not in {"run_manifest"}
    ]
    write_json(run_dir / "run_manifest.json", result["run_manifest"])

    repo_payload = {
        "news_items": normalized_news,
        "syndication_groups": groups,
        "syndication_members": syndication_members,
        "event_mentions": event_mentions,
        "canonical_events": batch_events,
        "canonical_event_members": canonical_members,
        "event_relations": event_relations,
        "event_asset_links": event_asset_links,
        "event_framework_node_links": event_framework_links,
        "persistent_topics": result["persistent_topics"],
        "topic_aliases": topic_aliases,
        "topic_memberships": topic_memberships,
        "topic_state_snapshots": topic_state_snapshots,
    }
    repo.upsert_all(repo_payload, agent_run=result["run_manifest"])
    projection = _project_object_catalog_if_enabled(result, root_path, run_dir / "run_manifest.json")
    result["object_catalog_projection"] = projection
    result["run_manifest"]["object_catalog_projection"] = projection
    if projection.get("status") == "succeeded":
        counts = projection.get("counts") if isinstance(projection.get("counts"), dict) else {}
        result["metrics"]["object_catalog_object_count"] = counts.get("research_object", 0)
        result["metrics"]["object_catalog_relation_count"] = counts.get("object_relation", 0)
        result["run_manifest"]["metrics"] = result["metrics"]
        write_json(run_dir / "metrics.json", result["metrics"])
    write_json(run_dir / "run_manifest.json", result["run_manifest"])
    return result


def _project_object_catalog_if_enabled(result: dict[str, Any], root_path: Path, manifest_path: Path) -> dict[str, Any]:
    from quanta_agents.adapters.news_event_catalog_adapter import register_news_event_batch_result
    from quanta_agents.repositories import CatalogRepository, object_catalog_enabled

    if not object_catalog_enabled():
        return {"status": "skipped", "reason": "QUANTA_OBJECT_CATALOG_ENABLED is not true"}
    repository = CatalogRepository(root_path)
    return register_news_event_batch_result(
        repository,
        result,
        manifest_uri=relative_to_root(manifest_path, root_path),
    )
