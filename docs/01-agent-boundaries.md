# Agent 边界与运行规则

`quanta_agents` 的目标不是让模型自由阅读所有文件后输出观点，而是把每次自动化研究拆成可追溯的运行记录。

## 模块职责

| 模块 | 输入 | 输出 |
| --- | --- | --- |
| crawler | 外部网页、新闻、研报、数据 API | `raw_objects`、`raw_manifests` |
| canonicalizer | raw manifest、原始文件 | `canonical_documents` |
| evidence extractor | canonical document、人工标注 | `gold_evidence`、`evidence_capsules`、`recall_refs` |
| prompt pack builder | system rules、框架、图谱、主题、证据 | `agent_workspace/prompt_packs` |
| futures daily agent | evidence capsule、主题状态、行情验证 | `market_brief`、`asset_analysis`、`review_package` |
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

## 禁止写入

- 不写 `.env`、token、cookie、API key 到 `quanta_data`。
- 不把 raw object 直接传给模型生成正式观点。
- 不直接修改 `gold`，除非运行的是受控 gold writer 且已有审核记录。
- 不把没有 evidence refs 的研究结论标记为 active。
