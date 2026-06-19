#!/usr/bin/env python3
"""Ingest hzzhqx WeChat research articles into quanta_data raw memory.

This crawler writes immutable raw artifacts only:
  fetch -> save raw -> generate manifest -> extract text -> write index

It intentionally does not run LLM cleaning, RAG indexing, evidence extraction,
or gold publishing.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup


SOURCE_NAME = "hzzhqx_wechat"
SOURCE_DISPLAY_NAME = "智汇期讯网"
SOURCE_TYPE = "wechat_research_article"
SOURCE_ADAPTER = "hzzhqx_wechat_article.v1"
SCRIPT_ROOT = Path(__file__).resolve().parent
WORKSPACE_ROOT = SCRIPT_ROOT.parents[2]


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
DEFAULT_INDEX = "hzzhqx_wechat_articles.jsonl"
WECHAT_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 "
    "Mobile/15E148 Safari/604.1 MicroMessenger/8.0.43 "
    "NetType/WIFI Language/zh_CN"
)
COMMON_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://mp.weixin.qq.com/",
}
VERIFY_PATTERNS = (
    "环境异常",
    "访问过于频繁",
    "操作频繁",
    "请在微信客户端打开",
    "继续访问",
    "验证",
)


def _detect_default_backend_path() -> Path:
    configured = os.getenv("GJ_QUANTA_PRO_BACKEND") or os.getenv("QUANTA_PRO_BACKEND")
    if configured and configured.strip():
        return Path(configured).expanduser()

    candidates = (
        WORKSPACE_ROOT / "quanta_pro" / "backend",
        Path.home() / "quanta_pro" / "backend",
        Path.home() / "Documents" / "quanta_pro" / "backend",
        Path.home() / "document" / "quanta_pro" / "backend",
        Path("/Volumes/数字大脑/quanta_pro/backend"),
        Path("/Volumes/数字大脑/quanta_pro"),
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


DEFAULT_BACKEND = _detect_default_backend_path()


class IngestError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReportRow:
    report_summary_id: str
    publish_time: str
    institution: str
    category: str
    title: str
    link_type: str
    link: str
    variety_num: int | None


@dataclass(frozen=True)
class FetchResult:
    url: str
    effective_url: str
    body: bytes
    http_status: int | None
    content_type: str
    stderr: str
    returncode: int
    attempts: int
    started_at: str
    completed_at: str


@dataclass(frozen=True)
class ExtractedArticle:
    title: str
    author: str
    publish_time: str
    content_text: str
    content_html: str
    image_urls: list[str]
    verify_like: bool
    has_content_node: bool


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def local_today() -> dt.date:
    return dt.datetime.now().date()


def subtract_months(value: dt.date, months: int) -> dt.date:
    month = value.month - months
    year = value.year
    while month <= 0:
        month += 12
        year -= 1
    day = min(value.day, days_in_month(year, month))
    return dt.date(year, month, day)


def days_in_month(year: int, month: int) -> int:
    if month == 12:
        next_month = dt.date(year + 1, 1, 1)
    else:
        next_month = dt.date(year, month + 1, 1)
    return (next_month - dt.timedelta(days=1)).day


def parse_date(value: str) -> dt.date:
    return dt.datetime.strptime(value, "%Y-%m-%d").date()


def parse_date_parts(value: str | None) -> tuple[str, str, str]:
    if value:
        match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", str(value))
        if match:
            return match.group(1), match.group(2), match.group(3)
    today = local_today()
    return f"{today.year:04d}", f"{today.month:02d}", f"{today.day:02d}"


def safe_path_part(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", text)
    text = text.strip("._")
    return (text or fallback)[:80]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(data: Any) -> bytes:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True, default=str).encode("utf-8")


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


def canonical_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url.strip())
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    # Keep all WeChat signature parameters, but sort them for stable dedupe keys.
    stable_query = urllib.parse.urlencode(sorted(query), doseq=True)
    return urllib.parse.urlunsplit(("https", parsed.netloc.lower(), parsed.path, stable_query, ""))


def source_url(row: ReportRow) -> str:
    return row.link


def clean_text(text: str) -> str:
    text = re.sub(r"[\u200b\u200c\u200d\ufeff\xa0]+", " ", text)
    lines = []
    for raw in text.splitlines():
        line = re.sub(r"[ \t]+", " ", raw).strip()
        if line:
            lines.append(line)
    return "\n".join(lines).strip()


def soup_from_html(html: bytes) -> BeautifulSoup:
    text = html.decode("utf-8", errors="replace")
    return BeautifulSoup(text, "lxml")


def node_text(soup: BeautifulSoup, selector: str) -> str:
    node = soup.select_one(selector)
    return clean_text(node.get_text("\n", strip=True)) if node else ""


def extract_article(html: bytes, row: ReportRow) -> ExtractedArticle:
    soup = soup_from_html(html)
    title = node_text(soup, "#activity-name") or node_text(soup, "title")
    author = node_text(soup, "#js_name")
    publish_time = node_text(soup, "#publish_time")
    content = soup.select_one("#js_content") if soup else None
    image_urls: list[str] = []
    content_html = ""
    content_text = ""
    if content:
        content_html = str(content)
        content_text = clean_text(content.get_text("\n", strip=True))
        seen: set[str] = set()
        for img in content.select("img"):
            for attr in ("data-src", "data-backsrc", "data-original", "src"):
                value = (img.get(attr) or "").strip()
                if value and value.startswith(("http://", "https://")) and value not in seen:
                    seen.add(value)
                    image_urls.append(value)
                    break

    body_head = clean_text(soup.get_text("\n", strip=True)[:1000]) if soup else ""
    verify_like = (not content) and any(pattern in body_head for pattern in VERIFY_PATTERNS)
    return ExtractedArticle(
        title=title or row.title,
        author=author or row.institution,
        publish_time=publish_time or row.publish_time,
        content_text=content_text,
        content_html=content_html,
        image_urls=image_urls,
        verify_like=verify_like,
        has_content_node=content is not None,
    )


def fetch_status(extracted: ExtractedArticle, result: FetchResult) -> str:
    if result.returncode != 0:
        return "transport_error"
    if result.http_status and result.http_status >= 400:
        return "http_error"
    if extracted.verify_like:
        return "blocked_or_verify"
    if extracted.has_content_node and (extracted.content_text or extracted.image_urls):
        return "success"
    if extracted.has_content_node:
        return "empty_content"
    return "no_content_node"


def build_fetch_metadata(
    row: ReportRow,
    result: FetchResult,
    extracted: ExtractedArticle,
    status: str,
) -> dict[str, Any]:
    return {
        "schema_version": "fetch_metadata.web_page.v1",
        "source_adapter": SOURCE_ADAPTER,
        "request_url": result.url,
        "effective_url": result.effective_url,
        "request_method": "GET",
        "request_headers": {
            "User-Agent": WECHAT_UA,
            **COMMON_HEADERS,
        },
        "started_at": result.started_at,
        "completed_at": result.completed_at,
        "http_status": result.http_status,
        "response_content_type": result.content_type,
        "response_bytes": len(result.body),
        "response_sha256": sha256_bytes(result.body),
        "attempts": result.attempts,
        "curl_returncode": result.returncode,
        "curl_stderr": result.stderr[-1000:],
        "fetch_status": status,
        "permission_status": "public_web_page" if status == "success" else "unknown_or_blocked",
        "anti_bot_policy": {
            "delay_required": True,
            "captcha_bypass": False,
            "stops_on_verify_page": True,
        },
        "source_record": {
            "report_summary_id": row.report_summary_id,
            "publish_time": row.publish_time,
            "institution": row.institution,
            "category": row.category,
            "title": row.title,
            "link_type": row.link_type,
            "link": row.link,
            "variety_num": row.variety_num,
        },
        "extraction_probe": {
            "has_content_node": extracted.has_content_node,
            "text_chars": len(extracted.content_text),
            "image_count": len(extracted.image_urls),
            "verify_like": extracted.verify_like,
        },
    }


def raw_object_dir(root: Path, raw_id: str, row: ReportRow) -> Path:
    yyyy, mm, dd = parse_date_parts(row.publish_time)
    account = safe_path_part(row.institution, "unknown_account")
    return root / "raw_objects" / "web_pages" / SOURCE_NAME / account / yyyy / mm / dd / raw_id


def manifest_paths(root: Path, raw_id: str, row: ReportRow) -> list[Path]:
    yyyy, mm, dd = parse_date_parts(row.publish_time)
    return [
        root / "raw_manifests" / "by_source" / SOURCE_NAME / f"{raw_id}.json",
        root / "raw_manifests" / "by_date" / yyyy / mm / dd / f"{raw_id}.json",
    ]


def build_manifest(
    *,
    root: Path,
    raw_dir: Path,
    raw_id: str,
    row: ReportRow,
    extracted: ExtractedArticle,
    fetch_metadata: dict[str, Any],
    original_sha256: str,
    content_sha256: str,
    content_html_sha256: str,
    status: str,
) -> dict[str, Any]:
    src_url = source_url(row)
    canon = canonical_url(src_url)
    dedupe_key = sha256_bytes(f"{canon}|{content_sha256}".encode("utf-8"))
    return {
        "schema_version": "raw_manifest.wechat_research_article.v1",
        "raw_id": raw_id,
        "source_path": str(raw_dir.relative_to(root)),
        "source_uri": src_url,
        "source_url": src_url,
        "canonical_url": canon,
        "source_name": "hzzhqx",
        "source_display_name": SOURCE_DISPLAY_NAME,
        "source_type": SOURCE_TYPE,
        "source_account": row.institution,
        "title": extracted.title or row.title,
        "author": extracted.author or row.institution,
        "published_at": row.publish_time,
        "source_time": row.publish_time,
        "ingested_at": fetch_metadata.get("completed_at") or utc_now(),
        "crawled_at": fetch_metadata.get("completed_at") or utc_now(),
        "hash": {
            "canonical_url": canon,
            "content_sha256": content_sha256,
            "source_sha256": original_sha256,
            "content_html_sha256": content_html_sha256,
            "dedupe_key": dedupe_key,
        },
        "sha256": original_sha256,
        "fetch_status": status,
        "processing_status": "raw_saved",
        "permission_status": "public_web_page" if status == "success" else "unknown_or_blocked",
        "permission_scope": "public_mp_weixin_url_from_hzzhqx_reports",
        "lineage": {
            "parent_ids": [],
            "source_adapter": SOURCE_ADAPTER,
            "database": {
                "source_database": "quant_data",
                "source_table": "hzzhqx_reports",
                "report_summary_id": row.report_summary_id,
                "link_type": row.link_type,
            },
            "generated_artifacts": [
                "original.html",
                "content_fragment.html",
                "extracted_text.txt",
                "assets/image_index.json",
                "fetch_metadata.json",
            ],
        },
        "artifacts": {
            "original_html": "original.html",
            "content_fragment_html": "content_fragment.html",
            "extracted_text": "extracted_text.txt",
            "assets_dir": "assets",
            "image_index": "assets/image_index.json",
            "fetch_metadata": "fetch_metadata.json",
        },
        "summary": {
            "report_summary_id": row.report_summary_id,
            "research_category": row.category,
            "institution": row.institution,
            "variety_num": row.variety_num,
            "text_chars": len(extracted.content_text),
            "image_count": len(extracted.image_urls),
        },
    }


def append_index(root: Path, row: dict[str, Any], index_name: str = DEFAULT_INDEX) -> None:
    index_path = root / "indexes" / "raw" / index_name
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with index_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")


def load_index(root: Path, index_name: str = DEFAULT_INDEX) -> tuple[dict[str, str], set[str]]:
    index_path = root / "indexes" / "raw" / index_name
    processed: dict[str, str] = {}
    dedupe_keys: set[str] = set()
    if not index_path.exists():
        return processed, dedupe_keys
    with index_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            report_summary_id = item.get("report_summary_id")
            if report_summary_id:
                processed[str(report_summary_id)] = str(item.get("fetch_status") or "")
            dedupe_key = item.get("dedupe_key")
            if dedupe_key:
                dedupe_keys.add(str(dedupe_key))
    return processed, dedupe_keys


def save_raw_document(
    *,
    root: Path,
    row: ReportRow,
    result: FetchResult,
    extracted: ExtractedArticle,
    status: str,
    force: bool,
    index_name: str,
) -> tuple[str, bool, Path, dict[str, Any]]:
    original = result.body
    content_html = extracted.content_html.encode("utf-8")
    content_text = extracted.content_text
    original_sha = sha256_bytes(original)
    content_sha = sha256_bytes(content_text.encode("utf-8"))
    content_html_sha = sha256_bytes(content_html)
    raw_id = f"RAW-HZZHQX-WECHAT-{row.report_summary_id}-{original_sha[:12]}"
    raw_dir = raw_object_dir(root, raw_id, row)
    manifest_file = raw_dir / "raw_manifest.json"

    fetch_metadata = build_fetch_metadata(row, result, extracted, status)
    manifest = build_manifest(
        root=root,
        raw_dir=raw_dir,
        raw_id=raw_id,
        row=row,
        extracted=extracted,
        fetch_metadata=fetch_metadata,
        original_sha256=original_sha,
        content_sha256=content_sha,
        content_html_sha256=content_html_sha,
        status=status,
    )
    if manifest_file.exists() and not force:
        return raw_id, False, raw_dir, manifest

    (raw_dir / "assets").mkdir(parents=True, exist_ok=True)
    write_bytes(raw_dir / "original.html", original, force=force)
    write_bytes(raw_dir / "content_fragment.html", content_html, force=force)
    write_text(raw_dir / "extracted_text.txt", content_text + ("\n" if content_text else ""), force=force)
    write_json(raw_dir / "assets" / "image_index.json", {"images": extracted.image_urls}, force=force)
    write_json(raw_dir / "fetch_metadata.json", fetch_metadata, force=force)
    write_json(manifest_file, manifest, force=force)
    for path in manifest_paths(root, raw_id, row):
        write_json(path, manifest, force=force)

    append_index(
        root,
        {
            "raw_id": raw_id,
            "report_summary_id": row.report_summary_id,
            "title": extracted.title or row.title,
            "source_url": manifest["source_url"],
            "canonical_url": manifest["canonical_url"],
            "published_at": row.publish_time,
            "source_account": row.institution,
            "fetch_status": status,
            "content_sha256": content_sha,
            "source_sha256": original_sha,
            "dedupe_key": manifest["hash"]["dedupe_key"],
            "text_chars": len(content_text),
            "image_count": len(extracted.image_urls),
            "raw_manifest": str(manifest_paths(root, raw_id, row)[0].relative_to(root)),
            "raw_object_dir": str(raw_dir.relative_to(root)),
            "indexed_at": utc_now(),
        },
        index_name=index_name,
    )
    return raw_id, True, raw_dir, manifest


def curl_fetch(
    url: str,
    *,
    timeout: int,
    connect_timeout: int,
    max_retries: int,
    retry_sleep: float,
) -> FetchResult:
    started_at = utc_now()
    last: FetchResult | None = None
    for attempt in range(1, max_retries + 2):
        with tempfile.TemporaryDirectory(prefix="hzzhqx_wechat_") as tmpdir:
            body_path = Path(tmpdir) / "body"
            header_path = Path(tmpdir) / "headers"
            cmd = [
                "curl",
                "-L",
                "--compressed",
                "--http1.1",
                "--connect-timeout",
                str(connect_timeout),
                "--max-time",
                str(timeout),
                "-A",
                WECHAT_UA,
            ]
            for key, value in COMMON_HEADERS.items():
                cmd.extend(["-H", f"{key}: {value}"])
            cmd.extend(["-D", str(header_path), "-o", str(body_path), "-w", "%{http_code}\t%{url_effective}\t%{content_type}", url])
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
            body = body_path.read_bytes() if body_path.exists() else b""
            http_status, effective_url, content_type = parse_curl_writeout(proc.stdout)
            last = FetchResult(
                url=url,
                effective_url=effective_url or url,
                body=body,
                http_status=http_status,
                content_type=content_type,
                stderr=proc.stderr.strip(),
                returncode=proc.returncode,
                attempts=attempt,
                started_at=started_at,
                completed_at=utc_now(),
            )
        if last.returncode == 0 and body:
            return last
        if attempt <= max_retries:
            time.sleep(retry_sleep * attempt)
    if last is None:
        raise IngestError(f"curl did not run for {url}")
    return last


def parse_curl_writeout(text: str) -> tuple[int | None, str, str]:
    parts = text.rstrip("\n").split("\t")
    if not parts:
        return None, "", ""
    try:
        status = int(parts[0]) if parts[0] else None
    except ValueError:
        status = None
    effective = parts[1] if len(parts) > 1 else ""
    content_type = parts[2] if len(parts) > 2 else ""
    return status, effective, content_type


def nap(delay: float, jitter: float) -> None:
    seconds = max(0.0, delay + random.uniform(0.0, jitter))
    if seconds:
        time.sleep(seconds)


def load_backend(backend_path: Path) -> tuple[Any, Any]:
    if str(backend_path) not in sys.path:
        sys.path.insert(0, str(backend_path))
    try:
        from quanta_backend.config import load_config
        from quanta_backend.db import create_engine
    except Exception as exc:  # pragma: no cover - environment diagnostic
        raise IngestError(
            f"Cannot import quanta_backend from {backend_path}. "
            "Pass --backend-path to your local quanta_pro backend checkout."
        ) from exc
    return load_config, create_engine


def fetch_report_rows(
    *,
    backend_path: Path,
    start_date: str,
    end_date: str,
    limit: int,
    institution: str | None,
    order: str,
) -> list[ReportRow]:
    load_config, create_engine = load_backend(backend_path)
    import sqlalchemy as sa

    sql = """
        SELECT report_summary_id, publish_time, institution, category, title,
               link_type, link, variety_num
        FROM hzzhqx_reports
        WHERE link_type = 'WECHAT'
          AND link IS NOT NULL
          AND link LIKE '%mp.weixin.qq.com%'
          AND publish_time >= :start
          AND publish_time < :end
    """
    params: dict[str, Any] = {"start": f"{start_date} 00:00:00", "end": f"{end_date} 00:00:00"}
    if institution:
        sql += " AND institution = :institution"
        params["institution"] = institution
    sql += " ORDER BY publish_time " + ("DESC" if order.lower() == "desc" else "ASC")
    if limit:
        sql += " LIMIT :limit"
        params["limit"] = int(limit)

    engine = create_engine(load_config())
    with engine.connect() as conn:
        rows = conn.execute(sa.text(sql), params).mappings().all()
    out: list[ReportRow] = []
    for item in rows:
        publish_time = item["publish_time"]
        if hasattr(publish_time, "isoformat"):
            publish_time = publish_time.isoformat(sep=" ")
        out.append(
            ReportRow(
                report_summary_id=str(item["report_summary_id"]),
                publish_time=str(publish_time or ""),
                institution=str(item["institution"] or ""),
                category=str(item["category"] or ""),
                title=str(item["title"] or ""),
                link_type=str(item["link_type"] or ""),
                link=str(item["link"] or ""),
                variety_num=int(item["variety_num"]) if item["variety_num"] is not None else None,
            )
        )
    return out


def default_dates(months: int) -> tuple[str, str]:
    today = local_today()
    start = subtract_months(today, months)
    end = today + dt.timedelta(days=1)
    return start.isoformat(), end.isoformat()


def main() -> int:
    default_start, default_end = default_dates(2)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quanta-root", type=Path, default=DEFAULT_QUANTA_ROOT)
    parser.add_argument(
        "--backend-path",
        type=Path,
        default=Path(
            os.getenv("GJ_QUANTA_PRO_BACKEND")
            or os.getenv("QUANTA_PRO_BACKEND")
            or str(DEFAULT_BACKEND)
        ),
    )
    parser.add_argument("--start-date", default=default_start, help="inclusive YYYY-MM-DD")
    parser.add_argument("--end-date", default=default_end, help="exclusive YYYY-MM-DD")
    parser.add_argument("--institution", help="optional institution filter")
    parser.add_argument("--limit", type=int, default=0, help="max DB rows to consider, 0 means all")
    parser.add_argument("--max-docs", type=int, default=0, help="max documents to fetch after skips, 0 means all")
    parser.add_argument("--order", choices=["asc", "desc"], default="asc")
    parser.add_argument("--delay", type=float, default=8.0)
    parser.add_argument("--jitter", type=float, default=4.0)
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--connect-timeout", type=int, default=15)
    parser.add_argument("--max-retries", type=int, default=1)
    parser.add_argument("--retry-sleep", type=float, default=10.0)
    parser.add_argument("--verify-cooldown", type=float, default=1800.0)
    parser.add_argument("--max-consecutive-blocks", type=int, default=3)
    parser.add_argument("--index-name", default=DEFAULT_INDEX)
    parser.add_argument("--force", action="store_true", help="rewrite files and refetch processed IDs")
    parser.add_argument("--retry-failed", action="store_true", help="retry IDs whose latest index status is not success")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    root: Path = args.quanta_root
    rows = fetch_report_rows(
        backend_path=args.backend_path,
        start_date=args.start_date,
        end_date=args.end_date,
        limit=args.limit,
        institution=args.institution,
        order=args.order,
    )
    processed, _dedupe_keys = load_index(root, args.index_name)
    print(
        f"range={args.start_date}..{args.end_date} rows={len(rows)} "
        f"indexed_ids={len(processed)} root={root}",
        flush=True,
    )
    if args.dry_run:
        for row in rows[:20]:
            status = processed.get(row.report_summary_id)
            print(f"{row.publish_time} sid={row.report_summary_id} status={status or '-'} {row.institution} {row.title[:80]}")
        return 0

    fetched = saved = skipped = failed = blocked = 0
    consecutive_blocks = 0
    for row in rows:
        prior_status = processed.get(row.report_summary_id)
        if prior_status and not args.force:
            if prior_status == "success" or not args.retry_failed:
                skipped += 1
                continue
        if args.max_docs and fetched >= args.max_docs:
            break

        fetched += 1
        print(f"[{fetched}] sid={row.report_summary_id} {row.publish_time} {row.institution} {row.title[:60]}", flush=True)
        result = curl_fetch(
            row.link,
            timeout=args.timeout,
            connect_timeout=args.connect_timeout,
            max_retries=args.max_retries,
            retry_sleep=args.retry_sleep,
        )
        extracted = extract_article(result.body, row) if result.body else ExtractedArticle(
            title=row.title,
            author=row.institution,
            publish_time=row.publish_time,
            content_text="",
            content_html="",
            image_urls=[],
            verify_like=False,
            has_content_node=False,
        )
        status = fetch_status(extracted, result)
        try:
            raw_id, wrote, raw_dir, _manifest = save_raw_document(
                root=root,
                row=row,
                result=result,
                extracted=extracted,
                status=status,
                force=args.force,
                index_name=args.index_name,
            )
            saved += int(wrote)
            print(
                f"    {status} raw_id={raw_id} text={len(extracted.content_text)} "
                f"images={len(extracted.image_urls)} wrote={wrote} dir={raw_dir}",
                flush=True,
            )
            processed[row.report_summary_id] = status
        except Exception as exc:
            failed += 1
            print(f"    save_error: {type(exc).__name__}: {exc}", flush=True)

        if status in {"blocked_or_verify", "transport_error", "http_error"}:
            blocked += 1
            consecutive_blocks += 1
            print(f"    block_like={consecutive_blocks}/{args.max_consecutive_blocks}", flush=True)
            if consecutive_blocks >= args.max_consecutive_blocks:
                print(f"    cooldown {args.verify_cooldown:.0f}s before stopping to avoid anti-bot escalation", flush=True)
                time.sleep(args.verify_cooldown)
                print("    stopped_after_consecutive_blocks", flush=True)
                break
        else:
            consecutive_blocks = 0

        nap(args.delay, args.jitter)

    print(
        f"done fetched={fetched} saved={saved} skipped={skipped} "
        f"failed={failed} block_like={blocked}",
        flush=True,
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
