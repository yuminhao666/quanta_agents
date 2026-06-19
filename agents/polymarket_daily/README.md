# Polymarket Daily Agent

调用本机 `polymarket` CLI 采集公开市场快照，生成热点排序、中文日报和 review package。
日报默认聚焦金融、政治、宏观和地缘相关市场，避免体育、天气、娱乐和临近结算小市场占据版面。
Markdown 日报优先展示中文市场标题；结构化 JSON 保留英文原文 `question` 和中文译文 `question_zh`。

## 运行

```bash
quanta-polymarket-daily --no-llm
```

常用参数：

```bash
quanta-polymarket-daily \
  --cli /Users/miniquanta/.cargo/bin/polymarket \
  --limit 100 \
  --top 30 \
  --focus finance,politics,macro \
  --quanta-root /Volumes/数字大脑/quanta_data
```

`--focus all` 可关闭过滤；`--focus politics,geopolitics` 可只看政治和地缘。

## 数据契约

- raw：`raw_objects/market_data/polymarket_cli/{yyyy}/{mm}/{dd}/{RUN-ID}/snapshot.json`
- manifest：`raw_manifests/by_source/polymarket_cli/{RUN-ID}.json`
- candidate：`agent_workspace/candidates/polymarket_daily/{yyyy}/{mm}/{dd}/{CAND-ID}/`
- latest：`agent_workspace/candidates/polymarket_daily/latest/`
- review：`agent_workspace/review_packages/{yyyy}/{mm}/{dd}/RP-{date}-POLYMARKET/`
- index：`indexes/polymarket/market_hotspots.jsonl`

## 口径

热点分数只用于分拣，综合 24h 成交、7日成交、流动性、1日/1小时概率变化和新市场因子。
raw snapshot 保留全量 CLI 样本，candidate 日报只对 focus 后的市场排序。日报不自动引入外部新闻证据，
因此不能把价格变化解释为真实事件原因；低流动性、宽价差和新市场都需要人工复核。
