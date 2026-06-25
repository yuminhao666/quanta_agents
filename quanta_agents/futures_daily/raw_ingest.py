from __future__ import annotations

import argparse
import json
import re
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any

from quanta_agents.core.config import first_env, load_default_env, quanta_data_root
from quanta_agents.core.io import dated_parts, sha256_bytes, utc_now_iso, write_json


RAW_BASE_RELATIVE = "raw_objects/futures_daily/daily_originals"
OSS_RAW_PREFIX = "raw_data/jr_data"


def _load_secret_env() -> None:
    load_default_env()


def _require_oss2():
    try:
        import oss2  # type: ignore
    except ImportError as exc:  # pragma: no cover - environment diagnostic
        raise RuntimeError('缺少 oss2 依赖，请安装：python3 -m pip install -e ".[oss]"') from exc
    return oss2


def _require_pandas():
    try:
        import pandas as pd  # type: ignore
    except ImportError as exc:  # pragma: no cover - environment diagnostic
        raise RuntimeError('缺少 pandas/openpyxl 依赖，请安装：python3 -m pip install -e ".[documents]"') from exc
    return pd


def _make_bucket() -> Any:
    _load_secret_env()
    oss2 = _require_oss2()
    access_key = first_env("OSS_ACCESS_KEY_ID", "ALIYUN_OSS_ACCESS_KEY_ID")
    secret = first_env("OSS_ACCESS_KEY_SECRET", "ALIYUN_OSS_ACCESS_KEY_SECRET")
    endpoint = first_env("OSS_ENDPOINT", "ALIYUN_OSS_ENDPOINT", default="https://oss-cn-shanghai.aliyuncs.com")
    bucket_name = first_env("OSS_BUCKET", default="nblab")
    if not access_key or not secret:
        raise RuntimeError("缺少 OSS_ACCESS_KEY_ID / OSS_ACCESS_KEY_SECRET。")
    return oss2.Bucket(oss2.Auth(access_key, secret), endpoint, bucket_name, connect_timeout=15)


def _safe_name(value: str, fallback: str) -> str:
    text = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", str(value or "").strip())
    text = text.strip("._")
    return (text or fallback)[:90]


def raw_folder_for_date(date_key: str, root: str | Path | None = None) -> Path:
    yyyy, mm, dd = dated_parts(date_key)
    return quanta_data_root(root) / RAW_BASE_RELATIVE / yyyy / mm / dd


def _oss_key_for_date(date_key: str) -> str:
    return f"{OSS_RAW_PREFIX}/{date_key[:4]}-{date_key[4:6]}/gzh_futures_{date_key}.zip"


def _write_missing_manifest(folder: Path, date_key: str, oss_key: str, error: str) -> dict[str, Any]:
    folder.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": "futures_daily_raw_manifest.v1",
        "status": "missing",
        "date": date_key,
        "source": {"type": "oss", "key": oss_key},
        "error": error,
        "generated_at": utc_now_iso(),
        "row_count": 0,
        "document_count": 0,
    }
    write_json(folder / "manifest.json", manifest)
    return manifest


def _extract_excel_rows(zip_bytes: bytes) -> list[dict[str, Any]]:
    pd = _require_pandas()
    rows: list[dict[str, Any]] = []
    with zipfile.ZipFile(BytesIO(zip_bytes), "r") as zf:
        excel_names = [
            name
            for name in zf.namelist()
            if not name.startswith("__MACOSX/")
            and not name.startswith(".")
            and not name.endswith("/")
            and name.lower().endswith((".xlsx", ".xls"))
        ]
        for excel_name in excel_names:
            with zf.open(excel_name) as handle:
                df = pd.read_excel(BytesIO(handle.read()))
            for _, row in df.iterrows():
                data = {str(key): (None if pd.isna(value) else value) for key, value in row.to_dict().items()}
                data["_source_excel"] = excel_name
                rows.append(data)
    return rows


def _row_text(row: dict[str, Any]) -> str:
    title = str(row.get("title") or row.get("标题") or "").strip()
    content = str(row.get("content") or row.get("pdf_txt") or row.get("正文") or "").strip()
    return f"{title}\n\n{content}".strip()


def _normalize_row(row: dict[str, Any], index: int, date_key: str) -> dict[str, Any]:
    org = str(row.get("account") or row.get("orgName") or row.get("org_name") or row.get("机构") or "").strip()
    title = str(row.get("title") or row.get("标题") or f"report-{index:04d}").strip()
    raw_date = row.get("data_desc") or row.get("date") or row.get("日期") or date_key
    content = str(row.get("content") or row.get("pdf_txt") or row.get("正文") or "").strip()
    return {
        "row_id": f"RPT-{date_key}-{index:04d}",
        "date": str(raw_date),
        "report_date": date_key,
        "org_name": org or "未知机构",
        "title": title,
        "content": content,
        "source_excel": row.get("_source_excel"),
        "raw": row,
    }


def ingest_oss_raw_reports(date_key: str, root: str | Path | None = None, *, force: bool = False) -> dict[str, Any]:
    date_key = date_key.replace("-", "")
    folder = raw_folder_for_date(date_key, root)
    oss_key = _oss_key_for_date(date_key)
    if (folder / "manifest.json").exists() and not force:
        return json.loads((folder / "manifest.json").read_text(encoding="utf-8"))

    bucket = _make_bucket()
    try:
        zip_bytes = bucket.get_object(oss_key).read()
    except Exception as exc:
        return _write_missing_manifest(folder, date_key, oss_key, str(exc))

    folder.mkdir(parents=True, exist_ok=True)
    source_dir = folder / "source"
    docs_dir = folder / "documents"
    source_dir.mkdir(exist_ok=True)
    docs_dir.mkdir(exist_ok=True)
    zip_path = source_dir / f"gzh_futures_{date_key}.zip"
    zip_path.write_bytes(zip_bytes)
    rows = [_normalize_row(row, i + 1, date_key) for i, row in enumerate(_extract_excel_rows(zip_bytes))]

    rows_path = folder / "rows.jsonl"
    with rows_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

    docs = []
    for index, row in enumerate(rows, 1):
        text = _row_text(row)
        if not text:
            continue
        name = f"{index:04d}_{_safe_name(row['org_name'], 'org')}_{_safe_name(row['title'], 'report')}.md"
        doc_path = docs_dir / name
        doc_path.write_text(
            "\n".join(
                [
                    "---",
                    f"row_id: {row['row_id']}",
                    f"report_date: {date_key}",
                    f"org_name: {row['org_name']}",
                    f"title: {row['title']}",
                    f"source_excel: {row.get('source_excel') or ''}",
                    "---",
                    "",
                    text,
                    "",
                ]
            ),
            encoding="utf-8",
        )
        docs.append({"row_id": row["row_id"], "path": str(doc_path.relative_to(folder)), "sha256": sha256_bytes(doc_path.read_bytes())})

    manifest = {
        "schema_version": "futures_daily_raw_manifest.v1",
        "status": "available",
        "date": date_key,
        "source": {
            "type": "oss",
            "key": oss_key,
            "zip_path": str(zip_path.relative_to(folder)),
            "zip_sha256": sha256_bytes(zip_bytes),
        },
        "generated_at": utc_now_iso(),
        "row_count": len(rows),
        "document_count": len(docs),
        "rows_path": "rows.jsonl",
        "documents": docs,
    }
    write_json(folder / "manifest.json", manifest)
    return manifest


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Ingest raw gzh futures reports from OSS into daily original folder.")
    parser.add_argument("--date", required=True, help="Report date, YYYYMMDD.")
    parser.add_argument("--quanta-root", help="Override GJ_QUANTA_DATA_ROOT.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing manifest/folder files.")
    args = parser.parse_args(argv)
    manifest = ingest_oss_raw_reports(args.date, args.quanta_root, force=args.force)
    folder = raw_folder_for_date(args.date.replace("-", ""), args.quanta_root)
    print(f"日报原文目录 → {folder}")
    print(f"status={manifest['status']} rows={manifest['row_count']} documents={manifest['document_count']}")
    if manifest["status"] != "available":
        print(f"missing source → {manifest['source']['key']}")
        print(f"error → {manifest.get('error', '')}")


if __name__ == "__main__":
    main()
