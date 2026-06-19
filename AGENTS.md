# quanta_agents Agent Guide

## Role

`quanta_agents` is the data pipeline and Agent runtime repository. It owns crawlers, canonicalizers, evidence extraction, Prompt Pack building, research agents, QA agents, and knowledge maintenance scans.

It does not own frontend UI, user permissions, human review UX, or active gold promotion.

## Read/Write Boundaries

- Read contracts from `quanta_data/configs` and docs from `quanta_data/docs`.
- Write raw/canonical/evidence/run/candidate/review artifacts according to `quanta_data` schemas.
- Use `GJ_PLATFORM_API_BASE` for platform-facing submissions when available.
- Never write active knowledge directly under `gold`.
- Never store model keys, crawler cookies, data-source passwords, or vendor tokens in code or `quanta_data`.

## Common Commands

```bash
# Install for local development
python3 -m pip install -e ".[dev,crawler,documents,llm,radar,oss]"

# Test
python3 -m pytest tests

# Generate framework registry
quanta-analysis-framework-registry --quanta-root "${GJ_QUANTA_DATA_ROOT:-/Volumes/数字大脑/quanta_data}"

# Run candidate contract from platform repo
python3 /Users/miniquanta/Documents/gj_chainplatform/scripts/check_candidate_contract.py \
  --root "${GJ_QUANTA_DATA_ROOT:-/Volumes/数字大脑/quanta_data}"
```

## Key Paths

- `quanta_agents/core/`: shared config, taxonomy, framework, LLM, and IO utilities.
- `quanta_agents/futures_daily/`: daily commodity research pipeline.
- `quanta_agents/opinion_radar/`: news and market radar.
- `quanta_agents/research_reports/`: WeChat/research report evidence extraction.
- `docs/quanta-agents-architecture.md`: runtime architecture.
- `docs/knowledge-maintenance-agent-runbook.md`: maintenance Agent operations.

## Agent Working Rules

- Start from a `dev_work_order.v1` when doing cross-repo work.
- If a new output shape is needed, add or reference a `quanta_data/configs/schemas` contract first.
- Every long-running command should write a run manifest, output refs, errors, and human review requirements.
- Use relative `quanta_data` paths in emitted JSON.
- Candidate and machine-published outputs must be reviewable and traceable to evidence.
