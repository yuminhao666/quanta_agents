# Research Object Relation Architecture

本文定义 Quanta 项目下一阶段的核心架构：新闻、研报、数据、预测市场、人工观点和 Agent 报告不直接通过文本相似度彼此相连，而是先转换成不同类型的知识对象，再通过 `Event`、`Topic`、`LogicNode`、`Asset`、`Time` 和 `Provenance` 等共享锚点建立显式、可查询、可审核、可回放的关系。

它补充：

- [quanta-research-knowledge-fabric-architecture.md](quanta-research-knowledge-fabric-architecture.md)：端到端 Research Knowledge Fabric 总纲。
- [quanta-knowledge-storage-layout.md](quanta-knowledge-storage-layout.md)：`quanta_data` 长期对象、状态和 Wiki 投影布局。
- [quanta-cognitive-langgraph-flow.md](quanta-cognitive-langgraph-flow.md)：Agent 编排、确定性服务和写入门控。
- [news-event-non-breaking-plan.md](news-event-non-breaking-plan.md)：新闻事件 sidecar 的非破坏式落地方案。
- [news-relation-agent-iteration-20260625.md](news-relation-agent-iteration-20260625.md)：多 Agent 真实数据验证、QA 发现和本轮开发方案调整。
- [news-processing-flowchart.md](news-processing-flowchart.md)：新闻处理流程、算法和提示词入口流程图。

## 1. 核心判断

Quanta 不应把所有输入都压成同一种“文章”或“摘要”。不同来源表达的是不同认知角色：

| 来源 | 转换后的知识对象 | 表达什么 |
| --- | --- | --- |
| 新闻 / 快讯 | `EventMention` -> `CanonicalEvent` | 现实中发生了什么，或被报道发生了什么 |
| 研报 / 年报 / 微信文章 | `Evidence` -> `AtomicClaim` | 作者提出了什么判断、条件和因果逻辑 |
| 基本面数据 | `Observation` | 库存、产量、需求、成本等现实变量如何变化 |
| 行情 / 资金 / 波动率 | `MarketObservation` | 价格、成交、持仓、波动率和资金如何反应 |
| Polymarket / 预测市场 | `ExpectationObservation` | 市场预期如何变化，不等同事实概率 |
| 人工观点 | `HumanClaim` | 研究员的判断、假设、修正和备注 |
| Agent 报告 / Wiki / 问答 | `DerivedReport` | 对已有对象的综合表达，默认不作为独立证据 |

核心链路：

```text
source artifact
  -> typed knowledge object
  -> candidate relation
  -> reviewable write
  -> state snapshot / read model
  -> report, QA, wiki, alert
```

## 2. 项目职责

| 项目 | 职责 |
| --- | --- |
| `quanta_agents` | source adapter、知识对象抽取、候选召回、关系判断、状态计算、报告和 Wiki 编译、维护扫描 |
| `quanta_data` | raw/canonical/evidence、对象、关系、状态、event log、candidate、review、gold 和 schema 真源 |
| `gj_chainplatform` | 查询、展示、审核、权限、协作、图谱和人工反馈入口 |

约束：

- `quanta_agents` 不直接写 `gold`。
- `gj_chainplatform` 不直接运行 LLM 修改事实或状态。
- `quanta_data/configs/schemas` 是 output shape 的 schema source of truth。
- 现有 `agent_workspace/candidates/.../latest` 文件和平台读取合同在迁移期保持兼容。

## 3. 六类共享锚点

### 3.1 Event

`Event` 判断对象是否围绕同一现实事件或事件链。

示例：

```text
E1: 霍尔木兹封锁传闻
E2: 伊朗否认封锁
E3: 油轮保险费上涨

E2 --CONTRADICTS--> E1
E3 --UPDATES--> 航运风险事件链
```

新闻先形成 `EventMention`，再归并到 `CanonicalEvent`。不同资产影响通过 `EventAssetLink` 表达，不复制多个现实事件。

### 3.2 Topic

`Topic` 表达可持续维护的市场话题，不是每日标题。

同一个 Topic 可以挂载：

- 新闻 `CanonicalEvent`
- 研报 `AtomicClaim`
- 数据 `Observation`
- 行情 `MarketObservation`
- 人工 `HumanClaim`
- 反向证据和撤回事件

Topic 的关系通过 `TopicMembership` 或通用 `ObjectRelation` 表达，不能只在对象 JSON 中写一个 `topic_id`。

### 3.3 Logic Node

`LogicNode` 表达市场变量或机制节点，例如：

```text
global.shipping_disruption
global.energy_supply_risk
global.energy_price
global.inflation_expectation
global.risk_appetite
```

新闻负责激活节点，研报解释节点之间的关系，数据验证节点状态。

### 3.4 Asset / Framework Node

`Asset` 和 `FrameworkNode` 表示影响最终落在哪个品种和分析维度。

同一个 `global.energy_price` 变化可以映射到：

- 原油：供应风险和地缘溢价。
- 铝：能源成本。
- 铜：冶炼利润、风险偏好和美元定价。
- 黄金：通胀预期和避险需求。

### 3.5 Time

所有对象和关系必须区分时间字段：

| 字段 | 含义 |
| --- | --- |
| `published_at` | 材料发布时间 |
| `event_time` | 事件发生或数据观测对应时间 |
| `effective_from` | 关系或影响开始生效时间 |
| `effective_to` | 关系或影响失效时间 |
| `as_of` | 状态快照计算时点 |

一篇六月研报引用四月库存数据，不能被当成六月新发生的基本面变化。

### 3.6 Provenance

Provenance 防止把转载、派生报告或同源资料误当成多源验证。

关键字段：

```text
root_source_id
source_group_id
syndication_group_id
source_independence_key
agent_run_id
prompt_version
policy_version
```

十篇媒体转载来自同一条官方消息时，mention 数可以增加，独立来源数和可信度不能按十个来源计。

## 4. 对象模型

### 4.1 ResearchObject

所有可关联对象都登记为 `ResearchObject`：

```json
{
  "object_id": "EV-...",
  "object_type": "event",
  "title": "伊朗否认将封锁霍尔木兹海峡",
  "source_type": "news",
  "source_id": "FLASH-...",
  "event_time": "2026-06-21T10:00:00+08:00",
  "truth_status": "partially_confirmed",
  "lifecycle_status": "candidate",
  "metadata": {}
}
```

`ResearchObject` 只负责统一目录、元数据和检索入口。对象的专有字段仍保存在类型表或文件产物中。

### 4.2 Typed Objects

| 对象 | 稳定 ID 依据 | 主要字段 |
| --- | --- | --- |
| `CanonicalEvent` | 事件 signature、event type、时间窗口 | `event_type`、`event_stage`、`truth_status`、`market_attention`、`mention_ids` |
| `AtomicClaim` | 来源文档、chunk、claim hash | `subject_node`、`predicate`、`object_node`、`conditions`、`time_horizon`、`stance` |
| `Observation` | series、release time、observation time | `series_id`、`value`、`previous_value`、`mapped_node_id` |
| `MarketObservation` | asset、metric、market time | `price_change`、`volume`、`open_interest`、`volatility` |
| `ExpectationObservation` | market id、snapshot time | `probability`、`probability_change`、`liquidity` |
| `HumanClaim` | author、note id、version | `claim_text`、`assumption`、`review_status` |
| `DerivedReport` | report id、run id、content hash | `input_object_ids`、`content_uri`、`model_version` |

## 5. 关系是一等对象

不要把关系埋在新闻、研报或报告 JSON 里。关系必须可单独查询、审核、失效和回放。

通用关系结构：

```json
{
  "relation_id": "REL-001",
  "from_object_id": "CLAIM-001",
  "relation_type": "SUPPORTS",
  "to_object_id": "EDGE-ENERGY-TO-OIL",
  "polarity": 1,
  "confidence": 0.82,
  "effective_from": "2026-06-20T00:00:00+08:00",
  "effective_to": null,
  "evidence_object_id": "EVID-001",
  "created_by": "claim_mapping_agent",
  "agent_run_id": "RUN-001",
  "review_status": "candidate",
  "status": "active",
  "policy_version": "relation_policy.v1"
}
```

核心关系类型：

| 关系 | 用途 |
| --- | --- |
| `SAME_EVENT` | mention 或 event 指向同一现实事件 |
| `UPDATES` | 后续事件更新前序事件或事件链 |
| `CONFIRMS` | 对前序事件、claim 或状态提供确认 |
| `CONTRADICTS` | 对前序事件、claim 或状态提供反向证据 |
| `RETRACTS` | 撤回、否认或修正已传播信息 |
| `BELONGS_TO_TOPIC` | 对象归入 Topic |
| `MAPS_TO_NODE` | 对象映射到 LogicNode 或 FrameworkNode |
| `AFFECTS_ASSET` | 对象或节点影响某资产 |
| `SUPPORTS` | 支持 LogicEdge、Topic 或 Driver |
| `VALIDATES` | 数据或行情验证某节点、claim 或状态 |
| `WEAKENS` | 削弱某 claim、edge 或 driver |
| `DERIVED_FROM` | 对象由底层文档、chunk 或 event 产生 |
| `GENERATED_FROM` | 报告由输入对象生成 |
| `SUPERSEDES` | 新关系或对象替代旧关系或对象 |

关系不能物理覆盖。旧关系失效时：

```text
OLD_REL.status = superseded
OLD_REL.effective_to = ...
NEW_REL --SUPERSEDES--> OLD_REL
```

这样才能回答任意历史时点“系统当时为什么这样判断”。

## 6. 稳定结构、证据和状态分离

### 6.1 Stable Object

长期稳定对象：

```text
Topic
LogicNode
LogicEdge
Asset
FrameworkNode
CanonicalEvent
```

稳定对象只描述定义、范围、别名和拓扑，不直接承载当前强弱。

### 6.2 Evidence Assertion

每个来源在某个时间对稳定对象提出支持、反驳、验证或背景关系：

```text
news Event        --ACTIVATES--> LogicNode
research Claim   --SUPPORTS--> LogicEdge
Observation      --VALIDATES--> LogicNode
MarketObservation --VALIDATES--> Asset reaction
HumanClaim       --SUPPORTS/WEAKENS--> Topic or LogicEdge
```

一篇研报不能直接修改 `LogicEdge.weight`。它只能增加一条带 provenance 的证据关系。

### 6.3 State Snapshot

状态由关系增量计算：

```json
{
  "snapshot_id": "SNAP-EDGE-OIL-INFLATION-20260621",
  "object_id": "EDGE-OIL-TO-INFLATION",
  "as_of": "2026-06-21",
  "activation": 0.72,
  "support_score": 0.81,
  "contradiction_score": 0.26,
  "confidence": 0.68,
  "trend": "strengthening",
  "source_relation_ids": ["REL-001", "REL-002"]
}
```

查询时读取物化状态，审计时沿 `source_relation_ids` 回到对象、关系、来源和 run。

## 7. 新信息进入流程

```text
1. source adapter 读取 raw/candidate/source API
2. 转换为 typed knowledge object
3. 注册 ResearchObject
4. 根据实体、时间、资产、节点、provenance 和文本召回候选
5. 确定性算法计算关联分数
6. 高置信关系自动写 candidate
7. 中置信关系交给 LLM 在少量候选中判断
8. 低置信关系 abstain 或进入 review queue
9. Knowledge Write Service 校验 schema、幂等、provenance 和 review policy
10. 写入 ObjectRelation / specialized relation
11. 只重算受影响的 Topic、LogicEdge、Asset 或 Driver state
12. 保存 StateSnapshot 和 read model
```

候选召回由算法负责。LLM 只做局部语义判断，例如：

```text
这条信息是在：
- updates Topic
- supports Topic
- contradicts Topic
- contextualizes Topic
- unrelated
```

LLM 不负责：

- 生成正式 ID。
- 直接修改 Topic、LogicEdge、Driver 或 Asset 状态。
- 决定最终权重。
- 写数据库或文件。
- 遍历完整知识库。

## 8. 写入门控

建议将生产写入收敛到 `Knowledge Write Service`：

```text
candidate object / relation
  -> schema validation
  -> idempotency check
  -> provenance check
  -> source independence check
  -> review policy
  -> append relation / versioned write
  -> impacted state recompute
  -> read model projection
```

当前仓库映射：

| 当前模块 | 下一步职责 |
| --- | --- |
| `quanta_agents/domain` | 补齐 object、relation、claim、observation、snapshot 的 domain model |
| `quanta_agents/repositories/catalog_repository.py` | 承接 `research_object`、`object_relation`、topic、event、report dependency 的 sidecar DB |
| `quanta_agents/services/event_service.py` | 统一 event 注册、source link、asset link 和 node activation |
| `quanta_agents/services/topic_service.py` | 统一 topic candidate recall、membership 写入和 topic score 刷新 |
| `quanta_agents/services/provenance_service.py` | 统一 `DERIVED_FROM`、`GENERATED_FROM` 和 `ReportDependency` |
| `quanta_agents/news_events` | 新闻 normalization、syndication、event mention、canonical event 和 legacy projection |
| `quanta_agents/research_reports` | Evidence 到 AtomicClaim、LogicNode 和 LogicEdge 的映射 |
| `quanta_agents/market_themes` | Topic extraction、TopicMembership、topic event log、topic evolution read model |
| `quanta_agents/asset_event_state` | Event/Signal/Driver state 的受约束状态机 |

## 9. 存储布局

迁移期先使用 sidecar DB 和 candidate 文件，正式晋级后写入 `quanta_data` 对应目录。

建议关系数据库表：

```text
research_object
object_version
object_relation
topic_membership
event_relation
edge_evidence
state_snapshot
report_dependency
agent_run
review_log
schema_migration
```

文件仍保存大对象和不可逆内容：

```text
raw_objects/
raw_manifests/
canonical_documents/
evidence_store/
agent_workspace/candidates/
agent_workspace/runs/
agent_workspace/review_packages/
wiki_projection/
```

索引定位：

| 索引 | 职责 |
| --- | --- |
| SQLite / relational catalog | 对象、关系、版本、状态和 provenance 真源或候选真源 |
| BM25 / fulltext | 候选召回 |
| Vector index | 语义候选召回，不作为合并依据 |
| Neo4j | 从 `ObjectRelation` 投影出的图查询层，不作为唯一真源 |
| Wiki projection | 人和 Agent 的可读投影，不作为计权证据 |

## 10. 运行节奏

### 实时或微批

- 新 `Event` / `Claim` / `Observation` 抽取。
- 事件归并和 `SyndicationGroup` 更新。
- `TopicMembership` candidate 写入。
- `SUPPORTS`、`CONTRADICTS`、`VALIDATES` 等关系写入。
- 受影响对象的轻量 state delta 更新。

### 每小时

- Topic heat 更新。
- credibility 和 source diversity 更新。
- 冲突检测。
- 事件 truth status 更新。
- 新闻主题和主线 projection 更新。

### 每日

- 重复 Topic 检测。
- Topic merge / split candidate。
- 过期关系关闭。
- LogicEdge activation 更新。
- State snapshot 归档。
- Review package 和 PM triage 生成。

### 每周

- 孤立对象检查。
- 未映射 Claim 检查。
- 关系误合并抽样。
- 阈值校准。
- 人工审核候选整理。

## 11. 端到端案例

新闻进入：

```text
伊朗可能封锁霍尔木兹海峡
```

生成：

```text
E1: 霍尔木兹封锁传闻
E1 --BELONGS_TO_TOPIC--> T1: 霍尔木兹航运与能源供应风险
E1 --MAPS_TO_NODE--> global.shipping_disruption
```

研报进入：

```text
如果海峡运输中断，原油供应风险将上升，并可能推升通胀预期。
```

生成：

```text
C1: shipping_disruption -> energy_supply_risk
C2: energy_supply_risk -> energy_price
C3: energy_price -> inflation_expectation

C1/C2/C3 --BELONGS_TO_TOPIC--> T1
C1/C2/C3 --SUPPORTS--> corresponding LogicEdge
```

数据进入：

```text
油轮保险费上涨 35%
Brent 上涨 4%
```

生成：

```text
O1: tanker_insurance_cost up
O2: energy_price up

O1 --VALIDATES--> global.shipping_disruption
O2 --VALIDATES--> global.energy_price
```

官方否认进入：

```text
E2: 伊朗否认封锁
E2 --CONTRADICTS--> E1
E2 --BELONGS_TO_TOPIC--> T1
```

Topic 状态：

```json
{
  "topic_id": "T1",
  "heat": "high",
  "credibility": "medium_low",
  "lifecycle_state": "disputed",
  "reason": "新闻关注度高，但官方否认与转载同源降低可信度。"
}
```

系统不应输出无证据的简化结论，例如“霍尔木兹利多原油”。它应输出可追溯状态：热度高、可信度中低、存在冲突、需要继续验证。

## 12. 与现有 JSON 引用的迁移关系

当前很多关系还嵌在 JSON 中：

- `news_logic.events[].theme_anchor_refs`
- `news_logic.events[].framework_node`
- `research_signal.asset_refs`
- `research_signal.theme_refs`
- `research_signal.framework_node_refs`
- `research_signal.evidence_refs`
- `wechat_evidence.canonical_ref`
- `recent_news_topics.topic_memberships`
- `topic-evidence.jsonl`

迁移原则：

1. 保留原 JSON 字段，保证平台和旧 pipeline 不破坏。
2. 每次产物生成后通过 adapter 将嵌入引用登记为 `ResearchObject` 和 `ObjectRelation`。
3. 查询和新 Agent 优先读 catalog relation，旧 UI 继续读 latest JSON。
4. sidecar 和 JSON 做一致性校验，发现 drift 进入 `knowledge_maintenance` candidate。
5. 关系稳定后再考虑将 candidate 真源迁到 `relations/`、`states/`、`event_logs/`。

## 13. 分阶段落地

### Phase 0: 合同冻结和基线

- 冻结 `news_logic`、`recent-news-topics`、`wechat_evidence`、`research_signal` 的当前平台合同。
- 建立 object/relation schema 草案。
- 建立小样本 benchmark：同事件、反驳事件、转载、同 Topic、错 Topic、无关。

### Phase 1: Relation Catalog MVP

- 扩展 `CatalogRepository` 的 relation query 和 provenance trace。
- 将 `news_logic`、`research_signal`、`recent_news_topics` 的嵌入引用登记为 `ObjectRelation`。
- 保留所有原 candidate JSON。

### Phase 2: News Event Sidecar

- 使用 `news_events` 完成 `NewsItem`、`SyndicationGroup`、`EventMention`、`CanonicalEvent`。
- 生成 legacy news logic projection。
- 将 `EventRelation` 和 `TopicMembership` 写入 relation catalog。

### Phase 3: AtomicClaim

- 从 `wechat_evidence` 和 structured profile 中抽取 `AtomicClaim`。
- 映射到 `LogicNode`、`LogicEdge`、`Topic` 和 `Asset`。
- 将研报 claim 的支持、反驳和条件关系显式化。

### Phase 4: Observation

- 基本面、行情和 Polymarket 统一成 `Observation` 子类型。
- 用 `VALIDATES`、`WEAKENS`、`CONTRADICTS` 连接 claim、node 和 state。
- 明确 `event_time`、`release_time`、`as_of`。

### Phase 5: State Engine

- 基于 relation delta 增量更新 `TopicState`、`LogicEdgeState`、`AssetState` 和 `DriverState`。
- 每次 state snapshot 保存 `source_relation_ids`。
- 报告和问答只读取 state/context package，不直接自由漫游原文。

### Phase 6: Review And Projection

- Alpha 审核台处理新 Topic、新 LogicEdge、merge/split、撤回污染和高影响关系。
- Wiki、Neo4j 和专题页都从 catalog/relation/state 投影。
- Knowledge Maintenance Agent 检查孤立对象、关系 drift 和派生报告污染。

## 14. 验收标准

进入主链路前至少满足：

- 新对象和关系引用 `quanta_data/configs/schemas` 中的 schema。
- 每个长运行命令写 run manifest、input refs、output refs、errors、metrics 和 review items。
- 所有 JSON 中使用相对 `quanta_data` 路径。
- 同一现实新闻多资产只创建一个 `CanonicalEvent`，资产影响通过关系表达。
- 转载不提高独立来源数。
- `DerivedReport` 和 Wiki 默认 `evidence_weight=0`。
- 候选输出明确区分 `supporting_evidence_count` 和 `derived_context_count`，包装层上下文不计入独立支持证据。
- 任意状态快照可追溯到 source relation，再追溯到 source object 和 raw/canonical ref。
- 旧平台 latest JSON 合同不破坏。
- 至少完成一个 artifact path 和一个 Alpha 页面 read model 的联调验证。
