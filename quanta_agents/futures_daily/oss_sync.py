from __future__ import annotations

import argparse
import re
from datetime import datetime
from typing import Any

from quanta_agents.core.config import first_env, quanta_data_root
from quanta_agents.core.io import sha256_bytes

from .publisher import publish_report_files


OSS_BUCKET = "nblab"
OSS_BASE_PREFIX = "quanta/output/commodity/"
FILE_RE = re.compile(r"(\d{8})_commodity_(marketreview|summary|report)\.(json|html)$")


def _require_oss2():
    try:
        import oss2  # type: ignore
    except ImportError as exc:  # pragma: no cover - environment diagnostic
        raise RuntimeError('缺少 oss2 依赖，请安装：python3 -m pip install -e ".[oss]"') from exc
    return oss2


def _make_bucket() -> Any:
    oss2 = _require_oss2()
    access_key = first_env("OSS_ACCESS_KEY_ID", "ALIYUN_OSS_ACCESS_KEY_ID")
    secret = first_env("OSS_ACCESS_KEY_SECRET", "ALIYUN_OSS_ACCESS_KEY_SECRET")
    endpoint = first_env("OSS_ENDPOINT", "ALIYUN_OSS_ENDPOINT", default="https://oss-cn-shanghai.aliyuncs.com")
    bucket_name = first_env("OSS_BUCKET", default=OSS_BUCKET)
    if not access_key or not secret:
        raise RuntimeError("缺少 OSS_ACCESS_KEY_ID / OSS_ACCESS_KEY_SECRET。")
    auth = oss2.Auth(access_key, secret)
    return oss2.Bucket(auth, endpoint, bucket_name, connect_timeout=15)


def discover(bucket: Any, month_prefix: str) -> dict[str, dict[str, str]]:
    oss2 = _require_oss2()
    days: dict[str, dict[str, str]] = {}
    for obj in oss2.ObjectIterator(bucket, prefix=month_prefix):
        match = FILE_RE.search(obj.key)
        if not match:
            continue
        date, kind, _ = match.groups()
        days.setdefault(date, {})[kind] = obj.key
    return days


def _discover_months(bucket: Any) -> list[str]:
    oss2 = _require_oss2()
    months: set[str] = set()
    for obj in oss2.ObjectIterator(bucket, prefix=OSS_BASE_PREFIX):
        match = re.search(r"/(\d{6})/", obj.key)
        if match:
            months.add(match.group(1))
    return sorted(months)


def sync_day(bucket: Any, date: str, keys: dict[str, str], *, root: str | None = None) -> dict[str, Any] | None:
    if "summary" not in keys:
        return None

    summary_bytes = bucket.get_object(keys["summary"]).read()
    market_review_bytes = (
        bucket.get_object(keys["marketreview"]).read()
        if "marketreview" in keys
        else b'{"market_events_summary":"","market_logic_summary":"","timestamp":""}'
    )
    preview_html_bytes = bucket.get_object(keys["report"]).read() if "report" in keys else None
    input_refs = [
        {"role": "commodity_summary", "oss_key": keys["summary"], "sha256": sha256_bytes(summary_bytes)},
    ]
    if "marketreview" in keys:
        input_refs.append({"role": "market_review", "oss_key": keys["marketreview"], "sha256": sha256_bytes(market_review_bytes)})
    if preview_html_bytes is not None:
        input_refs.append({"role": "preview_html", "oss_key": keys["report"], "sha256": sha256_bytes(preview_html_bytes)})

    return publish_report_files(
        summary_bytes=summary_bytes,
        market_review_bytes=market_review_bytes,
        preview_html_bytes=preview_html_bytes,
        report_date=date,
        root=root,
        input_refs=input_refs,
        generator={"source": "oss", "oss_bucket": first_env("OSS_BUCKET", default=OSS_BUCKET)},
        synced_by="quanta_agents.futures_daily.oss_sync",
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Sync commodity report triplets from OSS into quanta_data.")
    parser.add_argument("--month", help="Month to sync, YYYYMM. Defaults to current month.")
    parser.add_argument("--all", action="store_true", help="Discover and sync all months under the OSS prefix.")
    parser.add_argument("--quanta-root", help="Override GJ_QUANTA_DATA_ROOT.")
    args = parser.parse_args(argv)

    bucket = _make_bucket()
    root = str(quanta_data_root(args.quanta_root))
    months = _discover_months(bucket) if args.all else [args.month or datetime.now().strftime("%Y%m")]
    total = 0
    for month in months:
        days = discover(bucket, f"{OSS_BASE_PREFIX}{month}/")
        synced = skipped = 0
        for date in sorted(days):
            result = sync_day(bucket, date, days[date], root=root)
            if result:
                synced += 1
                print(f"  {date} → {result['source_paths']['commodity_summary']}")
            else:
                skipped += 1
                print(f"  {date} 跳过：无 summary")
        total += synced
        print(f"[{month}] 同步 {synced} 天，跳过 {skipped} 天")
    print(f"完成：共 {total} 个交易日报告入库 → {root}/agent_workspace/candidates/")


if __name__ == "__main__":
    main()
