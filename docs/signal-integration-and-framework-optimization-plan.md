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

### WO-DEV-20260620-007 v1 落地

`futures_daily.logic_chain` 已新增两个候选解释层产物，不改变 `market_brief` 和 `commodity_summary` 的主展示合同：

- `brief_thesis_anchor.json` / `brief_thesis_anchor.v1`
  - 从 `commodity_summary.detailed_analysis[asset]` 抽取资产级期市速递 baseline。
  - 记录 `direction`、`thesis_title`、`main_thesis`、`key_evidence`、`tracking_items`、`risk_items` 和 `invalidation_conditions`。
  - 定位是教师样本和质量锚，不是新的日报正文。

- `brief_logic_benchmark_map.json` / `brief_logic_benchmark_map.v1`
  - 把每条动态 `logic_chain` 映射回对应的 `brief_thesis_anchor`。
  - 对信号分为 `inherited_signals`、`new_signals`、`conflict_signals`、`pending_observations`。
  - 明确 `primary_display_contract=market_brief_and_commodity_summary`，`logic_chain_role=incremental_signal_evidence_skeleton`。
  - 同步嵌入 `logic_chains.assets[asset].brief_logic_benchmark_map`，供平台后续展示“继承/新增/冲突/待观察”。

质量边界：

- 支持 baseline 的动态信号可进入解释层和跟踪层。
- 与 baseline 冲突的实时信号只进入 `conflict_signals`，并要求人工复核，不自动改写期市速递结论。
- 只有当人类接受逻辑链叙事质量后，动态链才可升级为主展示候选。

### WO-DEV-20260620-008 合同冻结

`quanta_data` 已成为以下对象的 schema source of truth，`quanta_agents` 只消费或生成符合 schema 的对象，不在 runtime 内另造字段合同：

| 对象 | Schema | `quanta_agents` 写入定位 |
| --- | --- | --- |
| `brief_thesis_anchor.v1` | `/Volumes/数字大脑/quanta_data/configs/schemas/brief_thesis_anchor.v1.schema.json` | `futures_daily` run 产物；必要时包装进 `agent_workspace/candidates/signal_map` |
| `brief_logic_benchmark_map.v1` | `/Volumes/数字大脑/quanta_data/configs/schemas/brief_logic_benchmark_map.v1.schema.json` | `futures_daily` run 产物；平台只作为解释层读取 |
| `research_signal.v1` | `/Volumes/数字大脑/quanta_data/configs/schemas/research_signal.v1.schema.json` | 后续 opinion radar、研报、行情/基本面、Polymarket、外部 agent 的统一中间对象 |
| `theme_anchor.v1` | `/Volumes/数字大脑/quanta_data/configs/schemas/theme_anchor.v1.schema.json` | 后续研报反复主题、期市速递主线和新闻事件的主题生命周期对象 |

实现约束：

- `market_brief` 和 `commodity_summary` 的输出合同不在本阶段改变。
- `brief_logic_benchmark_map` 中的冲突只进入解释层和人工复核，不自动改写期市速递结论。
- `research_signal.source_role` 只能是 `news`、`research_report`、`market_data`、`fundamental_data`、`web_info`、`human`、`agent`。
- Polymarket 只能生成 `source_role=web_info`、`signal_kind=event_definition`、`confidence_label=low_confidence_signal` 的低置信事件定义信号，必须保留 settlement rule、流动性、价差和时间窗口。
- 新增 signal/theme 生成器时，验收命令必须包含 `quanta_data/configs/schemas/*.schema.json` 的 JSON Schema 校验。

### WO-DEV-20260620-009 runtime mapper v1

新增 `quanta_agents.signal_mapping`，第一版只做统一中间层的候选生成，不重构 `opinion_radar` 和 `polymarket_daily` runtime：

- CLI：`quanta-signal-theme-map` / `python -m quanta_agents.signal_mapping.mapper`
- 输入：
  - `brief_thesis_anchor.json`
  - `brief_logic_benchmark_map.json`
  - WeChat canonical document
  - WeChat `research_report_evidence_unit.v1`
- 输出：
  - `agent_workspace/candidates/signal_map/YYYY/MM/DD/CAND-SIGNAL-MAP-*/research_signals.json`
  - `agent_workspace/candidates/theme_anchor/YYYY/MM/DD/CAND-THEME-ANCHOR-*/theme_anchors.json`
  - `agent_workspace/runs/signal_mapping/YYYY/MM/DD/RUN-*/run_manifest.json`
- 校验：
  - `research_signal.v1.schema.json`
  - `theme_anchor.v1.schema.json`

映射策略：

- 期市速递 baseline 生成 `market_brief_thesis` 类型的 `theme_anchor`，并生成 `thesis_support` signal。
- `brief_logic_benchmark_map` 中的 inherited/new/conflict/pending 行分别转成 `thesis_support`、`thesis_conflict` 或 `agent_observation` signal。
- WeChat evidence 生成 `source_role=research_report`、`signal_kind=theme_update` 的 signal，并按研报标题/章节/规则化主题名生成 `theme_anchor`。
- 空标题或噪音标题不直接进入主题层，必须降噪成受控标题，例如“美联储政策转鹰”“美伊协议与霍尔木兹通航”。
- Polymarket 只提供 hook：必须保持 `source_role=web_info`、`signal_kind=event_definition`、`confidence_label=low_confidence_signal`，并保留 settlement rule、价差、流动性和时间窗口。

本轮真实样例：

- `agent_workspace/candidates/signal_map/2026/06/18/CAND-SIGNAL-MAP-20260618-043125900501/research_signals.json`
- `agent_workspace/candidates/theme_anchor/2026/06/18/CAND-THEME-ANCHOR-20260618-043125900501/theme_anchors.json`
- `agent_workspace/runs/signal_mapping/2026/06/18/RUN-WO-DEV-20260620-009-SIGNAL-THEME-043125900501/run_manifest.json`

剩余风险：

- 当前 WeChat 映射仍是单报告内的主题候选，尚未做跨报告重复主题归并。
- 部分研报栏目标题，如“贵金属”“国债期货”，仍需要下一步用 `theme_anchor` 生命周期和多来源重复出现频率做降噪。

### WO-DEV-20260620-012 incremental state v1

本轮新增 `quanta_agents.signal_mapping.incremental_state`，把既有 `research_signal` /
`theme_anchor` 候选从“截面候选集”推进到“历史状态更新”：

- CLI：`quanta-signal-incremental-state` / `python -m quanta_agents.signal_mapping.incremental_state`
- 输入：
  - 一个或多个 `research_signals.json`
  - 一个或多个 `theme_anchors.json`
  - 可选上一版 `incremental_state/latest/research_state.json`
- 输出：
  - `agent_workspace/candidates/incremental_state/YYYY/MM/DD/CAND-INCREMENTAL-STATE-*/research_state.json`
  - `agent_workspace/runs/incremental_state/YYYY/MM/DD/RUN-*/run_manifest.json`

状态对象分三层：

```text
theme_state    跨日报/新闻/Polymarket/研报的主题生命周期
event_state    同一事实或同一合约判定事件的观察历史
dimension_state 同一资产-框架维度-主题下的方向、冲突和证据累计
```

LLM 接入边界：

- LLM 不直接改历史 state。
- LLM 在信息处理层生成 `llm_normalization` / `semantic_normalization` 字段，例如：
  - `canonical_theme_id`
  - `canonical_title_zh`
  - `canonical_event_id`
  - `theme_type`
  - `framework_node_refs`
- 增量状态机优先消费这些 canonical 字段；缺失时才使用确定性 `title + asset + theme_type`
  匹配兜底。

运行示例：

```bash
quanta-signal-incremental-state \
  --root /Users/miniquanta/Documents/quanta_data \
  --signal-set agent_workspace/candidates/signal_map/2026/06/18/CAND-SIGNAL-MAP-*/research_signals.json \
  --theme-set agent_workspace/candidates/theme_anchor/2026/06/18/CAND-THEME-ANCHOR-*/theme_anchors.json \
  --date 20260618
```

生产模式如需推进下一轮增量基线，显式加 `--write-latest`。默认只写 candidate，避免诊断运行覆盖
`latest`。

### WO-DEV-20260620-010 opinion radar anchoring v1

`opinion_radar` 的主题生成顺序改为：

1. 读取 `agent_workspace/candidates/theme_anchor/**/theme_anchors.json`，或使用 CLI/API 传入的固定 theme anchor candidate set。
2. `service.snapshot` 对快讯 bucket 先做 theme_anchor 匹配；命中时用 anchor 标题作为主题主名，原自由聚类名保留为 `free_theme`。
3. `news_logic` 对每条 asset/framework event 先做 theme_anchor 匹配，再聚合到 dimension 和 asset。
4. 未命中的主题或事件必须标记为 `anchoring_status=unanchored_theme_candidate`，继续作为自由候选而不是伪装成研报已验证主题。
5. 命中的对象必须输出 `theme_anchor_refs`、`theme_anchor_match_method`、`theme_type`，并保留 `promotion_policy=review_required` 与 `review_state=machine_candidate`。

当前输出位置不变：

- `agent_workspace/candidates/opinion_radar/latest/radar.json`
- `agent_workspace/candidates/opinion_radar/news_logic/latest/news-logic.json`
- `agent_workspace/candidates/opinion_radar/reports/latest/market-radar-report.json`

治理边界：

- `theme_anchor` 仍是 candidate，不是 gold。Radar 只能引用它做主题锚定和候选印证，不能自动晋级正式知识。
- 匹配策略是保守关键词/资产重叠，匹配失败时 fallback 自由聚类；后续可以加入跨报告频率、人工审核标签和语义匹配。
- Polymarket runtime 暂未接入本轮 matcher，后续仍按 `web_info/event_definition/low_confidence_signal` 进入同一主题层。

### WO-DEV-20260620-011 Polymarket signal mapper v1

新增 `quanta_agents.signal_mapping.polymarket_mapper`，把 Polymarket 日报热点映射为 `research_signal.v1`，但不改 `polymarket_daily` runtime，也不写 gold：

- CLI：`python -m quanta_agents.signal_mapping.polymarket_mapper`
- 默认输入：
  - `agent_workspace/candidates/polymarket_daily/latest/hotspots.json`
  - `agent_workspace/candidates/polymarket_daily/latest/manifest.json`
  - 可选固定 `theme_anchors.json`，用于复现主题锚匹配
- 输出：
  - `agent_workspace/candidates/signal_map/YYYY/MM/DD/CAND-SIGNAL-MAP-*/research_signals.json`
  - `agent_workspace/candidates/signal_map/YYYY/MM/DD/CAND-SIGNAL-MAP-*/manifest.json`
  - `agent_workspace/runs/signal_mapping/YYYY/MM/DD/RUN-*-POLYMARKET-SIGNAL-*/run_manifest.json`

强约束：

- 每个 Polymarket signal 必须是 `source_role=web_info`、`signal_kind=event_definition`、`confidence_label=low_confidence_signal`。
- Polymarket 源字段 `primary_probability` 只能作为合约交易价格写入 `market_observation.price`；`market_observation.probability` 必须保持 `null`。
- `market_observation` 必须保留 `price`、`spread`、`liquidity`、`liquidity_label`、`settlement_rule`、`time_window`。
- 命中主题锚时只在 `theme_refs` 写 schema-safe 的 `{id,label,ref_type=theme}`；匹配详情写入 candidate set 的 `theme_anchor_matches`。
- 未命中主题锚时保留 `theme_anchor_status=unanchored_theme_candidate` 注释，不能伪装成已被研报验证的主题。

本轮真实样例：

- `agent_workspace/candidates/signal_map/2026/06/19/CAND-SIGNAL-MAP-20260619-205904909795/research_signals.json`
- `agent_workspace/runs/signal_mapping/2026/06/19/RUN-WO-DEV-20260620-011-POLYMARKET-SIGNAL-205904909795/run_manifest.json`
- 生成 25 个 `research_signal`；其中 2 个锚定到研报主题，23 个保留为 unanchored 候选；schema validation passed。

剩余风险：

- 当前匹配仍是保守关键词/资产规则。US/Iran 这类事件如果研报主题只有“沪镍”等宽泛标题，不会强行锚定，避免把 prediction-market 事件误接到错误品种。
- 后续需要跨报告重复主题归并和人工审核标签，才能提升 Polymarket 与研报主题的召回率。

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

2026-06-20 运行约束补充：

- 舆情雷达卡片标题必须代表当前 radar bucket（如 `美联储与利率`、`铜`），`theme_anchor` 只作为追踪引用，不能把候选锚点标题反向覆盖到宏观/品种桶。
- 宏观 bucket 不接受带具体期货品种资产引用的 theme anchor 作为直接锚定，避免新闻文本里出现“黄金”等词就把宏观主题改名为贵金属主题。
- 品种 bucket 只有在 bucket 品种与 theme anchor 的 `asset_refs` 一致时才允许锚定；宽泛多品种标题（如 `黄金/白银`）不应覆盖单品种 radar 卡片标题。

## 7. Polymarket 改造

Polymarket 输出必须降级为辅助信号：

- `source_role=web_info`
- `confidence_label=low_confidence_signal`
- 必填 `event_definition_layers`
- 必填 liquidity/spread/settlement_rule/time_window
- 必须尝试映射到 theme_anchor 或 framework_node

展示时写成“预测市场对事件定义/定价的观察”，不要写成“真实概率判断”。

2026-06-20 运行约束补充：

- `fact_layer` 和候选主题展示字段使用单一中文显示名，不再拼接 `中文 / English question`。
- 原始英文问题只保留在 `source_ref.url`、原始 `hotspots.json` 和溯源字段里，供审计回查。
- 规则翻译只能兜底常见问题形态；正式方案仍需 LLM 对 event / condition / outcome 做中文主题归并，尤其是同一事件下多个到期日或区间合约。

## 8. 增量主题报告维护

`signal_mapping.theme_report` 是第一版把“单篇研报画像 + 新闻快讯”推进到历史主题状态的候选链路。它不替代正式舆情雷达，也不写 `gold`，只写 `agent_workspace/candidates/theme_report_maintenance`。

输入：

- `futures_daily_single_report_analysis/{date}/WECHAT-PROFILE-V1/per_report/**/*.json`
- MySQL 新闻快讯，按日读取并通过 taxonomy 映射资产/宏观桶
- `research_signal.v1` 和 `theme_anchor.v1` schema

处理顺序：

1. 单篇研报字段先按 `日期 + 品种 + 主题` 聚合成日级 signal，避免同一篇/同一天重复刷主题。
2. 新闻先过滤低信号市场通知，例如 ETF 日报、挂单、LOF 停牌、金饰报价和营销型直播/点击标题。
3. 价格路径、技术面和期权/ETF 交易型段落默认排除出主题主线；需要回溯时用 `--include-price-validation`。
4. 研报侧资产名用 taxonomy 归一，避免同一品种在新闻和研报里出现两个 asset id。
5. 日级 signal/theme 逐日进入 `incremental_state`，维护 `first_seen_at`、`last_seen_at`、`support_count` 和 `observation_count`。

2026-06-20 V4 真实运行：

```bash
quanta-theme-report-maintain \
  --root /Volumes/数字大脑/quanta_data \
  --start-date 20260401 \
  --end-date 20260620 \
  --max-report-items-per-asset 3 \
  --max-news-per-day 500 \
  --work-order-id WO-DEV-20260620-THEME-REPORT-FULL-INCREMENTAL-V4 \
  --write-latest
```

输出：

- `agent_workspace/candidates/theme_report_maintenance/2026/06/20/CAND-THEME-REPORT-20260620-195701051067/`
- `agent_workspace/candidates/theme_report_maintenance/latest/theme_report.json`
- `agent_workspace/candidates/theme_report_maintenance/latest/research_state.json`

关键指标：

- 日期覆盖：2026-04-01 到 2026-06-20，共 81 天。
- 研报画像：3438 篇；活跃研报日期 61 天；研报日级主题信号 16912 条。
- 新闻：原始快讯 80381 条；映射新闻 29430 条；新闻事件信号 5511 条。
- 过滤：价格/技术项 12635 条；低信号新闻 3316 条。
- 状态：主题 827 个；事件 20831 个；schema validation passed。

剩余问题：

- `其他逻辑跟踪` 仍偏高，主要来自跨资产复盘、营销型标题和上游单篇研报抽取未能给出稳定字段。
- 下一步应在信息处理层接入 LLM normalization，输出 `canonical_title_zh`、`canonical_theme_id`、`canonical_event_id` 和 `theme_type`，再让状态机消费，而不是靠关键词继续堆规则。
- 前端展示应优先使用资产主题、宏观主题和新拆出的 `避险需求与央行购金`、`资金持仓与交易情绪` 等可解释主题；全局 `other` 只作为诊断桶。

## 9. 任务顺序

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
