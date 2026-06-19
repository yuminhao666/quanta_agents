# 期市逻辑链可视化页面需求文档

## 目标

基于 futures daily agent 当前产出的候选结果，开发一个“期市逻辑链”可视化页面，用于查看研报原文抽取后的框架映射、维度评分、品种交易主线、逻辑链和框架补充候选。

这个页面不是日报文章展示页，而是投研决策与框架进化工作台。

核心问题：

- 今天哪些品种有明确交易主线？
- 这些主线分别由哪些 framework 维度驱动？
- 框架维度的方向、重要性和证据是什么？
- 研报原始情绪分和框架加权分是否分歧？
- 哪些因素没有被现有框架很好覆盖，需要补充框架？

## 当前样例数据

当前已生成一套样例结果：

```text
/Volumes/数字大脑/quanta_data/agent_workspace/candidates/futures_daily_raw_runs/2026/06/16/RUN-20260616-002317-RAW-DAILY/
```

该目录下主要文件：

```text
manifest.json
20260616_commodity_summary.json
20260616_commodity_marketreview.json
framework_alignment.json
dimension_scores.json
trade_thesis.json
logic_chains.json
framework_update_candidates.json
framework_format_review.json
```

页面应优先通过 `manifest.json.outputs` 发现同目录下的文件，而不是硬编码文件名。

## 页面信息架构

建议页面名：`期市逻辑链`

页面分为 5 个区域：

1. 顶部运行信息区
2. 全市场概览区
3. 品种评分与主线列表
4. 品种详情区
5. 框架进化候选区

### 1. 顶部运行信息区

显示：

- 日期：`manifest.date`
- run id：`manifest.run_id`
- 原文目录：`manifest.raw_folder`
- 状态：`manifest.status`
- 产物完整性：summary / alignment / dimension scores / logic chains 是否存在

交互：

- 日期选择
- run 选择
- 刷新按钮
- 只看有分歧品种 toggle
- 只看 pending review toggle

### 2. 全市场概览区

数据来源：

```text
*_commodity_marketreview.json
```

字段：

- `market_events_summary`
- `market_logic_summary`
- `analysis_method`
- `timestamp`

展示方式：

- 左侧：市场事件摘要
- 右侧：核心逻辑摘要
- 下方：标签化展示关键宏观/产业主题，可从 `framework_alignment.factor_clusters` 取前若干个 cluster。

### 3. 品种评分与主线列表

数据来源：

```text
dimension_scores.json
trade_thesis.json
```

建议表格列：

| 列 | 字段 |
|---|---|
| 品种 | `trade_thesis.assets[asset].asset` |
| 框架分 | `trade_thesis.assets[asset].framework_score` |
| 原始研报分 | `trade_thesis.assets[asset].source_sentiment_score` |
| 推荐状态 | `trade_thesis.assets[asset].recommendation` |
| 分歧状态 | `trade_thesis.assets[asset].score_divergence.status` |
| 证据数 | `dimension_scores.assets[asset].evidence_count` |
| 主线摘要 | `trade_thesis.assets[asset].main_trade_thesis` |

排序：

- 默认按 `abs(framework_score)` 降序
- 支持按 `framework_score`、`evidence_count`、`score_divergence.status` 排序

颜色建议：

- 正分：红色系，表示偏多
- 负分：绿色系，表示偏空
- 接近 0：灰色
- 不要只依赖颜色，必须同时显示文字标签

分歧状态：

- `aligned`：框架分与原始研报分一致或差异小
- `opposite_direction`：方向相反，重点提示
- `large_gap`：方向一致但分差大
- `no_source_score`：缺少原始研报分

### 4. 品种详情区

点击品种后，在右侧或下方展示详情。

建议 tabs：

- `交易主线`
- `维度评分`
- `逻辑链`
- `原始证据`
- `框架映射`

#### 交易主线 tab

数据来源：

```text
trade_thesis.json
```

展示字段：

- `main_trade_thesis`
- `recommendation`
- `framework_score`
- `source_sentiment_score`
- `score_divergence`
- `supporting_dimensions`
- `opposing_dimensions`
- `watch_points`
- `invalidation_signals`

推荐布局：

- 顶部展示品种、框架分、推荐状态、分歧 badge
- 中间展示主线文本
- 下方两栏：
  - 后续跟踪点 `watch_points`
  - 证伪信号 `invalidation_signals`

#### 维度评分 tab

数据来源：

```text
dimension_scores.assets[asset].dimensions
```

核心字段：

```ts
type DimensionScore = {
  dimension_id: string
  dimension_label: string
  direction_score: number       // -10 到 10
  importance_score: number      // 0 到 100
  effective_weight: number      // 0 到 1
  evidence_count: number
  avg_confidence: number
  field_counts: Record<string, number>
  match_methods: Record<string, number>
  status_counts: Record<string, number>
  positive_factors: EvidenceFactor[]
  negative_factors: EvidenceFactor[]
  neutral_factors: EvidenceFactor[]
}
```

推荐可视化：

- 每个维度一行
- 左侧维度名
- 中间方向条：`direction_score`，范围 -10 到 10
- 右侧重要性条：`importance_score`，范围 0 到 100
- 显示 `effective_weight`、`evidence_count`、`avg_confidence`

点击维度后展开证据：

- 利多证据：`positive_factors`
- 利空证据：`negative_factors`
- 中性证据：`neutral_factors`

#### 逻辑链 tab

数据来源：

```text
logic_chains.assets[asset].logic_chains
```

核心字段：

```ts
type LogicChain = {
  chain_id: string
  asset: string
  dimension_id: string
  dimension_label: string
  logic_chain: Array<{
    step: "trigger" | "framework_dimension" | "directional_effect" | "trade_thesis" | "tracking_signal"
    text: string
  }>
  evidence: EvidenceFactor[]
  dimension_score: number
  importance_score: number
  confidence: number
}
```

推荐可视化：

- 用横向或纵向 stepper 展示：

```text
触发因素 -> 框架维度 -> 方向影响 -> 交易主线 -> 后续跟踪
```

示例：

```text
霍尔木兹海峡重新开放
-> 原油/地缘政治
-> pressure_price
-> 原油偏空主线
-> 后续跟踪地缘政治是否被新闻或数据库验证
```

每条链下方显示证据列表和置信度。

#### 原始证据 tab

数据来源：

```text
20260616_commodity_summary.json
```

字段：

- `detailed_analysis[asset].bullish_factors`
- `detailed_analysis[asset].bearish_factors`
- `detailed_analysis[asset].key_data`
- `detailed_analysis[asset].key_events`
- `detailed_analysis[asset].supply_demand`
- `detailed_analysis[asset].price_forecast`
- `detailed_analysis[asset].original_sources`

展示方式：

- 分组列表
- 每条证据显示来源字段
- 来源展示机构和标题

#### 框架映射 tab

数据来源：

```text
framework_alignment.json
```

筛选当前 asset 的：

```text
framework_alignment.alignments[].asset === selectedAsset
```

主要字段：

- `text`
- `direction`
- `source_field`
- `framework.framework_id`
- `framework_node.label`
- `match.method`
- `match.confidence`
- `status`

用途：

- 让研究员检查某条证据是否映射到了正确维度
- 对 `pending_review` 项做人工复核

## 5. 框架进化候选区

数据来源：

```text
framework_update_candidates.json
framework_format_review.json
```

### 框架补充候选

字段：

```ts
type FrameworkUpdateCandidate = {
  candidate_id: string
  target_framework_id: string
  asset_id: string
  standard_name: string
  candidate_type: "new_dimension" | "dimension_update" | "new_indicator" | "new_logic_template" | "graph_link"
  proposal_summary: string
  proposed_payload: {
    asset: string
    suggested_dimension_name: string
    suggested_keywords: string[]
    evidence_samples: string[]
    suggested_action: string
  }
  evidence_quotes: string[]
  confidence: number
  status: "candidate" | "accepted" | "rejected"
  review_status: "pending_review" | "approved" | "rejected"
}
```

展示建议：

- 按品种分组
- 展示候选类型、置信度、建议摘要
- 展示 suggested keywords
- 展示 evidence quotes
- 操作按钮预留：
  - 通过
  - 拒绝
  - 转人工编辑

当前阶段前端只展示，不需要真正写回 active framework。

### 框架格式审查

数据来源：

```text
framework_format_review.json
```

展示：

- `findings[].severity`
- `findings[].summary`
- `findings[].recommendation`
- `recommended_dimension_extensions`

用途：

- 给研究员/系统维护者看当前框架格式缺什么
- 后续用于规划 framework schema 升级

## TypeScript 数据结构建议

```ts
type RunManifest = {
  schema_version: string
  status: string
  run_id: string
  date: string
  raw_folder: string
  generated_at: string
  outputs: Record<string, string>
}

type EvidenceFactor = {
  factor_id?: string
  direction?: string
  source_field?: string
  text: string
  confidence?: number
  status?: string
}

type TradeThesisAsset = {
  asset: string
  framework_score: number
  source_sentiment_score: number | null
  score_divergence: {
    status: "aligned" | "opposite_direction" | "large_gap" | "no_source_score"
    source_score?: number
    framework_score: number
    gap?: number
  }
  recommendation: string
  main_trade_thesis: string
  primary_dimensions: Array<{
    dimension_id: string
    dimension_label: string
    direction_score: number
    importance_score: number
    effective_weight: number
    key_evidence: EvidenceFactor[]
  }>
  supporting_dimensions: string[]
  opposing_dimensions: string[]
  watch_points: string[]
  invalidation_signals: string[]
}

type DimensionScoresAsset = {
  asset: string
  source_sentiment_score: number | null
  framework_score: number
  dimension_count: number
  evidence_count: number
  dimensions: DimensionScore[]
}
```

## 数据加载建议

前端建议加载顺序：

1. 先加载 `manifest.json`
2. 根据 `manifest.outputs` 加载其他 JSON
3. 若某个文件缺失，页面对应区域展示 empty state

伪代码：

```ts
const manifest = await fetchJson(`${runDir}/manifest.json`)
const outputs = manifest.outputs

const summary = await fetchJson(`${runDir}/${outputs.summary}`)
const marketReview = await fetchJson(`${runDir}/${outputs.market_review}`)
const alignment = await fetchJson(`${runDir}/${outputs.framework_alignment}`)
const dimensionScores = await fetchJson(`${runDir}/${outputs.dimension_scores}`)
const tradeThesis = await fetchJson(`${runDir}/${outputs.trade_thesis}`)
const logicChains = await fetchJson(`${runDir}/${outputs.logic_chains}`)
const updateCandidates = await fetchJson(`${runDir}/${outputs.framework_update_candidates}`)
const formatReview = await fetchJson(`${runDir}/${outputs.framework_format_review}`)
```

## 后端接口建议

如果前端不能直接读文件，建议后端提供以下接口：

```text
GET /api/v1/futures-daily/logic-runs?date=20260616
GET /api/v1/futures-daily/logic-runs/{run_id}
GET /api/v1/futures-daily/logic-runs/{run_id}/assets
GET /api/v1/futures-daily/logic-runs/{run_id}/assets/{asset}
GET /api/v1/futures-daily/logic-runs/{run_id}/framework-candidates
```

`GET /logic-runs/{run_id}` 建议返回聚合后的页面首屏数据：

```ts
type LogicRunDetail = {
  manifest: RunManifest
  market_review: object
  asset_rankings: TradeThesisAsset[]
  factor_clusters: object[]
  framework_format_review: object
}
```

`GET /assets/{asset}` 返回品种详情：

```ts
type AssetLogicDetail = {
  asset: string
  summary_detail: object
  dimension_scores: DimensionScoresAsset
  trade_thesis: TradeThesisAsset
  logic_chains: LogicChain[]
  alignments: object[]
  framework_update_candidates: FrameworkUpdateCandidate[]
}
```

## UI 状态要求

必须支持：

- loading
- 文件缺失
- JSON 解析失败
- 某品种没有 logic chain
- 某品种没有 framework update candidate
- `20260618` 这种 OSS 原文缺失，只存在 missing manifest 的情况

缺失状态文案示例：

```text
该日期暂无可用日报原文，当前仅记录了 missing manifest。
```

## 当前样例可视化重点

用当前 `20260616` 样例，可以优先展示这些品种：

- 原油：偏空观察，地缘政治和供给维度主导
- 豆粕：偏空观察，供给和库存维度主导
- 铁矿石：偏空观察，供给、需求、库存共同作用
- 焦煤：小幅偏多，供给支撑但需求压制
- 铜：框架分与研报原始分方向相反，应高亮为分歧
- 沪深300股指：框架分与研报原始分方向相反，应高亮为分歧

## 设计风格建议

这是投研工作台，不是营销页面。

建议：

- 信息密度适中，表格和详情面板为主
- 使用 tabs、筛选器、分歧 badge、置信度 badge
- 避免大面积装饰图
- 分数和维度条要可扫读
- 所有颜色都配文字标签
- 重点突出“主线、证据、框架维度、分歧、待审核候选”
