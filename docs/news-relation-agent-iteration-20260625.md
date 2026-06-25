# News Relation Agent Iteration 2026-06-25

本文记录 2026-06-25 对新闻聚类、事件主题抽取、新闻-主题映射、动态主题时间线和证据链维护的架构迭代。它是一次真实数据开发记录，不替代长期 schema，也不把候选结果提升为 `gold`。

## 1. 目标

本轮目标是把“新闻、研报、数据直接互相链接”的旧思路，收敛为“先转换成不同知识对象，再通过共享锚点和显式关系汇合”的架构：

```text
news -> EventMention -> CanonicalEvent
research report -> Evidence -> AtomicClaim
fundamental data -> Observation
market data -> MarketObservation
prediction market -> ExpectationObservation
human note -> HumanClaim
agent report -> DerivedReport
```

这些对象通过 `Event`、`Topic`、`LogicNode`、`Asset`、`Time` 和 `Provenance` 关联；关系以 `ObjectRelation` 或 typed relation 表保存，不能只嵌在 JSON 字段里。

## 2. Agent 分工

| Agent | 角色 | 结果 |
| --- | --- | --- |
| Erdos | 数据和运行侦察 | 只读检查 `quanta_data` latest artifacts、LLM key 可用性、候选运行命令和当前架构缺口 |
| Bohr | 显式关系实现 | 增加 `news_event_batch` 到 object catalog 的投影 adapter，并把 news event run 的对象和关系写入 sidecar catalog |
| Leibniz | 内容质量 QA | 对真实 LLM 运行产物打分 `58/100`，指出事件归并、Topic 命名、review queue、证据断链和主线过度断言问题 |
| Raman | 并行开发尝试 | 超时关闭，结果未进入主线；本轮不依赖其输出 |

主线程负责架构文档、关键 bug 修复、真实数据 LLM 跑数、测试补齐和开发方案调整。

## 3. 真实数据运行

### 3.1 News Event LLM batch

命令：

```bash
.venv/bin/python -m quanta_agents.news_events.cli \
  --hours 12 \
  --limit 80 \
  --llm-batch-size 8 \
  --llm-workers 1 \
  --dry-run \
  --root /Volumes/数字大脑/quanta_data
```

第一次运行暴露 `event_matching` 中 naive/aware datetime 相减错误。修复后成功输出：

```text
/Volumes/数字大脑/quanta_data/agent_workspace/candidates/news_events/2026/06/25/RUN-NEWS-EVENT-20260625-144955
raw_news_count=80
filtered_news_count=69
event_mention_count=79
canonical_event_count=77
multi_asset_event_count=29
topic_count=52
llm_call_failure_count=0
llm_extracted_mention_count=79
```

结论：LLM 抽取链路可跑通，但 canonical merge 偏严，Topic 过多，低置信 mention 没有自动进入 review，是下一轮重点。

### 3.2 Recent News Topics

命令：

```bash
.venv/bin/python -m quanta_agents.market_themes.recent_news_topics \
  --quanta-root /Volumes/数字大脑/quanta_data \
  --half-day-path /Volumes/数字大脑/quanta_data/agent_workspace/candidates/news_brief/half_day/latest/half-day-news-brief.json \
  --no-hourly \
  --top 8 \
  --llm-evidence-limit 60 \
  --llm-batch-size 30 \
  --llm-batch-workers 1 \
  --require-llm
```

修复前运行：

```text
RUN-RECENT-NEWS-TOPICS-20260625-145549
topics=2
topic_ids=MKT_TOPIC_GOLD_SILVER_DOLLAR_RATE,MKT_TOPIC_HORMUZ_OIL
invalid_ref_count=0
```

修复 `DerivedReport/event_report` 计权后重新运行：

```text
RUN-RECENT-NEWS-TOPICS-20260625-151839
topics=2
topic_ids=MKT_TOPIC_GOLD_SILVER_CRASH,MKT_TOPIC_US_CRUDE_INVENTORY
invalid_ref_count=0
topic-evidence evidence_weight_1=0
topic-memberships updates=0
topic-memberships contextualizes=28
```

结论：半日简报这类派生报告或包装层 `event_report` 只应作为归类上下文，`evidence_weight` 必须为 `0.0`，计权只能来自底层 flash/event refs 或后续显式 `Observation`。

本轮后续补充了平台可直接读取的权重摘要字段，避免展示层把包装层行误读为独立证据：

```json
{
  "extraction": {
    "supporting_evidence_count": 0,
    "derived_context_count": 28,
    "evidence_weight_summary": {
      "candidate_evidence_count": 60,
      "candidate_positive_weight_count": 0,
      "candidate_zero_weight_count": 60,
      "used_evidence_count": 28,
      "supporting_evidence_count": 0,
      "contextual_evidence_count": 28,
      "derived_context_count": 28,
      "total_evidence_weight": 0.0
    }
  },
  "topic_states": [
    {
      "topic_id": "MKT_TOPIC_US_CRUDE_INVENTORY",
      "supporting_evidence_count": 0,
      "contextual_evidence_count": 4,
      "derived_context_count": 4,
      "evidence_weight_sum": 0.0
    }
  ]
}
```

字段约定：

- `supporting_evidence_count` 只统计 `evidence_weight > 0` 的证据关系。
- `derived_context_count` 统计 `DerivedReport`、`derived_summary`、`event_report` 等零权重包装层上下文。
- `evidence_weight_summary.total_evidence_weight` 是本 run 被 topic 使用的证据权重和；半日简报包装层应保持 `0.0`。

### 3.3 Topic Evolution

命令：

```bash
.venv/bin/python -m quanta_agents.market_themes.topic_evolution \
  --quanta-root /Volumes/数字大脑/quanta_data
```

输出：

```text
/Volumes/数字大脑/quanta_data/agent_workspace/candidates/market_topics/latest/topic-evolution-read-model.json
source_run_count=51
topic_count=421
```

结论：read model 能串起历史 run，但 `latest/` 是可变读点，严格 QA 应固定 `run_id` 和 source hashes。

### 3.4 News Event Object Catalog Projection

命令：

```bash
QUANTA_OBJECT_CATALOG_ENABLED=true \
.venv/bin/python -m quanta_agents.news_events.cli \
  --hours 6 \
  --limit 30 \
  --no-llm \
  --root /Volumes/数字大脑/quanta_data
```

输出：

```text
/Volumes/数字大脑/quanta_data/agent_workspace/candidates/news_events/2026/06/25/RUN-NEWS-EVENT-20260625-150405
raw_news_count=30
filtered_news_count=17
canonical_event_count=17
same_event_count=3
multi_asset_event_count=4
object_catalog_object_count=96
object_catalog_relation_count=71
```

结论：新闻侧已能把 `NewsItem/EventMention/CanonicalEvent/TopicMembership` 投影为显式 `ResearchObject/ObjectRelation`，覆盖 `DERIVED_FROM`、`BELONGS_TO_TOPIC`、`AFFECTS_ASSET` 和 `MAPS_TO_NODE`。

## 4. 已落地改动

| 文件 | 改动 |
| --- | --- |
| `docs/research-object-relation-architecture.md` | 新增对象、共享锚点、显式关系、状态快照和分阶段落地架构 |
| `quanta_agents/news_events/event_matching.py` | 修复 mixed timezone 事件匹配错误 |
| `quanta_agents/news_events/service.py` | 写出 `event_framework_node_links.jsonl`，增加 review queue gate，支持 env-gated object catalog projection |
| `quanta_agents/adapters/news_event_catalog_adapter.py` | 新增 news event batch -> object catalog adapter |
| `quanta_agents/adapters/__init__.py` | 支持 `kind="news_event_batch"` |
| `quanta_agents/market_themes/recent_news_topics.py` | Derived brief / event report 证据计权改为 `0.0`，并输出 evidence weight summary |
| `quanta_agents/news_events/topic_mapping.py` | rule-created Topic membership score 从高置信降为 `0.55` |
| `quanta_agents/prompts/news_event_mention_*.txt` | 明确 `market_attention` 和 `extraction_confidence` 必须是真实评分，不能复制 schema 示例值 |
| `tests/test_news_events.py` | 补 timezone、object catalog projection、rule Topic review、低置信未来日历事件 review 测试 |
| `tests/test_recent_news_topics.py` | 补 DerivedReport 计权和 contextual relation 回归测试 |

## 5. QA 发现

内容质量 Agent 对真实产物评分 `58/100`，只适合作为 candidate / 内部 QA 材料。

关键问题：

- `CanonicalEvent` 合并偏严，79 个 mention 只合成 77 个 event；双语和近重复未合并。
- 少量合并偏松，政策陈述和资金托管等相关但不同事实被合并。
- 日程提醒被当成原子事件，需要 `calendar_notice` / `release_time`。
- `event_time` 混淆报道时间和目标时间，需要拆 `reported_at`、`event_time`、`target_time`。
- `review_queue` 原本为空，但大量 mention 的 `extraction_confidence=0.0`。
- Topic 命名仍有日期、情绪词和日内标题化问题。
- `latest/` 非原子同步会导致主线引用和 evidence 文件断链。
- `news-mainlines` 有过度断言，需要 unsupported assertion 和 evidence completeness 检查。
- DerivedReport 已不计权，但仍需要在展示层和计分层继续隔离。

## 6. 方案调整

### 6.1 立即调整

- rule-created Topic 默认 `membership_score=0.55`，必须进入 `review_queue`。
- 低置信 mention、未来 event_time、日历提醒进入 `review_queue`。
- DerivedReport / half-day brief / mainline summary 的 `evidence_weight=0.0`。
- mixed timezone 全部归一到 UTC 后再计算。

### 6.2 下一轮开发优先级

1. **事件时间模型**：增加 `reported_at`、`event_time`、`target_time`、`release_time`，避免把未来指引当成已发生事实。
2. **CanonicalEvent 召回**：加入 bilingual alias、quote fingerprint、实体归一和更稳的 LLM pair judge fallback。
3. **Topic 稳定命名**：长期 Topic ID 保持中性稳定，把 `crash/selloff/date` 放入 `TopicState`、alias 或 event log。
4. **AtomicClaim adapter**：把研报 `Evidence` 转成 `AtomicClaim`，显式写入 `SUPPORTS/MAPS_TO_NODE/AFFECTS_ASSET/DERIVED_FROM`。
5. **Observation adapter**：把库存、价格、Polymarket 概率变成 typed observations，用 `VALIDATES/WEAKENS/CONTRADICTS` 连接到 claim/node/state。
6. **State engine**：只从关系 delta 增量更新 `TopicState`、`LogicEdgeState`、`AssetState`，并保存 `source_relation_ids`。
7. **Atomic latest publish**：run-scoped 文件校验通过后，再一次性更新 `latest` manifest、hashes 和 read model。
8. **Mainline QA gate**：增加 `evidence_completeness`、`unsupported_assertions`、`contradiction_score`，证据断链时自动降级措辞。

## 7. 验证

本轮代码验证：

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest \
  tests/test_news_events.py \
  tests/test_object_catalog.py \
  tests/test_recent_news_topics.py \
  tests/test_topic_evolution.py \
  tests/test_news_market_update.py
```

结果：

```text
31 passed
```

Lint：

```bash
.venv/bin/python -m ruff check \
  quanta_agents/news_events \
  quanta_agents/adapters \
  quanta_agents/market_themes \
  tests/test_news_events.py \
  tests/test_recent_news_topics.py \
  tests/test_topic_evolution.py \
  tests/test_news_market_update.py
```

结果：

```text
All checks passed
```
