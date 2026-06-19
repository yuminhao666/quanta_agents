from __future__ import annotations

import json

from quanta_agents.core.framework_audit import audit_active_frameworks


def test_audit_active_frameworks_builds_optimization_candidate(tmp_path, monkeypatch):
    taxonomy_path = tmp_path / "taxonomy.json"
    taxonomy_path.write_text(
        json.dumps(
            {
                "schema_version": "asset_taxonomy.v1",
                "assets": [{"canonical_name": "原油", "aliases": ["原油"]}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    framework_dir = tmp_path / "data_lake/active_knowledge/research_frameworks/commodities"
    framework_dir.mkdir(parents=True)
    (framework_dir / "futures.INE.crude_oil.json").write_text(
        json.dumps(
            {
                "schema_version": "research_framework.v1",
                "artifact_type": "research_framework",
                "framework_id": "fw_futures_ine_crude_oil",
                "asset_id": "futures.INE.crude_oil",
                "standard_name": "原油",
                "generic_name": "原油",
                "status": "active",
                "review_status": "pending_review",
                "core_dimensions": [
                    {
                        "dimension_id": "geo",
                        "dimension_name": "地缘政治",
                        "dimension_type": "geopolitics",
                    }
                ],
                "logic_templates": [],
                "prompt_pack_fragments": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("GJ_ASSET_TAXONOMY_PATH", str(taxonomy_path))

    result = audit_active_frameworks(tmp_path)

    assert result["summary"]["framework_count"] == 1
    assert result["summary"]["optimization_candidate_count"] == 1
    candidate = result["optimization_candidates"][0]
    assert candidate["proposed_payload"]["schema_version"] == "commodity_research_framework.v1"
    assert candidate["proposed_payload"]["core_dimensions"][0]["default_weight"] > 0
    assert candidate["proposed_payload"]["logic_templates"]
