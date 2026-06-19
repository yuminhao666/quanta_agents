#!/usr/bin/env python3
"""Crawler for hzzhqx.com meeting minutes.

The meeting APIs require an authenticated hzzhqx.com session. Pass either the
`authorization` header value or an exported Cookie string via environment
variables or CLI options.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


BASE_URL = "https://hzzhqx.com"
LIST_PATH = "/api/meeting/listPage"
INFO_PATH_TEMPLATE = "/api/meeting/info/{meeting_id}"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)


class ApiError(RuntimeError):
    """Raised when the remote API returns an application-level error."""


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def clean_secret(value: str | None) -> str:
    """Accept raw env values or JSON-quoted localStorage-style values."""

    if not value:
        return ""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        try:
            decoded = json.loads(value)
            if isinstance(decoded, str):
                return decoded.strip()
        except json.JSONDecodeError:
            pass
    return value


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def write_json(path: Path, payload: Any) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def build_query(params: dict[str, Any]) -> str:
    cleaned: dict[str, str] = {}
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, (list, tuple, set)):
            value = ",".join(str(item) for item in value if item is not None and str(item) != "")
        value = str(value)
        if value == "":
            continue
        cleaned[key] = value
    return urllib.parse.urlencode(cleaned)


class HzzhqxClient:
    def __init__(
        self,
        *,
        authorization: str = "",
        cookie: str = "",
        timeout: float = 30.0,
        delay: float = 0.4,
    ) -> None:
        self.authorization = clean_secret(authorization)
        self.cookie = clean_secret(cookie)
        self.timeout = timeout
        self.delay = delay
        if not self.authorization and not self.cookie:
            raise ApiError(
                "missing credentials: set HZZHQX_AUTHORIZATION or HZZHQX_COOKIE "
                "(or pass --authorization/--cookie)"
            )

    def headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Referer": f"{BASE_URL}/meeting/list",
            "Referrer-Policy": "strict-origin-when-cross-origin",
            "User-Agent": USER_AGENT,
            "zh-platform": "WEB",
        }
        if self.authorization:
            headers["authorization"] = self.authorization
        if self.cookie:
            headers["Cookie"] = self.cookie
        return headers

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = BASE_URL + path
        if params:
            query = build_query(params)
            if query:
                url = f"{url}?{query}"
        request = urllib.request.Request(url, headers=self.headers(), method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise ApiError(f"HTTP {exc.code} from {url}: {body[:500]}") from exc
        except urllib.error.URLError as exc:
            raise ApiError(f"request failed for {url}: {exc}") from exc

        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ApiError(f"non-JSON response from {url}: {body[:500]}") from exc

        if payload.get("success") is False:
            code = payload.get("errCode", "")
            desc = payload.get("errDesc", payload)
            raise ApiError(f"API error {code}: {desc}")
        return payload

    def list_page(self, *, page: int, limit: int, filters: dict[str, Any]) -> dict[str, Any]:
        params = {"page": page, "limit": limit, **filters}
        payload = self.get_json(LIST_PATH, params)
        result = payload.get("result", payload)
        if result == "refused":
            raise ApiError("API returned refused; this account may not have meeting access")
        if not isinstance(result, dict):
            raise ApiError(f"unexpected list response shape: {result!r}")
        return result

    def meeting_info(self, meeting_id: str | int) -> dict[str, Any]:
        path = INFO_PATH_TEMPLATE.format(meeting_id=urllib.parse.quote(str(meeting_id)))
        payload = self.get_json(path)
        result = payload.get("result", payload)
        if result == "refused":
            raise ApiError("API returned refused; this account may not have meeting detail access")
        if not isinstance(result, dict):
            raise ApiError(f"unexpected info response shape for {meeting_id}: {result!r}")
        return result

    def nap(self) -> None:
        if self.delay > 0:
            time.sleep(self.delay)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crawl hzzhqx.com meeting minutes.")
    parser.add_argument("--authorization", default=os.getenv("HZZHQX_AUTHORIZATION", ""))
    parser.add_argument("--cookie", default=os.getenv("HZZHQX_COOKIE", ""))
    parser.add_argument("--page-size", type=int, default=50)
    parser.add_argument(
        "--max-pages",
        type=int,
        default=0,
        help="0 means crawl until total is exhausted",
    )
    parser.add_argument("--start-date", default="", help="YYYY-MM-DD")
    parser.add_argument("--end-date", default="", help="YYYY-MM-DD")
    parser.add_argument("--variety", default="", help="Variety code/name accepted by the site API")
    parser.add_argument(
        "--institution-ids",
        default="",
        help="Comma-separated institution ids, same value used by the website filter",
    )
    parser.add_argument("--no-details", action="store_true", help="Only crawl list pages")
    parser.add_argument(
        "--delay",
        type=float,
        default=0.4,
        help="Delay between requests in seconds",
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--output-root", default=".")
    return parser.parse_args()


def filters_from_args(args: argparse.Namespace) -> dict[str, Any]:
    filters: dict[str, Any] = {}
    if args.institution_ids:
        filters["institutionIds"] = args.institution_ids
    if args.start_date:
        filters["startDate"] = args.start_date
    if args.end_date:
        filters["endDate"] = args.end_date
    if args.variety:
        filters["variety"] = args.variety
    return filters


def normalize_records(
    records: list[dict[str, Any]],
    details_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    normalized = []
    for record in records:
        meeting_id = str(record.get("meetingId", ""))
        detail = details_by_id.get(meeting_id, {})
        normalized.append(
            {
                "meeting_id": meeting_id,
                "title": record.get("title") or detail.get("title"),
                "meeting_date": record.get("meetingDate") or detail.get("meetingDate"),
                "institution_name": record.get("institutionName") or detail.get("institutionName"),
                "speaker_names": record.get("speakerNames"),
                "duration": record.get("duration"),
                "variety": record.get("variety", []),
                "paragraph_summary": detail.get("paragraphSummary"),
                "keywords": detail.get("keyword", []),
                "source_url": f"{BASE_URL}/meeting/info?id={meeting_id}" if meeting_id else "",
                "has_detail": bool(detail),
            }
        )
    return normalized


def main() -> int:
    args = parse_args()
    run_id = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    output_root = Path(args.output_root)
    object_root = output_root / "raw_objects" / "hzzhqx_meetings" / run_id
    manifest_path = output_root / "raw_manifests" / f"hzzhqx_meetings_{run_id}.json"

    client = HzzhqxClient(
        authorization=args.authorization,
        cookie=args.cookie,
        timeout=args.timeout,
        delay=args.delay,
    )
    filters = filters_from_args(args)

    all_records: list[dict[str, Any]] = []
    detail_ids_seen: set[str] = set()
    details_by_id: dict[str, dict[str, Any]] = {}
    total: int | None = None
    page = 1

    while True:
        result = client.list_page(page=page, limit=args.page_size, filters=filters)
        write_json(object_root / "list_pages" / f"page_{page:04d}.json", result)

        records = result.get("records") or []
        if not isinstance(records, list):
            raise ApiError(f"unexpected records shape on page {page}: {records!r}")
        all_records.extend(records)
        total_value = result.get("total")
        total = int(total_value) if total_value is not None else total

        print(f"fetched list page {page}: {len(records)} records", file=sys.stderr)

        if not args.no_details:
            for record in records:
                meeting_id = record.get("meetingId")
                if not meeting_id:
                    continue
                meeting_id_str = str(meeting_id)
                if meeting_id_str in detail_ids_seen:
                    continue
                detail_ids_seen.add(meeting_id_str)
                client.nap()
                detail = client.meeting_info(meeting_id)
                details_by_id[meeting_id_str] = detail
                write_json(object_root / "details" / f"{meeting_id_str}.json", detail)
                print(f"fetched detail {meeting_id_str}", file=sys.stderr)

        if not records:
            break
        if args.max_pages and page >= args.max_pages:
            break
        if total is not None and page * args.page_size >= total:
            break
        page += 1
        client.nap()

    normalized = normalize_records(all_records, details_by_id)
    write_json(object_root / "records.json", normalized)

    manifest = {
        "source": "hzzhqx_meetings",
        "source_url": f"{BASE_URL}/meeting/list",
        "run_id": run_id,
        "crawled_at": utc_now(),
        "filters": filters,
        "page_size": args.page_size,
        "pages_fetched": page,
        "records_fetched": len(all_records),
        "details_fetched": len(details_by_id),
        "reported_total": total,
        "raw_object_dir": str(object_root),
        "normalized_records": str(object_root / "records.json"),
    }
    write_json(manifest_path, manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ApiError as exc:
        print(f"hzzhqx crawler error: {exc}", file=sys.stderr)
        raise SystemExit(1)
