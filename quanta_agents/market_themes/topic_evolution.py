from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from quanta_agents.core.config import quanta_data_root
from quanta_agents.core.io import atomic_write_text, read_json, relative_to_root, write_json


SCHEMA_VERSION = "topic_evolution_read_model.v1"
AGENT_VERSION = "0.1.0"


def _clean_text(value: Any, limit: int | None = None) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text).strip()
    if limit and len(text) > limit:
        return text[: max(limit - 1, 0)].rstrip() + "…"
    return text


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _relative(path: Path, root: Path) -> str:
    return relative_to_root(path, root)


def _parse_time(value: Any) -> datetime:
    text = str(value or "").strip()
    if not text:
        return datetime.min
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min


def _numeric(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _round_delta(current: Any, previous: Any) -> float:
    return round(_numeric(current) - _numeric(previous), 3)


def _source_ref_key(ref: dict[str, Any]) -> tuple[str, str]:
    return (_clean_text(ref.get("ref_type"), 40), _clean_text(ref.get("id"), 160))


def _evidence_key(item: dict[str, Any]) -> str:
    object_id = _clean_text(item.get("object_id") or item.get("evidence_id"), 160)
    if object_id:
        return object_id
    refs = [_source_ref_key(ref) for ref in _as_list(item.get("source_refs")) if isinstance(ref, dict)]
    if refs:
        return "|".join(f"{kind}:{ref_id}" for kind, ref_id in refs)
    return _clean_text(item.get("summary"), 160)


def _load_payload(path: Path) -> dict[str, Any] | None:
    try:
        payload = read_json(path)
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _discover_topic_runs(
    root: Path,
    source_dir: Path | None = None,
    *,
    run_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    base = source_dir or root / "agent_workspace" / "candidates" / "market_topics"
    payload_paths = sorted(base.glob("20[0-9][0-9]/*/*/RUN-*/recent-news-topics.json"))
    seen_run_ids: set[str] = set()
    runs: list[dict[str, Any]] = []
    for path in payload_paths:
        payload = _load_payload(path)
        if not payload:
            continue
        run_id = _clean_text(payload.get("run_id") or path.parent.name, 120)
        if not run_id or run_id in seen_run_ids:
            continue
        if run_ids is not None and run_id not in run_ids:
            continue
        seen_run_ids.add(run_id)
        runs.append(
            {
                "run_id": run_id,
                "generated_at": _clean_text(payload.get("generated_at"), 64),
                "path": _relative(path, root),
                "payload": payload,
            }
        )
    runs.sort(key=lambda item: (_parse_time(item.get("generated_at")), item.get("run_id") or ""))
    return runs


def _by_topic(items: list[dict[str, Any]], *, id_field: str = "topic_id") -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        if isinstance(item, dict):
            topic_id = _clean_text(item.get(id_field), 120)
            if topic_id:
                grouped[topic_id].append(item)
    return dict(grouped)


def _first_by_topic(items: list[dict[str, Any]], *, id_field: str = "topic_id") -> dict[str, dict[str, Any]]:
    result = {}
    for item in items:
        if isinstance(item, dict):
            topic_id = _clean_text(item.get(id_field), 120)
            if topic_id and topic_id not in result:
                result[topic_id] = item
    return result


def _state_for_topic(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return _first_by_topic(_as_list(payload.get("topic_states")))


def _nodes_for_topic(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return _first_by_topic(_as_list(payload.get("topic_nodes")))


def _evidence_for_topic(payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    memberships = _as_list(payload.get("topic_memberships"))
    if memberships:
        links = [item for item in _as_list(payload.get("evidence_links")) if isinstance(item, dict)]
        link_by_membership = {
            _clean_text(item.get("membership_id"), 160): item
            for item in links
            if _clean_text(item.get("membership_id"), 160)
        }
        link_by_evidence = {
            _clean_text(item.get("evidence_id"), 160): item
            for item in links
            if _clean_text(item.get("evidence_id"), 160)
        }
        enriched = []
        for item in memberships:
            if not isinstance(item, dict):
                continue
            link = link_by_membership.get(_clean_text(item.get("membership_id"), 160))
            if not link:
                link = link_by_evidence.get(_clean_text(item.get("object_id"), 160))
            merged = dict(item)
            if link:
                merged.setdefault("summary", link.get("summary") or "")
                merged.setdefault("source_path", link.get("source_path") or "")
                merged.setdefault("source_field", link.get("source_field") or "")
            enriched.append(merged)
        return _by_topic(enriched)
    links = _as_list(payload.get("evidence_links"))
    evidence = []
    for item in links:
        if not isinstance(item, dict):
            continue
        evidence.append(
            {
                "membership_id": item.get("membership_id") or "",
                "object_id": item.get("evidence_id") or "",
                "object_type": item.get("object_type") or "evidence_link",
                "topic_id": item.get("topic_id") or "",
                "membership_role": "primary",
                "membership_score": item.get("classification", {}).get("confidence", 0.0)
                if isinstance(item.get("classification"), dict)
                else 0.0,
                "relation_to_topic": "updates",
                "stance": "supports",
                "effective_time": item.get("generated_at") or "",
                "source_refs": item.get("source_refs") or [],
                "truth_status": item.get("truth_status") or "unverified",
                "epistemic_status": item.get("epistemic_status") or "unknown",
                "evidence_weight": item.get("evidence_weight", 1.0),
                "summary": item.get("summary") or "",
                "reason": (item.get("classification") or {}).get("reason", "")
                if isinstance(item.get("classification"), dict)
                else "",
            }
        )
    return _by_topic(evidence)


def _state_delta(current: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, float]:
    if not previous:
        return {"heat": 0.0, "strength": 0.0, "confidence": 0.0}
    return {
        "heat": _round_delta(current.get("heat"), previous.get("heat")),
        "strength": _round_delta(current.get("strength"), previous.get("strength")),
        "confidence": _round_delta(current.get("confidence"), previous.get("confidence")),
    }


def _lifecycle_state(timeline: list[dict[str, Any]]) -> str:
    observed = [item for item in timeline if item.get("observed")]
    if not observed:
        return "candidate"
    if not timeline[-1].get("observed"):
        return "fading"
    if len(observed) == 1:
        return "emerging"
    delta = observed[-1].get("delta") or {}
    strength_delta = _numeric(delta.get("strength"))
    if strength_delta >= 0.05:
        return "strengthening"
    if strength_delta <= -0.05:
        return "weakening"
    return "active"


def _compact_evidence(items: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    compacted = []
    for item in items[:limit]:
        compacted.append(
            {
                "membership_id": item.get("membership_id") or "",
                "object_id": item.get("object_id") or item.get("evidence_id") or "",
                "object_type": item.get("object_type") or "unknown",
                "membership_role": item.get("membership_role") or "primary",
                "relation_to_topic": item.get("relation_to_topic") or "updates",
                "stance": item.get("stance") or "supports",
                "effective_time": item.get("effective_time") or "",
                "summary": _clean_text(item.get("summary") or item.get("reason"), 220),
                "source_refs": item.get("source_refs") or [],
                "truth_status": item.get("truth_status") or "unverified",
                "epistemic_status": item.get("epistemic_status") or "unknown",
                "evidence_weight": _numeric(item.get("evidence_weight"), 0.0),
            }
        )
    return compacted


def build_topic_evolution(
    *,
    root: str | Path | None = None,
    source_dir: str | Path | None = None,
    run_ids: list[str] | None = None,
    max_evidence_per_snapshot: int = 6,
    now: datetime | None = None,
) -> dict[str, Any]:
    root_path = quanta_data_root(root)
    source_path = Path(source_dir).expanduser() if source_dir else None
    include_run_ids = {_clean_text(item, 120) for item in run_ids or [] if _clean_text(item, 120)}
    runs = _discover_topic_runs(
        root_path,
        source_path,
        run_ids=None if run_ids is None else include_run_ids,
    )
    generated_at = (now or datetime.now()).isoformat(sep=" ")
    topics_seen: dict[str, dict[str, Any]] = {}
    topic_ids: set[str] = set()
    run_views = []

    for run in runs:
        payload = run["payload"]
        nodes = _nodes_for_topic(payload)
        states = _state_for_topic(payload)
        evidence = _evidence_for_topic(payload)
        topic_ids.update(nodes)
        topic_ids.update(states)
        topic_ids.update(evidence)
        run_views.append(
            {
                "run_id": run["run_id"],
                "generated_at": run["generated_at"],
                "path": run["path"],
                "nodes": nodes,
                "states": states,
                "evidence": evidence,
            }
        )

    evolutions = []
    graph_nodes = []
    graph_edges = []

    for topic_id in sorted(topic_ids):
        timeline = []
        events = []
        seen_evidence: set[str] = set()
        previous_state: dict[str, Any] | None = None
        previous_name = ""
        first_seen = ""
        last_seen = ""
        names: list[str] = []

        for run in run_views:
            node = run["nodes"].get(topic_id) or {}
            state = run["states"].get(topic_id) or {}
            evidence_items = run["evidence"].get(topic_id, [])
            observed = bool(node or state or evidence_items)
            as_of = _clean_text(state.get("as_of") or run["generated_at"], 64)
            name = _clean_text(node.get("canonical_name") or previous_name or topic_id, 120)

            if observed:
                if not first_seen:
                    first_seen = as_of
                    events.append(
                        {
                            "event_type": "topic_first_seen",
                            "at": as_of,
                            "run_id": run["run_id"],
                            "summary": f"首次观察到主题：{name}",
                        }
                    )
                last_seen = as_of
                if name and name not in names:
                    names.append(name)
                if previous_name and name != previous_name:
                    events.append(
                        {
                            "event_type": "topic_name_changed",
                            "at": as_of,
                            "run_id": run["run_id"],
                            "from": previous_name,
                            "to": name,
                        }
                    )

                delta = _state_delta(state, previous_state)
                if previous_state:
                    events.append(
                        {
                            "event_type": "state_updated",
                            "at": as_of,
                            "run_id": run["run_id"],
                            "delta": delta,
                            "trend": state.get("trend") or "",
                            "summary": _clean_text(state.get("change_explanation"), 220),
                        }
                    )

                new_evidence = []
                for item in evidence_items:
                    key = _evidence_key(item)
                    if key and key not in seen_evidence:
                        seen_evidence.add(key)
                        new_evidence.append(item)
                        events.append(
                            {
                                "event_type": "evidence_added",
                                "at": item.get("effective_time") or as_of,
                                "run_id": run["run_id"],
                                "object_id": item.get("object_id") or item.get("evidence_id") or "",
                                "object_type": item.get("object_type") or "unknown",
                                "relation_to_topic": item.get("relation_to_topic") or "updates",
                                "truth_status": item.get("truth_status") or "unverified",
                                "source_refs": item.get("source_refs") or [],
                                "summary": _clean_text(item.get("summary") or item.get("reason"), 220),
                            }
                        )

                snapshot = {
                    "run_id": run["run_id"],
                    "as_of": as_of,
                    "observed": True,
                    "canonical_name": name,
                    "state": {
                        "heat": _numeric(state.get("heat")),
                        "strength": _numeric(state.get("strength")),
                        "confidence": _numeric(state.get("confidence")),
                        "trend": state.get("trend") or "",
                        "dominant_assets": state.get("dominant_assets") or node.get("asset_refs") or [],
                        "driver_refs": state.get("driver_refs") or node.get("driver_refs") or [],
                        "change_explanation": _clean_text(state.get("change_explanation"), 260),
                    },
                    "delta": delta,
                    "membership_count": len(evidence_items),
                    "new_evidence_count": len(new_evidence),
                    "primary_evidence": _compact_evidence(
                        evidence_items, limit=max_evidence_per_snapshot
                    ),
                }
                timeline.append(snapshot)
                previous_state = state or previous_state
                previous_name = name
            elif first_seen:
                timeline.append(
                    {
                        "run_id": run["run_id"],
                        "as_of": run["generated_at"],
                        "observed": False,
                        "canonical_name": previous_name or topic_id,
                        "state": {},
                        "delta": {},
                        "membership_count": 0,
                        "new_evidence_count": 0,
                        "primary_evidence": [],
                    }
                )
                events.append(
                    {
                        "event_type": "topic_not_observed",
                        "at": run["generated_at"],
                        "run_id": run["run_id"],
                        "summary": "本次运行未再次观察到该主题，进入待衰减观察。",
                    }
                )

        if not timeline:
            continue

        latest_observed = next((item for item in reversed(timeline) if item.get("observed")), {})
        canonical_name = _clean_text(
            latest_observed.get("canonical_name") or (names[-1] if names else topic_id), 120
        )
        evolution = {
            "schema_version": "market_topic_evolution.v1",
            "topic_id": topic_id,
            "canonical_name": canonical_name,
            "aliases": [name for name in names if name != canonical_name],
            "first_seen": first_seen,
            "last_seen": last_seen,
            "lifecycle_state": _lifecycle_state(timeline),
            "observation_count": len([item for item in timeline if item.get("observed")]),
            "missing_after_seen_count": len([item for item in timeline if not item.get("observed")]),
            "current_state": latest_observed.get("state") or {},
            "timeline": timeline,
            "events": sorted(events, key=lambda item: (_parse_time(item.get("at")), item.get("event_type") or "")),
        }
        evolutions.append(evolution)

        topic_node_id = f"topic:{topic_id}"
        graph_nodes.append(
            {
                "id": topic_node_id,
                "type": "topic",
                "label": canonical_name,
                "topic_id": topic_id,
                "lifecycle_state": evolution["lifecycle_state"],
                "current_state": evolution["current_state"],
            }
        )
        for snapshot in timeline:
            snapshot_id = f"snapshot:{topic_id}:{snapshot['run_id']}"
            graph_nodes.append(
                {
                    "id": snapshot_id,
                    "type": "topic_snapshot",
                    "label": snapshot["as_of"],
                    "topic_id": topic_id,
                    "run_id": snapshot["run_id"],
                    "observed": snapshot["observed"],
                    "state": snapshot.get("state") or {},
                }
            )
            graph_edges.append(
                {
                    "id": f"edge:{topic_id}:{snapshot['run_id']}",
                    "from": topic_node_id,
                    "to": snapshot_id,
                    "type": "has_snapshot",
                    "label": "演化快照",
                }
            )
            for evidence_item in snapshot.get("primary_evidence") or []:
                evidence_id = evidence_item.get("object_id") or evidence_item.get("membership_id")
                if not evidence_id:
                    continue
                evidence_node_id = f"evidence:{evidence_id}"
                if evidence_node_id not in topics_seen:
                    topics_seen[evidence_node_id] = {"seen": True}
                    graph_nodes.append(
                        {
                            "id": evidence_node_id,
                            "type": "evidence",
                            "label": _clean_text(evidence_item.get("summary"), 80) or evidence_id,
                            "object_id": evidence_id,
                            "object_type": evidence_item.get("object_type") or "",
                            "truth_status": evidence_item.get("truth_status") or "unverified",
                            "source_refs": evidence_item.get("source_refs") or [],
                        }
                    )
                graph_edges.append(
                    {
                        "id": f"edge:{snapshot_id}:{evidence_id}",
                        "from": snapshot_id,
                        "to": evidence_node_id,
                        "type": "snapshot_evidence",
                        "label": evidence_item.get("relation_to_topic") or "updates",
                    }
                )

    evolutions.sort(key=lambda item: (item.get("lifecycle_state") != "strengthening", item["topic_id"]))
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "candidate",
        "generated_at": generated_at,
        "generator": {
            "name": "topic_evolution_read_model",
            "version": AGENT_VERSION,
        },
        "source_run_count": len(runs),
        "source_runs": [
            {
                "run_id": run["run_id"],
                "generated_at": run["generated_at"],
                "path": run["path"],
                "topic_count": len(_as_list(run["payload"].get("topic_nodes"))),
            }
            for run in runs
        ],
        "topic_count": len(evolutions),
        "topics": evolutions,
        "graph": {
            "nodes": graph_nodes,
            "edges": graph_edges,
        },
        "quality_notes": []
        if runs
        else ["No recent-news topic runs found under market_topics archive."],
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Topic 演化读模型",
        "",
        f"- 生成时间: {payload.get('generated_at')}",
        f"- 来源 run 数: {payload.get('source_run_count')}",
        f"- topic 数: {payload.get('topic_count')}",
        "",
    ]
    if payload.get("source_runs"):
        lines.append("## 来源运行")
        lines.append("")
        for run in payload["source_runs"]:
            lines.append(
                f"- `{run.get('run_id')}` / {run.get('generated_at')} / topics {run.get('topic_count')}"
            )
        lines.append("")

    for topic in payload.get("topics") or []:
        state = topic.get("current_state") or {}
        lines.extend(
            [
                f"## {topic.get('canonical_name')}",
                "",
                f"- topic_id: `{topic.get('topic_id')}`",
                f"- 生命周期: {topic.get('lifecycle_state')}",
                f"- 首次观察: {topic.get('first_seen')}",
                f"- 最近观察: {topic.get('last_seen')}",
                f"- 当前状态: trend {state.get('trend', '')} / strength {state.get('strength', 0)} / heat {state.get('heat', 0)} / confidence {state.get('confidence', 0)}",
                "",
                "### 时间线",
                "",
            ]
        )
        for snapshot in topic.get("timeline") or []:
            observed = "observed" if snapshot.get("observed") else "not observed"
            delta = snapshot.get("delta") or {}
            state = snapshot.get("state") or {}
            lines.append(
                f"- {snapshot.get('as_of')} / `{snapshot.get('run_id')}` / {observed} / "
                f"strength {state.get('strength', '')} ({delta.get('strength', 0):+}) / "
                f"heat {state.get('heat', '')} ({delta.get('heat', 0):+}) / "
                f"new evidence {snapshot.get('new_evidence_count')}"
            )
            explanation = _clean_text(state.get("change_explanation"), 180)
            if explanation:
                lines.append(f"  - 变化说明: {explanation}")
            for evidence in (snapshot.get("primary_evidence") or [])[:3]:
                refs = "；".join(
                    f"{ref.get('ref_type')}:{ref.get('id')}"
                    for ref in evidence.get("source_refs") or []
                    if isinstance(ref, dict)
                )
                lines.append(f"  - 证据: {_clean_text(evidence.get('summary'), 160)}")
                if refs:
                    lines.append(f"    - refs: {refs}")
        lines.append("")
    return "\n".join(lines)


def publish_topic_evolution(
    *,
    root: str | Path | None = None,
    source_dir: str | Path | None = None,
    run_ids: list[str] | None = None,
    output_dir: str | Path | None = None,
    max_evidence_per_snapshot: int = 6,
    now: datetime | None = None,
) -> dict[str, Any]:
    root_path = quanta_data_root(root)
    payload = build_topic_evolution(
        root=root_path,
        source_dir=source_dir,
        run_ids=run_ids,
        max_evidence_per_snapshot=max_evidence_per_snapshot,
        now=now,
    )
    latest_dir = root_path / "agent_workspace" / "candidates" / "market_topics" / "latest"
    latest_json = latest_dir / "topic-evolution-read-model.json"
    latest_md = latest_dir / "topic-evolution.md"
    per_topic_dir = latest_dir / "topic-evolution"
    markdown = render_markdown(payload)
    write_json(latest_json, payload)
    atomic_write_text(latest_md, markdown)
    for topic in payload.get("topics") or []:
        topic_id = topic.get("topic_id")
        if topic_id:
            write_json(per_topic_dir / f"{topic_id}.json", topic)

    result = {
        "payload": payload,
        "latest_json": str(latest_json),
        "latest_markdown": str(latest_md),
        "per_topic_dir": str(per_topic_dir),
    }
    if output_dir:
        archive_dir = Path(output_dir).expanduser()
        archive_json = archive_dir / "topic-evolution-read-model.json"
        archive_md = archive_dir / "topic-evolution.md"
        archive_per_topic_dir = archive_dir / "topic-evolution"
        write_json(archive_json, payload)
        atomic_write_text(archive_md, markdown)
        for topic in payload.get("topics") or []:
            topic_id = topic.get("topic_id")
            if topic_id:
                write_json(archive_per_topic_dir / f"{topic_id}.json", topic)
        result.update(
            {
                "archive_json": str(archive_json),
                "archive_markdown": str(archive_md),
                "archive_per_topic_dir": str(archive_per_topic_dir),
            }
        )
    return result


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Build MarketTopic evolution read model.")
    parser.add_argument("--quanta-root", help="Override GJ_QUANTA_DATA_ROOT.")
    parser.add_argument("--source-dir", help="Override market_topics archive directory.")
    parser.add_argument("--run-id", action="append", dest="run_ids", help="Limit evolution to specific topic run id. Can be repeated.")
    parser.add_argument("--output-dir", help="Also write a run-scoped copy under this directory.")
    parser.add_argument("--max-evidence-per-snapshot", type=int, default=6)
    args = parser.parse_args(argv)

    result = publish_topic_evolution(
        root=args.quanta_root,
        source_dir=args.source_dir,
        run_ids=args.run_ids,
        output_dir=args.output_dir,
        max_evidence_per_snapshot=args.max_evidence_per_snapshot,
    )
    payload = result["payload"]
    print(f"latest_json: {result['latest_json']}")
    print(f"latest_markdown: {result['latest_markdown']}")
    print(f"per_topic_dir: {result['per_topic_dir']}")
    print(f"source_run_count: {payload['source_run_count']}")
    print(f"topic_count: {payload['topic_count']}")
    for topic in payload["topics"][:8]:
        state = topic.get("current_state") or {}
        print(
            f"- {topic['canonical_name']} | {topic['topic_id']} | "
            f"{topic['lifecycle_state']} strength={state.get('strength', 0)} "
            f"observations={topic['observation_count']}"
        )


if __name__ == "__main__":
    main()
