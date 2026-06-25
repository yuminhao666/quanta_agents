# News Processing Flowchart

本文把当前新闻处理链路、主要算法和提示词入口整理成一张流程图，便于前端、后端和内容 QA 对齐展示边界。

## 总览

```mermaid
flowchart TD
  A[MySQL / jin10_flash<br/>原始快讯] --> B[normalize_and_filter<br/>清洗标题正文、规范时间、生成 news_id]
  B --> C{filter_news_item<br/>噪音过滤}
  C -->|filtered_out| C0[filtered_news.jsonl<br/>记录过滤原因]
  C -->|priority / relevant / candidate| D[build_syndication_groups<br/>转载 / 近重复分组]

  D --> E{EventMention 抽取}
  E -->|LLM batch| E1[prompt: news_event_mention_batch_extraction.v1.txt<br/>逐条抽取现实事件，不跨 news_id 合并]
  E -->|LLM single| E2[prompt: news_event_mention_extraction.v1.txt]
  E -->|fallback| E3[rule_fallback<br/>关键词推断 event_type / stage / truth_status]
  E1 --> F[event_mentions.jsonl]
  E2 --> F
  E3 --> F

  F --> R1[review gate<br/>low_extraction_confidence<br/>future_event_time<br/>calendar_notice]

  F --> G[retrieve_candidate_events<br/>实体、动作、对象、地点、时间窗口召回]
  G --> H[score_mention_to_event<br/>subject/object/action/entity/type/location/time/stage/source 加权]
  H --> I{choose_event_match}
  I -->|高分自动| I1[SAME_EVENT / UPDATE / CONFIRM / CONTRADICT / RETRACT]
  I -->|中分模糊| I2[prompt: event_pair_judge.v1.txt<br/>判断现实事件关系，不只看文本相似]
  I -->|低分| I3[新建 CanonicalEvent]

  I1 --> J[CanonicalEvent / EventRelation<br/>canonical_events.jsonl<br/>event_relations.jsonl]
  I2 --> J
  I3 --> J

  J --> K[map_event_assets_and_frameworks<br/>规则召回资产 taxonomy + framework nodes]
  K -->|候选少且明确| K1[event_asset_links.jsonl<br/>event_framework_node_links.jsonl]
  K -->|需要 LLM 判断时| K2[prompt: event_asset_framework_mapping.v1.txt<br/>direct / indirect / contextual]
  K2 --> K1

  J --> L[map_event_to_topic<br/>Persistent Topic 映射]
  K1 --> L
  L --> L1[score_event_to_topic<br/>实体重叠、event_type、标题、资产范围]
  L1 --> L2{Topic 决策}
  L2 -->|高置信| L3[updates / contradicts existing Topic]
  L2 -->|中置信 + LLM| L4[prompt: topic_membership_judge.v1.txt<br/>creates / updates / supports / contradicts / validates / contextualizes]
  L2 -->|无合适候选| L5[rule-created Topic<br/>membership_score=0.55<br/>进入 review_queue]
  L3 --> M[topic_memberships.jsonl<br/>topic_state_snapshots.jsonl]
  L4 --> M
  L5 --> M
  L5 --> R2[review_queue.jsonl<br/>rule_created_topic_candidate]

  J --> N[legacy projection<br/>legacy_news_logic_events.jsonl]
  M --> O[object catalog projection<br/>ResearchObject / ObjectRelation]
  K1 --> O
  N --> P[平台旧读模型兼容]
  O --> Q[object_relation<br/>DERIVED_FROM / BELONGS_TO_TOPIC / AFFECTS_ASSET / MAPS_TO_NODE]

  subgraph RecentTopic[recent_news_topics 链路]
    S[half-day-news-brief.json<br/>top_flashes + graph_trigger_candidates + llm_brief] --> T[_evidence_from_brief<br/>生成 NTEV evidence candidates]
    T --> T0[evidence_weight 规则<br/>derived_summary / event_report / mapped_flash = 0.0<br/>只作上下文，不作独立支持证据]
    T0 --> U[_select_llm_evidence<br/>按 role_priority、weight、heat 选入 prompt]
    U --> V[_prompt in recent_news_topics.py<br/>最近新闻主题维护 Agent<br/>抽取稳定 MarketTopic]
    V --> W[LLM topics<br/>topic_nodes / evidence_ids / state / watch_items]
    W --> X[本地校验 evidence_id / source_refs<br/>invalid_ref_count]
    X --> Y[topic_memberships.jsonl<br/>relation=updates if weight>0 else contextualizes<br/>stance=supports if weight>0 else neutral]
    Y --> Z[topic-evidence.jsonl<br/>保留 flash/event source_refs]
    Y --> AA[recent-news-topics.json<br/>evidence_weight_summary<br/>supporting_evidence_count / derived_context_count]
  end

  AA --> AB[topic_evolution<br/>历史 runs 编译时间线和图谱 read model]
  AB --> AC[gj_chainplatform /api/v1/opinion-radar<br/>news_relation_quality]
  Q --> AC
  AC --> AD[舆情雷达页面<br/>普通用户展示：候选主题、相关资讯、上下文线索、待复核]
```

## 当前关键算法

| 阶段 | 算法 / 规则 | 作用 |
| --- | --- | --- |
| 新闻标准化 | `stable_news_id(source_system, row_id, publish_time, content_hash)` | 让同一条新闻 ID 稳定 |
| 过滤 | `filter_news_item()` + 资产 taxonomy | 去广告、过滤纯价格 tick、保留重要/有信号新闻 |
| 转载分组 | `build_syndication_groups()` | 防止转载被误认为独立来源 |
| Mention 抽取 | LLM prompt + rule fallback | 把一条新闻拆成一个或多个 `EventMention` |
| 事件召回 | `retrieve_candidate_events()` | 从已有 `CanonicalEvent` 中找候选 |
| 事件打分 | `score_mention_to_event()` | subject/object/action/entity/type/location/time/stage/source 加权 |
| 事件裁决 | 阈值自动 + `event_pair_judge` LLM | 判断 `SAME_EVENT`、`UPDATE`、`CONTRADICT` 等关系 |
| 资产映射 | taxonomy 召回 + framework node 召回 + 可选 LLM | 生成 `AFFECTS_ASSET`、`MAPS_TO_NODE` |
| Topic 映射 | `score_event_to_topic()` + 可选 `topic_membership_judge` | 生成 `BELONGS_TO_TOPIC` / `TopicMembership` |
| Review gate | 低置信、未来时间、日历提醒、规则新建 Topic | 防止候选被误展示为高置信事实 |
| 主题抽取 | `recent_news_topics._prompt()` | 从半日新闻 signals 抽稳定 MarketTopic |
| 证据权重 | `evidence_weight=0` 上下文，`>0` 才是支持证据 | 防止半日简报 / 派生报告污染证据链 |
| 平台展示 | `news_relation_quality` | 给普通用户展示候选态质量概览 |

## 当前提示词入口

| 提示词 | 文件 / 函数 | 用途 |
| --- | --- | --- |
| EventMention batch 抽取 | `quanta_agents/prompts/news_event_mention_batch_extraction.v1.txt` | 逐条新闻抽取现实事件；不补外部事实；不把预测当事实 |
| EventMention single 抽取 | `quanta_agents/prompts/news_event_mention_extraction.v1.txt` | 单条新闻抽取；与 batch 规则一致 |
| EventPair 裁决 | `quanta_agents/prompts/event_pair_judge.v1.txt` | 判断新 mention 与已有 event 的现实关系 |
| 资产 / 框架节点映射 | `quanta_agents/prompts/event_asset_framework_mapping.v1.txt` | 判断 direct / indirect / contextual 资产影响 |
| Topic membership 裁决 | `quanta_agents/prompts/topic_membership_judge.v1.txt` | 判断 creates / updates / supports / contradicts / validates / contextualizes |
| 最近新闻主题抽取 | `quanta_agents/market_themes/recent_news_topics.py::_prompt()` | 从 signals 抽持续维护的 MarketTopic |
| Topic merge | `quanta_agents/market_themes/recent_news_topics.py::_merge_prompt()` | 多 batch topic 合并 |

## 展示边界

- `EventMention` 和 `CanonicalEvent` 默认是候选，不是已确认事实。
- `event_report`、`derived_summary`、半日简报包装层默认 `evidence_weight=0.0`，只能展示为“上下文线索”。
- 只有 `evidence_weight > 0` 的对象才能计入 `supporting_evidence_count`。
- `review_queue_count > 0` 时，页面应提示“待复核”，不要展示为正式结论。
- Topic 名称应尽量中性稳定；情绪化短标题只能作为展示别名或状态说明。

