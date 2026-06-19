from __future__ import annotations

from quanta_agents.maintenance.kbm_runner import (
    build_task_proposals,
    normalize_suggestions_payload,
)


def test_legacy_items_normalize_to_suggestions() -> None:
    payload = {
        "schema_version": "knowledge_maintenance_suggestions.v1",
        "candidate_id": "CAND-KBM-HERMES-20260619-002",
        "items": [
            {
                "suggestion_id": "KMS-20260619-011",
                "type": "missing_evidence",
                "priority": "high",
                "target_ref": "canonical_documents/news/research_reports/",
                "reason": "canonical_documents business directories are empty.",
                "proposed_action": "Run canonicalizer.",
                "evidence_refs": [],
                "impact_scope": "canonicalization_layer / evidence_pipeline",
                "human_review_required": True,
                "related_files": [],
            }
        ],
    }

    normalized = normalize_suggestions_payload(
        payload,
        source_path=(
            "/Volumes/数字大脑/quanta_data/agent_workspace/candidates/"
            "knowledge_maintenance/2026/06/19/CAND-KBM-HERMES-20260619-002/suggestions.json"
        ),
        root="/Volumes/数字大脑/quanta_data",
    )

    assert len(normalized) == 1
    assert normalized[0].suggestion_type == "missing_evidence"
    assert normalized[0].source_ref == "CAND-KBM-HERMES-20260619-002#KMS-20260619-011"
    assert normalized[0].impact_layer == "canonical_evidence_bridge"


def test_schema_suggestions_normalize_to_same_shape() -> None:
    payload = {
        "schema_version": "knowledge_maintenance_suggestions.v1",
        "candidate_id": "CAND-KBM-CODEX-20260620-001",
        "suggestions": [
            {
                "suggestion_id": "KBM-VALIDATOR-001",
                "suggestion_type": "workflow_code_issue",
                "priority": "medium",
                "target_ref": {"ref_type": "validator", "path": "validator/candidate_contract"},
                "reason": "validator reported an issue",
                "proposed_action": "fix it",
                "evidence_refs": [],
                "impact_scope": {"publications": ["platform_visibility"]},
                "human_review_required": True,
                "status": "candidate",
            }
        ],
    }

    normalized = normalize_suggestions_payload(payload, source_path="/tmp/suggestions.json")

    assert normalized[0].suggestion_type == "workflow_code_issue"
    assert normalized[0].target_ref == "validator/candidate_contract"
    assert normalized[0].impact_scope == "publications:platform_visibility"


def test_high_canonical_and_evidence_items_group_into_bridge_proposal() -> None:
    payload = {
        "candidate_id": "CAND-KBM-HERMES-20260619-002",
        "items": [
            {
                "suggestion_id": "KMS-20260619-011",
                "type": "missing_evidence",
                "priority": "high",
                "target_ref": "canonical_documents/news/research_reports/market_data/filings/data_tables/",
                "reason": "canonical_documents business directories are empty.",
                "proposed_action": "Start canonicalizer service.",
                "evidence_refs": [],
                "impact_scope": "canonicalization_layer / evidence_pipeline",
                "human_review_required": True,
                "related_files": [],
            },
            {
                "suggestion_id": "KMS-20260619-012",
                "type": "missing_evidence",
                "priority": "high",
                "target_ref": "evidence_store/{gold_evidence,evidence_capsules,recall_refs,conflict_sets,annotations}/",
                "reason": "evidence_store core subdirectories are empty.",
                "proposed_action": "Start evidence_extractor service.",
                "evidence_refs": [],
                "impact_scope": "evidence_layer / whole agent reasoning quality",
                "human_review_required": True,
                "related_files": [],
            },
        ],
    }
    normalized = normalize_suggestions_payload(payload, source_path="/tmp/suggestions.json")

    proposals = build_task_proposals(normalized, date_key="20260620")
    bridge = [
        item
        for item in proposals
        if item["dependency"] == "canonicalizer_before_evidence_extractor"
    ]

    assert len(bridge) == 1
    assert bridge[0]["impact_layer"] == "canonical_evidence_bridge"
    assert bridge[0]["priority"] == "high"
    assert bridge[0]["source_refs"] == [
        "CAND-KBM-HERMES-20260619-002#KMS-20260619-011",
        "CAND-KBM-HERMES-20260619-002#KMS-20260619-012",
    ]


def test_pm_rank_prioritizes_bridge_while_preserving_stable_proposal_id() -> None:
    payload = {
        "candidate_id": "CAND-KBM-HERMES-20260619-002",
        "items": [
            {
                "suggestion_id": "KMS-20260619-003",
                "type": "stale_framework",
                "priority": "high",
                "target_ref": "gold/frameworks/碳酸锂-2026",
                "reason": "framework needs revision.",
                "proposed_action": "Revise framework dimensions.",
                "evidence_refs": [],
                "impact_scope": "framework_active_kb / 碳酸锂主线",
                "human_review_required": True,
                "related_files": [],
            },
            {
                "suggestion_id": "KMS-20260619-011",
                "type": "missing_evidence",
                "priority": "high",
                "target_ref": "canonical_documents/news/research_reports/market_data/filings/data_tables/",
                "reason": "canonical_documents business directories are empty.",
                "proposed_action": "Start canonicalizer service.",
                "evidence_refs": [],
                "impact_scope": "canonicalization_layer / evidence_pipeline",
                "human_review_required": True,
                "related_files": [],
            },
        ],
    }

    proposals = build_task_proposals(
        normalize_suggestions_payload(payload, source_path="/tmp/suggestions.json"),
        date_key="20260620",
    )

    assert proposals[0]["dependency"] == "canonicalizer_before_evidence_extractor"
    assert proposals[0]["proposal_id"] == "TP-KBM-20260620-002"
    assert proposals[0]["pm_rank"] == 1
    assert proposals[0]["recommended_sequence"].startswith("01_")


def test_schema_contract_issue_is_not_absorbed_by_conflict_set_keyword() -> None:
    payload = {
        "candidate_id": "CAND-KBM-HERMES-20260619-002",
        "items": [
            {
                "suggestion_id": "KMS-20260619-013",
                "type": "schema_contract_issue",
                "priority": "low",
                "target_ref": "configs/schemas/knowledge_maintenance_suggestions.v1.schema.json",
                "reason": "schema needs suggestion_graph support for conflict_set dependencies.",
                "proposed_action": "Add cross_layer_refs and conflict_set relationship metadata.",
                "evidence_refs": [],
                "impact_scope": "schema_contract",
                "human_review_required": True,
                "related_files": [],
            }
        ],
    }

    normalized = normalize_suggestions_payload(payload, source_path="/tmp/suggestions.json")

    assert normalized[0].impact_layer == "schema_contract"
    assert normalized[0].dependency == "schema_cross_layer_refs"


def test_task_proposal_preserves_candidate_item_source_refs() -> None:
    payload = {
        "candidate_id": "CAND-KBM-HERMES-20260619-002",
        "items": [
            {
                "suggestion_id": "KMS-20260619-001",
                "type": "workflow_code_issue",
                "priority": "high",
                "target_ref": "candidates/framework_optimization/latest/framework-weight-optimization.json",
                "reason": "LLM review failed.",
                "proposed_action": "Block promotion until review passes.",
                "evidence_refs": [],
                "impact_scope": "framework_optimization_pipeline / gold/frameworks",
                "human_review_required": True,
                "related_files": [],
            }
        ],
    }

    proposals = build_task_proposals(
        normalize_suggestions_payload(payload, source_path="/tmp/suggestions.json"),
        date_key="20260620",
    )

    assert proposals[0]["source_refs"] == [
        "CAND-KBM-HERMES-20260619-002#KMS-20260619-001"
    ]
    assert proposals[0]["source_items"][0]["candidate_id"] == "CAND-KBM-HERMES-20260619-002"
