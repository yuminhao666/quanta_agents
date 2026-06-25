# Project Structure And Outputs

本文维护 `quanta_agents` 当前真实项目结构、CLI 入口和 `quanta_data` 产物落点。它是给后续 Agent、平台联调和人工复核用的索引，不替代 `quanta_data/configs/schemas` 中的正式契约。

## 边界

`quanta_agents` 是数据管道和 Agent runtime，负责采集、标准化、证据抽取、框架映射、逻辑验证、候选报告和运行 manifest。

它不负责：

- 前端页面、权限、审核动作和展示，归 `gj_chainplatform`。
- 直接写 `gold` 或 active knowledge。
- 保存模型 key、cookie、数据源密码或供应商 token。

所有机器产出默认是 `candidate`、`run`、`review_package`、raw/canonical/evidence 或 index。需要晋级为正式知识时，必须进入人工审核链路。

## 代码结构

| 路径 | 当前职责 | 主要产物 |
| --- | --- | --- |
| `quanta_agents/core` | 配置、路径、JSON IO、LLM、taxonomy、framework registry、框架审计和权重优化 | `analysis_framework`、`framework_audit`、`framework_optimization` |
| `quanta_agents/futures_daily` | 期市日报原文导入、框架对齐、维度评分、主线、逻辑链、逻辑演化和商品数据简报 | `futures_daily_raw_runs`、`market_brief`、`asset_analysis`、`commodity_data_briefs` |
| `quanta_agents/opinion_radar` | 新闻快讯读取、噪音过滤、主题聚类、新闻逻辑雷达、市场舆情报告 | `opinion_radar/latest`、`opinion_radar/news_logic`、`opinion_radar/reports` |
| `quanta_agents/news_brief` | 小时和半日新闻中间产物，带 `source_refs` 和 graph trigger candidates | `news_brief/hourly`、`news_brief/half_day`、`agent_workspace/runs/news` |
| `quanta_agents/research_reports` | 微信/研报画像、canonical evidence bridge、研报 evidence 合成 | `canonical_documents/research_reports`、`research_reports/wechat_evidence` |
| `docs/research-logic-graph-framework-layout.md` | 稳定 framework、指标/事件 catalog、研报逻辑图谱的分层与算法设计 | `research_logic_graph` 设计入口 |
| `docs/quanta-cognitive-langgraph-flow.md` | LangGraph 编排、Agent 职责、确定性服务、写入门控和 rollout 计划 | 多源认知处理链设计入口 |
| `docs/research-object-relation-architecture.md` | 新闻、研报、数据先转换为不同知识对象，再通过共享锚点和显式关系汇合 | object relation / state snapshot 设计入口 |
| `quanta_agents/signal_mapping` | 多来源 signal/theme 映射、增量状态、主题报告维护 | `signal_map`、`theme_anchor`、`incremental_state`、`theme_report_maintenance` |
| `quanta_agents/asset_event_state` | legacy 输出收敛为 canonical event，更新 driver state machine，生成 driver-only narrative | `indexes/asset_event_state/events.sqlite3`、`asset_event_state` candidates |
| `quanta_agents/polymarket_daily` | Polymarket CLI 快照采样、热点分析、日报候选 | `polymarket_daily` candidates、`indexes/polymarket` |
| `quanta_agents/maintenance` | 知识库健康巡检 runner | `knowledge_maintenance` candidates |
| `quanta_agents/commodity_industry_graph.py` | 商品产业链结构图谱和测试传播计算 | `industry_graph` candidates |
| `agents/*/README.md` | 面向单个 Agent 的运行说明 | 人类/Agent 操作入口 |
| `docs/quanta-knowledge-storage-layout.md` | L0/L1 结构化真源、market topic 节点库、Wiki 投影和同步门控设计 | 知识库长期存放设计 |
| `docs` | 架构、数据流、运行反馈、集成计划和产物索引 | 项目文档 |
| `tests` | 回归测试和产物契约测试 | pytest suite |

## CLI 入口

| CLI | 模块 | 作用 |
| --- | --- | --- |
| `quanta-opinion-radar-export` | `opinion_radar.export` | 导出舆情雷达、新闻逻辑引用和市场舆情报告 |
| `quanta-opinion-radar-news-logic` | `opinion_radar.news_logic` | 单独生成新闻逻辑雷达 |
| `quanta-opinion-radar-report` | `opinion_radar.report` | 基于 latest radar/news_logic 重算报告 |
| `quanta-hourly-news-brief` | `news_brief.hourly` | 生成小时新闻中间产物 |
| `quanta-half-day-news-brief` | `news_brief.hourly` | 生成半日新闻中间产物 |
| `quanta-futures-daily-import` | `futures_daily.importer` | 导入旧三件套 |
| `quanta-futures-daily-oss-sync` | `futures_daily.oss_sync` | 从 OSS 同步期市日报 |
| `quanta-futures-daily-raw-ingest` | `futures_daily.raw_ingest` | 拉取日报原文 zip |
| `quanta-futures-daily-run-raw` | `futures_daily.raw_run` | 从日报原文生成日频逻辑产物 |
| `quanta-futures-daily-align` | `futures_daily.framework_alignment` | 对已有 summary 重算框架映射 |
| `quanta-futures-daily-logic-chain` | `futures_daily.logic_chain` | 对 summary/alignment 重算主线和逻辑链 |
| `quanta-futures-daily-logic-evolution` | `futures_daily.logic_evolution` | 比较两期日报逻辑演化 |
| `quanta-futures-daily-data-brief` | `futures_daily.data_brief` | 生成品种商品数据简报 |
| `quanta-asset-taxonomy-bootstrap` | `core.taxonomy_bootstrap` | 初始化 futures asset taxonomy candidate |
| `quanta-analysis-framework-registry` | `core.analysis_framework` | 生成 framework registry 和 indicator-event catalog |
| `quanta-commodity-industry-graph` | `commodity_industry_graph` | 生成商品产业链图谱候选 |
| `quanta-framework-audit` | `core.framework_audit` | 审计框架格式和历史遗留问题 |
| `quanta-framework-weight-optimize` | `core.framework_weight_optimizer` | 生成框架权重优化候选 |
| `quanta-wechat-research-evidence` | `research_reports.wechat_evidence` | 生成微信研报证据池 |
| `quanta-wechat-canonical-evidence-bridge` | `research_reports.wechat_canonical_bridge` | canonical evidence bridge |
| `quanta-wechat-single-report-store` | `research_reports.single_report_store` | 生成单篇研报结构化画像 |
| `quanta-research-logic-graph` | `research_reports.logic_graph` | 从四月以来结构化研报生成优化框架设计、逻辑节点、时间状态和 driver 触发候选 |
| `quanta-signal-theme-map` | `signal_mapping.mapper` | 映射 research signal 和 theme anchor |
| `quanta-signal-incremental-state` | `signal_mapping.incremental_state` | 推进增量 research state |
| `quanta-theme-report-maintain` | `signal_mapping.theme_report` | 维护增量主题报告候选 |
| `quanta-polymarket-daily` | `polymarket_daily.export` | 生成 Polymarket 热点日报 |
| `quanta-asset-event-state` | `asset_event_state.pipeline` | 收敛 legacy 输出为 canonical event / driver state |
| `quanta-market-theme-state` | `market_themes.state` | 生成日频市场主题状态候选 |
| `quanta-recent-news-topics` | `market_themes.recent_news_topics` | 从新闻简报抽取最近新闻 MarketTopic 候选 |
| `quanta-topic-evolution` | `market_themes.topic_evolution` | 汇总历史 MarketTopic runs，生成 topic 演化时间线和图谱 read model |
| `quanta-news-topic-backfill` | `market_themes.news_topic_backfill` | 从 MySQL 历史快讯分窗口回填 MarketTopic，并重建 topic 演化 read model |
| `quanta-news-market-update` | `market_themes.news_market_update` | 按半日真实更新窗口串联半日报、新闻主题、topic 演化和主线梳理 |

## 产物索引

以下路径均相对 `GJ_QUANTA_DATA_ROOT`。

| 产物域 | 稳定读取位置 | 归档/运行位置 | 说明 |
| --- | --- | --- | --- |
| raw object | `raw_objects/...` | `raw_manifests/by_source/...` | 外部原文、快照和抓取 manifest |
| taxonomy | `gold/reference_data/assets/futures_assets.v1.json` | `agent_workspace/candidates/taxonomy/latest/futures_assets.v1.json` | 标准资产名、别名、板块、宏观桶 |
| analysis framework | `agent_workspace/candidates/analysis_framework/latest/analysis-framework-registry.json` | `agent_workspace/candidates/analysis_framework/latest/indicator-event-catalog.json` | active framework 的可计算 registry |
| framework audit | `agent_workspace/candidates/framework_audit/latest/framework-audit.json` | `agent_workspace/candidates/framework_audit/{yyyy}/{mm}/{dd}/framework-audit-{time}.json` | active framework 格式审计 |
| framework optimization | `agent_workspace/candidates/framework_optimization/latest/framework-weight-optimization.json` | `agent_workspace/candidates/framework_optimization/{yyyy}/{mm}/{dd}/...` | 权重和维度优化候选 |
| 期市日报 raw run | 无单一 latest | `agent_workspace/candidates/futures_daily_raw_runs/{yyyy}/{mm}/{dd}/RUN-*/` | `manifest`、summary、market_review、alignment、scores、trade_thesis、logic_chains |
| 期市速递候选 | `agent_workspace/candidates/market_brief/...` | `agent_workspace/review_packages/{yyyy}/{mm}/{dd}/RP-{date}-COMMODITY/` | 平台期市速递展示基准 |
| 品种分析候选 | `agent_workspace/candidates/asset_analysis/...` | 同期 review package | 商品 summary / asset analysis |
| 商品数据简报 | run 内 `commodity_data_briefs/{asset}.json` | run 内 `commodity_data_briefs_manifest.json` | Alpha 指标映射到维度和图表序列 |
| 微信单篇画像 | `canonical_documents/research_reports/structured_profiles/hzzhqx_wechat/{yyyy}/{mm}/{dd}/RREP-PROFILE-*.json` | `agent_workspace/runs/research_reports/single_report_store/{yyyy}/{mm}/{dd}/RUN-*/` | 按正文 hash 复用 |
| 微信研报 evidence | `agent_workspace/candidates/research_reports/wechat_evidence/latest/wechat-research-evidence.json` | `agent_workspace/candidates/research_reports/wechat_evidence/{yyyy}/{mm}/{dd}/...` | 研报证据池和主线更新 |
| 研报逻辑图谱 | `agent_workspace/candidates/research_logic_graph/latest/{logic-nodes,logic-edges,temporal-episodes,driver-trigger-candidates}.json` | `agent_workspace/runs/research_logic_graph/{yyyy}/{mm}/{dd}/RUN-*/` | 四月以来结构化研报 -> framework dimension -> catalog term -> logic node/time episode |
| signal map | 无单一 latest | `agent_workspace/candidates/signal_map/{yyyy}/{mm}/{dd}/CAND-SIGNAL-MAP-*/research_signals.json` | 多来源 research signal |
| theme anchor | 可由 matcher 读取近几期 | `agent_workspace/candidates/theme_anchor/{yyyy}/{mm}/{dd}/CAND-THEME-ANCHOR-*/theme_anchors.json` | 主题锚点，仍需 review |
| incremental state | `agent_workspace/candidates/incremental_state/latest/research_state.json` | `agent_workspace/candidates/incremental_state/{yyyy}/{mm}/{dd}/CAND-INCREMENTAL-STATE-*/` | 主题、事件、维度历史状态 |
| theme report maintenance | `agent_workspace/candidates/theme_report_maintenance/latest/theme_report.json` | `agent_workspace/candidates/theme_report_maintenance/{yyyy}/{mm}/{dd}/CAND-THEME-REPORT-*/` | `research_signals`、`theme_anchors`、`research_state`、`theme_report` |
| cognitive object store | 规划：`canonical_objects/{documents,events,claims,evidence,observations}/...` | 规划：`raw_objects`、`raw_manifests`、`relations`、`states`、`event_logs` | 来源目录只用于采集和 provenance；长期知识按 Event / Claim / Evidence / Observation / HumanClaim 等认知角色组织 |
| market topic nodes | 规划：`canonical_objects/market_topics/registry/topic-registry.json`、`canonical_objects/market_topics/topics/{topic_id}.json`、`states/market_topics/latest/{topic_id}.json` | 规划：`relations/topic_memberships/{yyyy}/{mm}/{dd}/topic-memberships.jsonl`、`event_logs/market_topics/{yyyy}/{mm}/{dd}/topic-events.jsonl`、`evidence_links/market_topics/{yyyy}/{mm}/{dd}/topic-evidence.jsonl` | 稳定话题节点库；topic 定义、对象归类关系、状态历史和展示用证据视图分开存；每个 topic 同时是新闻热点和资产 driver logic node |
| recent news topics | `agent_workspace/candidates/market_topics/latest/recent-news-topics.{json,md}`、`topic-registry.json`、`topic-memberships.jsonl`、`topic-states.jsonl`、`topic-evidence.jsonl`、`topic-events.jsonl` | `agent_workspace/candidates/market_topics/{yyyy}/{mm}/{dd}/RUN-RECENT-NEWS-TOPICS-*/`、`agent_workspace/runs/market_topics/{yyyy}/{mm}/{dd}/RUN-*/manifest.json` | 从半日/小时新闻简报抽取最近新闻主题候选；LLM 负责主题抽取和证据归类，代码校验 evidence_id/source_refs，并把归类关系落成 `TopicMembership` |
| topic evolution read model | `agent_workspace/candidates/market_topics/latest/topic-evolution-read-model.json`、`topic-evolution.md`、`topic-evolution/{topic_id}.json` | 由 `agent_workspace/candidates/market_topics/{yyyy}/{mm}/{dd}/RUN-RECENT-NEWS-TOPICS-*/recent-news-topics.json` 编译 | 把多次 topic run 串成演化时间线和图谱节点；供 Alpha 画 topic 演化过程，不作为新的事实或证据源 |
| news topic backfill | `agent_workspace/candidates/market_topics/latest/news-topic-backfill-manifest.json` | `agent_workspace/runs/market_topics/news_topic_backfill/{yyyy}/{mm}/{dd}/RUN-NEWS-TOPIC-BACKFILL-*/` | 从 MySQL `RADAR_FLASH_TABLE` / `jin10_flash` 读取历史快讯，按窗口写临时 brief，再调用 `recent_news_topics` 抽取主题 |
| news market update | `agent_workspace/candidates/market_topics/latest/news-market-update-manifest.json`、`news-mainlines.{json,md}` | `agent_workspace/runs/market_topics/news_market_update/{yyyy}/{mm}/{dd}/RUN-NEWS-MARKET-UPDATE-*/` | 真实半日窗口链路：正式 `half_day_news_brief.v1` -> `recent_news_topics` -> `topic_evolution` -> 新闻主线梳理 |
| wiki projection | 规划：`wiki_projection/index.md`、`wiki_projection/topics/*.md`、`wiki_projection/assets/*.md`、`wiki_projection/drivers/*.md` | 由结构化对象编译，可重建 | 给人和 Agent 阅读的 L2 知识投影，不作为独立计权证据 |
| 舆情雷达 | `agent_workspace/candidates/opinion_radar/latest/radar.json` | `agent_workspace/candidates/opinion_radar/{yyyy}/{mm}/{dd}/radar-{HHMMSS}.json` | 主题热度、时间线、top flashes |
| 新闻逻辑雷达 | `agent_workspace/candidates/opinion_radar/news_logic/latest/news-logic.json` | `agent_workspace/candidates/opinion_radar/news_logic/{yyyy}/{mm}/{dd}/news-logic-{HHMMSS}.json` | 新闻到 framework 维度和研报主线关系 |
| 市场舆情雷达报告 | `agent_workspace/candidates/opinion_radar/reports/latest/market-radar-report.{json,md}` | `agent_workspace/candidates/opinion_radar/reports/{yyyy}/{mm}/{dd}/market-radar-report-{HHMMSS}.{json,md}` | 投资逻辑验证、修正动作、预警 |
| 小时新闻简报 | `agent_workspace/candidates/news_brief/hourly/latest/hourly-news-brief.{json,md}` | `agent_workspace/candidates/news_brief/hourly/{yyyy}/{mm}/{dd}/CAND-NEWS-BRIEF-HOURLY-*/` | graph trigger candidates 和引用证据 |
| 半日新闻简报 | `agent_workspace/candidates/news_brief/half_day/latest/half-day-news-brief.{json,md}` | `agent_workspace/candidates/news_brief/half_day/{yyyy}/{mm}/{dd}/CAND-NEWS-BRIEF-HALF-DAY-*/` | 半日窗口新闻中间产物 |
| 新闻简报 run | 无单一 latest | `agent_workspace/runs/news/{hourly|half_day}/{yyyy}/{mm}/{dd}/RUN-*/` | raw flashes、news_logic、brief、run_manifest |
| Polymarket | `agent_workspace/candidates/polymarket_daily/latest/` | `agent_workspace/candidates/polymarket_daily/{yyyy}/{mm}/{dd}/CAND-POLYMARKET-*/` | daily_report、radar、hotspots、research_signals |
| Polymarket index | `indexes/polymarket/market_hotspots.jsonl` | `indexes/polymarket/daily_reports.jsonl` | 预测市场检索索引 |
| 资产事件状态机 | `agent_workspace/candidates/asset_event_state/latest/{events,drivers,narratives}.json` | `agent_workspace/candidates/asset_event_state/{yyyy}/{mm}/{dd}/CAND-ASSET-EVENT-STATE-*/` | canonical events、graph events、causal paths、signals、drivers、narratives |
| 资产事件 index | `indexes/asset_event_state/events.sqlite3` | `agent_workspace/runs/asset_event_state/{yyyy}/{mm}/{dd}/RUN-*/run_manifest.json` | event/graph/signal/driver/narrative tables |
| 商品产业链图谱 | `agent_workspace/candidates/industry_graph/latest/commodity-industry-graph.json` | `agent_workspace/candidates/industry_graph/{yyyy}/{mm}/{dd}/commodity-industry-graph-{HHMMSS}.json` | 低频结构骨架和测试传播计算 |
| 知识库维护 | `agent_workspace/candidates/knowledge_maintenance/...` | `agent_workspace/runs/knowledge_maintenance/...` | schema、候选、review package、framework 健康巡检 |

## 模块产出依赖关系登记

后续每确认一个模块，都要在这里补一条依赖关系。每条记录必须说明：代码入口、输入依赖、运行产物、稳定读取位置、下游消费方、验证命令和最近一次确认结果。

### 新闻半日简报 / `news_brief.hourly`

| 项 | 内容 |
| --- | --- |
| 代码入口 | `quanta_agents/news_brief/hourly.py` |
| CLI | `quanta-half-day-news-brief` 或 `python3 -m quanta_agents.news_brief.hourly --period half-day --hours 12` |
| 上游输入 | `opinion_radar.db.fetch_flashes()` 从 `RADAR_FLASH_TABLE` / `jin10_flash` 读取窗口快讯；`opinion_radar.filters.keep_flash()` 做快讯过滤；`opinion_radar.news_logic.build_news_logic_radar()` 做资产和框架维度映射；`core.llm_client.chat()` 只基于 `evidence_pack` 生成叙事层 |
| 间接依赖 | `core.taxonomy`、analysis framework registry、`opinion_radar.news_logic` 的资产/维度映射规则 |
| 运行产物 | `agent_workspace/runs/news/half_day_news_brief/{yyyy}/{mm}/{dd}/RUN-HALF-DAY-NEWS-BRIEF-*/raw_flashes.json`、`news_logic.json`、`half-day-news-brief.{json,md}`、`run_manifest.json`、`run_summary.json` |
| 候选产物 | `agent_workspace/candidates/news_brief/half_day/{yyyy}/{mm}/{dd}/CAND-NEWS-BRIEF-HALF-DAY-*/manifest.json`、`half-day-news-brief.{json,md}` |
| 稳定读取位置 | `agent_workspace/candidates/news_brief/half_day/latest/manifest.json`、`half-day-news-brief.json`、`half-day-news-brief.md` |
| 下游消费方 | `gj_chainplatform/backend/app/opinion_radar_store.py` 读取 latest 文件并注入 `/api/v1/opinion-radar` 的 `half_day_brief`；`frontend/src/OpinionRadarWorkbench.tsx` 读取 `data.half_day_brief.payload` 渲染半日汇总；`asset_event_state` 后续可按 `next_step.input_role = news_intermediate` 消费 |
| 叙事规则 | `key_news` 优先做重点新闻梳理、合并同类项和事件链聚类；`watch_items` 提示后续动向；`asset_notes` / `graph_notes` 是可选栏目，证据不足时留空；所有模型文字必须带有效 `source_refs` |
| 验证命令 | `python3 -m pytest tests/test_hourly_news_brief.py`；`python3 -m compileall -q quanta_agents/news_brief tests/test_hourly_news_brief.py`；前端接入用 `npm run build` 在 `gj_chainplatform/frontend` 验证 |
| 最近确认 | `RUN-HALF-DAY-NEWS-BRIEF-20260621-175201`，raw `184`，kept `178`，mapped events `2`，assets `2`，`citation_policy_status=all_items_cited`，`invalid_ref_count=0`，`discarded_uncited_items=0` |
| 注意事项 | UI 展示受 `gj_chainplatform` 登录态保护；页面未显示不等于生成链路失败，需要区分产物生成、平台读取和用户登录态三层 |

### 舆情雷达主题卡 / `opinion_radar.export`

| 项 | 内容 |
| --- | --- |
| 代码入口 | `quanta_agents/opinion_radar/export.py` 调用 `opinion_radar.service.snapshot()`；LLM 命名和合成在 `opinion_radar/llm.py` |
| CLI | `quanta-opinion-radar-export` 或 `python3 -m quanta_agents.opinion_radar.export`；`--no-llm` 会关闭主题命名和合成主线 |
| 上游输入 | `opinion_radar.db.fetch_flashes()` 从 `RADAR_FLASH_TABLE` / `jin10_flash` 读取窗口快讯；`opinion_radar.filters.keep_flash()` 做噪音过滤；`opinion_radar.dictionary.classify()` 生成品种/宏观/板块/类别命中 |
| 主题卡生成 | `service.snapshot()` 对每条快讯按 `_bucket_hits()` 分桶，按 `_heat_weight()` 累计 `heat`，取 top bucket；每个 bucket 生成 `theme`、`summary`、`logic`、`top_flashes`，写入 `windows.{hours}.themes` |
| 合成主线生成 | 当 `name_llm=True` 且 `config.LLM_ENABLED=True` 时，`llm.synthesize()` 把多个主题桶合并成 5-8 条 `windows.{hours}.synthesis`；LLM 不可用、返回格式错误或异常时返回空数组 |
| 稳定读取位置 | `agent_workspace/candidates/opinion_radar/latest/radar.json`；归档在 `agent_workspace/candidates/opinion_radar/{yyyy}/{mm}/{dd}/radar-{HHMMSS}.json` |
| 下游消费方 | `gj_chainplatform/backend/app/opinion_radar_store.py` 只读 latest 并注入 `half_day_brief`；`frontend/src/OpinionRadarWorkbench.tsx` 用 `windows[windowKey].themes.slice(0, 12)` 渲染“核心主题情报卡”，用 `windows[windowKey].synthesis.slice(0, 3)` 渲染“合成主线” |
| 当前确认 | latest `radar.json` 为 `generated_at=2026-06-20 18:01:10.035534`，24h `themes=15`，`synthesis=0`，`llm.enabled=false`；因此当前页面有主题卡数据，但合成主线为空属于产物状态，不是前端临时丢失 |
| 注意事项 | 页面取数受 `GJ_AUTH_REQUIRED` / 登录态控制；未登录时会显示“请先登录后再查看舆情雷达。”并进入空态。`synthesis.members` 当前保存主题 `label`，而前端 `activeSynth` 用 `theme.key` 匹配；后续若要主题卡点击联动合成主线，应改为同时输出 `member_keys`，或统一前后端匹配字段 |

### 市场主题日更状态 / `market_theme_state`

| 项 | 内容 |
| --- | --- |
| 设计文档 | `docs/market-theme-daily-iteration.md` |
| 代码入口 | `quanta-market-theme-state` / `python3 -m quanta_agents.market_themes.state`；独立于 `opinion_radar.export`，避免把主题生命周期和雷达展示耦合 |
| 上游输入 | 默认只读期市速递汇总层：`*_commodity_marketreview.json` 的 `market_events_summary` / `market_logic_summary`，以及 `logic_summary.json` 的 `important_events_summary` / `important_logic_summary`；舆情雷达和新闻半日简报为显式可选输入 |
| 核心逻辑 | 以期市速递“重要市场事件 / 市场核心逻辑”和逻辑链“重要事件总结 / 重要逻辑梳理”生成稳定 `theme_id` 的市场主题状态；默认使用模型 Agent 做主题抽取和证据归类，代码层校验 `evidence_id` / source refs / concept ID；规则只作为 fallback 与质量门；不读取 `commodity_summary.detailed_analysis` 或 `brief_thesis_anchor.assets` 的品种级明细 |
| 候选产物 | `agent_workspace/candidates/market_themes/{yyyy}/{mm}/{dd}/market-theme-state.{json,md}` |
| 稳定读取位置 | `agent_workspace/candidates/market_themes/latest/market-theme-state.{json,md}` |
| 下游消费方 | 舆情雷达页可展示“今日强化/新增/冲突/待验证主题”；`market_topics` 维护器后续把日快照合并为稳定 topic node；`asset_event_state` 可消费为 driver update 输入；`research_logic_graph` 可消费为概念节点拆分/合并/补充候选 |
| 注意事项 | `market-theme-state` 是日频观察快照，不是长期真源；长期主题定义、`TopicMembership`、事件日志、证据读模型、状态历史和 Wiki 投影应进入 `market_topics` / `wiki_projection`；第一版不自动改写期市速递结论，不把 LLM 标题作为主题 ID；所有主题变化必须保留汇总文件 source refs；可用 `--no-llm` 进入规则兜底模式，`--require-llm` 强制检查模型链路 |

### 最近新闻主题 / `market_themes.recent_news_topics`

| 项 | 内容 |
| --- | --- |
| 代码入口 | `quanta-recent-news-topics` / `python3 -m quanta_agents.market_themes.recent_news_topics` |
| 上游输入 | `news_brief/half_day/latest/half-day-news-brief.json`，可选 `news_brief/hourly/latest/hourly-news-brief.json` |
| 核心逻辑 | 模型 Agent 从新闻简报 evidence candidates 中抽取可持续维护的 `MarketTopic`；代码层校验 `evidence_id`、稳定 `topic_id`、`source_refs` 和 schema；派生简报条目只用于归类和理解，`evidence_weight=0`，不能被当作独立证据来源 |
| 稳定读取位置 | `agent_workspace/candidates/market_topics/latest/recent-news-topics.json`、`recent-news-topics.md`、`topic-registry.json`、`topic-memberships.jsonl`、`topic-states.jsonl`、`topic-evidence.jsonl`、`topic-events.jsonl` |
| 下游消费方 | 舆情雷达主题页、后续 `market_topics` 维护器、资产事件状态机、图谱可视化页面 |
| 最近确认 | `RUN-RECENT-NEWS-TOPICS-20260622-001149`，DeepSeek LLM，候选证据 `41`，主题 `7`，TopicMembership `39`，证据展示链接 `39`，无效引用 `2`；latest 已写入 `agent_workspace/candidates/market_topics/latest/` |
| 注意事项 | `topic-memberships.jsonl` 是归类关系真源候选；`topic-evidence.jsonl` 是给前端展示的读模型。新闻快讯默认 `truth_status=unverified`，可以提高 topic heat / market_attention，但不能直接变成已确认事实 |

### Topic 演化读模型 / `market_themes.topic_evolution`

| 项 | 内容 |
| --- | --- |
| 代码入口 | `quanta-topic-evolution` / `python3 -m quanta_agents.market_themes.topic_evolution` |
| 上游输入 | `agent_workspace/candidates/market_topics/{yyyy}/{mm}/{dd}/RUN-RECENT-NEWS-TOPICS-*/recent-news-topics.json` |
| 核心逻辑 | 确定性汇总多个 topic run；对每个 topic 生成 `first_seen`、`last_seen`、state delta、`evidence_added`、`topic_not_observed`、`lifecycle_state` 和图谱节点；不调用模型，不新增事实 |
| 稳定读取位置 | `agent_workspace/candidates/market_topics/latest/topic-evolution-read-model.json`、`topic-evolution.md`、`topic-evolution/{topic_id}.json` |
| 下游消费方 | Alpha 话题演化图谱页面、舆情雷达主题详情、后续 `market_topics` 维护器 |
| 注意事项 | 这是 read model / projection。它把 `TopicMembership`、`TopicState` 和 archived runs 串成时间线，不能替代 canonical topic、membership 或 event log 真源 |

### 新闻主题历史回填 / `market_themes.news_topic_backfill`

| 项 | 内容 |
| --- | --- |
| 代码入口 | `quanta-news-topic-backfill` / `python3 -m quanta_agents.market_themes.news_topic_backfill` |
| 上游输入 | `opinion_radar.db.fetch_flashes()` 从 MySQL `RADAR_FLASH_TABLE` / `jin10_flash` 读取历史快讯；`opinion_radar.filters.keep_flash()` 过滤噪音 |
| 核心逻辑 | 按窗口读取历史新闻，确定性筛选高价值快讯写成临时 `backfill_news_brief.v1`，再调用 `market_themes.recent_news_topics` 做模型主题抽取，最后调用 `market_themes.topic_evolution` 只基于本次 `topic_run_ids` 重建演化 read model |
| 稳定读取位置 | `agent_workspace/candidates/market_topics/latest/news-topic-backfill-manifest.json`、`topic-evolution-read-model.json`、`topic-evolution.md` |
| 最近确认 | `RUN-NEWS-TOPIC-BACKFILL-20260423-20260622-010455`，覆盖 `2026-04-23 00:47:46` 至 `2026-06-22 00:47:46`，raw `59918`，kept `52326`，important `2423`，selected `540`，窗口 `9`，topic runs `9`，errors `0`；clean evolution 使用本次 `9` 个 run，topic `122`，graph nodes `952`，edges `839` |
| 质量备注 | 当前 `9` 个窗口中 LLM 成功 `4` 个、规则兜底 `5` 个；因此这次产物是历史回填 smoke / baseline，不能直接作为稳定 topic 库。后续应缩小窗口、增强提示词、引入候选 topic registry，并限制规则兜底进入前台 |

### 半日新闻市场更新 / `market_themes.news_market_update`

| 项 | 内容 |
| --- | --- |
| 代码入口 | `quanta-news-market-update` / `python3 -m quanta_agents.market_themes.news_market_update` |
| 上游输入 | MySQL `RADAR_FLASH_TABLE` / `jin10_flash` 最近 N 天快讯；每个窗口调用正式 `news_brief.hourly.publish_hourly_news_brief(period="half_day")` 生成 `half_day_news_brief.v1` |
| 核心逻辑 | 按 12 小时窗口模拟真实更新：半日报 -> 模型抽取最近新闻 topic -> 只用本次成功 `topic_run_ids` 编译 topic 演化 read model -> 基于演化 topic 合成新闻主线报告 |
| 模型原则 | 半日报叙事、topic 抽取和主线梳理优先使用模型 Agent；topic 抽取默认 `require_llm=True`，模型失败时该窗口记失败，不写规则兜底 topic 进前台；主线可在模型失败时降级为规则投影并写质量备注 |
| Topic 输入控制 | `recent_news_topics` 从半日报提取 `top_flashes`、`graph_trigger_candidates` 和已通过引用校验的简报条目，先编译为 topic signal pack：按原始快讯/派生简报条目聚合，一条 signal 内挂载相关 graph events；默认将全部结构化 signals 交给 LLM，但不展开 `source_refs`；高流量窗口用 `--topic-batch-size` / `--topic-batch-workers` 做并行 batch topic extraction + topic merge，manifest 记录 batch 数、实际 signal 数和 prompt 长度；`--topic-evidence-limit` 仅用于压测裁剪 |
| 稳定读取位置 | `agent_workspace/candidates/market_topics/latest/news-market-update-manifest.json`、`topic-evolution-read-model.json`、`topic-evolution.md`、`news-mainlines.json`、`news-mainlines.md`、`news-mainlines-derived-report.json`、`news-mainlines-report-dependencies.jsonl` |
| 运行产物 | `agent_workspace/runs/market_topics/news_market_update/{yyyy}/{mm}/{dd}/RUN-NEWS-MARKET-UPDATE-*/manifest.json`、`topic-evolution-read-model.json`、`topic-evolution.md`、`news-mainlines.{json,md}`、`news-mainlines-derived-report.json`、`news-mainlines-report-dependencies.jsonl`；每个半日报仍写入 `agent_workspace/runs/news/half_day_news_brief/...` |
| 下游消费方 | 舆情雷达主题演化页、新闻主线卡、后续 `market_topics` 维护器和资产事件状态机 |
| 验证命令 | `python3 -m pytest tests/test_news_market_update.py tests/test_recent_news_topics.py tests/test_topic_evolution.py tests/test_hourly_news_brief.py`；`ruff check quanta_agents/market_themes/news_market_update.py ...` |
| 注意事项 | CLI 会逐窗口输出 fetch / brief / topic / evolution / mainline 进度；隔离模型调用必须先从子进程结果队列读取 payload 再 join，避免大半日报 payload 在 Queue feeder 阶段造成假阻塞。`news-mainlines` 是 projection / DerivedReport，不是独立证据源；每条主线必须保留 `topic_ids` 和 `evidence_refs`，进一步回到 `source_refs`；同时写出 `derived_report.v1` 和 `report_dependency.v1`，保持 `evidence_weight=0` |

## 当前 Radar 质量门

新闻主题聚类和投资逻辑报告必须满足这些约束：

- 短 ASCII 别名必须按单词边界匹配；`AU`、`AG` 这类两字符代码区分大小写。
- 易混中文别名必须带语境保护；`苹果`、`玻璃`、`铅`、`锡` 不能被科技股、显示面板、铅笔、无锡等文本误触发。
- 已命中具体品种时，主题桶优先保留品种和宏观桶，避免同一新闻同时膨胀到板块/品类。
- 汇总新闻和纯行情涨跌要降权；真正的政策、库存、供需、地缘、央行、就业通胀事件应成为代表样本。
- `market-radar-report` 的 `updated_logic` 必须来自最新新闻证据和 `news_logic` 一致性计数，不能复述过期研报文本。
- theme anchor 仍是 candidate，不是 gold；若卡片标题泄漏为长篇研报主线标题，需要回落到自由主题名或品种桶名。
- 所有“修正/降级/条件性主线”必须保留证据文本、source ref 和关联 logic run，便于人工复核。

## 当前 Latest 样例

截至当前项目文档维护时，`opinion_radar/latest` 的真实样例为：

```text
generated_at: 2026-06-20 18:01:10.035534
default_window: 24h
24h flash_total: 725
24h noise_filtered: 40
24h important_total: 136
24h theme_count: 15
24h variety_covered: 6
news_logic mapped_event_count: 358
news_logic asset_count: 34
news_logic consistency_counts:
  thesis_conflict: 41
  dimension_conflict: 15
  supports_thesis: 29
  new_signal: 1
  tracking: 272
latest report: agent_workspace/candidates/opinion_radar/reports/latest/market-radar-report.{json,md}
```

这个样例只用于说明当前输出形态和规模，不作为业务判断。若 `latest` 已被新 run 覆盖，以产物文件自身的 `generated_at` 和 `run_manifest` 为准。

## 维护规则

- 新增输出形态前，先引用或补充 `quanta_data/configs/schemas` 契约。
- 主题抽取、证据归类、聚类、叙事综合、图谱节点触发等认知类任务默认优先使用模型 Agent；确定性规则主要用于 schema、source refs、质量门、硬约束、可回放校验和 fallback。只有规则明显更合适时才以规则为主路径。
- LangGraph 只负责编排、分支和 Agent 调用；生产写入必须通过统一 `Knowledge Write Service` 或等价写入门控，不能让任一 Agent 直接覆盖正式 state、正式 graph edge 或 `gold`。
- 来源类型目录只服务于采集和 provenance；长期知识不要按新闻/研报/数据/人工文章/Agent 文章分割真源，而要转成 Document、Event、Claim、Evidence、Observation、HumanClaim、DerivedReport 等认知对象。
- Topic 不是文件夹，也不是一篇报告；Topic 定义、TopicMembership、TopicState、TopicEventLog、ReportDependency 必须分开存放，方便持续更新、审计和回放。
- `truth_status` 和 `market_attention` 必须分开：未确认但被市场交易的信息可以提高热度，不能直接提高可信事实状态。
- Agent 报告、Wiki 和自动简报属于 `DerivedReport` / projection，默认 `evidence_weight=0`，不得作为独立证据再次提高 topic credibility 或 driver confidence。
- 结构化对象和状态真源放在 L0/L1；Wiki / Markdown 只作为 L2 可读投影，不能成为 driver state、edge weight、data value 的直接真源。
- 每个长任务必须写 `run_manifest`，包含输入、输出、错误、模型引用和人工复核要求。
- JSON 内路径优先使用相对 `quanta_data` 的路径。
- 机器候选产物必须可追踪到 evidence 或 source refs。
- README 只放入口和高频命令；细节放在本文件、架构文档和对应 Agent README。
