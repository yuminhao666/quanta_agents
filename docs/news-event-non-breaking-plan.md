# News Event Sidecar Non-Breaking Plan

## Scope

This plan implements only the news-event sidecar:

```text
Raw News Item
  -> normalization
  -> exact / near dedup
  -> syndication group
  -> EventMention
  -> CanonicalEvent
  -> Persistent Topic
  -> EventAssetLink / EventFrameworkNodeLink
  -> legacy news_logic-compatible view
```

It does not migrate historical news, handle research-report Atomic Claims,
introduce Neo4j, rewrite `opinion_radar`, write `gold`, or change the
`gj_chainplatform` formal read contract.

## Storage Boundary

The new durable store is a separate SQLite sidecar:

```text
indexes/news_event_catalog/news_events.sqlite3
```

Runtime envs:

```text
QUANTA_NEWS_EVENT_PIPELINE_ENABLED=true
QUANTA_NEWS_EVENT_CATALOG_PATH=/path/to/news_events.sqlite3
```

If `QUANTA_NEWS_EVENT_PIPELINE_ENABLED` is absent or false, the old
`opinion_radar` export path stays exactly as it is.

## Tables

Phase 1 creates these tables with migration metadata and idempotent upserts:

- `news_item`
- `syndication_group`
- `syndication_member`
- `event_mention`
- `canonical_event`
- `canonical_event_member`
- `event_relation`
- `event_asset_link`
- `event_framework_node_link`
- `persistent_topic`
- `topic_alias`
- `topic_membership`
- `topic_state_snapshot`
- `agent_run`
- `schema_migration`

All batch writes run in a transaction. `--dry-run` builds artifacts and metrics
without mutating SQLite.

## Configuration

The scoring policy is loaded in this order:

1. `quanta_data/configs/policies/news_event_clustering.v1.yaml`
2. bundled fallback `quanta_agents/config/news_event_clustering.v1.yaml`

Default score weights:

```text
subject_object_match 0.22
action_match 0.18
entity_overlap 0.15
event_type_match 0.10
location_match 0.10
time_proximity 0.10
semantic_similarity 0.08
stage_compatibility 0.04
source_reference_match 0.03
```

Default gates:

- `score >= 0.86`: automatic `SAME_EVENT`
- `0.65 <= score < 0.86`: LLM pair judge when enabled, otherwise conservative
  new event or relation fallback
- `score < 0.65`: create a new canonical event

## CLI

Add these entry points:

```text
quanta-news-event-init
quanta-news-event-batch
quanta-news-event-show
quanta-news-event-list
quanta-news-topic-list
quanta-news-topic-show
quanta-news-event-backfill
```

`quanta-news-event-batch` supports:

```text
--date YYYY-MM-DD
--hours N
--limit N
--no-llm
--dry-run
--root PATH
```

Backfill defaults to a small limit and never performs full-history processing
without explicit user intent.

## Legacy Adapter

The sidecar produces `legacy_news_logic_events` from:

```text
CanonicalEvent
  + EventAssetLink
  + EventFrameworkNodeLink
```

This compatibility view is allowed to duplicate rows by asset because old
`news_logic` consumers expect asset-level rows. The durable event catalog remains
one canonical event per real-world event.

## Acceptance Tests

New focused tests cover:

- stable news IDs and idempotent upsert;
- exact duplicate and near duplicate syndication grouping;
- one multi-asset real-world event with at least three asset links;
- `SAME_EVENT` merge across media;
- `CONTRADICT` relation between rumor and official denial;
- cross-day topic continuity;
- no-key LLM fallback;
- old `news_logic` shape staying intact.

The full repository test command remains:

```bash
python3 -m pytest tests
```

## Rollback

Rollback is intentionally simple:

1. unset `QUANTA_NEWS_EVENT_PIPELINE_ENABLED`;
2. stop using the `quanta-news-event-*` CLIs;
3. remove only generated sidecar artifacts under
   `agent_workspace/candidates/news_events/` and the SQLite file under
   `indexes/news_event_catalog/` if a clean demo reset is needed.

No active `gold` data or platform UI contract is modified by this phase.
