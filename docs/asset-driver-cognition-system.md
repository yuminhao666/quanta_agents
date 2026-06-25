# 资产驱动认知系统

本文定义 `quanta_agents.asset_event_state` 的目标形态：基于 Event Sourcing 的多图谱市场认知状态机。它不是传统知识库，也不是 RAG 问答系统；所有认知状态都必须从 canonical event 和 append-only log 回放得到。

## 系统边界

- `quanta_data` 是记忆层：保存 raw data、canonical event、event log、graph event log、driver state history、candidate snapshot 和 run manifest。
- `quanta_agents` 是认知计算层：负责 event extraction、graph mapping、causal propagation、signal generation、driver state update 和 validation。
- `gj_chainplatform` 是交互层：查询和展示 event、driver、causal path，提供人工反馈，不参与核心状态计算。

禁止路径：

- 不绕过 Event Layer 直接生成 driver 或报告。
- 不用 embedding-only clustering 作为核心逻辑。
- 不让 LLM 直接生成最终市场结论。
- 不混用 driver、theme、signal。
- 不写无法回放的推理路径。

## L1: Event Layer

唯一事实入口是 `asset_canonical_event.v1`。新闻、研报、基本面数据、Polymarket、人工文本都必须先转为同一个 schema。

当前 schema 已统一支持：

- `event_type`: `policy | macro | supply | demand | sentiment | data | research`
- `source_type`: `news | research | data | polymarket | human | legacy_agent_output | unknown`
- `event_time`: 事件发生时间。
- `update_time`: 系统处理时间。
- `effective_time`: 影响开始生效的时间。

`AssetEventStore.insert_events()` 会先写 `event_log`，再更新 `event_table` 当前态。当前态可通过 `AssetEventStore.replay_events()` 从日志重建。

## L2: Graph Layer

系统维护三类图谱日志，不直接不可追踪地改图：

| 图谱 | 当前日志动作 | 职责 |
| --- | --- | --- |
| Event Graph | `event_observed` | 记录发生了什么，为后续去重、聚类和演化跟踪提供事件流 |
| Industry Graph | `event_mapped_to_industry_node` | 将事件映射到资产/产业节点 |
| Causal Graph | `causal_node_activated` | 将事件激活的节点送入因果传播 |

`GraphLayerService.build_graph_events()` 只生成 graph mutation records，`AssetEventStore.record_graph_events()` 将其写入 `graph_event_log`。

## Causal Propagation

新闻和研报不会直接影响资产。当前实现路径是：

```text
Event -> impact node activation -> causal path -> signal update
```

`CausalPropagationEngine` 会对每个 event 的 `impact_nodes` 生成 observed path，并沿配置边生成 propagated path。每条 path 记录：

- `event_id`
- `steps`
- `path_signal`
- `path_confidence`
- `final_node`
- `assumptions`

每一跳都会乘以 edge confidence 和 depth decay。多路径并存，不强行合并。

## Signal

Signal 是 driver 更新的唯一输入。每条 signal 必须保留：

- `event_ids`
- `causal_path_ids`
- `driver_id`
- `framework_node`
- `direction`
- `magnitude`
- `confidence`
- `effective_time`

`AssetEventStore.insert_signals()` 会写 `signal_table` 和 `event_log`。后续平台可以从 signal 回查 causal path，再回查 canonical event 和 raw source。

## Driver State Machine

`DriverStateMachine.update_from_signals()` 是当前主路径。旧的 `update_drivers(events)` 仅保留兼容。

Driver state 使用有符号强度：

```text
strength: -1.0 ~ +1.0
trend: strengthening | weakening | stable | reversing
confidence: 0.0 ~ 1.0
```

状态更新规则：

```text
Driver(t) = clip(decay(Driver(t-1)) + signed_signal, -1, 1)
```

其中：

```text
signed_signal = direction * magnitude * confidence
```

同向 signal 进入 `supporting_signals`，反向 signal 进入 `contradicting_signals`。状态保留 `event_id -> signal_id -> driver_id` 和 causal path traceability。

## Pipeline

当前主流程：

```text
Raw Data
  -> EventCanonicalAdapter / EventExtractor
  -> AssetEventStore.event_log + event_table
  -> GraphLayerService + graph_event_log
  -> CausalPropagationEngine
  -> causal_path_table
  -> signal_table
  -> DriverStateMachine.update_from_signals
  -> driver_state_table
  -> ThemeNarrativeService
```

候选输出会包含：

- `events.json`
- `graph_events.json`
- `causal_paths.json`
- `signals.json`
- `drivers.json`
- `narratives.json`
- `manifest.json`

## 当前限制

- Industry Graph 目前是 event impact node 的最小映射骨架，不是完整产业链本体。
- Causal Graph 使用内置最小边集，后续应迁移到 quanta_data 可审核配置。
- Multi-source validation 在 demo pipeline 已有一版，在正式 pipeline 中还需要提升为独立 service。
- Polymarket 目前通过 adapter 归一到 event/source_type，尚未单独实现概率变化到 driver 的专用权重。

## 验证

核心行为由 `tests/test_asset_event_state.py` 覆盖：

- research/data source 进入同一个 canonical event schema。
- event log 可以 replay current event state。
- 三图谱日志、causal path 和 signal 表被写入。
- driver 可以从 signal 而不是 raw event 更新。
- narrative 只读取 driver state。
