from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any

from quanta_agents.core.taxonomy import load_asset_taxonomy


def _items(values: Any, limit: int = 5) -> str:
    if not isinstance(values, list):
        return "<span class=\"empty\">N/A</span>"
    rows = [str(v).strip() for v in values if str(v).strip()][:limit]
    if not rows:
        return "<span class=\"empty\">N/A</span>"
    return "<ul>" + "".join(f"<li>{escape(row)}</li>" for row in rows) + "</ul>"


def _paragraphs(markdown_like: str) -> str:
    text = str(markdown_like or "").replace("**", "")
    blocks = [line.strip() for line in text.splitlines() if line.strip()]
    if not blocks:
        return "<p>暂无数据</p>"
    return "\n".join(f"<p>{escape(line)}</p>" for line in blocks)


def _categorized(
    summary: dict[str, Any], root: str | Path | None = None
) -> dict[str, list[tuple[str, dict[str, Any]]]]:
    analysis = summary.get("detailed_analysis") or {}
    sentiment_scores = summary.get("sentiment_scores") or {}
    taxonomy = load_asset_taxonomy(root)
    categories = sorted(
        {str(asset.get("category")) for asset in taxonomy.assets if asset.get("category")}
    )
    result: dict[str, list[tuple[str, dict[str, Any]]]] = {name: [] for name in categories}
    result.setdefault("其他", [])
    for name, payload in analysis.items():
        if name == "_metadata" or not isinstance(payload, dict):
            continue
        score = payload.get("sentiment_score", sentiment_scores.get(name, 0))
        try:
            payload["_display_score"] = float(score)
        except (TypeError, ValueError):
            payload["_display_score"] = 0.0
        result.setdefault(taxonomy.category_for(name), []).append((name, payload))
    for rows in result.values():
        rows.sort(key=lambda item: item[1].get("_display_score", 0.0), reverse=True)
    return result


def render_html_report(
    summary: dict[str, Any],
    market_review: dict[str, Any],
    report_date: str,
    root: str | Path | None = None,
) -> str:
    categorized = _categorized(summary, root)
    rows: list[str] = []
    for category, items in categorized.items():
        if not items:
            continue
        for idx, (name, data) in enumerate(items):
            score = float(data.get("_display_score", 0.0))
            tone = "bullish" if score > 1 else "bearish" if score < -1 else "neutral"
            category_cell = (
                f"<td class=\"category\" rowspan=\"{len(items)}\">{escape(category)}</td>" if idx == 0 else ""
            )
            rows.append(
                "<tr>"
                f"{category_cell}"
                f"<td>{escape(name)}</td>"
                f"<td class=\"score {tone}\">{score:.2f}</td>"
                f"<td>{_items(data.get('bullish_factors'), 4)}</td>"
                f"<td>{_items(data.get('bearish_factors'), 4)}</td>"
                f"<td>{_items(data.get('key_data'), 4)}</td>"
                f"<td>{_items(data.get('key_events'), 4)}</td>"
                "</tr>"
            )

    table_rows = "\n".join(rows) or "<tr><td colspan=\"7\">暂无品种分析</td></tr>"
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ModelQuanta 期市速递 - {escape(report_date)}</title>
  <style>
    body {{ margin: 0; padding: 24px; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif; color: #172033; background: #f7f9fc; }}
    main {{ max-width: 1320px; margin: 0 auto; }}
    h1 {{ margin: 0 0 8px; font-size: 28px; }}
    h2 {{ margin: 0 0 12px; font-size: 20px; }}
    .meta {{ color: #667085; margin-bottom: 24px; }}
    .grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px; margin-bottom: 22px; }}
    section {{ background: #fff; border: 1px solid #e4e8f0; border-radius: 8px; padding: 18px; box-shadow: 0 10px 24px rgba(15, 23, 42, 0.05); }}
    p {{ margin: 0 0 10px; line-height: 1.75; }}
    table {{ width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #e4e8f0; border-radius: 8px; overflow: hidden; }}
    th, td {{ border-bottom: 1px solid #e4e8f0; padding: 10px; text-align: left; vertical-align: top; font-size: 13px; line-height: 1.55; }}
    th {{ background: #eef3f8; color: #344054; white-space: nowrap; }}
    ul {{ margin: 0; padding-left: 18px; }}
    .category {{ background: #f3f7fb; font-weight: 700; text-align: center; color: #344054; }}
    .score {{ font-weight: 700; white-space: nowrap; }}
    .bullish {{ color: #c2410c; }}
    .bearish {{ color: #2563eb; }}
    .neutral {{ color: #7c6f1d; }}
    .empty {{ color: #98a2b3; }}
    @media (max-width: 900px) {{ body {{ padding: 14px; }} .grid {{ grid-template-columns: 1fr; }} table {{ display: block; overflow-x: auto; }} }}
  </style>
</head>
<body>
<main>
  <h1>ModelQuanta 期市速递</h1>
  <div class="meta">报告日期：{escape(report_date)} · 状态：candidate / pending review</div>
  <div class="grid">
    <section>
      <h2>重要市场事件</h2>
      {_paragraphs(str(market_review.get("market_events_summary", "")))}
    </section>
    <section>
      <h2>市场核心逻辑</h2>
      {_paragraphs(str(market_review.get("market_logic_summary", "")))}
    </section>
  </div>
  <table>
    <thead>
      <tr>
        <th>板块</th>
        <th>品种</th>
        <th>多空得分</th>
        <th>利多因素</th>
        <th>利空因素</th>
        <th>重点数据</th>
        <th>重要事件</th>
      </tr>
    </thead>
    <tbody>
      {table_rows}
    </tbody>
  </table>
</main>
</body>
</html>
"""
