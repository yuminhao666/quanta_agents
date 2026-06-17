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
| `agents/assistant_qa` | 前端问答助手的受控召回、证据回答和缺证据处理 |
| `writers` | 按 `quanta_data/configs/schemas` 写 manifest、candidate、review package |
| `schemas` | 本项目缓存的 schema 或 schema loader；源头仍是 `quanta_data/configs/schemas` |
| `configs` | 本项目运行配置模板，不放密钥 |
| `docs` | Agent 边界、运行方式和部署说明 |
| `tests` | schema、writer、pipeline 的最小回归测试 |

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
GJ_QUANTA_DATA_ROOT=/Users/miniquanta/document/quanta_data
GJ_PLATFORM_API_BASE=http://127.0.0.1:8000/api/v1
```

模型 key、数据源 cookie、供应商 token、数据库密码不进入 `quanta_data`，也不提交到 Git。

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
