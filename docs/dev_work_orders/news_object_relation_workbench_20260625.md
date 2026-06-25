# dev_work_order.v1: News Object Relation Workbench

## Objective

Design and implement the next iteration of the Quanta news cognition architecture:
convert news, research reports, market data, expectations, human views, and agent reports into explicit knowledge objects, then connect them through first-class relations anchored on Event, Topic, Logic Node, Asset, Time, and Provenance.

## Scope

- `quanta_agents`: news event/topic/relation pipelines, prompts, QA scans, generated candidate artifacts.
- `gj_chainplatform`: opinion radar UI/API facades and human-facing review surfaces.
- `quanta_data`: read/write candidate artifacts only through existing candidate/review contracts; do not write active `gold`.

## Roles

| Workbench | Owner Role | Responsibility |
| --- | --- | --- |
| `quanta-architect` | Architect | Maintain architecture decisions, task board, acceptance criteria, and integration status. |
| `quanta-frontend` | Frontend engineer | Update `gj_chainplatform` opinion radar and review-facing UI for ordinary users. |
| `quanta-backend` | Backend engineer | Maintain `gj_chainplatform` read facades, API contracts, and smoke tests. |
| `quanta-news-pipeline` | Pipeline engineer | Maintain `quanta_agents` event/topic/object-relation processing and prompt contracts. |
| `quanta-content-qa` | Content QA | Run real-data samples, inspect generated content quality, and file adjustment requests. |

## Current User-Facing Decisions

- News, reports, and data must not be flattened into one generic article type.
- Knowledge objects are distinct: `EventMention`, `CanonicalEvent`, `Evidence`, `AtomicClaim`, `Observation`, `MarketObservation`, `ExpectationObservation`, `HumanClaim`, `DerivedReport`.
- Relationships must be explicit and queryable through `ObjectRelation`-style records.
- Stable objects, evidence assertions, and state snapshots are separate layers.
- User-facing pages should show candidate/review boundaries clearly and avoid internal run/debug fields.

## Active Artifacts

- News flowchart: `docs/news-processing-flowchart.md`
- Relation architecture: `docs/research-object-relation-architecture.md`
- Platform page boundary: `/Users/miniquanta/Documents/gj_chainplatform/docs/project-pages-and-dependencies.md`
- CLI workbench root: `/tmp/quanta_cli_workbench_20260625`

## Acceptance Criteria

- Every implementation task has a source path, output path, review boundary, and verification command.
- Real-data runs report input count, candidate count, topic/event/relation count, and failure/review count.
- Content QA distinguishes confirmed facts from context-only derived summaries.
- Frontend ordinary-user pages hide run IDs, schema names, provider names, and raw JSON/Markdown paths unless in maintenance/debug views.
- Backend remains a facade over `quanta_data` and does not implement long-running agent orchestration.
