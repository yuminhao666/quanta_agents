from __future__ import annotations

import argparse
import hashlib
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from quanta_agents.core.config import quanta_data_root
from quanta_agents.core.io import dated_parts, read_json, relative_to_root, utc_now_iso, write_json
from quanta_agents.core.taxonomy import AssetTaxonomy, load_asset_taxonomy
from quanta_agents.research_reports.wechat_evidence import (
    BOILERPLATE_PATTERNS,
    RAW_WECHAT_RELATIVE,
    _canonical_date,
    _clean_spaces,
    _hash_id,
    _infer_direction,
    _is_heading,
    _is_market_observation,
    _manifest_source,
    _sentence_parts,
    _variety_names,
    load_wechat_articles,
)


BRIDGE_VERSION = "wechat_canonical_evidence_bridge.v1"
PM_TRIAGE_REF = "agent_workspace/agents/shared/pm_triage/2026/06/20/PM-TRIAGE-20260620-002.json"
KB_SUGGESTIONS_REF = (
    "agent_workspace/candidates/knowledge_maintenance/2026/06/19/"
    "CAND-KBM-HERMES-20260619-002/suggestions.json"
)
SOURCE_KB_REFS = (
    {"id": "PM-TRIAGE-20260620-002", "path": PM_TRIAGE_REF},
    {"id": "TP-KBM-20260620-002", "path": PM_TRIAGE_REF},
    {"id": "CAND-KBM-HERMES-20260619-002#KMS-20260619-011", "path": KB_SUGGESTIONS_REF},
    {"id": "CAND-KBM-HERMES-20260619-002#KMS-20260619-012", "path": KB_SUGGESTIONS_REF},
)
CANONICAL_BOILERPLATE_PATTERNS = tuple(
    marker for marker in BOILERPLATE_PATTERNS if marker != "风险提示"
)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_file_if_exists(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _strip_canonical_boilerplate_tail(text: str) -> str:
    cut = len(text)
    for marker in CANONICAL_BOILERPLATE_PATTERNS:
        idx = text.find(marker)
        if idx >= 0 and idx < cut:
            cut = idx
    return text[:cut].strip()


def _ref(
    ref_type: str,
    path: Path | str,
    *,
    root: Path,
    artifact_id: str | None = None,
    content_hash: str | None = None,
    lineage_id: str | None = None,
) -> dict[str, Any]:
    ref_path = relative_to_root(Path(path), root) if isinstance(path, Path) else path
    return {
        "ref_type": ref_type,
        "id": artifact_id,
        "path": ref_path,
        "hash": content_hash,
        "lineage_id": lineage_id,
    }


def _dedupe_refs(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for ref in refs:
        key = (str(ref.get("ref_type") or ""), str(ref.get("id") or ""), str(ref.get("path") or ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(ref)
    return deduped


def _source_kb_artifact_refs() -> list[dict[str, Any]]:
    return [
        {
            "ref_type": "external",
            "id": item["id"],
            "path": item["path"],
            "hash": None,
            "lineage_id": None,
        }
        for item in SOURCE_KB_REFS
    ]


def _raw_by_date_manifest(root: Path, date_key: str, raw_id: str) -> Path:
    yyyy, mm, dd = dated_parts(date_key)
    return root / "raw_manifests" / "by_date" / yyyy / mm / dd / f"{raw_id}.json"


def _article_from_manifest_path(root: Path, manifest_path: Path, *, date_key: str) -> dict[str, Any]:
    manifest = read_json(manifest_path)
    if not isinstance(manifest, dict):
        raise ValueError(f"raw_manifest is not a JSON object: {manifest_path}")
    text_path = manifest_path.with_name("extracted_text.txt")
    text = text_path.read_text(encoding="utf-8", errors="ignore") if text_path.exists() else ""
    text = _strip_canonical_boilerplate_tail(text)
    raw_id = str(manifest.get("raw_id") or manifest_path.parent.name)
    by_date_manifest = _raw_by_date_manifest(root, date_key, raw_id)
    return {
        "article_id": raw_id,
        "raw_id": raw_id,
        "title": str(manifest.get("title") or ""),
        "source_account": _manifest_source(manifest_path, manifest),
        "source_display_name": str(manifest.get("source_display_name") or ""),
        "published_at": str(manifest.get("published_at") or manifest.get("source_time") or ""),
        "source_url": manifest.get("source_url") or manifest.get("canonical_url") or manifest.get("source_uri"),
        "canonical_url": manifest.get("canonical_url") or manifest.get("source_url") or manifest.get("source_uri"),
        "raw_object_dir": str(manifest_path.parent),
        "raw_manifest": str(manifest_path),
        "by_date_raw_manifest": str(by_date_manifest) if by_date_manifest.exists() else "",
        "text_path": str(text_path),
        "text_chars": len(text),
        "text": text,
    }


def load_wechat_bridge_articles(
    root: str | Path | None = None,
    *,
    date: str,
    raw_id: str | None = None,
    article_limit: int = 1,
) -> list[dict[str, Any]]:
    root_path = quanta_data_root(root)
    date_key = _canonical_date(date)
    if raw_id:
        yyyy, mm, dd = dated_parts(date_key)
        matches = sorted(
            (root_path / RAW_WECHAT_RELATIVE).glob(f"*/{yyyy}/{mm}/{dd}/{raw_id}/raw_manifest.json")
        )
        if not matches:
            raise FileNotFoundError(f"未找到 hzzhqx_wechat raw object: date={date_key} raw_id={raw_id}")
        return [_article_from_manifest_path(root_path, matches[0], date_key=date_key)]
    return load_wechat_articles(root_path, date=date_key, article_limit=article_limit)


def _asset_ref(taxonomy: AssetTaxonomy, name: str) -> dict[str, Any]:
    asset = next(
        (item for item in taxonomy.assets if item.get("canonical_name") == name),
        {"canonical_name": name},
    )
    return {
        "name": str(asset.get("canonical_name") or name),
        "asset_id": asset.get("asset_id"),
        "commodity_code": asset.get("commodity_code") or asset.get("exchange_code"),
        "category": asset.get("category"),
        "sector": asset.get("sector"),
    }


def _find_span(text: str, needle: str, *, start_at: int = 0) -> tuple[int | None, int | None]:
    if not needle:
        return None, None
    start = text.find(needle, start_at)
    if start >= 0:
        return start, start + len(needle)
    normalized = _clean_spaces(needle)
    for part in re.split(r"[。；;，,\n]+", normalized):
        part = part.strip()
        if len(part) < 12:
            continue
        idx = text.find(part, start_at)
        if idx >= 0:
            return idx, idx + len(part)
    return None, None


def _split_canonical_sections(article: dict[str, Any], taxonomy: AssetTaxonomy) -> list[dict[str, Any]]:
    text = str(article.get("text") or "")
    lines = [line.strip() for line in re.split(r"[\r\n]+", text) if line.strip()]
    canonical_text = "\n".join(lines)
    sections: list[dict[str, Any]] = []
    buffer: list[str] = []
    heading = ""
    heading_assets: list[str] = []
    search_from = 0

    def flush() -> None:
        nonlocal buffer, heading, heading_assets, search_from
        body = "\n".join(buffer).strip()
        if not body:
            buffer = []
            heading = ""
            heading_assets = []
            return
        char_start, char_end = _find_span(canonical_text, body, start_at=search_from)
        if char_end is not None:
            search_from = char_end
        assets = heading_assets or _variety_names(taxonomy, body)
        section_id = _hash_id(
            "CSECT",
            article.get("raw_id"),
            len(sections),
            heading,
            _sha256_text(body)[:16],
        )
        sections.append(
            {
                "section_id": section_id,
                "order": len(sections),
                "heading": heading,
                "assets": [_asset_ref(taxonomy, name) for name in assets[:8]],
                "text": body,
                "text_hash": _sha256_text(body),
                "source_span": {"char_start": char_start, "char_end": char_end},
            }
        )
        buffer = []
        heading = ""
        heading_assets = []

    for line in lines:
        if any(marker in line for marker in CANONICAL_BOILERPLATE_PATTERNS):
            break
        if re.fullmatch(r"\d{1,2}", line):
            continue
        is_heading, assets = _is_heading(line, taxonomy)
        if is_heading:
            flush()
            heading = line
            heading_assets = assets
            buffer = [line]
            continue
        buffer.append(line)
    flush()
    if sections:
        return sections
    body = canonical_text.strip()
    return [
        {
            "section_id": _hash_id("CSECT", article.get("raw_id"), "whole", _sha256_text(body)[:16]),
            "order": 0,
            "heading": "",
            "assets": [_asset_ref(taxonomy, name) for name in _variety_names(taxonomy, body)[:8]],
            "text": body,
            "text_hash": _sha256_text(body),
            "source_span": {"char_start": 0 if body else None, "char_end": len(body) if body else None},
        }
    ]


def _chunk_section(section: dict[str, Any], *, max_chars: int = 900) -> list[dict[str, Any]]:
    parts = _sentence_parts(str(section.get("text") or ""))
    if not parts:
        return []
    chunks: list[dict[str, Any]] = []
    current: list[str] = []
    current_len = 0

    def flush() -> None:
        nonlocal current, current_len
        text = _clean_spaces(" ".join(current))
        if text:
            chunks.append(
                {
                    "chunk_id": _hash_id(
                        "CCHUNK",
                        section.get("section_id"),
                        len(chunks),
                        _sha256_text(text)[:16],
                    ),
                    "section_id": section.get("section_id"),
                    "order": len(chunks),
                    "heading": section.get("heading") or "",
                    "assets": section.get("assets") or [],
                    "text": text,
                    "text_hash": _sha256_text(text),
                }
            )
        current = []
        current_len = 0

    for part in parts:
        clean = _clean_spaces(part)
        if not clean:
            continue
        if current and current_len + len(clean) > max_chars:
            flush()
        current.append(clean)
        current_len += len(clean)
        if len(clean) >= max_chars:
            flush()
    if current:
        flush()
    return chunks


def build_canonical_document(
    article: dict[str, Any],
    *,
    root: str | Path | None = None,
    taxonomy: AssetTaxonomy | None = None,
    source_kb_refs: tuple[dict[str, str], ...] = SOURCE_KB_REFS,
) -> dict[str, Any]:
    root_path = quanta_data_root(root)
    taxonomy = taxonomy or load_asset_taxonomy(root_path)
    text = _strip_canonical_boilerplate_tail(str(article.get("text") or ""))
    sections = _split_canonical_sections({**article, "text": text}, taxonomy)
    chunks: list[dict[str, Any]] = []
    for section in sections:
        section_chunks = _chunk_section(section)
        section["chunk_ids"] = [chunk["chunk_id"] for chunk in section_chunks]
        chunks.extend(section_chunks)

    raw_id = str(article.get("raw_id") or article.get("article_id") or "")
    content_hash = _sha256_text(text)
    document_id = _hash_id("CAN-RREP-WECHAT", raw_id, content_hash)
    lineage_id = _hash_id("LINEAGE-RREP-WECHAT", raw_id, content_hash)
    raw_manifest_path = Path(str(article.get("raw_manifest") or ""))
    by_date_manifest = Path(str(article.get("by_date_raw_manifest") or ""))
    text_path = Path(str(article.get("text_path") or ""))
    raw_object_dir = Path(str(article.get("raw_object_dir") or raw_manifest_path.parent))

    raw_refs = []
    if by_date_manifest.exists():
        raw_refs.append(
            _ref(
                "raw_manifest",
                by_date_manifest,
                root=root_path,
                artifact_id=raw_id,
                content_hash=_sha256_file_if_exists(by_date_manifest),
                lineage_id=lineage_id,
            )
        )
    if raw_manifest_path.exists():
        raw_refs.append(
            _ref(
                "raw_manifest",
                raw_manifest_path,
                root=root_path,
                artifact_id=f"{raw_id}:object_manifest",
                content_hash=_sha256_file_if_exists(raw_manifest_path),
                lineage_id=lineage_id,
            )
        )
    raw_refs.append(
        _ref(
            "raw_object",
            raw_object_dir,
            root=root_path,
            artifact_id=raw_id,
            content_hash=_sha256_file_if_exists(text_path),
            lineage_id=lineage_id,
        )
    )

    return {
        "schema_version": "canonical_research_report_document.v1",
        "artifact_type": "canonical_document",
        "document_id": document_id,
        "status": "canonicalized",
        "domain": "research_reports",
        "source_type": "wechat_article",
        "source_system": "hzzhqx_wechat",
        "generated_at": utc_now_iso(),
        "content_hash": content_hash,
        "metadata": {
            "raw_id": raw_id,
            "title": article.get("title") or "",
            "source_account": article.get("source_account") or "",
            "source_display_name": article.get("source_display_name") or "",
            "published_at": article.get("published_at") or "",
            "source_url": article.get("source_url"),
            "canonical_url": article.get("canonical_url") or article.get("source_url"),
            "language": "zh-CN",
            "text_chars": len(text),
            "section_count": len(sections),
            "chunk_count": len(chunks),
        },
        "lineage": {
            "lineage_id": lineage_id,
            "bridge_version": BRIDGE_VERSION,
            "input_refs": _dedupe_refs(raw_refs),
            "source_kb_refs": list(source_kb_refs),
        },
        "sections": sections,
        "chunks": chunks,
    }


def _chunk_asset(chunk: dict[str, Any]) -> dict[str, Any] | None:
    assets = chunk.get("assets") if isinstance(chunk.get("assets"), list) else []
    for asset in assets:
        if isinstance(asset, dict) and asset.get("name"):
            return asset
    return None


def _claim_text(snippet: str) -> str:
    parts = _sentence_parts(snippet)
    text = _clean_spaces(parts[0] if parts else snippet)
    return text[:360]


def extract_evidence_units(
    canonical_document: dict[str, Any],
    *,
    canonical_path: str | None = None,
    max_units: int = 12,
) -> list[dict[str, Any]]:
    metadata = canonical_document.get("metadata") or {}
    lineage = canonical_document.get("lineage") or {}
    raw_refs = lineage.get("input_refs") if isinstance(lineage.get("input_refs"), list) else []
    units: list[dict[str, Any]] = []
    for chunk in canonical_document.get("chunks") or []:
        snippet = _clean_spaces(str(chunk.get("text") or ""))
        if len(snippet) < 24:
            continue
        scoring_role = "market_observation" if _is_market_observation(snippet) else "fundamental_evidence"
        direction, direction_score, direction_meta = _infer_direction(snippet, scoring_role=scoring_role)
        asset = _chunk_asset(chunk)
        evidence_id = _hash_id(
            "EVID-RREP-WECHAT",
            canonical_document.get("document_id"),
            chunk.get("chunk_id"),
            asset.get("name") if asset else "",
            _sha256_text(snippet)[:16],
        )
        unit = {
            "schema_version": "research_report_evidence_unit.v1",
            "artifact_type": "evidence_unit",
            "evidence_id": evidence_id,
            "status": "candidate",
            "generated_at": utc_now_iso(),
            "content_hash": _sha256_text(snippet),
            "source": {
                "source_system": canonical_document.get("source_system"),
                "source_type": canonical_document.get("source_type"),
                "source_account": metadata.get("source_account") or "",
                "title": metadata.get("title") or "",
                "published_at": metadata.get("published_at") or "",
                "source_url": metadata.get("source_url"),
                "canonical_url": metadata.get("canonical_url"),
            },
            "canonical_ref": {
                "document_id": canonical_document.get("document_id"),
                "path": canonical_path,
                "content_hash": canonical_document.get("content_hash"),
                "section_id": chunk.get("section_id"),
                "chunk_id": chunk.get("chunk_id"),
                "chunk_hash": chunk.get("text_hash"),
            },
            "raw_refs": raw_refs,
            "lineage": {
                "lineage_id": lineage.get("lineage_id"),
                "bridge_version": BRIDGE_VERSION,
                "source_kb_refs": lineage.get("source_kb_refs") or [],
            },
            "claim": {
                "text": _claim_text(snippet),
                "claim_type": "research_report_statement",
                "direction": direction,
                "direction_score": direction_score,
                "direction_inference": direction_meta,
                "scoring_role": scoring_role,
            },
            "snippet": {
                "text": snippet[:900],
                "section_heading": chunk.get("heading") or "",
                "char_start": None,
                "char_end": None,
            },
            "asset": asset,
            "theme": None,
            "conflict_set": {
                "conflict_set_id": None,
                "role": None,
                "status": "unassigned",
                "candidate_group_key": _hash_id(
                    "CONFLICT-CAND",
                    asset.get("name") if asset else "",
                    direction,
                    _claim_text(snippet)[:80],
                ),
            },
            "quality": {
                "confidence": 0.55 if asset else 0.35,
                "extraction_method": "rule_chunk_to_evidence_unit",
                "human_review_required": True,
            },
        }
        units.append(unit)
        if len(units) >= max_units:
            break
    return units


def _canonical_output_path(root: Path, date_key: str, document_id: str) -> Path:
    yyyy, mm, dd = dated_parts(date_key)
    return (
        root
        / "canonical_documents"
        / "research_reports"
        / "hzzhqx_wechat"
        / yyyy
        / mm
        / dd
        / f"{document_id}.json"
    )


def _evidence_output_path(root: Path, date_key: str, evidence_id: str) -> Path:
    yyyy, mm, dd = dated_parts(date_key)
    return (
        root
        / "evidence_store"
        / "evidence_capsules"
        / "research_reports"
        / "hzzhqx_wechat"
        / yyyy
        / mm
        / dd
        / f"{evidence_id}.json"
    )


def _run_manifest_path(root: Path, date_key: str, run_id: str) -> Path:
    yyyy, mm, dd = dated_parts(date_key)
    return (
        root
        / "agent_workspace"
        / "runs"
        / "research_reports"
        / "canonical_evidence_bridge"
        / yyyy
        / mm
        / dd
        / run_id
        / "run_manifest.json"
    )


def _run_id(work_order_id: str | None = None) -> str:
    prefix = re.sub(r"[^A-Z0-9_-]+", "-", str(work_order_id or "WO-DEV-20260620-005").upper())
    return f"RUN-{prefix}-CANONICAL-EVIDENCE-{datetime.now().strftime('%H%M%S')}"


def run_wechat_canonical_evidence_bridge(
    root: str | Path | None = None,
    *,
    date: str,
    raw_id: str | None = None,
    article_limit: int = 1,
    max_evidence_units_per_doc: int = 12,
    work_order_id: str = "WO-DEV-20260620-005",
) -> dict[str, Any]:
    root_path = quanta_data_root(root)
    date_key = _canonical_date(date)
    run_id = _run_id(work_order_id)
    manifest_path = _run_manifest_path(root_path, date_key, run_id)
    created_at = utc_now_iso()
    input_refs = _source_kb_artifact_refs()
    output_refs: list[dict[str, Any]] = []
    evidence_refs: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    canonical_paths: list[str] = []
    evidence_paths: list[str] = []

    try:
        taxonomy = load_asset_taxonomy(root_path)
        articles = load_wechat_bridge_articles(
            root_path,
            date=date_key,
            raw_id=raw_id,
            article_limit=article_limit,
        )
        if not articles:
            errors.append({"code": "no_articles", "message": f"date={date_key} no source articles"})
        for article in articles:
            canonical = build_canonical_document(article, root=root_path, taxonomy=taxonomy)
            canonical_path = _canonical_output_path(root_path, date_key, str(canonical["document_id"]))
            canonical_rel = relative_to_root(canonical_path, root_path)
            for ref in (canonical.get("lineage") or {}).get("input_refs") or []:
                input_refs.append(ref)
            canonical_ref = _ref(
                "canonical_document",
                canonical_path,
                root=root_path,
                artifact_id=str(canonical["document_id"]),
                content_hash=str(canonical["content_hash"]),
                lineage_id=str((canonical.get("lineage") or {}).get("lineage_id") or ""),
            )
            write_json(canonical_path, canonical)
            output_refs.append(canonical_ref)
            canonical_paths.append(canonical_rel)

            evidence_units = extract_evidence_units(
                canonical,
                canonical_path=canonical_rel,
                max_units=max_evidence_units_per_doc,
            )
            if not evidence_units:
                errors.append(
                    {
                        "code": "no_evidence_units",
                        "message": f"canonical document produced no evidence units: {canonical['document_id']}",
                        "artifact_ref": canonical_ref,
                    }
                )
            for unit in evidence_units:
                evidence_path = _evidence_output_path(root_path, date_key, str(unit["evidence_id"]))
                evidence_ref = _ref(
                    "evidence_capsule",
                    evidence_path,
                    root=root_path,
                    artifact_id=str(unit["evidence_id"]),
                    content_hash=str(unit["content_hash"]),
                    lineage_id=str((unit.get("lineage") or {}).get("lineage_id") or ""),
                )
                write_json(evidence_path, unit)
                output_refs.append(evidence_ref)
                evidence_refs.append(evidence_ref)
                evidence_paths.append(relative_to_root(evidence_path, root_path))
    except Exception as exc:
        errors.append({"code": exc.__class__.__name__, "message": str(exc)})

    status = "succeeded" if canonical_paths and evidence_paths and not errors else "partial"
    if not canonical_paths:
        status = "failed"
    run_manifest = {
        "schema_version": "agent_run_manifest.v1",
        "run_id": run_id,
        "run_type": "canonicalizer",
        "status": status,
        "created_at": created_at,
        "completed_at": utc_now_iso(),
        "trading_date": f"{date_key[:4]}-{date_key[4:6]}-{date_key[6:8]}",
        "owner_project": "quanta_agents",
        "agent": {
            "name": "wechat_canonical_evidence_bridge",
            "version": BRIDGE_VERSION,
            "git_commit": None,
            "runtime": "python",
        },
        "environment_ref": {
            "quanta_data_root_env": "GJ_QUANTA_DATA_ROOT",
            "platform_api_env": "GJ_PLATFORM_API_BASE",
            "server_role": "pipeline_agent",
        },
        "input_refs": _dedupe_refs(input_refs),
        "output_refs": _dedupe_refs(output_refs),
        "prompt_pack_ref": None,
        "evidence_refs": _dedupe_refs(evidence_refs),
        "candidate_refs": [],
        "review_package_ref": None,
        "model_refs": [],
        "config_refs": [],
        "logs": [],
        "human_review_required": True,
        "promotion_target": "candidate",
        "errors": errors,
    }
    write_json(manifest_path, run_manifest)
    return {
        "run_id": run_id,
        "run_manifest_path": str(manifest_path),
        "paths": {
            "run_manifest": relative_to_root(manifest_path, root_path),
            "canonical_documents": canonical_paths,
            "evidence_units": evidence_paths,
        },
        "status": status,
        "canonical_count": len(canonical_paths),
        "evidence_count": len(evidence_paths),
        "errors": errors,
        "run_manifest": run_manifest,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Canonicalize hzzhqx WeChat research reports and write evidence units."
    )
    parser.add_argument("--date", required=True, help="YYYYMMDD or YYYY-MM-DD")
    parser.add_argument("--quanta-root", help="Override quanta_data root.")
    parser.add_argument("--raw-id", help="Optional single RAW-HZZHQX-WECHAT id.")
    parser.add_argument("--article-limit", type=int, default=1, help="Small sample size when raw-id is omitted.")
    parser.add_argument("--max-evidence-units-per-doc", type=int, default=12)
    parser.add_argument("--work-order-id", default="WO-DEV-20260620-005")
    args = parser.parse_args(argv)
    result = run_wechat_canonical_evidence_bridge(
        args.quanta_root,
        date=args.date,
        raw_id=args.raw_id,
        article_limit=args.article_limit,
        max_evidence_units_per_doc=args.max_evidence_units_per_doc,
        work_order_id=args.work_order_id,
    )
    print(f"run_manifest={result['paths']['run_manifest']}")
    print(
        "status={status} canonical={canonical_count} evidence={evidence_count}".format(
            **result
        )
    )
    if result["errors"]:
        print(f"errors={len(result['errors'])}")


if __name__ == "__main__":
    main()
