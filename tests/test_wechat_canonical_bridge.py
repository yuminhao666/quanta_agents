from __future__ import annotations

import json

from quanta_agents.core.io import read_json, write_json
from quanta_agents.research_reports.wechat_canonical_bridge import (
    run_wechat_canonical_evidence_bridge,
)


RAW_ID = "RAW-HZZHQX-WECHAT-TEST-BRIDGE"


def _write_taxonomy(root):
    path = root / "gold/reference_data/assets/futures_assets.v1.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "asset_taxonomy.v1",
                "assets": [
                    {
                        "asset_id": "FUT-SC",
                        "canonical_name": "原油",
                        "commodity_code": "SC",
                        "aliases": ["原油", "霍尔木兹"],
                    },
                    {
                        "asset_id": "FUT-MA",
                        "canonical_name": "甲醇",
                        "commodity_code": "MA",
                        "aliases": ["甲醇", "MTO"],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _write_raw_article(root):
    raw_dir = root / f"raw_objects/web_pages/hzzhqx_wechat/测试期货/2026/06/18/{RAW_ID}"
    raw_dir.mkdir(parents=True)
    manifest = {
        "raw_id": RAW_ID,
        "title": "测试期货早参-20260618",
        "source_account": "测试期货",
        "source_display_name": "测试期货公众号",
        "published_at": "2026-06-18 08:30:00",
        "source_url": "https://example.com/wechat/raw",
        "canonical_url": "https://example.com/wechat/canonical",
    }
    write_json(raw_dir / "raw_manifest.json", manifest)
    write_json(root / f"raw_manifests/by_date/2026/06/18/{RAW_ID}.json", manifest)
    (raw_dir / "extracted_text.txt").write_text(
        "\n".join(
            [
                "编辑日期：2026/6/18",
                "原油",
                "霍尔木兹海峡通航恢复，地缘风险溢价回吐，原油供应担忧下降，短期油价承压。",
                "风险提示：海外地缘局势反复。",
                "甲醇",
                "港口库存开始累积，MTO开工疲软，需求端驱动不足，甲醇价格整体或继续承压。",
                "免责声明 本报告不构成投资建议。",
            ]
        ),
        encoding="utf-8",
    )


def test_wechat_bridge_writes_canonical_document_evidence_and_run_manifest(tmp_path):
    _write_taxonomy(tmp_path)
    _write_raw_article(tmp_path)

    result = run_wechat_canonical_evidence_bridge(
        tmp_path,
        date="20260618",
        raw_id=RAW_ID,
        max_evidence_units_per_doc=3,
    )

    assert result["status"] == "succeeded"
    assert result["canonical_count"] == 1
    assert result["evidence_count"] >= 1

    canonical_path = tmp_path / result["paths"]["canonical_documents"][0]
    canonical = read_json(canonical_path)
    assert canonical["schema_version"] == "canonical_research_report_document.v1"
    assert canonical["metadata"]["raw_id"] == RAW_ID
    assert canonical["metadata"]["section_count"] >= 2
    assert canonical["metadata"]["chunk_count"] >= 2
    assert canonical["content_hash"]
    assert canonical["sections"]
    assert canonical["chunks"]
    assert any("甲醇" in chunk["text"] for chunk in canonical["chunks"])

    lineage = canonical["lineage"]
    raw_manifest_paths = [
        ref["path"] for ref in lineage["input_refs"] if ref["ref_type"] == "raw_manifest"
    ]
    assert f"raw_manifests/by_date/2026/06/18/{RAW_ID}.json" in raw_manifest_paths
    assert any(path.startswith("raw_objects/web_pages/hzzhqx_wechat/") for path in raw_manifest_paths)
    assert any(ref["ref_type"] == "raw_object" for ref in lineage["input_refs"])
    assert any("KMS-20260619-011" in ref["id"] for ref in lineage["source_kb_refs"])

    evidence_path = tmp_path / result["paths"]["evidence_units"][0]
    evidence = read_json(evidence_path)
    assert evidence["schema_version"] == "research_report_evidence_unit.v1"
    assert evidence["status"] == "candidate"
    assert evidence["canonical_ref"]["document_id"] == canonical["document_id"]
    assert evidence["canonical_ref"]["path"] == result["paths"]["canonical_documents"][0]
    assert evidence["raw_refs"]
    assert evidence["claim"]["text"]
    assert evidence["snippet"]["text"]
    assert set(evidence["conflict_set"]) == {
        "conflict_set_id",
        "role",
        "status",
        "candidate_group_key",
    }
    assert evidence["quality"]["human_review_required"] is True

    run_manifest = read_json(tmp_path / result["paths"]["run_manifest"])
    assert run_manifest["schema_version"] == "agent_run_manifest.v1"
    assert run_manifest["run_type"] == "canonicalizer"
    assert run_manifest["status"] == "succeeded"
    assert run_manifest["human_review_required"] is True
    assert any(ref["ref_type"] == "canonical_document" for ref in run_manifest["output_refs"])
    assert any(ref["ref_type"] == "evidence_capsule" for ref in run_manifest["output_refs"])
    assert any(ref["id"] == "TP-KBM-20260620-002" for ref in run_manifest["input_refs"])
