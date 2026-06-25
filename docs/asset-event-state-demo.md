# Asset Event State 最小 Demo

本文记录“新闻/研报/数据 -> 因果传播 -> signal update -> driver 状态 -> 品种报告”的隔离 demo。它只写入 `quanta_data/agent_workspace/candidates/asset_event_state_demo/`，不写 `gold`，不修改 `gj_chainplatform` 正式 UI，也不迁移生产数据。

## 目标

这条 demo 用现有数据证明链路可运行，而不是统一所有历史 schema。当前版本使用 deterministic rule fallback，不要求 LLM 可用；所有传播参数、source weight、状态权重放在 `quanta_agents/asset_event_state/demo_config.v1.json`。

## 输入

- 新闻逻辑：`agent_workspace/candidates/opinion_radar/news_logic/latest/news-logic.json` 以及同日期分区下的 `news-logic-*.json`。
- 微信研报证据：`agent_workspace/candidates/research_reports/wechat_evidence/latest/wechat-research-evidence.json`。
- 旧状态参考：`agent_workspace/candidates/signal_mapping/incremental_state/latest/incremental-state.json`，存在时作为 driver before 的参考输入。
- 品种框架：优先读取铜候选框架 `agent_workspace/candidates/frameworks/2026/06/16/FWK-CU-20260616.json`，并保留 framework id/version。
- 基本面数据：优先用 `gj_chainplatform/backend/app/data_agent/data_dict.json` 选择铜相关序列，再通过 `GJ_DATA_AGENT_*` 或 `MYSQL_*` 配置查询 MySQL；若期货行情库缺少 `GJ_FUTURES_MYSQL_*`，会记录到 `errors.jsonl` 并跳过 `tushare_fut_*`。

## 运行

建议使用隔离环境，避免污染系统 Python：

```bash
python3 -m venv /Users/miniquanta/.cache/quanta_agents_demo_venv
/Users/miniquanta/.cache/quanta_agents_demo_venv/bin/python -m pip install -e ".[dev,radar]"
/Users/miniquanta/.cache/quanta_agents_demo_venv/bin/python -m quanta_agents.asset_event_state.demo_pipeline \
  --root "/Volumes/数字大脑/quanta_data" \
  --date 20260620
```

运行成功后会生成：

```text
/Volumes/数字大脑/quanta_data/agent_workspace/candidates/asset_event_state_demo/<run_id>/
```

并更新：

```text
/Volumes/数字大脑/quanta_data/agent_workspace/candidates/asset_event_state_demo/latest_manifest.json
```

## 输出合约

每次运行至少包含：

- `run_manifest.json`：运行入口、输入引用、输出引用、统计、复现命令和风险。
- `selected_inputs.json`：固定本次 asset、time window、news/report/data/framework 选择。
- `filtered_news.jsonl`：所有候选新闻的 `relevant/candidate/filtered_out` 结果和原因。
- `atomic_events.jsonl`：新闻、研报、基本面 observation 转出的原子事件。
- `causal_activations.jsonl` 和 `causal_paths.jsonl`：节点激活和受约束传播路径。
- `signal_updates.jsonl`：映射到框架节点和 driver 的 signal update。
- `driver_state_before.json` 和 `driver_state_after.json`：运行前后状态。
- `validation_results.json`：按 driver 汇总的多源验证、冲突和 warning。
- `demo_report.md`：可读报告，不含买卖指令。
- `audit_samples.md`：至少 10 条新闻和 5 条 signal 的人工审计样本。
- `agent_records.json` 和 `task_state.json`：五个工程 Agent 的共享任务记录。
- `execution_log.jsonl` 和 `errors.jsonl`：阶段日志和可恢复错误。

## 传播与降权规则

- 原子事件先激活 causal node，不直接写投资结论。
- 多跳路径的置信度按 `event_confidence * edge_confidence_product * depth_decay` 计算。
- `depth_decay` 当前为 `0.8 ^ (hop_count - 1)`，最大传播深度为 4。
- 超过配置允许 hop、框架映射低置信、路径置信度过低的 signal 会标记为 `low_confidence` 或 `needs_review`。
- Driver 更新使用透明加权规则：`direction * magnitude * confidence * source_weight * time_decay * duplication_penalty * status_weight`。
- 新闻、研报、基本面数据使用不同 source weight；转载或同事件组不会重复提高 source diversity。

## 已知限制

- 新闻相关性依赖现有 `news_logic` 映射，铜箔股票题材和泛科技新闻仍可能进入候选，需要人工复核。
- 因果图是 demo 配置，不是完整铜产业链知识图谱；长链条只作为 risk hint。
- MySQL 指标可计算近端变化和分位数；从文本抽取的 observation 没有完整历史分位数。
- 缺少期货行情库环境变量时，`tushare_fut_daily/wsr` 只记录跳过，不阻塞 demo。
