from __future__ import annotations

import json

from quanta_agents.core.taxonomy import load_asset_taxonomy


def test_taxonomy_short_ascii_aliases_require_boundaries_and_case(tmp_path, monkeypatch):
    taxonomy_path = tmp_path / "futures_assets.v1.json"
    taxonomy_path.write_text(
        json.dumps(
            {
                "schema_version": "asset_taxonomy.v1",
                "assets": [
                    {
                        "asset_id": "FUT-L",
                        "canonical_name": "聚乙烯",
                        "aliases": ["PE", "聚乙烯"],
                    },
                    {
                        "asset_id": "FUT-IC",
                        "canonical_name": "中证500股指",
                        "aliases": ["IC", "中证500"],
                    },
                    {
                        "asset_id": "FUT-AP",
                        "canonical_name": "苹果",
                        "aliases": ["苹果"],
                    },
                    {
                        "asset_id": "FUT-FG",
                        "canonical_name": "玻璃",
                        "aliases": ["玻璃"],
                    },
                    {
                        "asset_id": "FUT-PB",
                        "canonical_name": "铅",
                        "aliases": ["铅", "沪铅"],
                    },
                    {
                        "asset_id": "FUT-SN",
                        "canonical_name": "锡",
                        "aliases": ["锡", "沪锡"],
                    },
                ],
                "macro_buckets": [
                    {
                        "bucket_id": "usd_fx",
                        "label": "美元与汇率",
                        "keywords": ["美元指数", "美元", "汇率"],
                    },
                    {
                        "bucket_id": "fed_rate",
                        "label": "美联储与利率",
                        "keywords": ["美联储", "加息", "利率决议"],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("GJ_ASSET_TAXONOMY_PATH", str(taxonomy_path))
    taxonomy = load_asset_taxonomy(tmp_path)

    assert not taxonomy.classify("Iranian officials repeated peace efforts.")
    assert not taxonomy.classify("if market liquidity improves")
    assert not taxonomy.classify("苹果(AAPL.O)将在巴西增加替代应用商店选项。")
    assert not taxonomy.classify("SpaceX成交额已超过微软、特斯拉和苹果成交额总和。")
    assert not taxonomy.classify("消息称台积电推进玻璃基板开发计划。")
    assert not taxonomy.classify("宏和科技主营电子级玻璃纤维布，TGV玻璃仍在研发。")
    assert not taxonomy.classify("点阵图是用铅笔画的，可以擦掉。")
    assert not taxonomy.classify("无锡市企业签署无人物流车采购协议。")
    assert not taxonomy.classify("WTI原油现报79美元/桶。")
    assert not taxonomy.classify("日本央行加息25个基点。")
    assert {hit.label for hit in taxonomy.classify("PE装置检修，聚乙烯供应收缩")} == {"聚乙烯"}
    assert {hit.label for hit in taxonomy.classify("IC主力合约走强，中证500反弹")} == {"中证500股指"}
    assert {hit.label for hit in taxonomy.classify("苹果期货新季套袋量上升")} == {"苹果"}
    assert {hit.label for hit in taxonomy.classify("玻璃期货库存累积，沙河成交转弱")} == {"玻璃"}
    assert {hit.label for hit in taxonomy.classify("沪铅库存减少，铅价震荡")} == {"铅"}
    assert {hit.label for hit in taxonomy.classify("沪锡库存减少，锡价震荡")} == {"锡"}
    assert {hit.label for hit in taxonomy.classify("美联储点阵图显示鹰派加息路径")} == {"美联储与利率"}
    assert {hit.label for hit in taxonomy.classify("美元指数走强，人民币汇率承压")} == {"美元与汇率"}
