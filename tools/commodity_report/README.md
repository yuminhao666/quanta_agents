# Commodity Report Daily Pipeline

This directory contains the `tmp_code/commodity_report` compatible futures daily pipeline.
It is intentionally kept separate from the agent framework modules; it only reuses shared
utilities for config, LLM calls, taxonomy, and publishing.

## Main Files

- `hzzhqx_wechat_daily_update.py` builds the daily raw folder from hzzhqx WeChat raw objects,
  runs the report pipeline, and publishes the result into `quanta_data`.
- `commodity_report_legacy.py` implements the legacy commodity report flow:
  single-report analysis, per-commodity weighted merge, market review generation, and cache reuse.

## Run

```bash
PYTHONUNBUFFERED=1 .venv/bin/python tools/commodity_report/hzzhqx_wechat_daily_update.py --date 20260618 --max-workers 2
```

The default engine is `commodity_report`, which follows the legacy `tmp_code/commodity_report`
logic. Use `--engine raw` only for comparison with the newer raw extraction path.

## Single-Report Cache

Single-report extraction results are cached under:

```text
/Volumes/数字大脑/quanta_data/agent_workspace/candidates/futures_daily_single_report_analysis/YYYY/MM/DD/COMMODITY-LEGACY
```

On rerun, a report is skipped when `row_id`, raw text hash, detected assets, and analysis status
match the cached JSON.
