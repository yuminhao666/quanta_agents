# Opinion Radar Agent

生成商品/宏观新闻舆情雷达候选快照，供 `gj_chainplatform` 的“舆情雷达”页读取。

当前实现位于 `quanta_agents.opinion_radar`：

- 读取 `jin10_flash` 快讯表。
- 从 `gold/reference_data/assets/futures_assets.v1.json` 读取品种、别名、板块和宏观主题桶。
- 过滤纯行情涨跌/盘口噪音，并降低汇总新闻权重。
- 对短 ASCII 别名做边界匹配，对 `苹果`、`玻璃`、`铅`、`锡` 等易混别名做语境保护。
- 聚类时具体品种优先于板块/品类，避免同一条新闻同时膨胀成多个泛化主题。
- 可选调用 LLM 命名主题并缓存。
- 将快讯映射到品种 framework 维度，并与最近研报主线对比。
- 生成市场舆情雷达报告，把支持、冲突、新信号转成投资逻辑修正动作；报告的 `updated_logic` 必须来自最新新闻证据，不能复述过期研报主线。
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
agent_workspace/candidates/opinion_radar/latest/radar.json
agent_workspace/candidates/opinion_radar/news_logic/latest/news-logic.json
agent_workspace/candidates/opinion_radar/reports/latest/market-radar-report.json
agent_workspace/candidates/opinion_radar/reports/latest/market-radar-report.md
agent_workspace/candidates/opinion_radar/reports/{yyyy}/{mm}/{dd}/market-radar-report-{HHMMSS}.{json,md}
```

## 质量复核

每次改聚类、taxonomy 或报告逻辑后，至少复核：

- top themes 是否被 `苹果公司`、`玻璃基板`、`铅笔`、`无锡` 等误映射污染。
- 具体品种新闻是否重复推高板块/品类桶。
- 汇总类新闻是否只是背景样本，而不是主题代表证据。
- FOMC、央行、通胀、就业等新闻是否进入宏观桶，并用最新新闻时间线更新投资逻辑。
- `market-radar-report.md` 中黄金、原油、铜等主线是否写成“新闻证据 -> 支持/冲突 -> 修正动作”，而不是复读旧研报。
- theme anchor 目前仍是 candidate；如果卡片标题变成长篇研报主线标题，应回落到自由主题或品种桶名。

当前完整结构和产物索引见 `docs/project-structure-and-outputs.md`。

去掉 `--no-llm` 后会按环境变量调用模型。默认跟随 `QUANTA_AGENT_LLM_PROVIDER`；
如需单独切 MiniMax M3：

```bash
RADAR_LLM_PROVIDER=m3
MINIMAX_API_KEY=sk-xxx
```

该产物状态为 `candidate`，不直接进入 `gold`。
