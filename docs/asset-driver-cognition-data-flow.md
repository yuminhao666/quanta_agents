# 资产驱动认知系统数据处理逻辑

本文整理“新闻 / 研报 / 数据 / Polymarket / 人工文本 -> 事件溯源 -> 三图谱 -> signal update -> driver state -> 报告 / 工作台图谱”的数据处理逻辑。它是 `quanta_agents.asset_event_state` 的工程执行口径，补充系统定义文档 [`asset-driver-cognition-system.md`](asset-driver-cognition-system.md) 和 demo 运行文档 [`asset-event-state-demo.md`](asset-event-state-demo.md)。

## 核心原则

这套系统不是 RAG，也不是把多源材料直接总结成结论。所有信息必须先进入唯一 Event Layer，再通过图谱传播和状态机形成可回放的认知状态。

不可破坏的规则：

- 所有事实入口必须统一为 `asset_canonical_event.v1`。
- 所有状态变化必须有 append-only log，可由 replay 重建。
- 新闻、研报、数据不能直接改 driver，只能先激活图谱节点，再产生 causal path 和 signal update。
- Driver state 只能由 signal 更新，报告只能读取 driver state 和可追溯证据。
- `gj_chainplatform` 只展示和人工反馈，不做抽取、传播、状态更新或 LLM 推理。

## 总体数据流

```text
Raw Data
  -> Source Adapter
  -> Event Extraction / Normalization
  -> Event Log + Event Table
  -> Event Graph Update
  -> Industry Graph Mapping
  -> Causal Graph Propagation
  -> Signal Update
  -> Driver State Machine
  -> Multi-source Validation
  -> Narrative / Human Report
  -> gj_chainplatform Trigger Graph / Workbench
```

对应到工作台图谱时，最小可视化链路是：

```text
Event -> Activated Node -> Causal Path -> Signal -> Driver
```

每条边都必须能回查：

- `event_id`
- `path_id`
- `signal_id`
- `driver_id`
- raw/source reference

## 模块边界

| 模块 | 职责 | 禁止事项 |
| --- | --- | --- |
| `quanta_data` | 存 raw data、event log、graph event log、signal、driver history、candidate snapshot、run manifest | 不推理、不生成结论、不直接改 driver |
| `quanta_agents` | source adapter、event extraction、graph mapping、causal propagation、signal generation、driver update、validation、report candidate | 不绕过 Event Layer，不写 `gold`，不混用 driver/theme/signal |
| `gj_chainplatform` | 读取 candidate/read model，展示 Trigger Graph、driver、event、causal path，收集人工反馈 | 不参与核心计算，不运行 LLM 生成市场结论 |

## 数据源与入口

| 数据类别 | 输入位置 | Adapter 输出 | 备注 |
| --- | --- | --- | --- |
| 新闻 / 快讯 | `agent_workspace/candidates/opinion_radar/news_logic/latest/news-logic.json`、同目录日期分区 `news-logic-*.json`，原始表默认 `jin10_flash` | `source_type=news` event | 依赖现有 `news_logic` 的资产和 framework node 映射，低置信映射进入 `needs_review` |
| 微信研报证据 | `agent_workspace/candidates/research_reports/wechat_evidence/latest/wechat-research-evidence.json` | `source_type=research` event | 一篇研报可拆多条 evidence/event，保留 article/profile 引用 |
| 单篇研报结构化 profile | `canonical_documents/research_reports/structured_profiles/hzzhqx_wechat/{yyyy}/{mm}/{dd}/RREP-PROFILE-*.json` | research observation | 作为研报 evidence 汇总的上游，不直接更新 driver |
| 基本面数据 | MySQL `dzq_data`，字典 `gj_chainplatform/backend/app/data_agent/data_dict.json` | `source_type=data` event | 指标序列先转成 observation，再变成 data event |
| 期货行情 / 持仓 | MySQL `tushare_fut_daily`、`tushare_fut_holding`、`tushare_fut_wsr`、`tushare_fut_active` | market/fundamental observation | 缺环境变量时记录 `errors.jsonl`，不阻塞主链路 |
| Polymarket | `polymarket_daily.hotspots` candidate / report | `source_type=polymarket` event | 概率变化是事件，不是结论；权重需要独立配置 |
| 人工文本 | 平台观点、研究笔记、人工输入 | `source_type=human` event | 需要保留 author/account/content ref |
| 品种框架 | active knowledge framework、framework candidate、analysis framework registry | mapping 骨架 | 用于 `impact_nodes`、driver id、causal graph 节点命名 |

## Canonical Event

所有 source adapter 的共同输出是 canonical event。最小字段如下：

```json
{
  "event_id": "string",
  "asset": "COPPER",
  "event_type": "policy | macro | supply | demand | sentiment | data | research",
  "summary": "事件摘要，不写投资结论",
  "key_facts": ["..."],
  "signal_vector": {
    "price": 0.0,
    "supply": 0.0,
    "demand": 0.0,
    "macro": 0.0,
    "sentiment": 0.0
  },
  "impact_nodes": ["FWK-CU-20260616::宏观/货币政策/美联储降息路径"],
  "confidence": 0.0,
  "source_type": "news | research | data | polymarket | human",
  "event_time": "ISO8601",
  "update_time": "ISO8601",
  "effective_time": "ISO8601",
  "source_ref": {
    "source_name": "opinion_radar.news_logic",
    "path": "agent_workspace/candidates/...",
    "id": "..."
  }
}
```

时间字段不能混用：

- `event_time`: 事情发生或材料发布时间。
- `update_time`: 系统处理和入库时间。
- `effective_time`: 事件开始影响 driver 的时间，用于 time decay。

## Event Sourcing

状态不能直接覆盖。写入顺序必须是：

```text
event_log append
  -> event_table current view
  -> graph_event_log append
  -> causal_path_table / signal_table
  -> driver_state_history append
  -> driver_state_table current view
```

关键约束：

- `event_table` 是 replay 后的当前视图，不是事实来源。
- `driver_state_table` 是 replay 后的当前状态，不是推理起点。
- `manifest.json` / `run_manifest.json` 必须记录输入、输出、参数、错误和复现命令。
- 所有 candidate 输出保持 reviewable，不写 `gold`。

## 三张图谱

### Event Graph

职责是记录“发生了什么”，以及事件之间的去重、聚类、演化关系。

当前产物：

- `atomic_events.jsonl`
- `event_groups.jsonl`
- `graph_event_log`

典型动作：

- `event_observed`
- `event_grouped`
- `event_deduplicated`
- `event_evolved`

### Industry Graph

职责是表达现实经济结构和 framework node 映射，包括产业链、上下游、替代、成本传导。

当前映射骨架来自：

- `active_knowledge/research_frameworks/commodities/*.json`
- `agent_workspace/candidates/analysis_framework/latest/analysis-framework-registry.json`
- `agent_workspace/candidates/analysis_framework/latest/indicator-event-catalog.json`
- `gold/reference_data/assets/futures_assets.v1.json`

典型动作：

- `event_mapped_to_industry_node`
- `indicator_mapped_to_framework_node`
- `asset_alias_resolved`

### Causal Graph

职责是表达驱动关系和多跳传播。边结构必须显式保留：

```json
{
  "from": "node_a",
  "to": "node_b",
  "weight": 0.0,
  "polarity": 1,
  "delay": "hours/days",
  "confidence": 0.0
}
```

传播约束：

- 每一跳降低置信度。
- 每条 path 独立保存，不强行合并。
- 低置信路径只进入 `needs_review` 或 `low_confidence`，不能强更新 driver。
- 超过最大深度的路径截断，并记录假设。

## Causal Propagation

传播输入是 canonical event 的 `impact_nodes` 或 adapter 产出的 direct node。

输出 causal path：

```json
{
  "path_id": "string",
  "event_id": "string",
  "target_asset": "COPPER",
  "steps": [
    {
      "from": "node_a",
      "to": "node_b",
      "weight": 0.7,
      "polarity": 1,
      "confidence_after_hop": 0.56
    }
  ],
  "path_signal": 0.35,
  "path_confidence": 0.56,
  "final_node": "copper_price_support",
  "path_type": "direct | propagated",
  "assumptions": []
}
```

计算口径：

```text
path_confidence =
event_confidence
* edge_confidence_product
* depth_decay
* mapping_confidence
```

demo 当前使用：

```text
depth_decay = 0.8 ^ (hop_count - 1)
max_propagation_depth = 4
```

## Signal Update

Signal 是 driver state 的唯一输入。每条 signal 都必须能追溯到 event 和 causal path。

```json
{
  "signal_id": "string",
  "asset": "COPPER",
  "framework_node": "FWK-CU-20260616::宏观/流动性/主要经济体货币政策外溢",
  "driver_id": "string",
  "direction": 1,
  "magnitude": 0.35,
  "confidence": 0.92,
  "source_type": "news",
  "event_ids": ["event_id"],
  "causal_path_ids": ["path_id"],
  "relation": "supports | contradicts",
  "effective_time": "ISO8601",
  "status": "accepted | low_confidence | needs_review",
  "traceability": {
    "source_id": "raw/source id",
    "source_type": "news",
    "summary": "source summary",
    "final_node": "copper_price_support",
    "hop_count": 0
  }
}
```

Signal 权重由以下因素共同决定：

```text
signed_signal =
direction
* magnitude
* confidence
* source_weight
* time_decay
* duplication_penalty
* status_weight
```

其中 source weight 需要区分新闻、研报、基本面数据、Polymarket 和人工文本；同事件转载或同事件组不能重复提高 source diversity。

## Driver State Machine

Driver 是认知输出的核心状态，不等于 theme，也不等于 signal。

```json
{
  "driver_id": "string",
  "asset": "COPPER",
  "state": {
    "strength": -1.0,
    "trend": "strengthening | weakening | stable | reversing",
    "confidence": 0.0
  },
  "supporting_signals": [],
  "contradicting_signals": [],
  "support_score": 0.0,
  "contradiction_score": 0.0,
  "as_of": "timestamp",
  "change_explanation": "string"
}
```

更新规则：

```text
Driver(t) =
clip(previous_strength * persistence + sum(weighted_signal), -1, 1)
```

趋势判断：

- `strengthening`: 当前强度同向增强。
- `weakening`: 当前强度同向减弱。
- `reversing`: 当前方向相对上一状态翻转。
- `stable`: 变化低于阈值。

Driver confidence 不是单条信号置信度，而是由 signal confidence、source diversity、conflict penalty、validation adjustment 综合得到。

## Multi-source Validation

同一个 driver 需要检查多源一致性。输出状态：

- `confirmed`: 新闻、研报、数据等至少两个独立来源方向一致。
- `conflicted`: 存在方向相反或口径冲突的关键证据。
- `partial_confirmed`: 部分来源支持，但覆盖不足。
- `insufficient`: 只有单一来源或证据太弱。

Validation 不删除冲突证据，只降低 confidence 或增加 warning。典型输出：

```json
{
  "driver_id": "string",
  "agreement": "confirmed | conflicted | partial_confirmed | insufficient",
  "supporting_sources": ["news", "research_report", "fundamental_data"],
  "contradicting_sources": [],
  "independent_source_count": 3,
  "key_conflicts": [],
  "confidence_adjustment": 0.08
}
```

## Narrative / Report

报告是最后一层，不能直接从 raw data 或 LLM 自由生成结论。报告输入只能是：

- driver state
- signal traceability
- causal paths
- validation results
- event/source references

输出必须明确：

- 当前核心 driver 是什么。
- 哪些事件强化或削弱 driver。
- 影响路径是什么。
- 多源信息是否一致。
- 当前市场处于什么认知状态。
- 哪些点需要人工复核。

报告不能输出无追溯的投资指令。

## 运行产物

正式 candidate 目标路径：

```text
agent_workspace/candidates/asset_event_state/latest/
agent_workspace/candidates/asset_event_state/{yyyy}/{mm}/{dd}/CAND-ASSET-EVENT-STATE-*/
agent_workspace/runs/asset_event_state/{yyyy}/{mm}/{dd}/RUN-*/
indexes/asset_event_state/events.sqlite3
```

正式 candidate 至少包含：

```text
events.json
graph_events.json
causal_paths.json
signals.json
drivers.json
narratives.json
manifest.json
```

当前 demo 路径：

```text
agent_workspace/candidates/asset_event_state_demo/latest_manifest.json
agent_workspace/candidates/asset_event_state_demo/RUN-*/
```

demo run 至少包含：

```text
run_manifest.json
selected_inputs.json
filtered_news.jsonl
atomic_events.jsonl
event_groups.jsonl
causal_activations.jsonl
causal_paths.jsonl
signal_updates.jsonl
driver_state_before.json
driver_state_after.json
validation_results.json
human_report.md
demo_report.md
audit_samples.md
execution_log.jsonl
errors.jsonl
```

## 平台展示 Read Model

`gj_chainplatform` 通过只读 API 展示：

```text
GET /api/v1/asset-event-state?asset=COPPER&date=YYYYMMDD
```

API 读正式 candidate；如果正式产物不存在，可回退读取 demo run。平台返回 read model：

- `events`
- `graph_events`
- `causal_paths`
- `signals`
- `drivers`
- `validation_results`
- `human_report`
- `source_paths`

分析工作台中的 Trigger Graph 应按以下层次展示：

```text
Event -> Activated Node -> Causal Path -> Signal -> Driver
```

图谱节点类型：

| 节点类型 | 数据来源 | 展示含义 |
| --- | --- | --- |
| Event | `events` / `atomic_events.jsonl` | 事实入口 |
| Activated Node | `impact_nodes` / `direct_nodes` / `causal_activations` | 被事件激活的框架或因果节点 |
| Causal Path | `causal_paths` | 传播路径和置信度衰减 |
| Signal | `signals` / `signal_updates.jsonl` | driver 更新输入 |
| Driver | `drivers` / `driver_state_after.json` | 当前认知状态 |

平台展示可以做聚合和筛选，但不能改写源字段含义。

## 工程 Agent 分工建议

| Agent | 任务 | 输入 | 输出 |
| --- | --- | --- | --- |
| Source Adapter Agent | 读取新闻、研报、数据、Polymarket、人工文本，统一成 observation | raw/candidate/source API | normalized source observation |
| Event Agent | 生成 canonical event，写 event log | observation | `asset_canonical_event.v1` |
| Graph Agent | 更新 Event Graph、Industry Graph、Causal Graph mutation | canonical event + framework registry | graph events |
| Propagation Agent | 多跳传播，生成 causal path | graph events + causal graph edges | causal paths |
| Signal Agent | 将 path 映射为 signal update | causal paths + event map | signal updates |
| Driver Agent | 更新 driver state | signal updates + previous state | driver state + history |
| Validation Agent | 多源验证、冲突检测 | driver + signals + sources | validation results |
| Report Agent | 生成 human report | driver + validation + traces | report markdown |
| Platform Agent | 维护 read API 和 Trigger Graph UI | candidate/read model | workbench visualization |

## 当前限制与下一步

当前限制：

- demo 的 causal graph 仍是配置化最小边集，不是完整产业链因果本体。
- 正式 pipeline 的 multi-source validation 还需要从 demo 中抽成独立 service。
- Polymarket 概率变化到 driver 的权重还需要专用配置。
- 部分新闻映射依赖现有 `news_logic`，股票题材新闻需要人工复核。
- 基本面文本 observation 没有完整历史分位数，强度计算应保守。

优先级：

1. 固化 `asset_canonical_event.v1` 和 `asset_driver_state.v1` schema。
2. 将 demo 的 validation、causal edge config、source weights 抽到正式 pipeline。
3. 将 causal graph edge 配置迁入 `quanta_data` 可审核目录。
4. 为 `gj_chainplatform` Trigger Graph 增加 path/event/signal drilldown。
5. 增加 replay smoke test，证明 current driver state 可从 event/signal/history 重建。
