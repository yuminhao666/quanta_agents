# Crawlers

抓取网页、新闻、研报、公告和数据源。

写入目标：

```text
raw_objects
raw_manifests
```

Crawler 不直接生成研究观点，也不把原始资料直接传给模型做结论。

## 智汇期讯网会议纪要

目标页：`https://hzzhqx.com/meeting/list`

这个站点的会议接口需要登录态。列表页本身是 SPA 壳，实际数据来自：

- `GET /api/meeting/listPage`
- `GET /api/meeting/info/{meetingId}`

运行方式：

```bash
HZZHQX_AUTHORIZATION='从浏览器开发者工具复制的 authorization 头' \
python hzzhqx_meetings.py --max-pages 1
```

也可以用导出的 Cookie：

```bash
HZZHQX_COOKIE='name=value; other=value' \
python hzzhqx_meetings.py --max-pages 1
```

默认会抓列表和详情。只抓列表可加 `--no-details`。输出目录：

```text
raw_objects/hzzhqx_meetings/<run_id>/
raw_manifests/hzzhqx_meetings_<run_id>.json
```

常用过滤：

```bash
python hzzhqx_meetings.py \
  --start-date 2026-06-01 \
  --end-date 2026-06-18 \
  --institution-ids 1,2,3 \
  --variety cu
```

## 智汇期讯网微信公众号研报

数据源：MySQL `quant_data.hzzhqx_reports` 中 `link_type='WECHAT'` 的
`mp.weixin.qq.com` 原文链接。

写入目标：

```text
/Volumes/数字大脑/quanta_data/raw_objects/web_pages/hzzhqx_wechat/{institution}/{yyyy}/{mm}/{dd}/{RAW-ID}/
├── original.html
├── content_fragment.html
├── extracted_text.txt
├── assets/
│   └── image_index.json
├── fetch_metadata.json
└── raw_manifest.json

/Volumes/数字大脑/quanta_data/raw_manifests/by_source/hzzhqx_wechat/
/Volumes/数字大脑/quanta_data/raw_manifests/by_date/
/Volumes/数字大脑/quanta_data/indexes/raw/hzzhqx_wechat_articles.jsonl
```

最近两个月增量抓取：

```bash
/Users/miniquanta/Documents/quanta_pro/backend/.venv/bin/python \
  hzzhqx_wechat_raw_ingest.py \
  --delay 8 \
  --jitter 4 \
  --max-consecutive-blocks 3 \
  --verify-cooldown 1800
```

脚本会跳过已写入 index 的文章；如需重试失败项，添加 `--retry-failed`。
为避免微信反爬，默认不批量下载图片文件，只在 `assets/image_index.json` 记录图片 URL。
