# Opinion Radar Agent

生成商品/宏观新闻舆情雷达候选快照，供 `gj_chainplatform` 的“舆情雷达”页读取。

当前实现位于 `quanta_agents.opinion_radar`：

- 读取 `jin10_flash` 快讯表。
- 从 `gold/reference_data/assets/futures_assets.v1.json` 读取品种、别名、板块和宏观主题桶。
- 过滤纯行情涨跌/盘口噪音。
- 可选调用 LLM 命名主题并缓存。
- 将快讯映射到品种 framework 维度，并与最近研报主线对比。
- 生成市场舆情雷达报告，把支持、冲突、新信号转成投资逻辑修正动作。
- 导出到 `agent_workspace/candidates/opinion_radar/latest/radar.json` 和按日归档。

运行：

```bash
quanta-opinion-radar-export --no-llm
```

单独重算报告：

```bash
quanta-opinion-radar-report
```

报告输出：

```text
agent_workspace/candidates/opinion_radar/reports/latest/market-radar-report.json
agent_workspace/candidates/opinion_radar/reports/latest/market-radar-report.md
```

去掉 `--no-llm` 后会按环境变量调用模型。默认跟随 `QUANTA_AGENT_LLM_PROVIDER`；
如需单独切 MiniMax M3：

```bash
RADAR_LLM_PROVIDER=m3
MINIMAX_API_KEY=sk-xxx
```

该产物状态为 `candidate`，不直接进入 `gold`。
