# Knowledge Maintenance Agent Runbook

The Knowledge Maintenance Agent keeps `quanta_data` healthy. It detects gaps and contract drift, then writes structured maintenance candidates and PM task proposals. It does not directly repair active knowledge.

The PM dispatch contract is KB-result-driven: PM must assign follow-up work from `PM-TRIAGE-*.json` task proposals, each of which points back to KBM candidate items or validator output. A generic health summary is not enough provenance for a work order.

## 1. Responsibilities

| Check | Purpose | Output |
| --- | --- | --- |
| schema lint | Detect invalid manifests, candidates, review packages, and run records | `workflow_code_issue` or `canonical_quality_issue` |
| path and lineage lint | Detect absolute paths, missing hashes, missing source refs | `workflow_code_issue` |
| evidence coverage | Find reports or machine-published assets without evidence refs | `missing_evidence` |
| review coverage | Find candidates not referenced by review packages | `workflow_code_issue` |
| framework health | Find stale frameworks, unbound indicators, missing claims, excessive unmapped evidence | `stale_framework` or `indicator_mapping` |
| source quality | Detect crawler/API failures, denied pages, low-quality snippets | `crawler_api_issue` or `source_quality_issue` |
| wiki freshness | Detect LLM wiki pages older than their source evidence/gold refs | `publication_update` or `prompt_pack_gap` |

## 2. Inputs

The Agent reads:

- `configs/quanta_data_layout.yaml`
- `configs/workflows/research_memory_state_machine.v1.json`
- `configs/schemas/*.schema.json`
- `raw_manifests`
- `canonical_documents`
- `evidence_store`
- `agent_workspace/runs`
- `agent_workspace/reports`
- `agent_workspace/candidates`
- `agent_workspace/review_packages`
- `gold`
- `indexes`

It may also call the platform API via `GJ_PLATFORM_API_BASE` for API-visible health checks.

## 3. Outputs

Maintenance suggestions go to:

```text
agent_workspace/candidates/knowledge_maintenance/{yyyy}/{mm}/{dd}/CAND-KBM-{AGENT}-{yyyyMMdd}-{NNN}/
├── manifest.json
└── suggestions.json
```

Private run traces go to:

```text
agent_workspace/agents/maintenance/runs/{yyyy}/{mm}/{dd}/RUN-KBM-{yyyyMMdd}-{NNN}/
├── run_manifest.json
├── checks.json
├── findings.json
└── logs.txt
```

If a batch needs human review, create a review package:

```text
agent_workspace/review_packages/{yyyy}/{mm}/{dd}/RP-KBM-{yyyyMMdd}-{NNN}/
```

PM triage and task proposals go to:

```text
agent_workspace/agents/shared/pm_triage/{yyyy}/{mm}/{dd}/PM-TRIAGE-{yyyyMMdd}-{NNN}.json
agent_workspace/agents/shared/pm_triage/{yyyy}/{mm}/{dd}/PM-TRIAGE-{yyyyMMdd}-{NNN}.md
```

Task proposals must preserve source KB references such as `CAND-KBM-...#KMS-...`. The PM Agent may group several suggestions into one work order when they share the same layer or dependency.

The MVP runner writes KB-result-to-task triage with:

```bash
cd /Users/miniquanta/Documents/quanta_agents
python3 -m quanta_agents.maintenance.kbm_runner \
  --root /Volumes/数字大脑/quanta_data \
  --run-id RUN-KBM-20260620-001 \
  --triage-id PM-TRIAGE-20260620-002
```

It reads both legacy `items` and structured `suggestions` in `knowledge_maintenance_suggestions.v1`, records validator command output, then groups findings by priority, impact layer, and dependency.

## 4. Priority Rules

| Priority | Trigger |
| --- | --- |
| high | active/gold risk, missing evidence on visible machine output, schema break blocking platform display, candidate promotion risk |
| medium | stale framework, unbound important indicator, repeated unmapped evidence, review package coverage gap |
| low | doc drift, minor source quality issue, old candidate cleanup, non-blocking lint warning |

High-priority items must include `human_review_required=true` and a concrete proposed action.

## 5. Suggested Nightly Sequence

```bash
# 1. Candidate/platform contract
python3 /Users/miniquanta/Documents/gj_chainplatform/scripts/check_candidate_contract.py \
  --root "${GJ_QUANTA_DATA_ROOT:-/Volumes/数字大脑/quanta_data}"

# 2. Silicon workspace visibility
python3 /Users/miniquanta/.codex/skills/silicon-alpha-research/scripts/validate_silicon_workspace.py \
  --root "${GJ_QUANTA_DATA_ROOT:-/Volumes/数字大脑/quanta_data}/agent_workspace"

# 3. Agent tests when code changed
python3 -m pytest tests
```

The first implementation can wrap these checks and convert failures into `knowledge_maintenance` suggestions.

## 6. Triage Flow

```text
PM assigned checks
  -> findings grouped by priority, impact layer, dependency, and target_ref
  -> CAND-KBM suggestions and PM triage
  -> PM Agent creates repair work orders from KB result sources
  -> engineering Agent fixes code/schema/data
  -> QA Agent verifies
  -> human maintainer approves
```

Do not silently mutate existing candidate or gold files during triage. Create a new candidate, work order, or review package.

PM handoff rule:

1. Read the newest `PM-TRIAGE-{date}-{NNN}.json`.
2. Pick from `task_proposals`, not from free-form agent narration.
3. Copy all `source_refs` into the work order, for example `CAND-KBM-HERMES-20260619-002#KMS-20260619-011`.
4. If PM overrides the proposals, record an explicit human override note and the validator or KB evidence that justified it.
5. Do not dispatch a repair task without KBM source refs, validator output, or a human override note.

## 7. Failure Handling

- If schema loading fails, emit one high-priority `workflow_code_issue`.
- If `quanta_data` is missing, fail fast and do not create partial output elsewhere.
- If platform API is unavailable, continue file-level checks and mark API checks as skipped.
- If a check produces too many findings, summarize counts and include top examples.

## 8. Promotion Boundary

The Knowledge Maintenance Agent may recommend:

- rerun a pipeline,
- fix a schema,
- add missing evidence,
- split or deprecate a framework node,
- create a review package,
- refresh a wiki/index.

It may not:

- write active `gold`,
- delete historical evidence,
- approve its own suggestions,
- overwrite human review records.
