from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from quanta_agents.core.config import quanta_data_root
from quanta_agents.core.io import (
    atomic_write_text,
    dated_parts,
    read_json,
    relative_to_root,
    write_json,
)


SHANGHAI_TZ = timezone(timedelta(hours=8))
DEFAULT_ROOT = Path("/Volumes/数字大脑/quanta_data")
DEFAULT_DATE_KEY = "20260620"
DEFAULT_RUN_ID = "RUN-KBM-20260620-001"
DEFAULT_TRIAGE_ID = "PM-TRIAGE-20260620-002"
DEFAULT_VALIDATOR_CANDIDATE_ID = "CAND-KBM-CODEX-20260620-001"
DEFAULT_WORK_ORDER_ID = "WO-DEV-20260620-002"
PRIORITY_RANK = {"urgent": 4, "high": 3, "medium": 2, "low": 1}
PM_DEPENDENCY_ORDER = {
    "canonicalizer_before_evidence_extractor": 10,
    "canonical_quality_review": 20,
    "evidence_gap_backfill": 25,
    "official_stats_ingest_before_macro_claims": 30,
    "conflict_set_timeline_collection": 40,
    "event_definition_layers_schema": 50,
    "schema_cross_layer_refs": 60,
    "failed_llm_review_blocks_framework_promotion": 70,
    "framework_dimension_refresh": 80,
    "prompt_pack_validation": 90,
    "fresh_market_brief_inputs": 100,
    "source_entity_linking": 110,
    "opinion_radar_source_quality": 120,
    "polymarket_low_confidence_labeling": 121,
    "runner_guardrail": 130,
}


@dataclass(frozen=True)
class NormalizedSuggestion:
    candidate_id: str
    suggestion_id: str
    source_ref: str
    source_path: str
    suggestion_type: str
    priority: str
    target_ref: str
    reason: str
    proposed_action: str
    evidence_refs: list[str]
    impact_scope: str
    impact_layer: str
    dependency: str
    human_review_required: bool
    related_files: list[str]
    raw: dict[str, Any]


def _now_iso() -> str:
    return datetime.now(SHANGHAI_TZ).isoformat(timespec="seconds")


def _date_key(value: str | None) -> str:
    if not value:
        return datetime.now(SHANGHAI_TZ).strftime("%Y%m%d")
    clean = value.strip().replace("-", "")
    if len(clean) != 8 or not clean.isdigit():
        raise ValueError("date must be YYYYMMDD or YYYY-MM-DD")
    return clean


def _date_dash(date_key: str) -> str:
    return f"{date_key[:4]}-{date_key[4:6]}-{date_key[6:8]}"


def _stable_suffix(*parts: Any) -> str:
    raw = "||".join(str(part) for part in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10].upper()


def _ref_to_text(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("path", "id", "ref_type"):
            text = str(value.get(key) or "").strip()
            if text:
                return text
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if value is None:
        return ""
    return str(value).strip()


def _refs_to_texts(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    refs: list[str] = []
    for item in value:
        text = _ref_to_text(item)
        if text:
            refs.append(text)
    return refs


def _impact_to_text(value: Any) -> str:
    if isinstance(value, dict):
        parts: list[str] = []
        for key in ("assets", "themes", "frameworks", "publications"):
            rows = value.get(key)
            if isinstance(rows, list) and rows:
                parts.append(f"{key}:{','.join(str(row) for row in rows)}")
        return " / ".join(parts)
    return _ref_to_text(value)


def _priority(value: Any) -> str:
    text = str(value or "medium").strip().lower()
    return text if text in PRIORITY_RANK else "medium"


def _combined_text(item: dict[str, Any], target_ref: str, impact_scope: str) -> str:
    values = [
        item.get("suggestion_type"),
        item.get("type"),
        target_ref,
        impact_scope,
        item.get("reason"),
        item.get("proposed_action"),
    ]
    return " ".join(str(value or "").lower() for value in values)


def _derive_layer_and_dependency(
    item: dict[str, Any], *, target_ref: str, impact_scope: str
) -> tuple[str, str]:
    text = _combined_text(item, target_ref, impact_scope)
    suggestion_type = str(item.get("suggestion_type") or item.get("type") or "").strip()
    target_text = target_ref.lower()

    if "canonicalization_layer" in text or "evidence_layer" in text:
        return "canonical_evidence_bridge", "canonicalizer_before_evidence_extractor"
    if "cpi_ppi" in text or "stats_bureau" in text:
        return "macro_evidence", "official_stats_ingest_before_macro_claims"
    if "framework_optimization" in text or "llm_review" in text:
        return "framework_review_gate", "failed_llm_review_blocks_framework_promotion"
    if "event_definition_uncertain" in text:
        return "theme_conflict_model", "event_definition_layers_schema"
    if suggestion_type == "schema_contract_issue" or "configs/schemas" in text:
        return "schema_contract", "schema_cross_layer_refs"
    if suggestion_type == "prompt_pack_gap" or "prompt_packs" in text or "prompt_pack" in text:
        return "prompt_pack", "prompt_pack_validation"
    if suggestion_type == "canonical_quality_issue":
        return "canonical_layer", "canonical_quality_review"
    if suggestion_type == "stale_framework" or "gold/frameworks" in text:
        return "active_framework", "framework_dimension_refresh"
    if suggestion_type == "crawler_api_issue" or "crawler" in text or "raw_objects" in text:
        return "crawler_api", "source_entity_linking"
    if suggestion_type == "publication_update" or "market_brief_workflow" in text:
        return "publication_workflow", "fresh_market_brief_inputs"
    if suggestion_type == "conflict_set" or "evidence_store/conflict_sets" in target_text:
        return "evidence_conflict_set", "conflict_set_timeline_collection"
    if "polymarket" in text:
        return "source_quality", "polymarket_low_confidence_labeling"
    if "opinion_radar" in text or "jin10" in text:
        return "source_quality", "opinion_radar_source_quality"
    if "canonical" in text:
        return "canonical_layer", "canonical_quality_review"
    if "evidence" in text:
        return "evidence_layer", "evidence_gap_backfill"
    if "workflow" in suggestion_type or "runner" in text:
        return "workflow_runtime", "runner_guardrail"
    return "maintenance", suggestion_type or "general_kb_maintenance"


def normalize_suggestions_payload(
    payload: dict[str, Any],
    *,
    source_path: str | Path,
    root: str | Path | None = None,
) -> list[NormalizedSuggestion]:
    candidate_id = str(payload.get("candidate_id") or Path(source_path).parent.name).strip()
    rows = payload.get("items")
    if not isinstance(rows, list):
        rows = payload.get("suggestions")
    if not isinstance(rows, list):
        rows = []

    source = Path(source_path)
    root_path = Path(root).expanduser() if root else None
    source_path_text = relative_to_root(source, root_path) if root_path else str(source)

    normalized: list[NormalizedSuggestion] = []
    for index, item in enumerate(rows, start=1):
        if not isinstance(item, dict):
            continue
        suggestion_id = str(
            item.get("suggestion_id") or f"KBM-{_stable_suffix(candidate_id, index)}"
        ).strip()
        suggestion_type = str(item.get("suggestion_type") or item.get("type") or "workflow_code_issue")
        target_ref = _ref_to_text(item.get("target_ref"))
        impact_scope = _impact_to_text(item.get("impact_scope"))
        impact_layer, dependency = _derive_layer_and_dependency(
            item, target_ref=target_ref, impact_scope=impact_scope
        )
        source_ref = f"{candidate_id}#{suggestion_id}"
        normalized.append(
            NormalizedSuggestion(
                candidate_id=candidate_id,
                suggestion_id=suggestion_id,
                source_ref=source_ref,
                source_path=source_path_text,
                suggestion_type=suggestion_type,
                priority=_priority(item.get("priority")),
                target_ref=target_ref,
                reason=str(item.get("reason") or "").strip(),
                proposed_action=str(item.get("proposed_action") or "").strip(),
                evidence_refs=_refs_to_texts(item.get("evidence_refs")),
                impact_scope=impact_scope,
                impact_layer=impact_layer,
                dependency=dependency,
                human_review_required=bool(item.get("human_review_required", True)),
                related_files=_refs_to_texts(item.get("related_files")),
                raw=item,
            )
        )
    return normalized


def load_candidate_suggestions(
    candidate_paths: list[str | Path], *, root: str | Path | None = None
) -> list[NormalizedSuggestion]:
    suggestions: list[NormalizedSuggestion] = []
    for path in candidate_paths:
        payload = read_json(Path(path))
        if not isinstance(payload, dict):
            continue
        suggestions.extend(normalize_suggestions_payload(payload, source_path=path, root=root))
    return suggestions


def _sort_suggestions(rows: list[NormalizedSuggestion]) -> list[NormalizedSuggestion]:
    return sorted(
        rows,
        key=lambda row: (
            -PRIORITY_RANK.get(row.priority, 0),
            row.impact_layer,
            row.dependency,
            row.candidate_id,
            row.suggestion_id,
        ),
    )


def _proposal_title(layer: str, dependency: str) -> str:
    titles = {
        "canonicalizer_before_evidence_extractor": "Restore canonical-to-evidence bridge",
        "official_stats_ingest_before_macro_claims": "Backfill official CPI/PPI evidence",
        "failed_llm_review_blocks_framework_promotion": "Gate failed framework LLM review",
        "event_definition_layers_schema": "Add event-definition uncertainty support",
        "conflict_set_timeline_collection": "Build conflict-set timeline evidence",
        "framework_dimension_refresh": "Refresh stale active framework dimensions",
        "prompt_pack_validation": "Add prompt-pack validation guardrails",
        "source_entity_linking": "Add source entity-linking pipeline hooks",
        "schema_cross_layer_refs": "Extend KBM cross-layer schema refs",
        "fresh_market_brief_inputs": "Refresh stale market-brief inputs",
        "polymarket_low_confidence_labeling": "Label Polymarket low-confidence signals",
        "opinion_radar_source_quality": "Improve opinion-radar source quality",
        "canonical_quality_review": "Repair canonical document quality",
        "evidence_gap_backfill": "Backfill missing evidence units",
        "runner_guardrail": "Add runner workflow guardrail",
    }
    return titles.get(dependency, f"Maintain {layer.replace('_', ' ')}")


def _proposal_acceptance_checks(dependency: str) -> list[str]:
    if dependency == "canonicalizer_before_evidence_extractor":
        return [
            "canonical_documents business subdirectories contain structured outputs from raw manifests",
            "evidence_extractor can consume canonical_documents and write evidence_store units",
            "a smoke run records source refs and hashes from raw -> canonical -> evidence",
        ]
    if dependency == "official_stats_ingest_before_macro_claims":
        return [
            "official stats bureau raw object and manifest are present",
            "canonical macro CPI/PPI document exists",
            "evidence refs for May CPI/PPI claims point to official-source evidence units",
        ]
    if dependency == "failed_llm_review_blocks_framework_promotion":
        return [
            "failed llm_review status blocks automatic framework promotion",
            "manual review path is recorded for all failed candidates",
            "system event or audit record captures the failed review reason",
        ]
    if dependency == "event_definition_layers_schema":
        return [
            "event_definition_layers are represented in schema or extraction output",
            "fact, political, time-window, and settlement-rule layers can be compared",
            "Polymarket, official statements, and media descriptions are cross-checked",
        ]
    if dependency == "conflict_set_timeline_collection":
        return [
            "conflict_set artifact preserves both supporting and weakening evidence",
            "timeline entries include source, timestamp, and claim polarity",
            "downstream reports can reference the conflict_set instead of one-sided evidence",
        ]
    return [
        "repair keeps source_refs from KBM suggestions",
        "validator or focused test demonstrates the issue no longer blocks PM dispatch",
    ]


def _proposal_problem(rows: list[NormalizedSuggestion]) -> str:
    reasons = [row.reason for row in rows if row.reason]
    if not reasons:
        return "KB maintenance suggestions identify a repair task for PM triage."
    return reasons[0]


def _proposal_action(rows: list[NormalizedSuggestion]) -> str:
    actions = [row.proposed_action for row in rows if row.proposed_action]
    if not actions:
        return "Create an engineering work order that resolves the grouped KB maintenance findings."
    return actions[0]


def _source_items(rows: list[NormalizedSuggestion]) -> list[dict[str, Any]]:
    return [
        {
            "source_ref": row.source_ref,
            "candidate_id": row.candidate_id,
            "suggestion_id": row.suggestion_id,
            "source_path": row.source_path,
            "suggestion_type": row.suggestion_type,
            "priority": row.priority,
            "target_ref": row.target_ref,
            "impact_scope": row.impact_scope,
        }
        for row in rows
    ]


def _legacy_group_sort_key(
    entry: tuple[tuple[str, str, str], list[NormalizedSuggestion]]
) -> tuple[int, str, str, str]:
    (priority, impact_layer, dependency), rows = entry
    return (
        -PRIORITY_RANK.get(priority, 0),
        impact_layer,
        dependency,
        min(row.source_ref for row in rows),
    )


def _pm_group_sort_key(proposal: dict[str, Any]) -> tuple[int, int, str]:
    dependency = str(proposal.get("dependency") or "")
    priority = str(proposal.get("priority") or "medium")
    return (
        PM_DEPENDENCY_ORDER.get(dependency, 999),
        -PRIORITY_RANK.get(priority, 0),
        str(proposal.get("proposal_id") or ""),
    )


def _recommended_sequence(dependency: str) -> str:
    labels = {
        "canonicalizer_before_evidence_extractor": (
            "01_kb_substrate_restore_canonical_evidence_bridge"
        ),
        "canonical_quality_review": "02_canonical_quality_before_downstream_claims",
        "evidence_gap_backfill": "03_backfill_evidence_units",
        "official_stats_ingest_before_macro_claims": "04_official_macro_evidence",
        "conflict_set_timeline_collection": "05_conflict_timeline_evidence",
        "event_definition_layers_schema": "06_event_definition_model",
        "schema_cross_layer_refs": "07_schema_contract_support",
        "failed_llm_review_blocks_framework_promotion": "08_framework_review_gate",
        "framework_dimension_refresh": "09_framework_dimension_refresh",
        "prompt_pack_validation": "10_prompt_pack_guardrails",
        "fresh_market_brief_inputs": "11_publication_input_refresh",
        "source_entity_linking": "12_source_entity_linking",
        "opinion_radar_source_quality": "13_source_quality_opinion_radar",
        "polymarket_low_confidence_labeling": "14_source_quality_polymarket",
        "runner_guardrail": "15_runtime_guardrail",
    }
    return labels.get(dependency, "99_general_kb_maintenance")


def _sequence_reason(dependency: str) -> str:
    if dependency == "canonicalizer_before_evidence_extractor":
        return (
            "KB substrate comes first: framework and theme repair need canonical documents "
            "and evidence units as factual grounding."
        )
    if dependency in {
        "official_stats_ingest_before_macro_claims",
        "conflict_set_timeline_collection",
        "event_definition_layers_schema",
    }:
        return "Evidence and event-definition repairs should precede downstream interpretation work."
    if dependency == "framework_dimension_refresh":
        return "Framework edits should wait until the relevant evidence substrate is available."
    return "Ranked by KB dependency order, then priority within the same dependency band."


def build_task_proposals(
    suggestions: list[NormalizedSuggestion],
    *,
    date_key: str = DEFAULT_DATE_KEY,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[NormalizedSuggestion]] = {}
    for item in suggestions:
        key = (item.priority, item.impact_layer, item.dependency)
        grouped.setdefault(key, []).append(item)

    stable_id_groups = sorted(grouped.items(), key=_legacy_group_sort_key)

    proposals: list[dict[str, Any]] = []
    for index, ((priority, impact_layer, dependency), rows) in enumerate(stable_id_groups, start=1):
        ordered_rows = _sort_suggestions(rows)
        proposal_id = f"TP-KBM-{date_key}-{index:03d}"
        source_refs = [row.source_ref for row in ordered_rows]
        proposals.append(
            {
                "proposal_id": proposal_id,
                "title": _proposal_title(impact_layer, dependency),
                "priority": priority,
                "impact_layer": impact_layer,
                "dependency": dependency,
                "recommended_sequence": _recommended_sequence(dependency),
                "sequence_reason": _sequence_reason(dependency),
                "status": "proposed_for_pm_triage",
                "source_refs": source_refs,
                "source_items": _source_items(ordered_rows),
                "source_count": len(source_refs),
                "problem_statement": _proposal_problem(ordered_rows),
                "recommended_task": _proposal_action(ordered_rows),
                "acceptance_checks": _proposal_acceptance_checks(dependency),
                "pm_dispatch_note": (
                    "Create or update a work order only from these source_refs, "
                    "or add an explicit human override note."
                ),
            }
        )

    ordered_proposals = sorted(proposals, key=_pm_group_sort_key)
    for pm_rank, proposal in enumerate(ordered_proposals, start=1):
        proposal["pm_rank"] = pm_rank
    return ordered_proposals


def _parse_validator_issues(validator_id: str, stdout: str, stderr: str) -> list[str]:
    output = "\n".join(part for part in (stdout, stderr) if part)
    issues: list[str] = []
    if validator_id == "candidate_contract":
        for line in output.splitlines():
            stripped = line.strip()
            if stripped.startswith("- "):
                issues.append(stripped[2:].strip())
    elif validator_id == "silicon_workspace":
        in_errors = False
        for line in output.splitlines():
            stripped = line.strip()
            if stripped == "errors:":
                in_errors = True
                continue
            if in_errors and stripped.startswith("- "):
                issues.append(stripped[2:].strip())
            elif in_errors and stripped and not stripped.startswith("- "):
                in_errors = False
    if not issues and stderr.strip():
        issues.append(stderr.strip().splitlines()[-1])
    return issues


def run_validator_commands(
    *,
    root: Path,
    timeout: int,
    skip_validators: bool = False,
) -> list[dict[str, Any]]:
    if skip_validators:
        return []
    workspace_root = root / "agent_workspace"
    specs = [
        {
            "id": "candidate_contract",
            "command": [
                "python3",
                "/Users/miniquanta/Documents/gj_chainplatform/scripts/check_candidate_contract.py",
                "--root",
                str(root),
            ],
            "cwd": "/Users/miniquanta/Documents/gj_chainplatform",
        },
        {
            "id": "silicon_workspace",
            "command": [
                "python3",
                "/Users/miniquanta/.codex/skills/silicon-alpha-research/scripts/validate_silicon_workspace.py",
                "--root",
                str(workspace_root),
            ],
            "cwd": "/Users/miniquanta/Documents/quanta_agents",
        },
    ]
    results: list[dict[str, Any]] = []
    for spec in specs:
        started_at = _now_iso()
        try:
            completed = subprocess.run(
                spec["command"],
                cwd=spec["cwd"],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            stdout = completed.stdout
            stderr = completed.stderr
            exit_code = completed.returncode
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout or ""
            stderr = exc.stderr or f"validator timed out after {timeout}s"
            exit_code = 124

        issues = _parse_validator_issues(str(spec["id"]), stdout, stderr)
        status = "passed" if exit_code == 0 and not issues else "issues_found"
        results.append(
            {
                "validator_id": spec["id"],
                "command": spec["command"],
                "cwd": spec["cwd"],
                "started_at": started_at,
                "finished_at": _now_iso(),
                "exit_code": exit_code,
                "status": status,
                "issue_count": len(issues),
                "issues": issues,
                "stdout": stdout,
                "stderr": stderr,
            }
        )
    return results


def _validator_findings(validator_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for result in validator_results:
        issues = result.get("issues") if isinstance(result.get("issues"), list) else []
        if not issues and result.get("exit_code") not in (0, None):
            issues = [f"validator exited with code {result.get('exit_code')}"]
        for issue in issues:
            findings.append(
                {
                    "validator_id": result.get("validator_id"),
                    "priority": "high" if result.get("exit_code") not in (0, None) else "medium",
                    "issue": str(issue),
                    "command": result.get("command"),
                }
            )
    return findings


def _validator_candidate_items(findings: list[dict[str, Any]], *, date_key: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for index, finding in enumerate(findings, start=1):
        validator_id = str(finding.get("validator_id") or "validator")
        issue = str(finding.get("issue") or "validator issue")
        items.append(
            {
                "suggestion_id": f"KMS-{date_key}-CODEX-{index:03d}",
                "type": "workflow_code_issue",
                "priority": finding.get("priority") or "medium",
                "target_ref": f"validator/{validator_id}",
                "reason": f"{validator_id} reported: {issue}",
                "proposed_action": (
                    "PM should assign a repair task that fixes the validator finding, "
                    "then rerun the validator and attach the passing output to the run trace."
                ),
                "evidence_refs": [],
                "impact_scope": "validator_contract / platform_visibility",
                "human_review_required": True,
                "related_files": [str(part) for part in (finding.get("command") or [])[:2]],
            }
        )
    return items


def _write_validator_candidate(
    *,
    root: Path,
    run_id: str,
    date_key: str,
    candidate_id: str,
    findings: list[dict[str, Any]],
    generated_at: str,
) -> Path | None:
    if not findings:
        return None
    yyyy, mm, dd = dated_parts(date_key)
    candidate_dir = (
        root
        / "agent_workspace"
        / "candidates"
        / "knowledge_maintenance"
        / yyyy
        / mm
        / dd
        / candidate_id
    )
    items = _validator_candidate_items(findings, date_key=date_key)
    manifest = {
        "schema_version": "knowledge_maintenance_manifest.v1",
        "candidate_id": candidate_id,
        "title": f"{_date_dash(date_key)} validator-discovered KB maintenance findings",
        "agent": "codex",
        "run_id": run_id,
        "created_at": generated_at,
        "trading_date": _date_dash(date_key),
        "lifecycle_state": "candidate",
        "summary": f"{len(items)} validator finding(s) require PM triage before dispatch closure.",
        "items_count": len(items),
        "related_report_refs": [],
    }
    suggestions = {
        "schema_version": "knowledge_maintenance_suggestions.v1",
        "candidate_id": candidate_id,
        "generated_by": {"agent": "codex", "run_id": run_id},
        "trading_date": _date_dash(date_key),
        "generated_at": generated_at,
        "human_priority_actions": [item["suggestion_id"] for item in items[:5]],
        "items": items,
    }
    write_json(candidate_dir / "manifest.json", manifest)
    write_json(candidate_dir / "suggestions.json", suggestions)
    return candidate_dir / "suggestions.json"


def _input_ref(path: str | Path, *, root: Path, ref_type: str, item_id: str | None = None) -> dict[str, str]:
    path_obj = Path(path)
    return {
        "ref_type": ref_type,
        "id": item_id or path_obj.parent.name,
        "path": relative_to_root(path_obj, root),
    }


def _proposal_summary(proposals: list[dict[str, Any]]) -> dict[str, Any]:
    by_priority: dict[str, int] = {}
    by_layer: dict[str, int] = {}
    for proposal in proposals:
        priority = str(proposal.get("priority") or "medium")
        layer = str(proposal.get("impact_layer") or "maintenance")
        by_priority[priority] = by_priority.get(priority, 0) + 1
        by_layer[layer] = by_layer.get(layer, 0) + 1
    return {
        "proposal_count": len(proposals),
        "by_priority": by_priority,
        "by_impact_layer": by_layer,
        "recommended_sequence": [
            {
                "pm_rank": proposal.get("pm_rank"),
                "proposal_id": proposal.get("proposal_id"),
                "recommended_sequence": proposal.get("recommended_sequence"),
                "priority": proposal.get("priority"),
                "impact_layer": proposal.get("impact_layer"),
                "source_refs": proposal.get("source_refs"),
            }
            for proposal in proposals
        ],
    }


def _next_recommended_work_order(proposals: list[dict[str, Any]]) -> dict[str, Any]:
    bridge = next(
        (
            proposal
            for proposal in proposals
            if proposal.get("dependency") == "canonicalizer_before_evidence_extractor"
        ),
        proposals[0] if proposals else {},
    )
    return {
        "work_order_id": "WO-DEV-20260620-005",
        "decision": "next_engineering_task",
        "source_proposal_id": bridge.get("proposal_id"),
        "source_refs": bridge.get("source_refs") or [],
        "reason": (
            "Repair the KB substrate first: KMS-20260619-011/012 show canonical_documents "
            "and evidence_store are blocking downstream framework and theme work."
        ),
    }


def _build_pm_triage(
    *,
    triage_id: str,
    run_id: str,
    work_order_id: str,
    generated_at: str,
    input_refs: list[dict[str, Any]],
    validator_results: list[dict[str, Any]],
    proposals: list[dict[str, Any]],
    validator_candidate_ref: str | None,
) -> dict[str, Any]:
    top = proposals[:6]
    next_work_order = _next_recommended_work_order(proposals)
    return {
        "schema_version": "pm_kb_triage.v1",
        "triage_id": triage_id,
        "generated_at": generated_at,
        "generated_by": "knowledge_maintenance_agent",
        "mode": "kb_result_to_pm_task_proposals",
        "work_order_id": work_order_id,
        "run_id": run_id,
        "input_refs": input_refs,
        "validator_results": [
            {
                "validator_id": result.get("validator_id"),
                "status": result.get("status"),
                "exit_code": result.get("exit_code"),
                "issue_count": result.get("issue_count"),
                "issues": result.get("issues"),
            }
            for result in validator_results
        ],
        "validator_candidate_ref": validator_candidate_ref,
        "summary": _proposal_summary(proposals),
        "next_recommended_work_order": next_work_order,
        "dispatch_decisions": [
            next_work_order,
            {
                "work_order_id": "WO-DEV-20260620-004",
                "decision": "defer",
                "reason": (
                    "Platform workbench should continue to defer until PM task provenance "
                    "from WO-DEV-20260620-005 is visible and stable."
                ),
            },
        ],
        "top_findings": [
            {
                "pm_rank": proposal.get("pm_rank"),
                "proposal_id": proposal.get("proposal_id"),
                "priority": proposal.get("priority"),
                "recommended_sequence": proposal.get("recommended_sequence"),
                "impact_layer": proposal.get("impact_layer"),
                "dependency": proposal.get("dependency"),
                "finding": proposal.get("problem_statement"),
                "source_refs": proposal.get("source_refs"),
            }
            for proposal in top
        ],
        "task_proposals": proposals,
        "pm_dispatch_instruction": (
            "PM dispatch must consume task_proposals/source_refs from this triage. "
            "Do not create a work order from a generic health report without KBM, validator, "
            "or explicit human override provenance."
        ),
        "next_work_order_source_rule": (
            "New work orders require KBM candidate refs, validator output, or an explicit human override note."
        ),
    }


def _render_triage_markdown(triage: dict[str, Any]) -> str:
    lines = [
        f"# {triage['triage_id']} KB-result-to-task triage",
        "",
        f"- generated_at: {triage['generated_at']}",
        f"- run_id: {triage['run_id']}",
        f"- work_order_id: {triage['work_order_id']}",
        f"- proposal_count: {triage['summary']['proposal_count']}",
        "",
        "## Next Recommended Work Order",
        "",
    ]
    next_work_order = triage.get("next_recommended_work_order") or {}
    next_sources = ", ".join(next_work_order.get("source_refs") or [])
    lines.extend(
        [
            f"- work_order_id: {next_work_order.get('work_order_id')}",
            f"- decision: {next_work_order.get('decision')}",
            f"- source_proposal_id: {next_work_order.get('source_proposal_id')}",
            f"- source_refs: {next_sources}",
            f"- reason: {next_work_order.get('reason')}",
            "- platform_workbench: defer WO-DEV-20260620-004 until KB provenance is stable",
            "",
        ]
    )
    lines.extend(
        [
            "## PM Dispatch Rule",
            "",
            triage["pm_dispatch_instruction"],
            "",
            "## Task Proposals",
            "",
        ]
    )
    for proposal in triage.get("task_proposals") or []:
        source_refs = ", ".join(proposal.get("source_refs") or [])
        lines.extend(
            [
                f"### {proposal['proposal_id']} - {proposal['title']}",
                "",
                f"- pm_rank: {proposal['pm_rank']}",
                f"- recommended_sequence: {proposal['recommended_sequence']}",
                f"- priority: {proposal['priority']}",
                f"- impact_layer: {proposal['impact_layer']}",
                f"- dependency: {proposal['dependency']}",
                f"- source_refs: {source_refs}",
                f"- recommended_task: {proposal['recommended_task']}",
                "",
            ]
        )
    validator_results = triage.get("validator_results") or []
    if validator_results:
        lines.extend(["## Validator Results", ""])
        for result in validator_results:
            lines.append(
                f"- {result['validator_id']}: {result['status']} "
                f"(exit={result['exit_code']}, issues={result['issue_count']})"
            )
    return "\n".join(lines).rstrip() + "\n"


def _logs(validator_results: list[dict[str, Any]], *, validator_passed: bool) -> str:
    lines = [
        f"generated_at={_now_iso()}",
        f"validator_passed={str(validator_passed).lower()}",
        "",
    ]
    for result in validator_results:
        lines.extend(
            [
                f"## {result.get('validator_id')}",
                f"command={' '.join(str(part) for part in (result.get('command') or []))}",
                f"exit_code={result.get('exit_code')}",
                f"status={result.get('status')}",
                "stdout:",
                str(result.get("stdout") or "").rstrip(),
                "stderr:",
                str(result.get("stderr") or "").rstrip(),
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def run_maintenance_triage(
    *,
    root: str | Path | None = None,
    candidate_paths: list[str | Path] | None = None,
    previous_triage_path: str | Path | None = None,
    date: str = DEFAULT_DATE_KEY,
    run_id: str = DEFAULT_RUN_ID,
    triage_id: str = DEFAULT_TRIAGE_ID,
    validator_candidate_id: str = DEFAULT_VALIDATOR_CANDIDATE_ID,
    work_order_id: str = DEFAULT_WORK_ORDER_ID,
    validator_timeout: int = 180,
    skip_validators: bool = False,
) -> dict[str, Any]:
    root_path = quanta_data_root(root or DEFAULT_ROOT)
    date_key = _date_key(date)
    yyyy, mm, dd = dated_parts(date_key)
    generated_at = _now_iso()
    workspace_root = root_path / "agent_workspace"

    if candidate_paths is None:
        candidate_paths = [
            workspace_root
            / "candidates"
            / "knowledge_maintenance"
            / "2026"
            / "06"
            / "19"
            / "CAND-KBM-HERMES-20260619-002"
            / "suggestions.json",
            workspace_root
            / "candidates"
            / "knowledge_maintenance"
            / "2026"
            / "06"
            / "19"
            / "CAND-KBM-HERMES-20260619-001"
            / "suggestions.json",
        ]
    if previous_triage_path is None:
        previous_triage_path = (
            workspace_root
            / "agents"
            / "shared"
            / "pm_triage"
            / "2026"
            / "06"
            / "20"
            / "PM-TRIAGE-20260620-001.json"
        )

    suggestions = load_candidate_suggestions(list(candidate_paths), root=root_path)
    validator_results = run_validator_commands(
        root=root_path, timeout=validator_timeout, skip_validators=skip_validators
    )
    validator_findings = _validator_findings(validator_results)
    validator_candidate_path = _write_validator_candidate(
        root=root_path,
        run_id=run_id,
        date_key=date_key,
        candidate_id=validator_candidate_id,
        findings=validator_findings,
        generated_at=generated_at,
    )
    if validator_candidate_path is not None:
        suggestions.extend(
            load_candidate_suggestions([validator_candidate_path], root=root_path)
        )

    proposals = build_task_proposals(suggestions, date_key=date_key)
    run_dir = workspace_root / "agents" / "maintenance" / "runs" / yyyy / mm / dd / run_id
    triage_dir = workspace_root / "agents" / "shared" / "pm_triage" / yyyy / mm / dd
    triage_json_path = triage_dir / f"{triage_id}.json"
    triage_md_path = triage_dir / f"{triage_id}.md"
    validator_passed = bool(validator_results) and all(
        result.get("status") == "passed" for result in validator_results
    )

    input_refs = [
        _input_ref(path, root=root_path, ref_type="knowledge_maintenance_candidate")
        for path in candidate_paths
    ]
    input_refs.append(
        _input_ref(previous_triage_path, root=root_path, ref_type="previous_pm_triage", item_id="PM-TRIAGE-20260620-001")
    )
    if validator_candidate_path is not None:
        input_refs.append(
            _input_ref(
                validator_candidate_path,
                root=root_path,
                ref_type="knowledge_maintenance_candidate",
                item_id=validator_candidate_id,
            )
        )

    triage = _build_pm_triage(
        triage_id=triage_id,
        run_id=run_id,
        work_order_id=work_order_id,
        generated_at=generated_at,
        input_refs=input_refs,
        validator_results=validator_results,
        proposals=proposals,
        validator_candidate_ref=(
            relative_to_root(validator_candidate_path, root_path)
            if validator_candidate_path is not None
            else None
        ),
    )
    findings_payload = {
        "schema_version": "kbm_findings.v1",
        "run_id": run_id,
        "generated_at": generated_at,
        "normalized_suggestion_count": len(suggestions),
        "normalized_suggestions": [asdict(item) for item in suggestions],
        "validator_findings": validator_findings,
        "known_high_priority_refs": [
            ref
            for proposal in proposals
            if proposal.get("priority") in {"high", "urgent"}
            for ref in proposal.get("source_refs", [])
        ],
    }
    task_payload = {
        "schema_version": "kbm_task_proposals.v1",
        "run_id": run_id,
        "generated_at": generated_at,
        "task_proposals": proposals,
    }
    run_manifest = {
        "schema_version": "knowledge_maintenance_run.v1",
        "run_id": run_id,
        "run_type": "kb_result_to_task_triage",
        "work_order_id": work_order_id,
        "agent": "knowledge_maintenance_agent",
        "created_at": generated_at,
        "finished_at": _now_iso(),
        "quanta_data_root": str(root_path),
        "input_refs": input_refs,
        "output_refs": {
            "findings": relative_to_root(run_dir / "findings.json", root_path),
            "task_proposals": relative_to_root(run_dir / "task_proposals.json", root_path),
            "logs": relative_to_root(run_dir / "logs.txt", root_path),
            "pm_triage_json": relative_to_root(triage_json_path, root_path),
            "pm_triage_md": relative_to_root(triage_md_path, root_path),
            "validator_candidate": (
                relative_to_root(validator_candidate_path.parent, root_path)
                if validator_candidate_path is not None
                else None
            ),
        },
        "validator_passed": validator_passed,
        "validator_results": [
            {
                "validator_id": result.get("validator_id"),
                "status": result.get("status"),
                "exit_code": result.get("exit_code"),
                "issue_count": result.get("issue_count"),
            }
            for result in validator_results
        ],
        "proposal_summary": _proposal_summary(proposals),
        "human_review_required": True,
    }

    write_json(run_dir / "run_manifest.json", run_manifest)
    write_json(run_dir / "findings.json", findings_payload)
    write_json(run_dir / "task_proposals.json", task_payload)
    atomic_write_text(run_dir / "logs.txt", _logs(validator_results, validator_passed=validator_passed))
    write_json(triage_json_path, triage)
    atomic_write_text(triage_md_path, _render_triage_markdown(triage))

    return {
        "run_id": run_id,
        "triage_id": triage_id,
        "run_dir": str(run_dir),
        "triage_json": str(triage_json_path),
        "triage_md": str(triage_md_path),
        "validator_candidate": str(validator_candidate_path.parent) if validator_candidate_path else "",
        "proposal_count": len(proposals),
        "validator_passed": validator_passed,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build KBM-result-driven PM task proposals.")
    parser.add_argument("--root", default=str(DEFAULT_ROOT), help="quanta_data root")
    parser.add_argument("--date", default=DEFAULT_DATE_KEY, help="YYYYMMDD or YYYY-MM-DD")
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--triage-id", default=DEFAULT_TRIAGE_ID)
    parser.add_argument("--validator-candidate-id", default=DEFAULT_VALIDATOR_CANDIDATE_ID)
    parser.add_argument("--work-order-id", default=DEFAULT_WORK_ORDER_ID)
    parser.add_argument("--candidate", action="append", dest="candidates", default=[])
    parser.add_argument("--previous-triage", default="")
    parser.add_argument("--validator-timeout", type=int, default=180)
    parser.add_argument("--skip-validators", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = run_maintenance_triage(
        root=args.root,
        candidate_paths=args.candidates or None,
        previous_triage_path=args.previous_triage or None,
        date=args.date,
        run_id=args.run_id,
        triage_id=args.triage_id,
        validator_candidate_id=args.validator_candidate_id,
        work_order_id=args.work_order_id,
        validator_timeout=args.validator_timeout,
        skip_validators=args.skip_validators,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
