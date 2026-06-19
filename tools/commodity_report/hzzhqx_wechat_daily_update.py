from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]


def _bootstrap_repo() -> None:
    sys.path.insert(0, str(REPO_ROOT))


def _date_parts(date_key: str) -> tuple[str, str, str]:
    clean = date_key.replace("-", "")
    if not re.fullmatch(r"\d{8}", clean):
        raise ValueError("date must be YYYYMMDD")
    return clean[:4], clean[4:6], clean[6:8]


def _safe_name(value: str, fallback: str) -> str:
    text = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", str(value or "").strip())
    text = text.strip("._")
    return (text or fallback)[:90]


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _load_wechat_rows(root: Path, date_key: str, *, min_chars: int) -> list[dict[str, Any]]:
    yyyy, mm, dd = _date_parts(date_key)
    manifest_dir = root / "raw_manifests" / "by_date" / yyyy / mm / dd
    manifest_paths = sorted(manifest_dir.glob("RAW-HZZHQX-WECHAT-*.json"))
    rows: list[dict[str, Any]] = []

    for idx, manifest_path in enumerate(manifest_paths, 1):
        manifest = _read_json(manifest_path)
        if manifest.get("fetch_status") not in {None, "success"}:
            continue
        source_path = str(manifest.get("source_path") or "").strip()
        if not source_path:
            continue
        raw_dir = root / source_path
        artifacts = manifest.get("artifacts") if isinstance(manifest.get("artifacts"), dict) else {}
        text_name = str(artifacts.get("extracted_text") or "extracted_text.txt")
        text_path = raw_dir / text_name
        if not text_path.exists():
            continue
        content = text_path.read_text(encoding="utf-8", errors="ignore").strip()
        if len(content) < min_chars:
            continue

        raw_id = str(manifest.get("raw_id") or f"RAW-HZZHQX-WECHAT-{idx:04d}")
        title = str(manifest.get("title") or f"wechat-report-{idx:04d}").strip()
        org_name = str(manifest.get("source_account") or manifest.get("author") or "unknown").strip()
        rows.append(
            {
                "row_id": raw_id,
                "date": str(manifest.get("source_time") or manifest.get("published_at") or date_key),
                "report_date": date_key,
                "org_name": org_name or "unknown",
                "title": title,
                "content": content,
                "source_excel": "hzzhqx_wechat",
                "raw": {
                    "raw_id": raw_id,
                    "source_url": manifest.get("source_url") or manifest.get("canonical_url"),
                    "source_path": source_path,
                    "manifest_path": _relative(manifest_path, root),
                    "author": manifest.get("author"),
                    "summary": manifest.get("summary") or {},
                    "sha256": manifest.get("sha256"),
                },
            }
        )
    return rows


def build_daily_originals_folder(
    *,
    root: Path,
    date_key: str,
    min_chars: int = 80,
    force: bool = False,
) -> Path:
    yyyy, mm, dd = _date_parts(date_key)
    raw_folder = root / "raw_objects" / "futures_daily" / "daily_originals" / yyyy / mm / dd
    manifest_path = raw_folder / "manifest.json"
    if manifest_path.exists() and not force:
        manifest = _read_json(manifest_path)
        if manifest.get("source", {}).get("type") == "quanta_data_hzzhqx_wechat":
            return raw_folder

    rows = _load_wechat_rows(root, date_key, min_chars=min_chars)
    if not rows:
        raise RuntimeError(f"no hzzhqx wechat rows found for {date_key}")

    rows_path = raw_folder / "rows.jsonl"
    docs_dir = raw_folder / "documents"
    docs_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(rows_path, rows)

    documents = []
    for index, row in enumerate(rows, 1):
        doc_name = f"{index:04d}_{_safe_name(row['org_name'], 'org')}_{_safe_name(row['title'], 'report')}.md"
        doc_path = docs_dir / doc_name
        doc_path.write_text(
            "\n".join(
                [
                    "---",
                    f"row_id: {row['row_id']}",
                    f"report_date: {date_key}",
                    f"org_name: {row['org_name']}",
                    f"title: {row['title']}",
                    "source: hzzhqx_wechat",
                    "---",
                    "",
                    f"{row['title']}",
                    "",
                    str(row["content"]),
                    "",
                ]
            ),
            encoding="utf-8",
        )
        documents.append(
            {
                "row_id": row["row_id"],
                "path": _relative(doc_path, raw_folder),
                "source_path": row["raw"].get("source_path"),
                "manifest_path": row["raw"].get("manifest_path"),
            }
        )

    _write_json(
        manifest_path,
        {
            "schema_version": "futures_daily_raw_manifest.v1",
            "status": "available",
            "date": date_key,
            "source": {
                "type": "quanta_data_hzzhqx_wechat",
                "manifest_dir": f"raw_manifests/by_date/{yyyy}/{mm}/{dd}",
                "raw_object_base": "raw_objects/web_pages/hzzhqx_wechat",
            },
            "generated_at": datetime.now().isoformat(),
            "row_count": len(rows),
            "document_count": len(documents),
            "rows_path": "rows.jsonl",
            "documents": documents,
        },
    )
    return raw_folder


def run_update(args: argparse.Namespace) -> dict[str, Any]:
    _bootstrap_repo()

    from quanta_agents.core.config import load_env_file, quanta_data_root
    from quanta_agents.core.llm_client import get_provider
    from quanta_agents.futures_daily.framework_alignment import publish_framework_alignment
    from quanta_agents.futures_daily.publisher import publish_report_paths
    from quanta_agents.futures_daily.raw_run import run_from_raw_folder
    from tools.commodity_report.commodity_report_legacy import run_legacy_from_raw_folder

    for env_path in [
        REPO_ROOT / ".env",
        REPO_ROOT.parent / "gj_chainplatform" / ".env",
        REPO_ROOT.parent / "quanta_research" / ".env",
        REPO_ROOT.parent / "quanta_research_group" / ".env",
        Path.home() / ".hermes" / ".env",
    ]:
        load_env_file(env_path)
    if not os.environ.get("MINIMAX_API_KEY") and os.environ.get("MINIMAX_CN_API_KEY"):
        os.environ["MINIMAX_API_KEY"] = os.environ["MINIMAX_CN_API_KEY"]

    date_key = args.date.replace("-", "")
    root = quanta_data_root(args.quanta_root)
    os.environ["QUANTA_AGENT_LLM_PROVIDER"] = args.provider
    if args.provider_model:
        os.environ["QUANTA_AGENT_LLM_MODEL"] = args.provider_model

    if not args.no_llm:
        provider = get_provider(args.provider)
        if not provider.api_keys:
            raise RuntimeError(
                f"missing API key for provider={args.provider}; set {provider.api_key_hint}"
            )
        print(
            f"Using provider={provider.name} model={provider.model} "
            f"base_url={provider.base_url} keys={len(provider.api_keys)}"
        )
    else:
        print("LLM disabled; using rule fallback only.")

    raw_folder = build_daily_originals_folder(
        root=root,
        date_key=date_key,
        min_chars=args.min_chars,
        force=args.force_raw_folder,
    )
    raw_manifest = _read_json(raw_folder / "manifest.json")
    print(f"Raw folder: {raw_folder}")
    print(f"Raw rows: {raw_manifest.get('row_count')} documents={raw_manifest.get('document_count')}")

    if args.engine == "raw":
        result = run_from_raw_folder(
            raw_folder,
            root,
            use_llm=not args.no_llm,
            max_assets=args.max_assets,
            max_workers=args.max_workers,
            use_llm_postprocess=args.llm_postprocess,
        )
    else:
        result = run_legacy_from_raw_folder(
            raw_folder,
            root,
            use_llm=not args.no_llm,
            max_assets=args.max_assets,
            max_workers=args.max_workers,
            use_llm_postprocess=args.llm_postprocess,
        )
    run_dir = Path(result["run_dir"])
    print(f"Raw run: {run_dir}")

    publish_result: dict[str, Any] | None = None
    if not args.skip_publish:
        summary_path = run_dir / f"{date_key}_commodity_summary.json"
        market_review_path = run_dir / f"{date_key}_commodity_marketreview.json"
        publish_result = publish_report_paths(
            summary_path=summary_path,
            market_review_path=market_review_path,
            preview_html_path=None,
            report_date=date_key,
            root=root,
        )
        print(f"Published market_review: {publish_result['source_paths']['market_review']}")
        print(f"Published commodity_summary: {publish_result['source_paths']['commodity_summary']}")
        print(f"Published review_package: {publish_result['source_paths']['review_package']}")
        if not args.skip_align:
            align_paths = publish_framework_alignment(
                summary_path,
                root,
                use_llm_refinement=args.llm_postprocess,
            )
            for role, path in align_paths.items():
                print(f"Published {role}: {path}")

    return {
        "root": str(root),
        "date": date_key,
        "raw_folder": str(raw_folder),
        "run_dir": str(run_dir),
        "publish_result": publish_result,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Build ModelQuanta futures daily report from hzzhqx WeChat raw objects."
    )
    parser.add_argument("--date", required=True, help="Report date, YYYYMMDD.")
    parser.add_argument("--quanta-root", help="Override quanta_data root.")
    parser.add_argument("--provider", default="m3", help="LLM provider. Default: m3.")
    parser.add_argument("--provider-model", help="Optional model override.")
    parser.add_argument(
        "--engine",
        choices=["commodity_report", "raw"],
        default="commodity_report",
        help="Processing engine. Default uses tmp_code/commodity_report-compatible logic.",
    )
    parser.add_argument("--max-assets", type=int, help="Maximum assets to analyze. Omit or pass 0 for all assets.")
    parser.add_argument("--max-workers", type=int, default=2, help="Parallel asset-analysis workers.")
    parser.add_argument("--llm-postprocess", action="store_true", help="Use LLM for framework mapping and thesis postprocess.")
    parser.add_argument("--skip-align", action="store_true", help="Skip publishing framework/factor/timeline candidates.")
    parser.add_argument("--min-chars", type=int, default=80, help="Skip raw articles shorter than this.")
    parser.add_argument("--force-raw-folder", action="store_true", help="Rebuild daily_originals folder.")
    parser.add_argument("--skip-publish", action="store_true", help="Only generate raw run candidates.")
    parser.add_argument("--no-llm", action="store_true", help="Use rule fallback only.")
    args = parser.parse_args(argv)
    output = run_update(args)
    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
