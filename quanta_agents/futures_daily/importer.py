from __future__ import annotations

import argparse
from pathlib import Path

from .framework_alignment import publish_framework_alignment
from .publisher import publish_report_paths


def _default_neighbor(path: Path, suffix: str) -> Path:
    name = path.name
    if "commodity_summary" in name:
        return path.with_name(name.replace("commodity_summary.json", suffix))
    return path.with_name(suffix)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Import local commodity report files into quanta_data.")
    parser.add_argument("--summary", required=True, help="Path to *_commodity_summary.json")
    parser.add_argument("--market-review", help="Path to *_commodity_marketreview.json")
    parser.add_argument("--html", help="Path to *_commodity_report.html. If omitted, a preview is generated.")
    parser.add_argument("--date", help="Report date, YYYYMMDD. Defaults to summary['date'].")
    parser.add_argument("--quanta-root", help="Override GJ_QUANTA_DATA_ROOT.")
    parser.add_argument("--skip-align", action="store_true", help="Do not generate framework alignment candidates.")
    args = parser.parse_args(argv)

    summary_path = Path(args.summary).expanduser()
    market_review_path = (
        Path(args.market_review).expanduser()
        if args.market_review
        else _default_neighbor(summary_path, "commodity_marketreview.json")
    )
    html_path = Path(args.html).expanduser() if args.html else None

    result = publish_report_paths(
        summary_path=summary_path,
        market_review_path=market_review_path,
        preview_html_path=html_path,
        report_date=args.date,
        root=args.quanta_root,
    )
    print(f"导入完成：{result['date']}")
    print(f"market_review → {result['source_paths']['market_review']}")
    print(f"commodity_summary → {result['source_paths']['commodity_summary']}")
    print(f"review_package → {result['source_paths']['review_package']}")
    if not args.skip_align:
        paths = publish_framework_alignment(summary_path, args.quanta_root)
        for role, path in paths.items():
            print(f"{role} → {path}")


if __name__ == "__main__":
    main()
