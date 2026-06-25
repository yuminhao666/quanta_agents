# 市场主题日更状态设计

## 定位

市场主题不是一次性新闻聚类，也不是 LLM 直接写出的市场结论。

第一版先从期市速递的汇总层产物生成主题，不直接读取品种明细：

```text
期市速递 market_review.market_events_summary
  + 期市速递 market_review.market_logic_summary
  + 期市逻辑链 logic_summary.important_events_summary
  + 期市逻辑链 logic_summary.important_logic_summary
  -> 每日市场主题状态 market_theme_state
```

它的目标是每天迭代“市场正在交易什么、哪些主题在强化、哪些在降温、哪些需要继续观察”，并给后续事件状态机、图谱触发和前端主题页提供可追溯的中间状态。

长期设计见 `docs/quanta-knowledge-storage-layout.md`。本文中的 `market-theme-state` 是日频观察快照；稳定话题节点、`TopicMembership`、证据读模型、状态历史和 Wiki 投影后续由 `market_topics` 维护器承接。

## 为什么先用汇总层输入

| 输入 | 角色 | 使用方式 |
| --- | --- | --- |
| 期市速递 `*_commodity_marketreview.json` | 首屏日频质量基准 | 读取 `market_events_summary` 和 `market_logic_summary`，作为面向人类的“重要市场事件 / 市场核心逻辑” |
| 期市逻辑链 `logic_summary.json` | 结构化逻辑汇总 | 读取 `important_events_summary` 和 `important_logic_summary`，作为逻辑链页面专用的“重要事件总结 / 重要逻辑梳理” |
| 舆情雷达当前主题 | 可选盘中热度层 | 仅在显式打开时读取 `opinion_radar/latest/radar.json` 的 `windows.{hours}.themes` |
| 新闻半日简报 | 可选事件链叙事层 | 仅在显式打开时读取 `news_brief/half_day/latest/half-day-news-brief.json` 的 `llm_brief.key_news`、`watch_items`、`asset_notes` |

默认生成模式是 `futures_summary_only`，不读取 `commodity_summary.detailed_analysis[*]` 和 `brief_thesis_anchor.assets`，避免主题退化成具体品种卡片。研报全量聚类、Polymarket、基本面数据和人工输入后续再接入，不作为第一版阻塞项。

## 输出对象

建议新增候选产物 `market_theme_state.v1`。

```json
{
  "schema_version": "market_theme_state.v1",
  "status": "candidate",
  "as_of_date": "2026-06-21",
  "generated_at": "ISO8601",
  "source_refs": {
    "futures_daily_run_dir": "agent_workspace/candidates/futures_daily_raw_runs/...",
    "market_review": "agent_workspace/candidates/futures_daily_raw_runs/.../*_commodity_marketreview.json",
    "logic_summary": "agent_workspace/candidates/futures_daily_raw_runs/.../logic_summary.json",
    "opinion_radar": "agent_workspace/candidates/opinion_radar/latest/radar.json",
    "half_day_news_brief": "agent_workspace/candidates/news_brief/half_day/latest/half-day-news-brief.json",
    "previous_state": "agent_workspace/candidates/market_themes/latest/market-theme-state.json"
  },
  "themes": [
    {
      "theme_id": "MKT-THEME-...",
      "title": "美伊谈判与霍尔木兹通行不确定",
      "scope": "macro",
      "asset_refs": [],
      "driver_refs": ["geopolitics", "risk_premium"],
      "status": "emerging | active | cooling | archived",
      "strength": 0.0,
      "confidence": 0.0,
      "trend": "strengthening | weakening | stable | reversing",
      "first_seen": "ISO8601",
      "last_seen": "ISO8601",
      "evidence": {
        "futures_daily_events": [],
        "radar_theme_refs": [],
        "news_brief_refs": []
      },
      "change_explanation": "今日较上一状态的变化说明",
      "watch_items": []
    }
  ],
  "changes": {
    "new_themes": [],
    "reinforced_themes": [],
    "weakened_themes": [],
    "conflict_themes": [],
    "archive_candidates": []
  },
  "quality_notes": []
}
```

## 主题 ID 原则

主题聚类目标必须是稳定概念 ID，不是当天新闻的自然语言标题。

第一版 `theme_id` 可以由以下稳定字段生成：

```text
summary_scope + driver_refs + normalized_event_concept
```

示例：

```text
MKT-THEME-MACRO-GEOPOLITICS-US-IRAN-HORMUZ
MKT-THEME-MACRO-FED-RATE-PATH
MKT-THEME-MACRO-INVENTORY-PRESSURE
```

标题可以每天更新，`theme_id` 不随标题变化。方向、强弱、时间窗口都放在状态字段里，不写进节点名称。

## 与长期 Topic Node 的关系

每个稳定主题本质上是一个 `MarketTopic`：

- 它是新闻热点，可被舆情雷达、半日新闻简报和主题页展示；
- 它也是资产驱动逻辑节点，可连接 `DriverState`、`AssetSignal` 和 causal graph；
- 它需要长期维护 aliases、定义、证据、状态历史、资产链接、driver 链接和人工 review。

因此：

```text
market-theme-state.json
  -> 每日观察快照
  -> market_topics 维护器
  -> canonical_objects/market_topics/topics/{topic_id}.json
  -> relations/topic_memberships/{yyyy}/{mm}/{dd}/topic-memberships.jsonl
  -> event_logs/market_topics/{yyyy}/{mm}/{dd}/topic-events.jsonl
  -> evidence_links/market_topics/{yyyy}/{mm}/{dd}/topic-evidence.jsonl
  -> states/market_topics/latest/{topic_id}.json
  -> agent_workspace/candidates/market_topics/latest/topic-evolution-read-model.json
  -> wiki_projection/topics/{topic_slug}.md
```

`market-theme-state.json` 可以重复生成和覆盖 latest；`market_topics` 才是稳定节点库。Topic node 不直接复制每日报告全文，只保存结构化定义。对象为什么归入话题，要通过 `TopicMembership` 记录；状态变化通过 `TopicState` 和 event log 记录；`topic-evidence.jsonl` 只是给前端展示的读模型。

为了让人看到 topic 的演化过程，应额外编译 `topic-evolution-read-model.json`：

- 按 run 串起每个 topic 的 `first_seen`、`last_seen` 和生命周期状态；
- 展示每次观察的 heat、strength、confidence 和 delta；
- 展示新增 evidence / membership；
- 标记本次未再观察到的 topic，作为衰减和归档观察信号；
- 输出 `topic -> snapshot -> evidence` 的图谱 read model，给 Alpha 页面直接渲染。

建议 topic object：

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
  "aliases": ["霍尔木兹通航", "美伊和谈", "中东地缘缓和"],
  "linked_logic_node_ids": ["LOGIC_GEOPOLITICAL_RISK_PREMIUM"],
  "linked_driver_ids": ["DRIVER_OIL_RISK_PREMIUM"],
  "asset_refs": ["原油", "能化链", "贵金属"],
  "status": "candidate",
  "created_at": "ISO8601",
  "updated_at": "ISO8601"
}
```

Topic state 与 topic definition 分离：

```json
{
  "schema_version": "market_topic_state.v1",
  "topic_id": "MKT_TOPIC_US_IRAN_HORMUZ",
  "as_of": "ISO8601",
  "heat": 0.72,
  "strength": 0.45,
  "trend": "strengthening",
  "confidence": 0.63,
  "credibility_score": 0.61,
  "source_diversity": 0.73,
  "contradiction_score": 0.29,
  "dominant_assets": ["原油", "能化链", "贵金属"],
  "supporting_evidence_count": 4,
  "contradicting_evidence_count": 0,
  "source_run_ids": ["RUN-MARKET-THEME-..."]
}
```

Wiki 页面只作为 `MarketTopic` 的可读投影，不作为独立证据源，也不能直接回写 strength、edge weight、polarity 或数据值。

Topic 的热度和可信度必须分开：

- `heat` / `market_attention`：市场是否正在交易这个话题，允许由新闻频率、关注度和行情反应提高。
- `credibility_score` / `truth_status`：信息是否被确认，需要独立来源、官方口径、基本面数据或市场行为验证。

新闻快讯可以提高热度，但默认不等于已确认事实。Agent 简报、Wiki 和二次报告是派生产物，默认 `evidence_weight=0`，只能用于理解和归类，不能作为独立证据反复计权。

## 每日更新算法

1. 读取上一日或 latest `market-theme-state.json`，构成 previous state。
2. 读取当日最新期市速递 run 的两个汇总层文件：
   - `*_commodity_marketreview.json`
   - `logic_summary.json`
3. 从四个字段抽取主题 evidence：
   - `market_events_summary`
   - `market_logic_summary`
   - `important_events_summary`
   - `important_logic_summary`
4. 默认不读取 `commodity_summary` 的品种级明细；如需接入 `opinion_radar` 或 `news_brief`，必须显式打开。
5. 把汇总字段统一为 `theme_evidence`：
   - `source_type = futures_daily`
   - `asset_refs = []`
   - `driver_refs`
   - `summary`
   - `source_refs`
   - `weight`
6. 对每条 evidence 匹配已有 theme：
   - 优先按 `theme_id`
   - 其次按 asset scope + driver refs + normalized keywords
   - 匹配不到则生成 `new_theme_candidate`
   - 默认使用模型 Agent 做主题抽取和证据归类，模型只能引用候选证据包中的 `evidence_id`，不能补充外部事实。
   - 代码层校验模型输出的 `evidence_id`、driver、稳定 concept ID 和 source refs；无效引用丢弃，无法形成证据链的主题不入库。
   - 确定性规则只作为 fallback / guardrail：模型不可用、显式 `--no-llm`、或模型输出无法通过校验时，才使用规则分类。
   - 规则质量门仍需区分“主导变量”和“顺带出现的词”：例如 OPEC 主题必须命中 OPEC / 桶日等能源供给锚，贸易主题必须命中关税 / 制裁 / 出口限制 / 232 调查等政策扰动锚，库存主题必须命中仓单 / 累库 / 去库 / 港口库存 / 库存创新高等库存锚。
   - 对 `产量`、`出口政策`、`库存` 这类宽泛词不能单独建主题；证据不够强时退回 generic 候选，单条弱证据不进入前台主题列表。
7. 把对象与主题的关系写成 `TopicMembership`：
   - `object_id` 指向 Event / Claim / Evidence / Observation / Document / DerivedReport。
   - `membership_role` 区分 primary / secondary / contextual。
   - `relation_to_topic` 区分 creates / updates / supports / contradicts / contextualizes / validates。
   - `assigned_by`、`agent_run_id`、`method` 和 `reason` 必须保留，方便人工复核和回放。
8. 更新主题状态：
   - `market_review` 代表期市速递首屏质量基准。
   - `logic_summary` 代表结构化逻辑链的汇总表达。
   - 可选的半日新闻简报和舆情雷达只作为增量层，不参与默认主题生成。
   - 旧主题每日衰减，连续缺少证据进入 cooling / archive candidate。
9. 输出 state、changes 和 quality notes。

## 评分建议

第一版使用可解释线性打分，避免黑箱：

```text
strength_today =
  0.45 * futures_daily_score
  + 0.35 * half_day_news_score
  + 0.20 * radar_heat_score
  + previous_strength * decay
```

建议参数：

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `decay` | `0.70` | 主题未被新证据验证时衰减 |
| `new_theme_threshold` | `0.35` | 超过后进入 emerging |
| `active_threshold` | `0.55` | 超过后进入 active |
| `cooling_threshold` | `0.20` | 低于后进入 cooling |
| `archive_missing_days` | `3` | 连续三天缺证据后进入 archive candidate |

## 产物路径

```text
agent_workspace/candidates/market_themes/{yyyy}/{mm}/{dd}/market-theme-state.json
agent_workspace/candidates/market_themes/{yyyy}/{mm}/{dd}/market-theme-state.md
agent_workspace/candidates/market_themes/latest/market-theme-state.json
agent_workspace/candidates/market_themes/latest/market-theme-state.md
agent_workspace/runs/market_themes/{yyyy}/{mm}/{dd}/RUN-MARKET-THEME-*/manifest.json
```

`latest` 只作为前端和后续 Agent 的稳定读取入口，归档路径用于回放和审计。

## 前端呈现

第一版可以加在舆情雷达页或分析工作台页：

- 今日强化主题
- 今日新增主题
- 冲突/待验证主题
- 主题证据链：期市速递事件、雷达主题、半日新闻简报 source refs
- 主题生命周期：first_seen、last_seen、trend、status
- 主题归类关系：primary / secondary / contextual membership
- 信息可信状态：truth_status、credibility_score、contradiction_score
- 演化过程：每次 run 的状态 delta、新增证据、未再次观察和衰减提示

展示上不要把它包装成最终投资结论。它是“市场主题状态”，不是交易建议。

## 与现有模块关系

| 模块 | 关系 |
| --- | --- |
| `futures_daily.logic_chain` | 提供期市速递 baseline、重要事件和主线锚 |
| `opinion_radar.export` | 提供当前热度主题和重要新闻流 |
| `news_brief.hourly` | 提供半日新闻聚类和观察项 |
| `asset_event_state` | 后续可以消费主题状态作为 driver update 输入之一 |
| `research_logic_graph` | 后续可用主题状态反向发现需要拆分、合并或补充的概念节点 |

## 第一版边界

- 不重写 `opinion_radar.synthesis`，避免把主题生命周期和雷达展示耦合。
- 不自动改写期市速递结论，期市速递仍是日频基准。
- 不把 LLM 生成的标题作为主题 ID。
- 所有主题变化必须保留来源路径和 source refs。
- 不把 Agent 简报、Wiki 或二次报告当成新证据；这类派生产物只能通过 source refs 回到底层 Event / Claim / Observation。
- 若证据不足，输出 `quality_notes`，而不是补写看似完整的结论。
