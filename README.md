# Quanta Agents

`quanta_agents` 是共振 Alpha 的数据管道和研究 Agent 运行项目。它负责把外部混乱资料变成可追溯的 Research Memory 资产，再基于受控证据生成日报、候选知识和问答答案。

它不负责前端页面、用户权限和人工审核 UI；这些属于 `gj_chainplatform`。它也不把正式知识直接写入 `gold`；正式知识必须经过 `quanta_data` 的候选和审核链路。

## 项目边界

| 项目 | 角色 |
| --- | --- |
| `gj_chainplatform` | 前端工作台、平台 API、权限、审核动作、展示和问答入口 |
| `quanta_data` | Research Memory、schema、状态机、证据、候选、审核包和 gold |
| `quanta_agents` | crawler、canonicalizer、evidence extractor、Prompt Pack builder、日报 Agent、问答 Agent |

标准链路：

```text
raw object
  -> raw_manifest
  -> canonical document
  -> gold evidence
  -> evidence capsule
  -> prompt pack
  -> research draft
  -> candidate knowledge
  -> machine_published / pending_review
  -> active knowledge / gold
```

`machine_published` 允许模型成果先展示，但必须带证据、run manifest 和待复核状态，不能当作 active knowledge。

## 目录

| 目录 | 说明 |
| --- | --- |
| `crawlers` | 每日网页、新闻、研报、公告和数据源抓取；写 `raw_objects` 与 `raw_manifests` |
| `canonicalizers` | PDF、HTML、Excel、JSON、截图 OCR 等标准化；写 `canonical_documents` |
| `evidence_extractors` | snippet、表格单元、数据点、冲突证据和 evidence capsule 抽取 |
| `prompt_pack_builder` | 按 system rules、框架、图谱、主题、证据和 output schema 组装 Prompt Pack |
| `agents/futures_daily` | 期货市场日报、品种分析、主题生命周期和 review package 生成 |
| `agents/opinion_radar` | 商品/宏观快讯舆情雷达候选快照生成 |
| `agents/assistant_qa` | 前端问答助手的受控召回、证据回答和缺证据处理 |
| `writers` | 按 `quanta_data/configs/schemas` 写 manifest、candidate、review package |
| `schemas` | 本项目缓存的 schema 或 schema loader；源头仍是 `quanta_data/configs/schemas` |
| `configs` | 本项目运行配置模板，不放密钥 |
| `docs` | Agent 边界、运行方式和部署说明 |
| `tests` | schema、writer、pipeline 的最小回归测试 |

架构图见：[docs/quanta-agents-architecture.md](/Users/miniquanta/Documents/quanta_agents/docs/quanta-agents-architecture.md)。
期市速递、动态逻辑链、舆情雷达、研报主题和 Polymarket 的统一改造方案见：[docs/signal-integration-and-framework-optimization-plan.md](/Users/miniquanta/Documents/quanta_agents/docs/signal-integration-and-framework-optimization-plan.md)。
知识库维护 Agent 的运行手册见：[docs/knowledge-maintenance-agent-runbook.md](/Users/miniquanta/Documents/quanta_agents/docs/knowledge-maintenance-agent-runbook.md)。

## 已整合功能

### 期市速递

`/Users/miniquanta/Documents/tmp_code/commodity_report` 中的 AI 商品日报三件套已收敛为
`quanta_agents.futures_daily`：

- 本地导入：把 `*_commodity_marketreview.json`、`*_commodity_summary.json` 和可选
  `*_commodity_report.html` 写入 `quanta_data`。
- 框架对齐：把品种利多/利空/数据/事件/供需/预测映射到分析框架节点，形成
  `framework_alignment` candidate。
- 因素聚类：把跨品种共性因素聚成 `factor_clusters`，并给出同因素反向暴露的对冲候选。
- 逻辑时间线：把每条因素记录为 `logic_timeline` event，后续新闻快讯可继续追加同一条逻辑的演化。
- OSS 同步：从 `oss://nblab/quanta/output/commodity/{YYYYMM}/` 拉取每日三件套并入库。
- 自动生成审核包：写 `review_packages/{y}/{m}/{d}/RP-{date}-COMMODITY/manifest.json`
  和 `preview.html`。

平台读取的契约保持不变：

```text
agent_workspace/candidates/market_brief/{y}/{m}/{d}/CAND-MBRIEF-{date}-COMMODITY/{date}_commodity_marketreview.json
agent_workspace/candidates/asset_analysis/{y}/{m}/{d}/CAND-ANALYSIS-{date}-COMMODITY/{date}_commodity_summary.json
agent_workspace/review_packages/{y}/{m}/{d}/RP-{date}-COMMODITY/
agent_workspace/candidates/framework_alignment/{y}/{m}/{d}/ALIGN-{date}-COMMODITY.json
agent_workspace/candidates/factor_clusters/{y}/{m}/{d}/FCL-{date}-COMMODITY.json
agent_workspace/candidates/logic_timeline/{y}/{m}/{d}/LOGIC-TIMELINE-{date}-COMMODITY.json
```

本地样例导入：

```bash
quanta-futures-daily-import \
  --summary /Users/miniquanta/Documents/tmp_code/commodity_report/sample_data/20260605_commodity_summary.json \
  --market-review /Users/miniquanta/Documents/tmp_code/commodity_report/sample_data/20260605_commodity_marketreview.json \
  --html /Users/miniquanta/Documents/tmp_code/commodity_report/sample_data/20260605_commodity_report.html
```

OSS 同步：

```bash
python3 -m pip install -e ".[oss]"
quanta-futures-daily-oss-sync --month 202606
```

### 标准资产 taxonomy

资产标准名、别名、板块归属和宏观桶不再由运行代码硬编码。Agent 运行时读取：

```text
gold/reference_data/assets/futures_assets.v1.json
```

如果需要初始化这份知识库镜像：

```bash
quanta-asset-taxonomy-bootstrap --force
```

后续新增资产、别名、合并、层级调整应写入：

```text
agent_workspace/candidates/taxonomy/latest/futures_assets.v1.json
```

由负责人审核后再晋级到 `gold/reference_data`。

### 舆情雷达

`/Users/miniquanta/Documents/tmp_code/opinion_radar/backend/app` 的计算引擎已收敛为
`quanta_agents.opinion_radar`，负责读取 `jin10_flash`、词典分桶、过滤行情噪音、可选
LLM 主题命名，并导出给 `gj_chainplatform`：

```text
agent_workspace/candidates/opinion_radar/latest/radar.json
agent_workspace/candidates/opinion_radar/{y}/{m}/{d}/radar-{HHMMSS}.json
agent_workspace/candidates/opinion_radar/reports/latest/market-radar-report.{json,md}
agent_workspace/candidates/opinion_radar/reports/{y}/{m}/{d}/market-radar-report-{HHMMSS}.{json,md}
```

运行：

```bash
python3 -m pip install -e ".[radar]"
quanta-opinion-radar-export --no-llm
```

`quanta-opinion-radar-export` 默认会同时生成新闻逻辑雷达和市场舆情雷达报告。报告会把
`news_logic` 中的“支持研报主线 / 与研报冲突 / 新增信号”转成投资逻辑动作：
强化原主线、修正或降级主线、收敛为条件性主线、打开新增主线候选或维持跟踪。
如只想基于现有 latest 产物重算报告：

```bash
quanta-opinion-radar-report
```

### Polymarket 市场日报

`quanta_agents.polymarket_daily` 调用本机 `polymarket` CLI，按 24h 成交、7日成交、
流动性、1日概率变化和新市场多组排序采样，默认聚焦金融、政治、宏观及地缘相关市场，
生成热点快照和中文日报候选。日报 Markdown 优先展示中文市场标题，JSON 中同时保留
`question` 原文和 `question_zh` 译文。

写入位置：

```text
raw_objects/market_data/polymarket_cli/{y}/{m}/{d}/RUN-.../snapshot.json
raw_manifests/by_source/polymarket_cli/RUN-....json
agent_workspace/candidates/polymarket_daily/{y}/{m}/{d}/CAND-POLYMARKET-{date}-{time}/
agent_workspace/candidates/polymarket_daily/latest/
agent_workspace/review_packages/{y}/{m}/{d}/RP-{date}-POLYMARKET/
indexes/polymarket/market_hotspots.jsonl
indexes/polymarket/daily_reports.jsonl
```

运行：

```bash
quanta-polymarket-daily --no-llm
```

默认 `--focus finance,politics,macro` 会保留 `finance`、`crypto`、`macro`、`politics`、
`geopolitics` 分类；如果要看全市场，传入：

```bash
quanta-polymarket-daily --focus all --no-llm
```

如果 CLI 不在 `PATH`，可设置 `POLYMARKET_CLI=/path/to/polymarket` 或传入
`--cli /path/to/polymarket`。配置 DeepSeek 或 MiniMax M3 key 后去掉 `--no-llm`
可让模型在结构化热点上写日报；没有 key 会自动降级为规则日报。

## 依赖

建议使用 Python 3.11+：

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -e ".[dev,crawler,documents,llm]"
```

早期如果只做 schema writer 和目录校验，可先安装基础依赖：

```bash
python3 -m pip install -e .
```

## 环境变量

复制 `.env.example` 为 `.env`，真实密钥只放本机 `.env` 或部署系统密钥管理中：

```bash
GJ_QUANTA_DATA_ROOT=/Volumes/数字大脑/quanta_data
GJ_PLATFORM_API_BASE=http://127.0.0.1:8000/api/v1
```

模型 key、数据源 cookie、供应商 token、数据库密码不进入 `quanta_data`，也不提交到 Git。

LLM 默认使用 DeepSeek；如果要切到 MiniMax M3，在本机 `.env` 中放 key，并把 provider
设为 `m3` 或 `minimax`：

```bash
QUANTA_AGENT_LLM_PROVIDER=m3
MINIMAX_API_KEY=sk-xxx
MINIMAX_BASE_URL=https://api.minimaxi.com/v1
MINIMAX_MODEL=MiniMax-M3
MINIMAX_THINKING=disabled
```

只想舆情雷达单独走 M3 时，设置 `RADAR_LLM_PROVIDER=m3`；否则它会跟随
`QUANTA_AGENT_LLM_PROVIDER`。

## 写入原则

1. 爬虫只写 raw object 和 raw manifest，不直接生成研究观点。
2. Agent 只面对 canonical、evidence、controlled retrieval 和 Prompt Pack。
3. 新图谱节点、关系、框架、主题、因子权重、source mapping 和问答新结论默认写 candidate。
4. 自动日报可以写 `machine_published`，但必须带证据和待复核状态。
5. `gold` 只能由审核通过后的 gold writer 写入。

## 与平台问答助手的关系

前端问答助手的 UI、会话、权限、证据展开和反馈在 `gj_chainplatform`。本项目负责：

- 生成 retrieval plan。
- 召回 evidence capsule。
- 组装 Prompt Pack。
- 调用模型。
- 返回结构化答案和缺证据提示。
- 把 QA run、answer draft、candidate refs 写入 `quanta_data`。

这样问答不是“聊天记录”，而是可审计的研究运行记录。
