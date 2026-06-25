# 资产事件状态机

`quanta_agents.asset_event_state` 把 `quanta_data` 从多源资料存储推进到资产级事件流处理：

```text
raw / canonical / candidate input
  -> Event Canonical Layer
  -> Driver State Machine
  -> driver-only Theme / Narrative Layer
```

更完整的 Event Sourcing + Event/Industry/Causal 三图谱目标形态见
[`docs/asset-driver-cognition-system.md`](asset-driver-cognition-system.md)。
数据源、处理阶段、产物合约和平台 Trigger Graph 读模型见
[`docs/asset-driver-cognition-data-flow.md`](asset-driver-cognition-data-flow.md)。

## 边界

- 代码归属 `quanta_agents`，因为抽取、driver 更新和叙事是数据处理与 Agent runtime。
- 平台仓库 `gj_chainplatform` 只读取候选、索引和状态，用于展示、审核和权限。
- 原始 raw table 和 raw object 不修改；只新增索引和 candidate/run 产物。
- 不写 `gold`；所有输出保持 `candidate` 和 `review_required`。

## 存储

运行时会初始化：

```text
indexes/asset_event_state/events.sqlite3
```

核心表：

| 表 | 用途 |
| --- | --- |
| `event_table` | canonical event 主表，保留 `source_id/source_ref` |
| `event_node_mapping` | event 到 asset schema node 的映射 |
| `driver_state_table` | Driver State Machine 的当前状态，是 narrative 的唯一输入 |
| `driver_events` | event 到 driver 的归属、impact 和时间线 |
| `driver_state_history` | driver strength/trend 的状态变化 |
| `theme_narrative_outputs` | 只基于 driver 生成的叙事输出 |
| `event_cluster_table` | legacy 兼容表，不作为主 narrative 来源 |
| `cluster_events` | legacy cluster 归属和结构化评分明细 |
| `cluster_state_history` | legacy cluster 生命周期变化 |
| `narrative_outputs` | legacy cluster narrative 表 |

候选产物写入：

```text
agent_workspace/candidates/asset_event_state/{yyyy}/{mm}/{dd}/CAND-ASSET-EVENT-STATE-*/
agent_workspace/runs/asset_event_state/{yyyy}/{mm}/{dd}/RUN-*/
```

## 统一收敛规则

旧模块不删除，但它们的输出必须先通过 `EventCanonicalAdapter`：

| legacy 输出 | 收敛目标 |
| --- | --- |
| `opinion_radar.news_logic` events | `asset_canonical_event.v1` |
| `research_reports.wechat_evidence` evidence | `asset_canonical_event.v1` |
| `signal_mapping.incremental_state` events/themes | `asset_canonical_event.v1` |
| `signal_mapping.research_signal` | `asset_canonical_event.v1` |
| `polymarket_daily.hotspots` | `asset_canonical_event.v1` |

Event schema 只允许：

```text
policy | macro | supply | demand | sentiment
```

legacy `positioning` 会收敛为 `sentiment`，不会作为新 event type 继续扩散。

## Driver 更新规则

Driver 使用 deterministic state machine，不由 LLM 判定：

```text
Driver(t) =
Driver(t-1)
+ supporting_event_signal
- contradicting_event_signal
+ decay
```

`driver_id` 由 `asset + primary impact_node` 稳定生成。每个 driver 维护：

- `state.strength`：0 到 1。
- `state.trend`：`strengthening | weakening | reversing`。
- `timeline`：event_id、impact、timestamp、source_id。
- `supporting_events` / `contradicting_events`。

## Legacy 聚类规则

保留 legacy cluster 能力用于兼容和对照。聚类禁止 embedding-only，当前实现只用结构化评分：

```text
score =
0.4 * asset_match
+ 0.25 * driver_match
+ 0.2 * signal_alignment
+ 0.15 * time_decay
```

legacy cluster 归属前还会做 dedup gate：

- asset 一致。
- event_type 相同或属于相近 driver group。
- signal direction 一致。
- 时间窗口在 72h 内。

## CLI

```bash
quanta-asset-event-state \
  --root "${GJ_QUANTA_DATA_ROOT:-/Volumes/数字大脑/quanta_data}" \
  --input /path/to/raw_or_event_payload.json \
  --date 20260620
```

输入可以是单个 object、object list，或 `{ "items": [...] }`。每个 item 至少需要
`text/content/summary` 之一，也可以直接提供已结构化的 `event` 或 `events`。

LLM 只允许参与两个受控位置：

- `--use-llm-extractor`：raw text -> canonical event。
- `--use-llm-narrative`：driver state -> narrative。

Narrative 层不会读取 raw 文本，也不会跳过 event/driver 层。
