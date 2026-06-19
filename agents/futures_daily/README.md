# Futures Daily Agent

生成期货市场日报、品种分析、主题生命周期和审核包。

默认写入：

```text
agent_workspace/runs
agent_workspace/prompt_packs
agent_workspace/candidates/market_brief
agent_workspace/candidates/asset_analysis
agent_workspace/review_packages
```

自动结果可以是 `candidate` 或 `machine_published`，但不能直接进入 `gold`。

## 当前集成

`quanta_agents.futures_daily` 已接入旧 `commodity_report` 项目的期市速递产物：

- `quanta-futures-daily-import`：导入本地 `*_commodity_summary.json`、
  `*_commodity_marketreview.json` 和可选 HTML，并默认生成框架对齐候选。
- `quanta-futures-daily-oss-sync`：从 OSS 的 `quanta/output/commodity/{YYYYMM}/`
  同步日报三件套。
- `quanta-futures-daily-align`：单独把已有 summary 生成 `framework_alignment`、
  `factor_clusters` 和 `logic_timeline` candidate。
- `quanta-futures-daily-raw-ingest`：从 OSS 拉取 `gzh_futures_{date}.zip`，
  落到 `raw_objects/futures_daily/daily_originals/{y}/{m}/{d}/`。
- `quanta-futures-daily-run-raw`：从“日报原文”目录抽取品种因素，并生成
  框架对齐与逻辑链候选。
- `quanta-futures-daily-logic-chain`：对已有 summary + alignment 单独重算
  维度评分、交易主线、逻辑链和框架补充候选。

写入路径遵循 `gj_chainplatform` 当前 API：

```text
agent_workspace/candidates/market_brief/{y}/{m}/{d}/CAND-MBRIEF-{date}-COMMODITY/
agent_workspace/candidates/asset_analysis/{y}/{m}/{d}/CAND-ANALYSIS-{date}-COMMODITY/
agent_workspace/review_packages/{y}/{m}/{d}/RP-{date}-COMMODITY/
agent_workspace/candidates/framework_alignment/{y}/{m}/{d}/
agent_workspace/candidates/factor_clusters/{y}/{m}/{d}/
agent_workspace/candidates/logic_timeline/{y}/{m}/{d}/
agent_workspace/candidates/futures_daily_raw_runs/{y}/{m}/{d}/RUN-{date}-*-RAW-DAILY/
```

这些产物仍是候选/待审核内容；前端“期市速递”页会通过
`GET /api/v1/commodity/market-overview` 读取最新日期。

## 资产 taxonomy 与框架对齐

标准资产名称、别名、板块和宏观桶从 `quanta_data` 读取：

```text
gold/reference_data/assets/futures_assets.v1.json
```

研报抽取出的因素不会直接改框架骨架，而是生成候选映射：

```text
factor text -> framework node -> factor cluster -> logic timeline event
```

框架结构变更仍应走 `agent_workspace/candidates/frameworks` 或 taxonomy candidate 的审核流。

## 逻辑链候选

研报数据的作用是为动态研究框架提供证据，而不是只生成日报文本。raw run 会额外输出：

```text
dimension_scores.json
trade_thesis.json
logic_chains.json
framework_update_candidates.json
framework_format_review.json
```

- `dimension_scores`：按品种 framework 维度聚合证据，计算方向分、重要性分和短期有效权重。
- `trade_thesis`：梳理各品种当日交易主线，并标记原始研报情绪分与框架分是否分歧。
- `logic_chains`：把触发因素、框架维度、方向影响、交易主线和后续跟踪信号串成链。
- `framework_update_candidates`：对未归类或低置信映射提出框架补充候选，等待人工审核。
- `framework_format_review`：记录当前 active framework 格式的结构性缺口，例如权重、打分规则和逻辑模板。
