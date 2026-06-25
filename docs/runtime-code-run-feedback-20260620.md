# Existing Code Runtime Feedback - 2026-06-20

本文记录本轮直接运行 `quanta_agents` 现有源码后的真实结果。目标不是替代正式评测，而是把舆情雷达、Polymarket 和期市逻辑链的架构问题从“主观感觉”落到可复现的运行证据上。

## 运行上下文

- 源码分支：`codex/WO-DEV-20260620-005-canonical-evidence`
- 源码 worktree：`/Users/miniquanta/Documents/quanta_agents_existing_code_run`
- 运行输出根目录：`/Users/miniquanta/Documents/quanta_data/agent_workspace/candidates/quanta_agent_code_runs/2026/06/20/RUN-20260620-165725-CODE-SMOKE`
- 真实数据根：`/Users/miniquanta/Documents/quanta_data`
- 真实期市框架目录：`/Users/miniquanta/Documents/quanta_research_group/data_lake/active_knowledge/research_frameworks/commodities`

当前 `/Users/miniquanta/Documents/quanta_agents` 的 `master` 分支缺少多数 `quanta_agents/*.py` 源码，console script 会报 `ModuleNotFoundError: No module named 'quanta_agents'`。完整源码可从上述开发分支恢复或作为后续主线基线。

## 实际运行命令

舆情雷达使用 sandbox `quanta-root`，避免覆盖生产 `latest`：

```bash
PYTHONPATH=/Users/miniquanta/Documents/quanta_agents_existing_code_run \
/Users/miniquanta/Documents/quanta_agents/.venv/bin/python \
  -m quanta_agents.opinion_radar.export \
  --no-llm \
  --windows 24,48,72 \
  --top 25 \
  --timeline-days 7 \
  --timeline-top 12 \
  --quanta-root /Users/miniquanta/Documents/quanta_data/agent_workspace/candidates/quanta_agent_code_runs/2026/06/20/RUN-20260620-165725-CODE-SMOKE/quanta_root
```

Polymarket 使用真实 CLI 采样，输出同样落在 sandbox：

```bash
PYTHONPATH=/Users/miniquanta/Documents/quanta_agents_existing_code_run \
/Users/miniquanta/Documents/quanta_agents/.venv/bin/python \
  -m quanta_agents.polymarket_daily.export \
  --quanta-root /Users/miniquanta/Documents/quanta_data/agent_workspace/candidates/quanta_agent_code_runs/2026/06/20/RUN-20260620-165725-CODE-SMOKE/quanta_root \
  --date 20260619 \
  --limit 50 \
  --top 25 \
  --focus finance,politics,macro \
  --no-llm \
  --timeout 90
```

期市逻辑链先跑 3 个品种的真实 LLM 抽取样本，再用真实框架路径重做后处理：

```bash
PYTHONPATH=/Users/miniquanta/Documents/quanta_agents_existing_code_run \
/Users/miniquanta/Documents/quanta_agents/.venv/bin/python \
  -m quanta_agents.futures_daily.raw_run \
  --raw-folder /Users/miniquanta/Documents/quanta_data/raw_objects/futures_daily/daily_originals/2026/06/16 \
  --quanta-root /Users/miniquanta/Documents/quanta_data/agent_workspace/candidates/quanta_agent_code_runs/2026/06/20/RUN-20260620-165725-CODE-SMOKE/futures_quanta_root \
  --max-assets 3 \
  --max-workers 1
```

## 舆情雷达结果

| 窗口 | kept flashes | important | theme_count | top-flash 重复率 |
| --- | ---: | ---: | ---: | ---: |
| 24h | 792 | 149 | 14 | 11.25% |
| 48h | 2218 | 386 | 25 | 14.29% |
| 72h | 3970 | 788 | 25 | 21.89% |

关键证据：

- `theme_anchor_source_count=0`，所有主题都是 `unanchored_theme_candidate`。
- 时间线只覆盖窗口主题 key 的 `12/26 = 46.15%`，说明时间线和窗口主题没有共享稳定主题状态。
- 48h/72h 出现有色品种重复展示，例如 `V::锌`、`V::铅`、`V::镍`、`V::锡` 的 top flashes Jaccard 可到 `1.0`，同一组金属快讯被多个品种桶重复消费。
- `news_logic` 本轮 `flash_count=4344`、`mapped_event_count=378`、`unique_flash_keys=316`、`dup_expansion=62`、单条 flash 最多扩成 13 个事件。
- `news_logic` 的 `framework_mapped_count=0`，该链路没有稳定接入同一套 active framework discovery 和 theme anchor state。

架构判断：

- 当前舆情主题不是“事件/主题生命周期”，而是“滚动窗口内按词典桶重新聚合”。这解释了重复主题和 timeline churn。
- 去重发生在展示项层，不发生在统一 `event_definition` 层；同一快讯跨资产/宏观桶重复进入主题。
- `theme_anchor` 已经在设计里出现，但运行时没有强制可用，也没有在无 anchor 时降级出稳定 state store。

## Polymarket 结果

本轮真实 CLI 采样结果：

- raw rows：300
- unique market keys：230
- event groups：210
- repeated event groups：51
- largest event group：4
- 聚焦后市场：62
- candidate hotspots：25
- CLI pool errors：0

字段和翻译问题：

- 热点中显式 `probability` / `probability_yes` 字段覆盖为 0，当前主要保留 `best_bid`、`best_ask`、`last_trade_price`、`primary_probability`。
- `indexes/polymarket/market_hotspots.jsonl` 本轮只追加 25 行热点，不是 outcome-level probability time series。
- 规则翻译粗检有 21/25 条仍含明显英文或模板痕迹，例如 `Iran agrees to end enrichment of uranium by June 30?`、`Bitcoin Up or Down - June 20, 4AM ET`。
- 代码中的 LLM 只用于 `daily_report` 总结，不用于 event/topic-level 中文归并；打开 LLM 也不会自动修好 `question_zh` 和 `event_title_zh`。

架构判断：

- 当前 Polymarket 有 raw snapshot 和热点日报，但缺少独立的概率沉淀层。
- event 聚合只服务热点展示，没有形成 `event_id -> condition_id -> outcome_id -> probability_tick` 的可追加结构。
- 中文主题整理应该是独立的 LLM normalization stage，而不是日报正文生成的一部分。

## 期市逻辑链结果

原文输入：

- 日期：2026-06-16
- raw reports：14
- 本轮 LLM 样本限制：3 个品种
- 识别总品种：69
- 输出品种：铝、铁矿石、原油

单篇/单源结构化证据是保留的：

| 品种 | source_count | covered_source_count | positive_weight_source_count | sentiment_score |
| --- | ---: | ---: | ---: | ---: |
| 铝 | 10 | 10 | 10 | -0.52 |
| 铁矿石 | 9 | 9 | 8 | -0.94 |
| 原油 | 9 | 9 | 9 | -5.70 |

期市重对齐结果：

- 如果 sandbox root 没有显式挂载真实 framework 目录，68/68 条因素全部退化到 `default_dimension`，其中 42/68 是 `default::未归类`。
- 使用真实 `/Users/miniquanta/Documents/quanta_data` 作为 root 后，68 条因素中 66 条 `auto_linked`，2 条 `pending_review`；2 条未归类都是 LLM 明确判断为纯行情数据，不应映射到基本面维度。
- `frameworks_used` 覆盖 3/3 个品种，分别为原油、铁矿石、铝 active framework。
- 重对齐后的 `logic_summary` 有 `logic_chain_count=9`、`event_item_count=36`、`topic_summary_count=7`，重要事件摘要 410 字、重要逻辑摘要 334 字。

仍存在的问题：

- 运行根路径对 active framework discovery 影响很大，但代码不会 fail fast，容易静默退化到 `default_dimension`。
- 品种摘要字段和后处理字段存在契约不一致：摘要里是 `key_events`、`fundamental_summary`，部分下游诊断或历史代码仍按 `important_events`、`summary`、`logic_chain` 查找。
- `market_review` 摘要仍偏短，虽然重对齐后 logic summary 比旧结果更好，但和老版期市速递的“事件-数据-逻辑-预测”叙事密度仍有差距。

## 验证

```bash
PYTHONPATH=/Users/miniquanta/Documents/quanta_agents_existing_code_run python3 -m pytest -q
```

结果：`68 passed, 1 skipped in 0.26s`。

```bash
PYTHONPATH=/Users/miniquanta/Documents/quanta_agents_existing_code_run python3 -m py_compile $(find quanta_agents -name '*.py' -print)
```

结果：通过。

当前 `.venv` 缺少 `pytest`，系统 `python3` 可跑测试。`ruff` 未安装，因此未完成 lint。

## 架构优化建议

### 1. Runtime Root And Dependency Discovery

把 `quanta_data_root`、active framework root、theme anchor root 从隐式相邻目录发现改成显式 runtime context。核心链路缺少 active framework 时应 fail fast 或写入高严重度 run warning，不能静默 fallback 到 `default_dimension`。

建议新增共享对象：

```text
RuntimeContext(
  quanta_data_root,
  active_framework_root,
  theme_anchor_root,
  output_policy,
  run_id
)
```

每个 CLI 输出 manifest 必须记录该 context。

### 2. Safe Output Policy

`opinion_radar.export`、`opinion_radar.report`、`polymarket_daily.export` 当前默认覆盖 `latest`。诊断和回归测试需要统一支持：

- `--output-root`
- `--no-latest`
- `--run-id`
- `--dry-run-manifest`

这样可以用真实数据跑回归，而不污染生产 latest。

### 3. Opinion Radar Theme State

在 `opinion_radar` 内拆出两层：

```text
flash -> event_definition -> theme_state -> window_view/timeline_view
```

关键改造：

- 先生成稳定 `event_key`，由事实主体、动作、资产、时间窗口和来源 hash 组成。
- 同一快讯可以关联多个资产，但只能对应一个 primary event；展示时按 event 聚合，避免金属/能源/宏观桶重复消费同一 top flash。
- `theme_state` 增量更新，记录 `first_seen`、`last_seen`、`member_event_keys`、`alias_titles`、`merged_from`。
- timeline 读取 `theme_state`，而不是重新按窗口词典桶聚合。

### 4. Polymarket Probability Store And Topic Normalizer

新增 Polymarket 标准沉淀层：

```text
market_snapshot
event_snapshot
outcome_probability_tick
event_topic_normalization
```

最小概率 schema：

```json
{
  "run_id": "...",
  "observed_at": "...",
  "event_id": "...",
  "condition_id": "...",
  "market_id": "...",
  "outcome_id": "...",
  "outcome": "Yes",
  "probability": 0.42,
  "best_bid": 0.41,
  "best_ask": 0.43,
  "last_trade_price": 0.42,
  "spread": 0.02,
  "liquidity": 12345.0,
  "volume_24h": 1234.0,
  "end_date": "...",
  "settlement_rule": "...",
  "source_question": "..."
}
```

LLM 只写 normalization candidate：

- `event_title_zh`
- `event_summary_zh`
- `sub_questions_zh`
- `asset_refs`
- `theme_refs`
- `settlement_rule_zh`
- `normalization_confidence`

原始概率和原题不能被 LLM 改写。

### 5. Futures Single-Report Store

期市链路已经能保留 `score_audit.source_scores`，下一步应把“单篇研报结构化信息”独立沉淀，而不是只嵌在日频 summary 里：

```text
futures_daily_single_report_analysis/{date}/{run_id}/{row_id}.json
```

然后日频加工从该 store 读取：

```text
single_report_analysis
  -> asset_day_merge
  -> framework_alignment
  -> trade_thesis
  -> logic_summary / market_review
```

这样可以复用老版期市速递的字段密度，也能在某篇研报变更、重抓或重试时做局部增量，而不是整日重跑。

## 2026-06-20 增量主题报告 V4

本轮在当前 `quanta_agents` 项目代码上新增并运行 `signal_mapping.theme_report`，目标是从 2026-04-01 开始用已处理好的单篇研报 JSON 和新闻快讯维护增量主题报告。

运行命令：

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

- `agent_workspace/candidates/theme_report_maintenance/2026/06/20/CAND-THEME-REPORT-20260620-195701051067/theme_report.json`
- `agent_workspace/candidates/theme_report_maintenance/2026/06/20/CAND-THEME-REPORT-20260620-195701051067/research_state.json`
- `agent_workspace/candidates/theme_report_maintenance/latest/theme_report.json`
- `agent_workspace/candidates/theme_report_maintenance/latest/theme_report.md`

结果：

| 指标 | 数量 |
| --- | ---: |
| 日期覆盖 | 81 天 |
| 单篇研报画像 | 3438 |
| 活跃研报日期 | 61 |
| 原始新闻快讯 | 80381 |
| 研报日级主题信号 | 16912 |
| 新闻事件信号 | 5511 |
| 主题状态 | 827 |
| 事件状态 | 20831 |
| 过滤价格/技术项 | 12635 |
| 过滤低信号新闻 | 3316 |

schema 校验结果：

```text
schema_validation=passed
signal_count=22423
theme_anchor_count=1260
```

本轮针对真实输出做了四类优化：

- 研报不再逐字段直接入状态，而是先按 `日期 + 品种 + 主题` 聚合，三天烟测信号量从 2854 降到约 657。
- 价格路径、技术面、ETF/期权/LOF 等交易型文本默认排除出主题主线。
- 研报侧资产名通过 taxonomy 归一，避免新闻和研报对同一品种生成两个 asset id。
- 新增 `避险需求与央行购金` 和 `资金持仓与交易情绪`，减少黄金/白银新闻落入泛化 `其他逻辑跟踪`。

仍需二轮优化：

- `其他逻辑跟踪` 仍高，尤其全局宏观新闻和跨资产复盘；应由 LLM normalization 输出稳定中文主题、事件 ID 和主题类型。
- 当前新闻来自 MySQL，`canonical_documents/news` 仍为空；后续应把新闻也沉淀成可复用 evidence/source refs。
- 状态机已经能增量维护历史主题，但还缺面向前端的精选视图，例如资产主题榜、宏观主题榜、失败案例和主题合并审计。

## 下一轮优先级

1. 先修 runtime context 和 safe output policy，让真实数据回归测试可稳定运行。
2. 同步落地 Polymarket probability tick schema，因为这会影响后续图表、回测和事件归并。
3. 舆情雷达实现 `event_definition/theme_state` 增量层，再改窗口展示和 timeline。
4. 期市把单篇研报 store 抽成正式 candidate，并让 `market_review` 从结构化事件/逻辑项生成，减少直接摘要丢信息。
