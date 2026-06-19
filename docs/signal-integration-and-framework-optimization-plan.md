# Signal Integration And Framework Optimization Plan

本文聚焦 `quanta_agents` 的三类问题：期市逻辑链质量、舆情雷达主题混乱、Polymarket 与研报/新闻割裂，以及框架历史指标和权重优化不清晰。

## 1. 当前诊断

### 期市逻辑链

优点：

- `futures_daily.logic_chain` 已经有证据去重、方向净额、冲突降权、维度权重和 market observation 过滤。
- 固定工作流生成的期市速递质量较稳定，因为输入、字段和输出结构固定。
- 动态逻辑链的生成方式仍然是后续实时新闻、行情、研报增量和外部事件源处理所需要的骨架。

问题：

- 框架维度仍混有历史 `dimension_type`，如 `cost_profit`、`policy_macro`、`weather_yield`。
- `logic_templates`、`default_weight`、`scoring_rules` 缺失，使权重更多依赖当前证据临时打分。
- 输出主要留在 `agent_workspace/candidates/futures_daily_raw_runs`，没有统一沉淀成 canonical/evidence/signal。
- 当前版本的期市逻辑链在叙事质量、主次取舍和可读性上低于期市速递；不能直接用动态链替代期市速递。

### PM 决策：期市速递做质量基准，逻辑链做动态骨架

短期不把“期市逻辑链”当作新的日报成品，而是拆成两层：

| 层 | 定位 | 质量来源 | 输出方式 |
| --- | --- | --- | --- |
| `market_brief` / 期市速递 | 人类可读的日频研究成品和质量基准 | 固定工作流、稳定字段、人工可验收叙事 | 继续作为平台主展示和评测样本 |
| `logic_chain` / 动态逻辑链 | 实时信息源进入后的证据、主题、因果和跟踪骨架 | 多源 signal、theme anchor、framework node、冲突验证 | 先作为解释层、增量层和后续实时处理引擎 |

后续每次优化逻辑链，都必须回答三个问题：

1. 它继承了期市速递中的哪条高质量主线？
2. 它新增了哪些实时 signal、证据或冲突？
3. 它有没有降低成品叙事质量，如果有，必须留在候选解释层，不进入主展示。

### 舆情雷达

优点：

- 已支持 MySQL 快讯读取、噪音过滤、时间窗口、主题热度和 `news_logic`。
- `news_logic` 可把快讯映射到资产和 framework node，并与最近期市逻辑链做 supports/conflicts/new_signal 判断。

问题：

- 主题命名仍偏聚类和快讯热度，没有优先对齐研报中反复出现的主题。
- 新闻没有先成为 evidence/signal，再进入主题生命周期。
- 同一事件的多条快讯、Polymarket 合约和研报段落没有统一 event definition。

### Polymarket

优点：

- 已有独立采集、热点排序、candidate/review package。
- 适合作为事件关注度和市场定价分歧的辅助信号。

问题：

- 当前按 Polymarket 类别聚合，与期货资产、研报主题和框架维度割裂。
- 预测市场价格容易被误读为真实概率，缺少 settlement rule、时间窗口、流动性和价差标签。

## 2. 统一中间层

新增共享目标对象：`research_signal.v1`。代码上先不必新增根包，但所有工作流都要能输出或消费同形结构。

```text
evidence capsule
  -> research_signal
  -> theme update
  -> framework dimension score
  -> logic chain / report / dashboard
```

信号必须包含：

- `source_role`: news / research_report / market_data / fundamental_data / web_info / human / agent
- `asset_refs`
- `theme_refs`
- `framework_node_refs`
- `direction`, `strength`, `confidence`
- `time_window`
- `event_definition`
- `evidence_refs`
- `conflict_refs`

## 3. 研报作为主题锚

舆情雷达的第一步不应是自由聚类，而应是读取研报/期市速递中反复出现的主题锚：

```text
research_reports canonical/evidence
  -> repeated theme anchors
  -> news flash mapping
  -> Polymarket event mapping
  -> theme lifecycle update
```

主题锚字段：

```json
{
  "theme_anchor_id": "THA-20260620-001",
  "title": "美伊协议执行不确定",
  "source_refs": [],
  "asset_refs": ["原油", "黄金"],
  "framework_node_refs": [],
  "event_definition_layers": {
    "fact_layer": "是否签署文本",
    "political_layer": "双方是否执行",
    "time_window_layer": "协议生效后的观察窗口",
    "settlement_rule_layer": "Polymarket 合约判定规则"
  },
  "status": "candidate"
}
```

## 4. 期市逻辑链改造

目标不是替换当前期市速递，而是把期市速递变成 `logic_chain` 的质量基准和教师样本：

1. 保留固定工作流生成 `market_brief` 和 `commodity_summary`。
2. 从 `market_brief` 和 `commodity_summary` 抽取 `brief_thesis_anchor`：
   - 品种，
   - 主线标题，
   - 利多/利空/中性方向，
   - 关键证据句，
   - 跟踪指标，
   - 风险和失效条件。
3. 将每个因素落到 evidence/signal，而不是只留在 summary JSON。
4. `framework_alignment` 先用 normalized dimension aliases，减少历史维度污染。
5. `logic_chain` 读取同品种多来源 signals：
   - 研报解释信号，
   - 新闻边际信号，
   - 行情/基本面验证信号，
   - Polymarket event-definition 信号。
6. 输出 `brief_logic_benchmark_map` 和 `logic_chain_evidence_map`，说明：
   - 该动态主线对齐了哪条期市速递主线，
   - 哪些内容继承自期市速递，
   - 哪些内容来自新增实时信号，
   - 哪些冲突需要人工或后续数据验证。

第一阶段验收不是“逻辑链写得比期市速递好”，而是“逻辑链能解释期市速递，并承接新增信号而不破坏原成品质量”。

## 5. 框架权重优化

权重拆成四层，避免让短期新闻直接覆盖长期框架：

| 权重 | 来源 | 写入 |
| --- | --- | --- |
| `base_weight` | 人工审核的长期框架 | `gold/frameworks` |
| `activation_weight` | 近期 signal heat、source diversity、recency | candidate/run |
| `confidence_weight` | evidence 质量、冲突、人工 review | candidate/run |
| `validation_weight` | 价格/数据是否验证主线 | candidate/run |

推荐公式：

```text
effective_weight =
  base_weight * 0.45
  + activation_weight * 0.25
  + confidence_weight * 0.20
  + validation_weight * 0.10
```

`framework_weight_optimizer` 只能生成候选：

- LLM review failed 时必须 `pending_manual_review`。
- 任何权重变化必须引用 source signals 和 evidence refs。
- 不能自动写回 active framework。

## 6. 舆情雷达改造

舆情雷达分两层：

| 层 | 作用 | 输出 |
| --- | --- | --- |
| raw sentiment | 快讯热度、噪音过滤、时间窗口 | `opinion_radar` |
| logic signal | 映射到主题锚、framework node、研报主线和冲突 | `news_logic_signal_map` |

改造顺序：

1. 从研报 evidence 和期市逻辑链生成 `theme_anchors`。
2. 快讯先映射到 theme_anchor，再 fallback 到自由主题。
3. 对每个主题维护 supports/conflicts/new_signal/tracking。
4. 把 Polymarket 合约作为 `web_info` 信号参与同一主题，而不是单独展示一套结论。

## 7. Polymarket 改造

Polymarket 输出必须降级为辅助信号：

- `source_role=web_info`
- `confidence_label=low_confidence_signal`
- 必填 `event_definition_layers`
- 必填 liquidity/spread/settlement_rule/time_window
- 必须尝试映射到 theme_anchor 或 framework_node

展示时写成“预测市场对事件定义/定价的观察”，不要写成“真实概率判断”。

## 8. 任务顺序

1. 期市速递 baseline：生成 `brief_thesis_anchor` 和 `brief_logic_benchmark_map`，把当前高质量日报沉淀成评测样本。
2. `theme_anchor` 和 `research_signal.v1` 契约。
3. 研报 canonical/evidence 到 theme anchors。
4. Opinion radar 先映射 theme anchors。
5. Polymarket 映射 event definition。
6. Futures logic chain 消费 multi-source signals，但只作为增量解释层进入主展示。
7. Framework dimension alias 和 weight candidate review gate。

验收标准：

- 期市逻辑链能说明每条动态主线对应的期市速递主线、继承内容、新增内容和冲突内容。
- 任一资产页能看到研报、新闻、Polymarket、行情/基本面信号在同一主题下的支持/冲突关系。
- 任一框架权重候选能追溯到 evidence/signals，不再只靠历史指标名或 LLM 总结。
- 舆情雷达的主题能解释“来自哪些研报主题锚”，而不是只有聚类名称。
