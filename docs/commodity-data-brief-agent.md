# Commodity Data Brief Agent

`quanta_agents.futures_daily.data_brief` bridges Alpha 问答的数据指标体系 and the commodity analysis framework.

## Data Sources

- Alpha 问答指标字典：`gj_chainplatform/backend/app/data_agent/data_dict.json`
- Alpha 问答时间序列：MySQL `dzq_data(data_name, dt, value, unit)`
- 品种分析框架：`quanta_data` active/candidate commodity frameworks
- 期市逻辑：`futures_daily_raw_runs/.../trade_thesis.json`

## Output

For each commodity, the agent writes `commodity_data_brief.v1`:

- `indicator_mapping`: maintained Alpha indicators mapped to framework dimensions
- `dimension_data_cards`: latest values, 7/30/365 day changes, trend, and data signal score
- `logic_context`: the current futures daily thesis for the commodity
- `chart_series`: compact timeline data for the frontend
- `report`: model-generated or fallback commodity data brief

The frontend should treat this artifact as the single contract for the commodity data brief dashboard. Analysis logic stays in `quanta_agents`; the platform only reads and visualizes the candidate output.

## Platform Surface

共振 Alpha 平台优先读取 `run/commodity_data_briefs_market_brief/*.json`，旧的 `commodity_data_briefs/*.json` 只作为兜底：

- `GET /api/v1/commodity-data-briefs?date=YYYYMMDD`: list generated commodity briefs for the selected futures daily run
- `GET /api/v1/commodity-data-briefs/{asset}?run_id=...`: read one commodity brief
- Frontend route: `品种数据简报`

For 2026-06-18, `market_brief` mode uses `20260618_commodity_summary.json` from 期市速递 rather than `trade_thesis.json` or `logic_chains.json`.
The validated batch under `RUN-20260618-190408-COMMODITY-LEGACY` generated 55 commodity briefs with real `dzq_data` chart history and skipped 14 assets with no database history.

## CLI

```bash
quanta-futures-daily-data-brief \
  --asset 石油沥青 \
  --date 20260618 \
  --quanta-root /Volumes/数字大脑/quanta_data
```

Use `--no-llm` for structure-only generation during development.
