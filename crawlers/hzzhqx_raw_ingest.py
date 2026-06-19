#!/usr/bin/env python3
"""Ingest hzzhqx meeting minutes into quanta_data raw Research Memory.

This crawler only writes immutable raw artifacts and manifests. It does not run
LLM cleaning, canonicalization, evidence extraction, or gold publishing.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import random
import re
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
SOURCE_NAME = "hzzhqx"
SOURCE_DISPLAY_NAME = "智汇期讯网"
SOURCE_TYPE = "web_meeting_minutes"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
)


def _detect_default_quanta_root() -> Path:
    configured = os.getenv("GJ_QUANTA_DATA_ROOT") or os.getenv("QUANTA_DATA_ROOT")
    if configured and configured.strip():
        return Path(configured).expanduser()

    legacy_roots = (
        Path.home() / "quanta_data",
        Path.home() / "Documents" / "quanta_data",
        Path.home() / "document" / "quanta_data",
    )
    for candidate in legacy_roots:
        if candidate.exists():
            return candidate.expanduser()
    fallback_root = Path("/Volumes/数字大脑/quanta_data")
    if fallback_root.exists():
        return fallback_root
    return Path.home() / "quanta_data"


DEFAULT_QUANTA_ROOT = _detect_default_quanta_root()


class CrawlerError(RuntimeError):
    pass


class ApiResponseError(CrawlerError):
    def __init__(
        self,
        code: str,
        description: Any,
        payload: dict[str, Any],
        fetch_metadata: dict[str, Any],
    ) -> None:
        super().__init__(f"API error {code}: {description}")
        self.code = code
        self.description = description
        self.payload = payload
        self.fetch_metadata = fetch_metadata


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_date_parts(value: str | None) -> tuple[str, str, str]:
    if value:
        match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", value)
        if match:
            return match.group(1), match.group(2), match.group(3)
    now = dt.datetime.now()
    return f"{now.year:04d}", f"{now.month:02d}", f"{now.day:02d}"


def safe_path_part(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", text)
    text = text.strip("._")
    return (text or fallback)[:80]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(data: Any) -> bytes:
    text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    return text.encode("utf-8")


def write_bytes(path: Path, data: bytes, *, force: bool = False) -> None:
    if path.exists() and not force:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def write_text(path: Path, text: str, *, force: bool = False) -> None:
    write_bytes(path, text.encode("utf-8"), force=force)


def write_json(path: Path, data: Any, *, force: bool = False) -> None:
    write_bytes(path, json_bytes(data), force=force)


def clean_secret(value: str | None) -> str:
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


def build_query(params: dict[str, Any]) -> str:
    cleaned: dict[str, str] = {}
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, (list, tuple, set)):
            value = ",".join(str(item) for item in value if item is not None and str(item) != "")
        value = str(value)
        if value:
            cleaned[key] = value
    return urllib.parse.urlencode(cleaned)


def request_url(path: str, params: dict[str, Any] | None = None) -> str:
    url = BASE_URL + path
    query = build_query(params or {})
    return f"{url}?{query}" if query else url


def sanitize_headers(headers: dict[str, str]) -> dict[str, str]:
    redacted = {}
    for key, value in headers.items():
        if key.lower() in {"authorization", "cookie"}:
            redacted[key] = "[REDACTED]"
        else:
            redacted[key] = value
    return redacted


class HzzhqxClient:
    def __init__(
        self,
        *,
        authorization: str,
        cookie: str,
        delay: float,
        jitter: float,
        timeout: float,
        max_retries: int,
    ) -> None:
        self.authorization = clean_secret(authorization)
        self.cookie = clean_secret(cookie)
        self.delay = delay
        self.jitter = jitter
        self.timeout = timeout
        self.max_retries = max_retries
        if not self.authorization and not self.cookie:
            raise CrawlerError("missing HZZHQX_AUTHORIZATION or HZZHQX_COOKIE")

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

    def nap(self, multiplier: float = 1.0) -> None:
        seconds = max(0.0, self.delay * multiplier + random.uniform(0.0, self.jitter))
        if seconds:
            time.sleep(seconds)

    def get_json(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        url = request_url(path, params)
        headers = self.headers()
        started_at = utc_now()
        last_error = ""

        for attempt in range(1, self.max_retries + 2):
            req = urllib.request.Request(url, headers=headers, method="GET")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as response:
                    body = response.read()
                    decoded = body.decode("utf-8")
                    payload = json.loads(decoded)
                    metadata = {
                        "request_url": url,
                        "request_method": "GET",
                        "request_headers": sanitize_headers(headers),
                        "started_at": started_at,
                        "completed_at": utc_now(),
                        "http_status": response.status,
                        "response_content_type": response.headers.get("content-type"),
                        "response_bytes": len(body),
                        "response_sha256": sha256_bytes(body),
                        "attempts": attempt,
                    }
                    if payload.get("success") is False:
                        code = payload.get("errCode", "")
                        desc = payload.get("errDesc", payload)
                        raise ApiResponseError(str(code), desc, payload, metadata)
                    return payload, metadata
            except CrawlerError:
                raise
            except (TimeoutError, urllib.error.URLError, json.JSONDecodeError) as exc:
                last_error = str(exc)
                if attempt > self.max_retries:
                    break
                self.nap(multiplier=attempt * 2)

        raise CrawlerError(f"request failed after retries: {url}; {last_error}")

    def list_page(
        self,
        page: int,
        limit: int,
        filters: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        payload, metadata = self.get_json(LIST_PATH, {"page": page, "limit": limit, **filters})
        result = payload.get("result")
        if result == "refused":
            raise CrawlerError("list API returned refused")
        if not isinstance(result, dict):
            raise CrawlerError(f"unexpected list result shape: {type(result).__name__}")
        return result, metadata

    def meeting_info(self, meeting_id: str | int) -> tuple[dict[str, Any], dict[str, Any]]:
        path = INFO_PATH_TEMPLATE.format(meeting_id=urllib.parse.quote(str(meeting_id)))
        payload, metadata = self.get_json(path)
        result = payload.get("result")
        if result == "refused":
            raise CrawlerError(f"detail API returned refused for meeting {meeting_id}")
        if not isinstance(result, dict):
            raise CrawlerError(f"unexpected detail result shape: {type(result).__name__}")
        return result, metadata


def source_url(meeting_id: str | int) -> str:
    return f"{BASE_URL}/meeting/info?id={meeting_id}"


def extract_text(detail: dict[str, Any]) -> str:
    lines: list[str] = []
    title = detail.get("title")
    if title:
        lines.append(f"# {title}")
    for key, label in [
        ("institutionName", "机构"),
        ("meetingDate", "会议日期"),
        ("speakerNames", "发言人"),
        ("duration", "时长"),
    ]:
        if detail.get(key):
            lines.append(f"{label}: {detail[key]}")
    keywords = detail.get("keyword") or []
    if keywords:
        lines.append("关键词: " + "、".join(str(item) for item in keywords))
    if detail.get("paragraphSummary"):
        lines.extend(["", "## 全文概要", str(detail["paragraphSummary"])])

    content = detail.get("content") or []
    if content:
        lines.extend(["", "## 纪要原文"])
    for section in content:
        speaker = section.get("speakerName") or "未知发言人"
        post = section.get("post")
        lines.append("")
        lines.append(f"### {speaker}" + (f" ({post})" if post else ""))
        paragraphs = section.get("paragraphs") or []
        if paragraphs:
            for paragraph in paragraphs:
                sentences = paragraph.get("sentences") or []
                text = "".join(str(item.get("sentence") or "") for item in sentences).strip()
                if text:
                    lines.append(text)
        else:
            for sentence in section.get("sentences") or []:
                text = str(sentence.get("sentence") or "").strip()
                if text:
                    lines.append(text)

    summaries = detail.get("conversationalSummary") or []
    if summaries:
        lines.extend(["", "## 发言总结"])
    for item in summaries:
        speaker = item.get("speakerName") or "未知发言人"
        summary = item.get("summary") or ""
        if summary:
            lines.append(f"### {speaker}")
            lines.append(str(summary))

    qas = detail.get("questionAnswerSummary") or []
    if qas:
        lines.extend(["", "## 要点回顾"])
    for index, item in enumerate(qas, 1):
        question = item.get("question") or ""
        answer = item.get("answer") or ""
        if question or answer:
            lines.append(f"{index}. 问: {question}")
            lines.append(f"   答: {answer}")

    strategies = detail.get("strategy") or []
    if strategies:
        lines.extend(["", "## 核心观点提炼"])
    for item in strategies:
        variety = item.get("varietyName") or "未知品种"
        lines.append(f"### {variety}")
        for key, label in [
            ("tradeStrategy", "观点"),
            ("tradeLogic", "交易逻辑"),
            ("relationData", "相关数据"),
            ("riskFactor", "风险因素"),
        ]:
            value = item.get(key)
            if value:
                lines.append(f"{label}: {value}")

    return "\n".join(lines).strip() + "\n"


def raw_object_dir(root: Path, raw_id: str, detail: dict[str, Any]) -> Path:
    yyyy, mm, dd = parse_date_parts(detail.get("meetingDate"))
    account = safe_path_part(detail.get("institutionName"), "unknown_account")
    return root / "raw_objects" / "web_pages" / SOURCE_NAME / account / yyyy / mm / dd / raw_id


def manifest_paths(root: Path, raw_id: str, source_record: dict[str, Any]) -> list[Path]:
    yyyy, mm, dd = parse_date_parts(source_record.get("meetingDate"))
    return [
        root / "raw_manifests" / "by_source" / SOURCE_NAME / f"{raw_id}.json",
        root / "raw_manifests" / "by_date" / yyyy / mm / dd / f"{raw_id}.json",
    ]


def build_manifest(
    *,
    root: Path,
    raw_dir: Path,
    raw_id: str,
    detail: dict[str, Any],
    extracted_text_sha256: str,
    original_sha256: str,
    fetch_metadata: dict[str, Any],
) -> dict[str, Any]:
    meeting_id = str(detail.get("meetingId") or "")
    url = source_url(meeting_id)
    return {
        "schema_version": "raw_manifest.web_meeting_minutes.v1",
        "raw_id": raw_id,
        "source_path": str(raw_dir.relative_to(root)),
        "source_uri": url,
        "source_url": url,
        "canonical_url": url,
        "source_name": SOURCE_NAME,
        "source_display_name": SOURCE_DISPLAY_NAME,
        "source_type": SOURCE_TYPE,
        "source_account": detail.get("institutionName"),
        "title": detail.get("title"),
        "author": detail.get("speakerNames"),
        "published_at": detail.get("meetingDate"),
        "source_time": detail.get("meetingDate"),
        "ingested_at": fetch_metadata.get("completed_at") or utc_now(),
        "crawled_at": fetch_metadata.get("completed_at") or utc_now(),
        "hash": {
            "canonical_url": url,
            "content_sha256": extracted_text_sha256,
            "source_sha256": original_sha256,
            "dedupe_key": sha256_bytes(f"{url}|{extracted_text_sha256}".encode("utf-8")),
        },
        "sha256": original_sha256,
        "fetch_status": "success",
        "processing_status": "raw_saved",
        "permission_status": "authenticated_session",
        "permission_scope": "user_authorized_site_session",
        "lineage": {
            "parent_ids": [],
            "source_adapter": "hzzhqx_meeting_minutes.v1",
            "api": {
                "list_endpoint": LIST_PATH,
                "detail_endpoint": INFO_PATH_TEMPLATE,
                "meeting_id": meeting_id,
            },
            "generated_artifacts": [
                "original.json",
                "extracted_text.txt",
                "fetch_metadata.json",
                "assets/",
            ],
        },
        "artifacts": {
            "original_json": "original.json",
            "extracted_text": "extracted_text.txt",
            "assets_dir": "assets",
            "fetch_metadata": "fetch_metadata.json",
        },
        "summary": {
            "institution_name": detail.get("institutionName"),
            "speaker_names": detail.get("speakerNames"),
            "duration": detail.get("duration"),
            "keywords": detail.get("keyword") or [],
            "variety": detail.get("variety") or [],
        },
    }


def append_index(root: Path, row: dict[str, Any]) -> None:
    index_path = root / "indexes" / "raw" / "hzzhqx_meetings.jsonl"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with index_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def load_processed_meeting_ids(root: Path) -> set[str]:
    index_path = root / "indexes" / "raw" / "hzzhqx_meetings.jsonl"
    processed: set[str] = set()
    if not index_path.exists():
        return processed
    with index_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            meeting_id = item.get("meeting_id")
            if meeting_id:
                processed.add(str(meeting_id))
    return processed


def save_raw_document(
    *,
    root: Path,
    detail: dict[str, Any],
    fetch_metadata: dict[str, Any],
    force: bool,
) -> tuple[str, bool, Path]:
    text = extract_text(detail)
    original = json_bytes(detail)
    text_sha = sha256_bytes(text.encode("utf-8"))
    original_sha = sha256_bytes(original)
    meeting_id = str(detail.get("meetingId") or "unknown")
    raw_id = f"RAW-HZZHQX-MEETING-{meeting_id}-{text_sha[:12]}"
    raw_dir = raw_object_dir(root, raw_id, detail)
    manifest_file = raw_dir / "raw_manifest.json"
    if manifest_file.exists() and not force:
        return raw_id, False, raw_dir

    (raw_dir / "assets").mkdir(parents=True, exist_ok=True)
    write_bytes(raw_dir / "original.json", original, force=force)
    write_text(raw_dir / "extracted_text.txt", text, force=force)
    write_json(raw_dir / "fetch_metadata.json", fetch_metadata, force=force)

    manifest = build_manifest(
        root=root,
        raw_dir=raw_dir,
        raw_id=raw_id,
        detail=detail,
        extracted_text_sha256=text_sha,
        original_sha256=original_sha,
        fetch_metadata=fetch_metadata,
    )
    write_json(manifest_file, manifest, force=force)
    for path in manifest_paths(root, raw_id, detail):
        write_json(path, manifest, force=force)

    append_index(
        root,
        {
            "raw_id": raw_id,
            "meeting_id": meeting_id,
            "title": detail.get("title"),
            "source_url": manifest["source_url"],
            "published_at": detail.get("meetingDate"),
            "source_account": detail.get("institutionName"),
            "content_sha256": text_sha,
            "source_sha256": original_sha,
            "raw_manifest": str(manifest_paths(root, raw_id, detail)[0].relative_to(root)),
            "raw_object_dir": str(raw_dir.relative_to(root)),
            "indexed_at": utc_now(),
        },
    )
    return raw_id, True, raw_dir


def denied_text(record: dict[str, Any], error: ApiResponseError) -> str:
    meeting_id = record.get("meetingId") or ""
    lines = [
        f"# {record.get('title') or ''}",
        f"机构: {record.get('institutionName') or ''}",
        f"会议日期: {record.get('meetingDate') or ''}",
        f"发言人: {record.get('speakerNames') or ''}",
        f"时长: {record.get('duration') or ''}",
        f"source_url: {source_url(meeting_id)}",
        "",
        "## 抓取状态",
        f"详情接口返回: {error.code} {error.description}",
        "当前账号可见列表记录，但没有详情正文权限。",
    ]
    return "\n".join(lines).strip() + "\n"


def build_denied_manifest(
    *,
    root: Path,
    raw_dir: Path,
    raw_id: str,
    record: dict[str, Any],
    error: ApiResponseError,
    extracted_text_sha256: str,
    original_sha256: str,
) -> dict[str, Any]:
    meeting_id = str(record.get("meetingId") or "")
    url = source_url(meeting_id)
    completed_at = error.fetch_metadata.get("completed_at") or utc_now()
    return {
        "schema_version": "raw_manifest.web_meeting_minutes.v1",
        "raw_id": raw_id,
        "source_path": str(raw_dir.relative_to(root)),
        "source_uri": url,
        "source_url": url,
        "canonical_url": url,
        "source_name": SOURCE_NAME,
        "source_display_name": SOURCE_DISPLAY_NAME,
        "source_type": SOURCE_TYPE,
        "source_account": record.get("institutionName"),
        "title": record.get("title"),
        "author": record.get("speakerNames"),
        "published_at": record.get("meetingDate"),
        "source_time": record.get("meetingDate"),
        "ingested_at": completed_at,
        "crawled_at": completed_at,
        "hash": {
            "canonical_url": url,
            "content_sha256": extracted_text_sha256,
            "source_sha256": original_sha256,
            "dedupe_key": sha256_bytes(f"{url}|{extracted_text_sha256}".encode("utf-8")),
        },
        "sha256": original_sha256,
        "fetch_status": "permission_denied",
        "processing_status": "raw_discovery_only",
        "permission_status": "denied",
        "permission_scope": "authenticated_session_insufficient_entitlement",
        "lineage": {
            "parent_ids": [],
            "source_adapter": "hzzhqx_meeting_minutes.v1",
            "api": {
                "list_endpoint": LIST_PATH,
                "detail_endpoint": INFO_PATH_TEMPLATE,
                "meeting_id": meeting_id,
                "detail_error_code": error.code,
            },
            "generated_artifacts": [
                "original.json",
                "extracted_text.txt",
                "fetch_metadata.json",
                "assets/",
            ],
        },
        "artifacts": {
            "original_json": "original.json",
            "extracted_text": "extracted_text.txt",
            "assets_dir": "assets",
            "fetch_metadata": "fetch_metadata.json",
        },
        "summary": {
            "institution_name": record.get("institutionName"),
            "speaker_names": record.get("speakerNames"),
            "duration": record.get("duration"),
            "variety": record.get("variety") or [],
        },
    }


def save_denied_document(
    *,
    root: Path,
    record: dict[str, Any],
    error: ApiResponseError,
    force: bool,
) -> tuple[str, bool, Path]:
    text = denied_text(record, error)
    original_payload = {
        "list_record": record,
        "detail_error": error.payload,
    }
    original = json_bytes(original_payload)
    text_sha = sha256_bytes(text.encode("utf-8"))
    original_sha = sha256_bytes(original)
    meeting_id = str(record.get("meetingId") or "unknown")
    raw_id = f"RAW-HZZHQX-MEETING-{meeting_id}-DENIED-{text_sha[:12]}"
    raw_dir = raw_object_dir(root, raw_id, record)
    manifest_file = raw_dir / "raw_manifest.json"
    if manifest_file.exists() and not force:
        return raw_id, False, raw_dir

    (raw_dir / "assets").mkdir(parents=True, exist_ok=True)
    write_bytes(raw_dir / "original.json", original, force=force)
    write_text(raw_dir / "extracted_text.txt", text, force=force)
    write_json(raw_dir / "fetch_metadata.json", error.fetch_metadata, force=force)

    manifest = build_denied_manifest(
        root=root,
        raw_dir=raw_dir,
        raw_id=raw_id,
        record=record,
        error=error,
        extracted_text_sha256=text_sha,
        original_sha256=original_sha,
    )
    write_json(manifest_file, manifest, force=force)
    for path in manifest_paths(root, raw_id, record):
        write_json(path, manifest, force=force)

    append_index(
        root,
        {
            "raw_id": raw_id,
            "meeting_id": meeting_id,
            "title": record.get("title"),
            "source_url": manifest["source_url"],
            "published_at": record.get("meetingDate"),
            "source_account": record.get("institutionName"),
            "content_sha256": text_sha,
            "source_sha256": original_sha,
            "permission_status": "denied",
            "raw_manifest": str(manifest_paths(root, raw_id, record)[0].relative_to(root)),
            "raw_object_dir": str(raw_dir.relative_to(root)),
            "indexed_at": utc_now(),
        },
    )
    return raw_id, True, raw_dir


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ingest hzzhqx meeting raw objects into quanta_data."
    )
    parser.add_argument("--authorization", default=os.getenv("HZZHQX_AUTHORIZATION", ""))
    parser.add_argument("--cookie", default=os.getenv("HZZHQX_COOKIE", ""))
    parser.add_argument(
        "--quanta-root",
        default=os.getenv("GJ_QUANTA_DATA_ROOT", str(DEFAULT_QUANTA_ROOT)),
    )
    parser.add_argument("--page-size", type=int, default=20)
    parser.add_argument("--max-pages", type=int, default=0)
    parser.add_argument("--limit-docs", type=int, default=0, help="0 means no document limit")
    parser.add_argument("--start-date", default="")
    parser.add_argument("--end-date", default="")
    parser.add_argument("--variety", default="")
    parser.add_argument("--institution-ids", default="")
    parser.add_argument("--delay", type=float, default=1.5)
    parser.add_argument("--jitter", type=float, default=0.5)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument(
        "--refetch-processed",
        action="store_true",
        help="Fetch details even when meeting_id already exists in the raw index.",
    )
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.quanta_root).expanduser()
    if not root.exists():
        raise CrawlerError(f"quanta_data root does not exist: {root}")

    client = HzzhqxClient(
        authorization=args.authorization,
        cookie=args.cookie,
        delay=args.delay,
        jitter=args.jitter,
        timeout=args.timeout,
        max_retries=args.max_retries,
    )
    filters = filters_from_args(args)
    run_id = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    started_at = utc_now()
    run_dir = root / "raw_objects" / "web_pages" / SOURCE_NAME / "_crawl_runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    processed_meeting_ids = set() if args.refetch_processed else load_processed_meeting_ids(root)

    attempted = 0
    accessible = 0
    permission_denied = 0
    skipped_processed = 0
    saved = 0
    skipped = 0
    total: int | None = None
    page = 1
    errors: list[dict[str, Any]] = []

    while True:
        result, list_meta = client.list_page(page, args.page_size, filters)
        write_json(run_dir / "list_pages" / f"page_{page:04d}.json", result, force=True)
        list_meta_path = run_dir / "list_pages" / f"page_{page:04d}.fetch_metadata.json"
        write_json(list_meta_path, list_meta, force=True)
        records = result.get("records") or []
        if not isinstance(records, list):
            raise CrawlerError(f"unexpected records shape on page {page}")
        total_value = result.get("total")
        total = int(total_value) if total_value is not None else total
        print(
            f"[{utc_now()}] page={page} records={len(records)} "
            f"total={total} saved={saved} skipped={skipped} denied={permission_denied}",
            file=sys.stderr,
            flush=True,
        )

        for record in records:
            if args.limit_docs and attempted >= args.limit_docs:
                break
            meeting_id = record.get("meetingId")
            if not meeting_id:
                continue
            meeting_id_str = str(meeting_id)
            if meeting_id_str in processed_meeting_ids:
                skipped_processed += 1
                skipped += 1
                print(
                    f"[{utc_now()}] meeting={meeting_id} status=processed_skipped",
                    file=sys.stderr,
                    flush=True,
                )
                continue
            attempted += 1
            try:
                client.nap()
                detail, detail_meta = client.meeting_info(meeting_id)
                detail_meta["discovered_from"] = {
                    "list_page": page,
                    "list_record": record,
                    "list_fetch_metadata_path": str(list_meta_path.relative_to(root)),
                }
                raw_id, did_save, raw_dir = save_raw_document(
                    root=root,
                    detail=detail,
                    fetch_metadata=detail_meta,
                    force=args.force,
                )
                accessible += 1
                if did_save:
                    saved += 1
                else:
                    skipped += 1
                processed_meeting_ids.add(meeting_id_str)
                print(
                    f"[{utc_now()}] meeting={meeting_id} raw_id={raw_id} "
                    f"status={'saved' if did_save else 'skipped'} path={raw_dir.relative_to(root)}",
                    file=sys.stderr,
                    flush=True,
                )
            except ApiResponseError as exc:
                if exc.code == "30001":
                    raise CrawlerError("login expired while crawling") from exc
                if exc.code == "40006":
                    raw_id, did_save, raw_dir = save_denied_document(
                        root=root,
                        record=record,
                        error=exc,
                        force=args.force,
                    )
                    permission_denied += 1
                    if did_save:
                        saved += 1
                    else:
                        skipped += 1
                    processed_meeting_ids.add(meeting_id_str)
                    print(
                        f"[{utc_now()}] meeting={meeting_id} raw_id={raw_id} "
                        f"status={'denied_saved' if did_save else 'denied_skipped'} "
                        f"path={raw_dir.relative_to(root)}",
                        file=sys.stderr,
                        flush=True,
                    )
                else:
                    errors.append({"meeting_id": meeting_id, "error": str(exc), "at": utc_now()})
                    print(
                        f"[{utc_now()}] meeting={meeting_id} error={exc}",
                        file=sys.stderr,
                        flush=True,
                    )
            except Exception as exc:  # keep crawling other meetings
                errors.append({"meeting_id": meeting_id, "error": str(exc), "at": utc_now()})
                print(
                    f"[{utc_now()}] meeting={meeting_id} error={exc}",
                    file=sys.stderr,
                    flush=True,
                )

        if args.limit_docs and attempted >= args.limit_docs:
            break
        if not records:
            break
        if args.max_pages and page >= args.max_pages:
            break
        if total is not None and page * args.page_size >= total:
            break
        page += 1
        client.nap()

    run_manifest = {
        "schema_version": "raw_crawl_run.hzzhqx_meetings.v1",
        "run_id": run_id,
        "source_name": SOURCE_NAME,
        "source_url": f"{BASE_URL}/meeting/list",
        "started_at": started_at,
        "completed_at": utc_now(),
        "filters": filters,
        "page_size": args.page_size,
        "pages_fetched": page,
        "reported_total": total,
        "details_attempted": attempted,
        "details_accessible": accessible,
        "permission_denied": permission_denied,
        "saved": saved,
        "skipped_existing": skipped,
        "skipped_processed_without_refetch": skipped_processed,
        "errors": errors,
        "run_dir": str(run_dir.relative_to(root)),
        "index": "indexes/raw/hzzhqx_meetings.jsonl",
    }
    write_json(run_dir / "run_manifest.json", run_manifest, force=True)
    write_json(
        root / "raw_manifests" / "by_source" / SOURCE_NAME / "_crawl_runs" / f"{run_id}.json",
        run_manifest,
        force=True,
    )
    print(json.dumps(run_manifest, ensure_ascii=False, indent=2))
    return 0 if not errors else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CrawlerError as exc:
        print(f"hzzhqx raw ingest error: {exc}", file=sys.stderr)
        raise SystemExit(1)
