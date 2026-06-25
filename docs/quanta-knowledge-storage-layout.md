# Quanta Knowledge Storage Layout

本文记录 Quanta 知识库的长期存放思路。核心结论是：

```text
quanta_data  = raw data + structured objects + state truth
quanta_wiki  = LLM 编译出的可读知识投影
quanta_agents = 编译器、维护器、检查器、回放器
gj_chainplatform = 查询、浏览、图谱展示和人工审核界面
```

不要把 Wiki 当成事实库，也不要把每日 JSON 快照当成长期知识节点。结构化层负责可回放和可计算，Wiki 负责让人和 Agent 读得懂。

最关键的设计不是按新闻、研报、数据、人工文章、Agent 文章分别建长期知识目录，而是把不同来源转化成不同认知角色的对象：

```text
多源原始数据
  -> 标准文档与数据记录
  -> Event / Claim / Evidence / Observation / HumanClaim
  -> TopicMembership / LogicNodeMembership / AssetFrameMembership
  -> TopicState / LogicState / DriverState
  -> DerivedReport / WikiPage / AgentAnswer
```

来源只是 provenance；认知对象才是知识库的组织方式。

运行编排和职责边界见 [quanta-cognitive-langgraph-flow.md](quanta-cognitive-langgraph-flow.md)。该文档规定：LangGraph 负责编排和语义 Agent 调用，确定性服务负责去重、计分、传播、状态更新和写入门控。

## Layer Model

| 层级 | 名称 | 主要对象 | 真源位置 | 说明 |
| --- | --- | --- | --- | --- |
| L0 | 原始数据层 | 新闻、研报、PDF、MySQL 时间序列、Polymarket 快照、人工文本、Agent 原始输入输出 | `raw_objects`、`raw_manifests`、外部 MySQL | 不推理，只保存不可逆材料和抓取记录 |
| L1a | 标准文档与知识原子层 | `Document`、`DocumentChunk`、`Event`、`Claim`、`Evidence`、`Observation`、`HumanClaim` | `canonical_objects`、`documents`、`knowledge_atoms` | 把来源转为可引用、可验证的认知对象 |
| L1b | 关系与状态对象层 | `MarketTopic`、`TopicMembership`、`LogicNode`、`GraphEdge`、`TopicState`、`DriverState`、`AssetSignal`、`ValidationState` | `canonical_objects`、`relations`、`states`、`snapshots` | 关系对象单独存；当前状态必须能由原子和关系重算 |
| L2 | LLM Wiki 编译层 | 资产页、主题页、驱动页、逻辑边页、矛盾页、研究结论页 | `quanta_wiki` 或 `wiki_projection` | 给人和 Agent 阅读，不作为计权证据源 |
| L3 | 索引与计算层 | 全文索引、向量索引、关系索引、状态查询、时间线查询 | `indexes` | 从 L0/L1/L2 可重建 |
| L4 | Agent / Alpha 层 | 检索、推理、报告、审核、反馈 | `quanta_agents`、`gj_chainplatform` | 不直接篡改真源，所有高影响变更进 review gate |

## Source Types And Cognitive Roles

不同来源进入系统后，不能都被当成同一种文档：

| 来源 | 主要转化对象 | 认知角色 |
| --- | --- | --- |
| 新闻快讯 | `Event`、`EventReport` | 描述发生了什么 |
| 研报、年报 | `Claim`、`Evidence` | 提出逻辑主张并给出论据 |
| 行情数据 | `MarketObservation` | 观测市场反应 |
| 基本面数据 | `FundamentalObservation` | 验证供需、库存、成本等逻辑 |
| 预测市场 | `ExpectationObservation` | 表示市场预期，不代表真实概率或事实 |
| 人工收藏文章 | `Document`、`Claim`、`Evidence` | 外部研究资料 |
| 人工观点 | `HumanClaim`、`ResearchNote` | 人的判断或假设 |
| Agent 文章 | `DerivedReport` | 基于已有对象生成的派生结果 |

必须保留四条边界：

- 新闻不等于事实本身；
- 研报观点不等于事实；
- 预测市场不等于事实；
- Agent 报告不等于新证据。

每个知识原子都应区分：

```json
{
  "truth_status": "unverified | partially_confirmed | confirmed | disputed | false",
  "market_attention": 0.88,
  "epistemic_status": "fact | claim | opinion | expectation | derived",
  "source_independence_key": "source-or-origin-id"
}
```

未经确认但被市场交易的信息，可以提高 `market_attention` 和 topic heat，但不能直接改写为已验证事实。

## Recommended Directories

下面是目标形态，不要求一次迁移完成。路径默认相对 `GJ_QUANTA_DATA_ROOT`。

```text
raw_objects/
raw_manifests/

canonical_objects/
  documents/
  document_chunks/
  events/
  claims/
  evidence/
  observations/
  market_topics/
    registry/topic-registry.json
    registry/asset-topic-index.json
    registry/driver-topic-index.json
    topics/MKT_TOPIC_US_IRAN_HORMUZ.json
    topics/MKT_TOPIC_FED_RATE_PATH.json
  logic_graph/
    nodes/
    edges/

relations/
  topic_memberships/{yyyy}/{mm}/{dd}/topic-memberships.jsonl
  report_dependencies/{yyyy}/{mm}/{dd}/report-dependencies.jsonl

event_logs/
  market_topics/{yyyy}/{mm}/{dd}/topic-events.jsonl
  graph/{yyyy}/{mm}/{dd}/graph-events.jsonl
  drivers/{yyyy}/{mm}/{dd}/driver-events.jsonl

states/
  market_topics/latest/{topic_id}.json
  market_topics/history/{yyyy}/{mm}/{dd}/topic-states.jsonl
  drivers/latest/{driver_id}.json
  drivers/history/{yyyy}/{mm}/{dd}/driver-states.jsonl

evidence_links/
  market_topics/{yyyy}/{mm}/{dd}/topic-evidence.jsonl
  drivers/{yyyy}/{mm}/{dd}/driver-evidence.jsonl

snapshots/
  market_topics/{yyyy}/{mm}/{dd}/market-topic-snapshot.json
  graphs/{yyyy}/{mm}/{dd}/causal-graph-snapshot.json

wiki_projection/
  index.md
  SCHEMA.md
  assets/
  topics/
  drivers/
  logic/
  events/
  tensions/
  reports/

indexes/
  fulltext/
  vector/
  graph/
  timelines/
```

`agent_workspace/candidates/...` 仍用于候选产物和 run 输出。通过 review 后，稳定对象再进入 `canonical_objects`、`event_logs`、`states` 或 `wiki_projection`。

## Document And Knowledge Atom Objects

标准文档对象只描述来源和版本，原文仍指向 blob：

```json
{
  "schema_version": "document.v1",
  "document_id": "DOC-...",
  "document_type": "news | report | annual_report | note | agent_article",
  "source_id": "source_xxx",
  "title": "...",
  "content_uri": "quanta://raw_objects/...",
  "content_hash": "sha256:...",
  "published_at": "ISO8601",
  "event_time": "ISO8601",
  "ingested_at": "ISO8601",
  "author_type": "external | human | agent",
  "language": "zh",
  "version": 1
}
```

`DocumentChunk` 保留页码、段落、offset 和原文位置，让 Event / Claim / Evidence 能指回精确来源。

知识原子示例：

```json
{
  "schema_version": "event.v1",
  "event_id": "EV-...",
  "summary": "霍尔木兹海峡航运风险上升",
  "event_type": "geopolitics",
  "event_time": "ISO8601",
  "entities": ["霍尔木兹海峡", "油轮"],
  "truth_status": "partially_confirmed",
  "market_attention": 0.88,
  "source_document_ids": ["DOC-1", "DOC-2"]
}
```

```json
{
  "schema_version": "claim.v1",
  "claim_id": "CLAIM-...",
  "claim_type": "causal_hypothesis",
  "subject_node": "global.energy_supply_risk",
  "predicate": "increases",
  "object_node": "global.energy_price",
  "conditions": ["运输中断持续"],
  "time_horizon": "days_to_weeks",
  "stance": "support",
  "epistemic_status": "claim",
  "source_document_id": "DOC-REPORT-..."
}
```

```json
{
  "schema_version": "observation.v1",
  "observation_id": "OBS-...",
  "observation_type": "fundamental | market | expectation",
  "series_id": "shfe_copper_inventory",
  "value": 12345,
  "previous_value": 13500,
  "observation_time": "ISO8601",
  "release_time": "ISO8601",
  "mapped_node_id": "asset.copper.inventory.refined"
}
```

## Market Topic As Dual Node

市场主题不是一次性新闻聚类，也不是一个文件夹或一篇热点报告。它是跨来源、跨文档、可持续演化的动态知识对象。每个稳定主题应同时具备两个身份：

- `hotspot_role`: 新闻热点、舆情雷达主题、半日/日频报告主题。
- `driver_role`: 资产驱动逻辑节点，可连接 driver state、asset signal 和 causal graph。

建议的 topic object：

```json
{
  "schema_version": "market_topic.v1",
  "topic_id": "MKT_TOPIC_US_IRAN_HORMUZ",
  "canonical_name": "美伊和谈与霍尔木兹海峡通航恢复",
  "node_kind": "market_topic",
  "hotspot_role": true,
  "driver_role": true,
  "topic_type": "geopolitics",
  "definition": "美伊谈判、停火、霍尔木兹通航变化引发的地缘风险溢价变化。",
  "summary": "围绕中东航运、能源供应和风险偏好形成的市场主题。",
  "aliases": ["霍尔木兹通航", "美伊和谈", "中东地缘缓和"],
  "linked_logic_node_ids": ["LOGIC_GEOPOLITICAL_RISK_PREMIUM"],
  "linked_driver_ids": ["DRIVER_OIL_RISK_PREMIUM"],
  "asset_refs": ["原油", "能化链", "贵金属"],
  "heat_score": 0.82,
  "credibility_score": 0.61,
  "source_diversity": 0.73,
  "contradiction_score": 0.29,
  "lifecycle_state": "strengthening",
  "status": "candidate",
  "created_at": "ISO8601",
  "updated_at": "ISO8601",
  "source_refs": []
}
```

Topic 的定义文件不频繁改。每日证据、状态和边变化都通过 event log 追加。

`heat_score` 和 `credibility_score` 必须分开。热点可以很热但未确认，也可以重要但暂时不热。

## Topic Membership

归类关系本身要单独存储，不要只在 Event / Claim / Evidence 上写一个 `topic_id`：

```json
{
  "schema_version": "topic_membership.v1",
  "membership_id": "TMEM-...",
  "object_id": "EV-...",
  "object_type": "event | claim | evidence | observation | document | derived_report",
  "topic_id": "MKT_TOPIC_US_IRAN_HORMUZ",
  "membership_role": "primary | secondary | contextual",
  "membership_score": 0.86,
  "relation_to_topic": "creates | updates | supports | contradicts | contextualizes | validates",
  "stance": "supports | contradicts | neutral",
  "effective_time": "ISO8601",
  "assigned_by": "topic_mapping_agent",
  "agent_run_id": "RUN-...",
  "method": "llm | rule_fallback | human_review",
  "status": "active",
  "reason": "实体、逻辑节点和时间链条均指向霍尔木兹通航风险。"
}
```

这样后续可以解释：

- 为什么这个对象被归入这个话题；
- 是主话题、次话题还是背景话题；
- 是支持、反驳、验证还是仅提供背景；
- 是模型归类还是人工修正；
- 后来是否被移出话题。

Topic mapping 不应让模型从全量历史主题中自由选择。建议使用：

```text
候选召回
  = 关键词全文检索
  + 向量语义检索
  + 实体重叠
  + 逻辑节点重叠
  + 时间邻近
  + 资产重叠
```

再在少量候选上让 Topic Mapping Agent 判断。透明初始分数：

```text
TopicMembershipScore =
0.25 * entity_overlap
+ 0.20 * semantic_similarity
+ 0.20 * logic_node_overlap
+ 0.15 * event_chain_similarity
+ 0.10 * time_proximity
+ 0.10 * asset_overlap
```

建议门槛：

- `>= 0.80`: 自动关联；
- `0.60 - 0.80`: 进入 Topic Mapping Agent 局部判断；
- `< 0.60`: 暂不归类或进入新话题候选池。

## Topic Event Log

所有 topic 变化必须事件化：

```json
{"event_type":"topic_created","topic_id":"MKT_TOPIC_US_IRAN_HORMUZ","run_id":"RUN-...","at":"ISO8601"}
{"event_type":"evidence_added","topic_id":"MKT_TOPIC_US_IRAN_HORMUZ","evidence_id":"MTHEV-...","source_ref":{"path":"..."},"at":"ISO8601"}
{"event_type":"state_updated","topic_id":"MKT_TOPIC_US_IRAN_HORMUZ","from_strength":0.31,"to_strength":0.45,"at":"ISO8601"}
{"event_type":"asset_link_added","topic_id":"MKT_TOPIC_US_IRAN_HORMUZ","asset":"原油","confidence":0.9,"at":"ISO8601"}
{"event_type":"topic_alias_added","topic_id":"MKT_TOPIC_US_IRAN_HORMUZ","alias":"霍尔木兹风险","at":"ISO8601"}
{"event_type":"topic_merge_proposed","from_topic_id":"MKT_TOPIC_A","to_topic_id":"MKT_TOPIC_B","review_required":true,"at":"ISO8601"}
```

允许的事件包括：

- `topic_created`
- `topic_updated`
- `topic_alias_added`
- `evidence_added`
- `evidence_removed`
- `state_updated`
- `asset_link_added`
- `driver_link_added`
- `logic_edge_candidate_added`
- `topic_merge_proposed`
- `topic_split_proposed`
- `review_note_added`

高影响事件，例如 merge、split、polarity change、edge weight change，必须进入 review queue，不能由 Agent 直接写入 active state。

## Topic State

`states/market_topics/latest/{topic_id}.json` 给前端和 Agent 快速读取：

```json
{
  "schema_version": "market_topic_state.v1",
  "topic_id": "MKT_TOPIC_US_IRAN_HORMUZ",
  "as_of": "ISO8601",
  "heat": 0.72,
  "strength": 0.45,
  "trend": "strengthening",
  "lifecycle_state": "strengthening",
  "confidence": 0.63,
  "credibility_score": 0.61,
  "source_diversity": 0.73,
  "contradiction_score": 0.29,
  "supporting_evidence_count": 4,
  "contradicting_evidence_count": 0,
  "dominant_assets": ["原油", "能化链", "贵金属"],
  "driver_refs": ["geopolitics", "risk_premium"],
  "change_explanation": "新增多条美伊和谈与霍尔木兹通航恢复证据。",
  "last_event_ids": ["..."],
  "source_run_ids": ["RUN-MARKET-THEME-..."]
}
```

历史状态写入 `states/market_topics/history/{yyyy}/{mm}/{dd}/topic-states.jsonl`，便于回放、趋势图和审计。

Topic 生命周期建议：

```text
candidate -> emerging -> active -> strengthening -> mature -> fading -> archived
```

允许：

```text
fading -> reactivated
active -> disputed
```

HeatScore 主要来自事件新增速度、来源多样性、资产覆盖、市场价格反应、预测市场变化、新颖性，并扣除重复转载惩罚。

CredibilityScore 主要来自官方或一手来源权重、独立来源确认、基本面数据验证、市场行为验证，并扣除明确反驳、同源转载和不可靠来源。

## Topic Evidence Links

证据链接是 membership 的证据视图。它不要复制完整原文，只保留可追溯指针和模型归类信息：

```json
{
  "schema_version": "market_topic_evidence_link.v1",
  "topic_id": "MKT_TOPIC_US_IRAN_HORMUZ",
  "evidence_id": "MTHEV-...",
  "source_type": "futures_daily",
  "source_path": "agent_workspace/candidates/futures_daily_raw_runs/...",
  "source_field": "market_events_summary",
  "summary": "美伊签署14点停战谅解备忘录...",
  "classification": {
    "method": "llm",
    "concept_id": "US_IRAN_HORMUZ",
    "confidence": 0.95,
    "reason": "证据指向地缘缓和与通航恢复。"
  },
  "generated_by_run_id": "RUN-MARKET-THEME-...",
  "added_at": "ISO8601"
}
```

同一底层研报、新闻或数据 observation 只能按其原始 provenance 计权。自动日报、Wiki 综合页和二次报告不能反过来当作独立证据。

更通用的归类关系应落在 `TopicMembership`。`topic-evidence.jsonl` 可以作为便于展示的读模型，但不能替代 membership 真源。

## Derived Reports And Provenance

Agent 生成的日报、专题报告、Wiki 页面或问答结果都是派生产物，应保存输入依赖：

```json
{
  "schema_version": "derived_report.v1",
  "report_id": "REPORT-...",
  "report_type": "topic_daily",
  "topic_id": "MKT_TOPIC_US_IRAN_HORMUZ",
  "period_start": "ISO8601",
  "period_end": "ISO8601",
  "generated_by_run_id": "RUN-...",
  "input_event_ids": [],
  "input_claim_ids": [],
  "input_observation_ids": [],
  "input_logic_node_ids": [],
  "input_state_snapshot_ids": [],
  "content_uri": "quanta://artifacts/reports/...",
  "prompt_version": "topic_report_v3",
  "model_version": "deepseek-chat",
  "status": "draft"
}
```

并写入 `ReportDependency` / `GENERATED_FROM` 关系：

```json
{
  "schema_version": "report_dependency.v1",
  "report_id": "REPORT-...",
  "input_object_id": "EV-...",
  "input_object_type": "event",
  "relation": "used",
  "generated_by_run_id": "RUN-..."
}
```

派生报告默认 `evidence_weight = 0`。它可以帮助阅读，但不能作为独立来源再次提高 topic credibility。

## Storage Backends

不要强行用一种数据库承载所有东西：

| 存储 | 适合对象 | 说明 |
| --- | --- | --- |
| Blob Store | 全文、PDF、Markdown、图片、原始 JSON、大型 Agent 产物 | 保存不可逆原料和大对象 |
| Catalog DB | `research_object`、`object_version`、`object_relation`、`topic`、`topic_membership`、`event`、`claim`、`evidence`、`observation`、`agent_run`、`report_dependency`、`state_snapshot` | 对象 ID、时间、版本、关系和溯源的唯一真源 |
| Search Index | 标题、摘要、chunk、Event、Claim、Topic、LogicNode、Report Section | BM25 + metadata filter + embedding + reranker |
| Time-series Store | 行情和基本面序列 | 知识库只保存 observation 摘要和 series_id |
| Graph Projection | Topic、Event、Claim、Evidence、LogicNode、Asset、Report 关系 | 可投影到 Neo4j，但不作为唯一真源 |

## Selective Event Sourcing

不要对所有字段更新都采用完整 Event Sourcing。只在审计和历史重建收益足够高的领域采用追加式变更日志：

- 话题状态变更；
- topic merge / split；
- 逻辑边变更；
- 人工审核；
- 可信状态变化；
- 报告版本和依赖变化。

普通字段可使用 object version、updated_at 和 review log，避免过度复杂。

## Wiki Projection

Wiki 页面是 L2 投影，适合存：

- 概念定义；
- 当前认知摘要；
- 主要支持证据和反向证据的引用；
- 时间线；
- 关联资产；
- 关联逻辑边；
- 未解决问题；
- 人工研究备注。

示例：

```markdown
---
id: MKT_TOPIC_US_IRAN_HORMUZ
type: market_topic
object_ref: quanta://market-topic/MKT_TOPIC_US_IRAN_HORMUZ
status: candidate
updated: 2026-06-21
current_strength: 0.45
confidence: medium
---

# 美伊和谈与霍尔木兹海峡通航恢复

## 定义

美伊谈判、停火和霍尔木兹通航变化引发的地缘风险溢价变化。

## 当前状态

- 状态：emerging
- 趋势：strengthening
- 主要资产：原油、能化链、贵金属

## 支持证据

- `evidence:MTHEV-...`
- `event:EV-...`

## 影响路径

- [[霍尔木兹通航恢复]]
- [[原油地缘风险溢价]]
- [[能化链成本端]]

## 未解决问题

- 通航恢复是否会反复？
- 原油地缘溢价回吐会持续几天还是几周？
```

Wiki 链接只表示“值得关联阅读”，不等于计算图边。因果传播必须读取结构化 `GraphEdge`。

## Wiki Backflow Rules

允许从 Wiki / 人工备注回写为候选变更：

- topic alias；
- 定义修订；
- 逻辑条件补充；
- 证据评价；
- 人工审核意见；
- 候选链接；
- 矛盾说明。

禁止直接从 Wiki 回写：

- driver strength；
- topic strength / heat；
- 数据值；
- 正式边权重；
- polarity；
- 验证状态；
- 正式 topic merge / split；
- active knowledge 覆盖。

这些变更必须通过结构化校验和 review gate。

## Search Contract

Agent 检索顺序建议：

1. `search_wiki_pages()`：理解已有认知结构。
2. `get_wiki_page()` / `follow_wikilinks()`：读取概念、主题、驱动页面。
3. `get_current_state()`：获取最新 topic / driver / asset state。
4. `search_raw_evidence()`：验证关键证据。
5. `get_structured_object()`：读取 Event、Claim、Evidence、Observation、GraphEdge。
6. `get_timeline()`：查看时间演化。
7. `trace_provenance()`：追踪报告段落、主题状态和 driver 变化的来源。

不要直接让模型搜索所有原文并生成结论。Agent 应先读 Wiki 理解结构，再读状态获得当前数值，最后读原始证据验证关键结论。

## Migration Plan

第一阶段：

- 保留 `market-theme-state.json` 作为日频观察快照。
- 新增 `market_topics` 维护器，把每日主题抽取结果合并为稳定 topic candidates。
- 写入 `topic-registry.json`、`TopicMembership`、topic event log、topic state latest。
- 由 `TopicMembership` 投影生成 `topic-evidence.jsonl`，供前端和人类报告快速展示。
- 对 Agent 简报、Wiki 和二次报告写 `ReportDependency`，并保持 `evidence_weight=0`。

第二阶段：

- 编译 `wiki_projection/topics/*.md`。
- 为 topic 页面保留 `object_ref`、source refs、evidence ids、run ids。
- 在 Alpha 页面展示 topic timeline、资产链接、证据链和 review 状态。

第三阶段：

- Topic 与 logic graph / driver state 双向关联。
- 支持 topic merge / split proposal。
- 支持人工 Wiki 修订进入 review queue。

第四阶段：

- 建立 L3 搜索和图索引。
- Agent 查询统一走 Wiki -> state -> evidence -> provenance 的检索流程。
