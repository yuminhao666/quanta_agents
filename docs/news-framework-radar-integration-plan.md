# 框架优化与新闻快讯接入方案

## 背景

当前 futures daily agent 已经能把研报原文抽取成：

- 品种因素
- active framework 映射
- 维度评分
- 交易主线
- 逻辑链
- 框架补充候选

下一步需要把新闻快讯和 MySQL 舆情数据接进来，让新闻成为实时验证层：研报给出逻辑，新闻追踪事件演化，数据库/行情指标验证逻辑是否继续成立。

## 一、active framework 优化策略

### 当前问题

对 `/Users/miniquanta/Documents/quanta_research_group/data_lake/active_knowledge/research_frameworks/commodities/` 做审计后，发现：

- 86 份 active framework 全部仍是 `schema_version=research_framework.v1`
- 实际结构更接近商品框架，但 `artifact_type` 仍是 `research_framework`
- 86 份 `logic_templates` 为空
- 516 个维度缺少 `default_weight`
- 516 个维度缺少 `scoring_rules`
- `prompt_pack_fragments` 是 list，而 schema 期望 object
- 所有 active framework 仍是 `review_status=pending_review`
- 多个 `dimension_type` 是历史遗留命名，例如 `cost_profit`、`policy_macro`、`weather_yield`

### 不直接覆盖 active framework

这些历史数据仍有价值，不能直接批量改写 active 文件。当前策略是：

```text
active framework -> framework audit -> optimization candidates -> 人工审核 -> 写回 active framework
```

新增命令：

```text
quanta-framework-audit
```

输出位置：

```text
agent_workspace/candidates/framework_audit/{yyyy}/{mm}/{dd}/framework-audit-{time}.json
agent_workspace/candidates/framework_audit/latest/framework-audit.json
```

### 优化候选内容

每个 framework 会生成一个 `framework_schema_normalization` 候选，包含：

- schema_version 迁移建议：`commodity_research_framework.v1`
- artifact_type 迁移建议：`commodity_research_framework`
- 维度 `default_weight`
- 维度 `weight_bounds`
- 维度 `activation_rules`
- 维度 `scoring_rules`
- 默认 `logic_templates`
- 统一的 `prompt_pack_fragments`
- `evolution_policy`

### 建议目标结构

框架仍保持“长期知识”，不要写入每日研报结论。动态信息放在 candidate/run 里。

建议补充字段：

```json
{
  "core_dimensions": [
    {
      "dimension_id": "...",
      "dimension_name": "供给",
      "dimension_type": "supply",
      "default_weight": 0.18,
      "weight_bounds": {"min": 0.0, "max": 0.35},
      "activation_rules": {
        "evidence_count_boost": true,
        "important_news_boost": true,
        "report_consensus_boost": true,
        "time_decay_half_life_hours": 72
      },
      "scoring_rules": {
        "direction": "infer_from_evidence",
        "strength": "evidence_weighted",
        "confidence": "source_and_match_confidence",
        "time_decay": "enabled"
      }
    }
  ]
}
```

## 二、新闻快讯接入方式

### 当前舆情雷达

现有 `opinion_radar` 已支持：

- 从 MySQL 快讯表读取数据
- 噪音过滤
- taxonomy 词条匹配
- 主题热度聚类
- 多时间窗口 snapshot
- 时间线统计
- LLM 命名/合并主题

但它之前没有和品种 framework、研报逻辑链连接。

### 新增新闻逻辑雷达

新增模块：

```text
quanta_agents.opinion_radar.news_logic
```

新增命令：

```text
quanta-opinion-radar-news-logic
```

作用：

```text
新闻快讯 -> 标准资产识别 -> active framework 维度映射 -> 与研报交易主线对比 -> 输出新闻逻辑雷达
```

输出位置：

```text
agent_workspace/candidates/opinion_radar/news_logic/{yyyy}/{mm}/{dd}/news-logic-{time}.json
agent_workspace/candidates/opinion_radar/news_logic/latest/news-logic.json
```

### 新闻逻辑雷达结构

核心字段：

```json
{
  "schema_version": "news_logic_radar.v1",
  "logic_context": {
    "run_dir": "关联的 futures daily logic run"
  },
  "stats": {
    "flash_count": 0,
    "kept_flash_count": 0,
    "mapped_event_count": 0,
    "asset_count": 0,
    "framework_mapped_count": 0,
    "consistency_counts": {}
  },
  "assets": {},
  "events": []
}
```

每条新闻事件会包含：

- 快讯 ID
- 发布时间
- 新闻文本
- 资产
- framework_id
- framework_node
- 方向分
- heat
- 与研报主线关系

关系状态：

```text
supports_thesis       支持研报主线
thesis_conflict       与研报主线冲突
dimension_conflict    与对应维度方向冲突
new_signal            该品种暂无研报主线，是新增信号
tracking              中性跟踪事件
```

## 三、研报与新闻的协同关系

研报逻辑链：

```text
研报证据 -> framework 维度 -> 维度评分 -> 交易主线 -> 后续跟踪点
```

新闻逻辑雷达：

```text
新闻快讯 -> framework 维度 -> 新闻方向 -> 与研报主线对比 -> 支持/冲突/新增信号
```

两者连接后，可以回答：

- 研报提出的主线，新闻是否继续验证？
- 新闻是否出现反向证据，提示交易逻辑失效？
- 哪些新闻属于新信号，但研报还没覆盖？
- 哪些事件频繁出现，应该提升 framework 某个维度的短期权重？
- 哪些新闻无法映射到 framework，需要形成框架补充候选？

## 四、舆情雷达增强方案

原有雷达输出：

- 主题热度
- 重要快讯流
- 多时间窗口
- 主题时间线

增强后增加：

- 新闻到 active framework 的映射
- 新闻与研报交易主线的一致性
- 新闻驱动的维度热度
- 新闻驱动的主线强化/冲突提示
- 新信号发现

`quanta-opinion-radar-export` 已接入新闻逻辑雷达。导出 payload 会增加：

```json
{
  "news_logic": {
    "latest": "...",
    "archive": "...",
    "stats": {},
    "logic_context": {}
  }
}
```

如果当前环境缺少 MySQL/PyMySQL，则写入：

```json
{
  "news_logic": {
    "error": "..."
  }
}
```

不影响原有舆情雷达导出。

## 五、后续开发建议

### 第一阶段：已完成

- active framework 审计和优化候选生成
- 新闻快讯映射到 active framework 维度
- 新闻与最近研报逻辑链对比
- 舆情雷达导出接入 news_logic 引用
- 市场舆情雷达报告生成：`market-radar-report.{json,md}`
- 报告层把 `supports_thesis`、`thesis_conflict`、`dimension_conflict`、`new_signal` 转成投资逻辑动作：强化、修正/降级、条件性收敛、新主线候选或维持跟踪
- 聚类质量门：短 ASCII 别名边界匹配，易混中文别名语境保护，具体品种优先于板块/品类桶，汇总新闻和纯行情噪音降权

### 第二阶段：建议继续做

- MySQL 环境安装 `PyMySQL`，跑真实快讯
- 前端加入“新闻支持/冲突研报主线”视图
- 对 `thesis_conflict` 和 `dimension_conflict` 做提醒
- 从新闻事件反向生成 `framework_update_candidates`
- 加入数据指标验证层，例如库存、价格、持仓、基差

### 第三阶段：动态追踪

- 对同一事件建立生命周期
- 追踪逻辑从 “提出 -> 强化 -> 分歧 -> 证伪 -> 归档”
- 根据新闻和数据动态调整维度 `activation_weight`
- 对持续有效的候选进行人工审核后写回 active framework
