# News Brief Agent

`quanta-hourly-news-brief` 每小时生成一次过去一小时新闻简报，`quanta-half-day-news-brief` 生成过去半日新闻汇总。它们都属于 raw-to-intermediate 链路里的“新闻摘要 / news intermediate”产物，不直接更新资产 driver，也不生成最终投资结论。

若要模拟真实半日更新链路，不应直接使用历史回填脚本的轻量 brief，而应使用：

```bash
python3 -m quanta_agents.market_themes.news_market_update \
  --quanta-root /Volumes/数字大脑/quanta_data \
  --days 7 \
  --window-hours 12
```

该编排会逐个半日窗口生成正式 `half_day_news_brief.v1`，再接 `recent_news_topics`、`topic_evolution` 和 `news-mainlines`。
CLI 会按窗口打印 `fetch`、`brief`、`topic`、`topic evolution`、`news mainline` 进度。模型步骤默认在可终止子进程中运行，父进程先读取子进程返回 payload 再回收进程，避免大半日报结果在 multiprocessing Queue 退出阶段造成假阻塞。

如果半日报模型叙事未通过引用校验，`recent_news_topics` 不把半日报正文当成事实源，也不把每个 graph event 展开成一条重复新闻。它会先把 `top_flashes`、`graph_trigger_candidates` 和已带引用的简报条目编译成 topic signal pack：一条原始快讯或派生简报条目是一条 signal，相关 graph trigger 作为 `ev` 数组挂在该 signal 下。topic LLM 默认接收全部结构化 signals，但不展开 `source_refs`；完整溯源仍由本地 evidence map 回填到 `topic-evidence.jsonl`。高流量窗口按 `--topic-batch-size` 和 `--topic-batch-workers` 做并行 map-reduce：每批抽 topic，再合并 topic 候选；压测时可用 `--topic-evidence-limit` 临时裁剪 signal 数。

## Data Flow

```text
jin10_flash / RADAR_FLASH_TABLE
  -> opinion_radar.news_logic
  -> hourly_news_brief.v1 / half_day_news_brief.v1
  -> news_brief/{hourly|half_day} candidate + latest markdown
  -> asset_event_state / graph trigger input
```

## News Topic Agent Boundary

半日更新链路里每个 Agent 的职责要分开：

| 阶段 | 目的 | 模型可以做什么 | 不能做什么 |
| --- | --- | --- | --- |
| `half_day_news_brief.v1` | 把 raw flashes 过滤成可报告新闻事件、映射到 news_logic，并生成可读新闻中间产物 | 基于 evidence pack 聚类叙事、写 `key_news` / `watch_items`，且必须带 source_refs | 不能把行情涨跌、收盘播报、现报、技术面/多空评论当成重点新闻，不能生成最终投资结论，不能无引用写入正文 |
| `recent_news_topics` | 从半日报 signal pack 抽 MarketTopic 候选和 TopicMembership | 聚合多个 signals，命名稳定 topic_id，引用 signals[].id | 不能逐条改写新闻，不能把 DerivedReport 当独立证据，不能补外部事实 |
| `topic_evolution` | 把本次成功 topic runs 编译成时间线 read model | 不调用模型，只做确定性 first_seen / last_seen / delta / graph projection | 不能读取历史所有 run，不能新增事实 |
| `news-mainlines` | 基于 topic evolution 合并新闻主线 | 合并相关 topics，保留 topic_ids 和 evidence_refs | 不能替代 topic/evidence 真源，不能输出无证据主线 |

## Dependency Contract

本模块的依赖关系要和 [project-structure-and-outputs.md](project-structure-and-outputs.md) 中的“模块产出依赖关系登记”保持一致。

| 层级 | 依赖 / 产物 |
| --- | --- |
| 快讯读取 | `opinion_radar.db.fetch_flashes(start, end)` 从 `RADAR_FLASH_TABLE` / `jin10_flash` 读取窗口快讯；`--use-latest-data-time` 会先用 `db.latest_time()` 对齐数据库最新时间 |
| 新闻过滤 | `opinion_radar.filters.keep_news_brief_flash()` 保留可进入半日报正文和 news_logic 的快讯；行情涨跌、报价/收盘和技术评论只计入过滤统计 |
| 图谱映射 | `opinion_radar.news_logic.build_news_logic_radar()` 输出 `events`、`assets`、`graph_trigger_candidates` 和 `news_logic_stats` |
| 叙事模型 | `core.llm_client.chat()` 只接收 `_llm_evidence_pack()`，不能补外部事实、交易建议或仓位建议 |
| 引用校验 | `_normalize_source_refs()` 和 `_normalize_llm_items()` 校验 `source_refs`；无有效引用的模型文字进入 `discarded_uncited_items` |
| 平台读取 | `gj_chainplatform/backend/app/opinion_radar_store.py` 读取 `news_brief/half_day/latest` 并注入 `/api/v1/opinion-radar` |
| 前端展示 | `gj_chainplatform/frontend/src/OpinionRadarWorkbench.tsx` 读取 `data.half_day_brief.payload` |

叙事层优先级：

1. `key_news_paragraph`：给普通用户直接阅读的一段重点新闻正文，必须带 `source_refs`。
2. `key_news`：结构化事件要点，供回看来源和主题抽取使用。
3. `watch_items`：后续最值得跟踪的动向、确认点或分歧点。
4. `asset_notes`：可选，只有资产/维度映射清楚时填写。
5. `graph_notes`：可选，只有图谱触发关系清楚时填写。

半日报默认不把以下内容写入 Markdown 正文：

- 价格涨跌、日内涨跌幅、收盘/开盘播报、现报、跌破/站上等报价类信息。
- 主力合约涨跌、夜盘/日盘收盘、涨多跌少/跌多涨少等盘面综述。
- 技术面、多空、获利了结、反弹布局、基差/报价数据更新等行情评论。

这些内容后续应进入 `MarketObservation` 或行情验证链路，而不是作为新闻事件占据半日报正文。JSON 会在 `source.news_brief_filter` 和 `stats.filtered_market_update_count` 中保留过滤统计，便于质控复核。

## Run

```bash
python3 -m quanta_agents.news_brief.hourly \
  --quanta-root /Volumes/数字大脑/quanta_data \
  --hours 1
```

半日汇总：

```bash
python3 -m quanta_agents.news_brief.hourly \
  --quanta-root /Volumes/数字大脑/quanta_data \
  --period half-day \
  --hours 12
```

安装为 editable 后也可以使用：

```bash
quanta-half-day-news-brief --quanta-root /Volumes/数字大脑/quanta_data
```

默认会调用 LLM 生成可读叙事层。模型只能基于 evidence pack 改写，正式报告里的每条模型文字都必须带 `source_refs`，引用已有 `flash_id` 或 `event_id`。缺少有效引用的模型文字会进入 `discarded_uncited_items`，不会渲染到 Markdown 正文；如果主 JSON 生成没有产出可用 `key_news_paragraph`，会触发一次 focused paragraph retry，只要求模型基于 `important_event_candidates` 生成一段重点新闻。若 focused retry 仍失败，才用规则事件候选兜底，避免正文只剩“暂无”。

叙事层的优先级是“重点新闻梳理与聚类 -> 值得关注的后续动向 -> 可选资产观察/图谱触发说明”。`key_news` 应合并同一事件链的多条快讯，不做逐条翻译；`watch_items` 用来提示后续最值得跟踪的确认点或分歧点；`asset_notes` 和 `graph_notes` 只有在结构化映射足够清楚时才填写，证据不足时应为空。

测试或模型不可用时可以关闭叙事层：

```bash
python3 -m quanta_agents.news_brief.hourly \
  --quanta-root /Volumes/数字大脑/quanta_data \
  --hours 1 \
  --no-llm-brief
```

## Outputs

Run artifacts:

```text
agent_workspace/runs/news/{hourly_news_brief|half_day_news_brief}/{yyyy}/{mm}/{dd}/RUN-*-NEWS-BRIEF-{yyyyMMdd-HHMMSS}/
  raw_flashes.json
  news_logic.json
  {hourly-news-brief|half-day-news-brief}.json
  {hourly-news-brief|half-day-news-brief}.md
  run_manifest.json
  run_summary.json
```

Candidate artifacts:

```text
agent_workspace/candidates/news_brief/{hourly|half_day}/{yyyy}/{mm}/{dd}/CAND-NEWS-BRIEF-*-{yyyyMMdd-HHMMSS}/
  manifest.json
  {hourly-news-brief|half-day-news-brief}.json
  {hourly-news-brief|half-day-news-brief}.md

agent_workspace/candidates/news_brief/{hourly|half_day}/latest/
  manifest.json
  {hourly-news-brief|half-day-news-brief}.json
  {hourly-news-brief|half-day-news-brief}.md
```

## Traceability Rules

- `raw_flashes.json` preserves the source rows for the one-hour window.
- `news_logic.json` preserves asset and framework-node mapping.
- `hourly-news-brief.json` preserves `top_flashes`, `graph_trigger_candidates`, `llm_brief`, and `source_refs`.
- `manifest.json` links the candidate to raw flashes and generated brief files.
- `run_manifest.json` records the source table, output refs, model refs, and any model/citation failures.

## Last Confirmed

最近一次人工确认：

```text
run_id: RUN-HALF-DAY-NEWS-BRIEF-20260621-175201
candidate_id: CAND-NEWS-BRIEF-HALF-DAY-20260621-175201
latest: agent_workspace/candidates/news_brief/half_day/latest/half-day-news-brief.{json,md}
raw_flash_count: 184
kept_flash_count: 178
mapped_event_count: 2
asset_count: 2
citation_policy_status: all_items_cited
invalid_ref_count: 0
discarded_uncited_items: 0
```

验证命令：

```bash
python3 -m pytest tests/test_hourly_news_brief.py
python3 -m compileall -q quanta_agents/news_brief tests/test_hourly_news_brief.py
```

前端接入验证在 `gj_chainplatform/frontend` 运行：

```bash
npm run build
```
