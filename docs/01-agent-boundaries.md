# Agent 边界与运行规则

`quanta_agents` 的目标不是让模型自由阅读所有文件后输出观点，而是把每次自动化研究拆成可追溯的运行记录。

当前模块架构和数据流图见：[Quanta Agents 架构图](/Users/miniquanta/Documents/quanta_agents/docs/quanta-agents-architecture.md)。

## 模块职责

| 模块 | 输入 | 输出 |
| --- | --- | --- |
| crawler | 外部网页、新闻、研报、数据 API | `raw_objects`、`raw_manifests` |
| canonicalizer | raw manifest、原始文件 | `canonical_documents` |
| evidence extractor | canonical document、人工标注 | `gold_evidence`、`evidence_capsules`、`recall_refs` |
| prompt pack builder | system rules、框架、图谱、主题、证据 | `agent_workspace/prompt_packs` |
| futures daily agent | evidence capsule、主题状态、行情验证 | `market_brief`、`asset_analysis`、`review_package` |
| opinion radar agent | 快讯表、品种/宏观词典、LLM 主题命名 | `opinion_radar` candidate snapshot |
| polymarket daily agent | Polymarket CLI 市场快照、成交/流动性/概率变化 | 热点快照、市场日报、review package |
| framework alignment | 研报抽取因素、资产 taxonomy、分析框架 | framework link、factor cluster、logic timeline |
| assistant QA agent | 用户问题、权限、受控检索范围 | QA run、answer draft、candidate refs |
| writer | 结构化产物 | manifest、candidate、review package、audit refs |

## 运行记录

每次运行都应该生成 `agent_run_manifest.v1`，至少记录：

- run id、run type、状态、开始和结束时间。
- 输入 artifact refs。
- 输出 artifact refs。
- prompt pack ref。
- evidence refs。
- model refs 和 prompt hash。
- 是否需要人工审核。
- 如果失败，记录错误和对应 artifact。

## 问答助手

问答助手遵循“证据回答”而不是“模型闲聊”：

```text
question
  -> retrieval plan
  -> evidence capsule refs
  -> prompt pack
  -> answer draft
  -> claims with evidence refs
  -> candidate refs when new knowledge appears
```

无证据时返回 `insufficient_evidence`，不要用模型常识补齐金融结论。用户反馈、会话权限和证据展开由 `gj_chainplatform` 管理。

## 每日 LLM 期货日报

期货日报可以先写入 `machine_published`，适合成果展示和盘前/盘后快速浏览。必须满足：

- 有 `RUN-*` 运行记录。
- 有 `PP-*` Prompt Pack 引用。
- 有 evidence refs 或明确的缺证据标记。
- 有 review package 或可生成 review package 的候选清单。
- 前端展示时标记机器产物，不进入 `gold`。

人工审核通过后，再由 gold writer 写入 `gold/market_reports/commodity/daily` 或其他正式成果目录。

## 舆情雷达

舆情雷达是候选快照，不是前端服务。它读取快讯源并输出：

```text
agent_workspace/candidates/opinion_radar/latest/radar.json
agent_workspace/candidates/opinion_radar/{yyyy}/{mm}/{dd}/radar-{HHMMSS}.json
```

平台只消费这个文件，不直连 MySQL、不调用 LLM。

## Polymarket 日报

Polymarket 日报是预测市场候选快照，不是交易信号。它通过本机 `polymarket` CLI 读取公开市场数据，
保存 raw snapshot 和 raw manifest，再输出：

```text
agent_workspace/candidates/polymarket_daily/latest/hotspots.json
agent_workspace/candidates/polymarket_daily/latest/daily_report.md
agent_workspace/candidates/polymarket_daily/{yyyy}/{mm}/{dd}/CAND-POLYMARKET-{date}-{HHMMSS}/
agent_workspace/review_packages/{yyyy}/{mm}/{dd}/RP-{date}-POLYMARKET/
```

默认日报只聚焦金融、政治、宏观及地缘相关市场；体育、天气、泛娱乐等高频市场仍保留在 raw snapshot，
但不进入日报候选。日报只能解释 CLI 返回的成交、流动性、概率、价差和分类分布；未接入外部新闻证据时，
不得编造事件原因。正式使用前必须人工复核，尤其是低流动性、宽价差和新上线市场。

## 资产 taxonomy 与框架对齐

资产标准名、别名、板块归属和宏观桶属于知识库 reference data：

```text
gold/reference_data/assets/futures_assets.v1.json
```

Agent 运行时读取该文件组装 prompt 和规则分桶，不在代码里维护长期资产列表。新增资产、别名、合并或层级调整写入：

```text
agent_workspace/candidates/taxonomy/latest/futures_assets.v1.json
```

研报抽取出的因素先映射到框架节点，形成：

```text
agent_workspace/candidates/framework_alignment/{yyyy}/{mm}/{dd}/
agent_workspace/candidates/factor_clusters/{yyyy}/{mm}/{dd}/
agent_workspace/candidates/logic_timeline/{yyyy}/{mm}/{dd}/
```

因素聚类用于识别跨品种共性宏观驱动和同因素反向暴露的对冲组合候选；新闻快讯加入后追加
`logic_timeline`，用于观察同一逻辑的确认、弱化、扩散和反转。

## 禁止写入

- 不写 `.env`、token、cookie、API key 到 `quanta_data`。
- 不把 raw object 直接传给模型生成正式观点。
- 不直接修改 `gold`，除非运行的是受控 gold writer 且已有审核记录。
- 不把没有 evidence refs 的研究结论标记为 active。
