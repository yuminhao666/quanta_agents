from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from typing import Any

from quanta_agents.core.config import quanta_data_root
from quanta_agents.core.io import write_json


FUTURES_CATEGORIES: dict[str, list[tuple[str, str, list[str]]]] = {
    "黑色金属": [
        ("RB", "螺纹钢", ["螺纹", "螺纹钢", "rebar"]),
        ("HC", "热轧卷板", ["热卷", "热轧", "热轧卷板"]),
        ("SS", "不锈钢", ["不锈钢"]),
        ("I", "铁矿石", ["铁矿", "铁矿石", "iron ore"]),
        ("JM", "焦煤", ["焦煤", "炼焦煤"]),
        ("J", "焦炭", ["焦炭"]),
        ("SF", "硅铁", ["硅铁"]),
        ("SM", "锰硅", ["锰硅"]),
        ("ZC", "动力煤", ["动力煤"]),
    ],
    "贵金属": [
        ("AU", "黄金", ["沪金", "黄金", "gold"]),
        ("AG", "白银", ["沪银", "白银", "white silver", "silver"]),
        ("PT", "铂金", ["铂", "铂金"]),
        ("PD", "钯金", ["钯", "钯金"]),
    ],
    "有色金属": [
        ("CU", "铜", ["沪铜", "伦铜", "精铜", "铜", "copper"]),
        ("AL", "铝", ["沪铝", "电解铝", "铝"]),
        ("ZN", "锌", ["沪锌", "锌"]),
        ("PB", "铅", ["沪铅", "铅"]),
        ("NI", "镍", ["沪镍", "镍"]),
        ("SN", "锡", ["沪锡", "锡"]),
        ("BC", "国际铜", ["国际铜"]),
        ("AO", "氧化铝", ["氧化铝"]),
    ],
    "新能源": [
        ("SI", "工业硅", ["工业硅"]),
        ("PS", "多晶硅", ["多晶硅"]),
        ("LC", "碳酸锂", ["碳酸锂"]),
    ],
    "能源化工": [
        ("SC", "原油", ["原油", "crude", "wti", "brent", "opec"]),
        ("FU", "燃料油", ["燃料油", "高硫燃料油"]),
        ("LU", "低硫燃料油", ["低硫燃料油", "低硫燃油"]),
        ("BU", "石油沥青", ["石油沥青", "沥青"]),
        ("PG", "液化石油气", ["液化石油气", "LPG"]),
        ("L", "聚乙烯", ["聚乙烯", "PE", "塑料"]),
        ("PP", "聚丙烯", ["聚丙烯", "PP"]),
        ("V", "聚氯乙烯", ["聚氯乙烯", "PVC"]),
        ("EG", "乙二醇", ["乙二醇", "MEG"]),
        ("EB", "苯乙烯", ["苯乙烯"]),
        ("BZ", "纯苯", ["纯苯"]),
        ("TA", "PTA", ["PTA"]),
        ("MA", "甲醇", ["甲醇"]),
        ("FG", "玻璃", ["玻璃"]),
        ("SA", "纯碱", ["纯碱"]),
        ("UR", "尿素", ["尿素"]),
        ("PF", "短纤", ["短纤"]),
        ("PX", "对二甲苯", ["对二甲苯", "PX"]),
        ("SH", "烧碱", ["烧碱"]),
        ("RU", "天然橡胶", ["天然橡胶", "橡胶"]),
        ("NR", "20号胶", ["20号胶", "20号橡胶"]),
        ("BR", "合成橡胶", ["合成橡胶", "丁二烯橡胶"]),
    ],
    "农产品": [
        ("A", "黄大豆1号", ["黄大豆1号", "豆一", "美豆"]),
        ("B", "黄大豆2号", ["黄大豆2号", "豆二"]),
        ("M", "豆粕", ["豆粕", "美豆粕"]),
        ("Y", "豆油", ["豆油"]),
        ("P", "棕榈油", ["棕榈", "棕榈油"]),
        ("C", "玉米", ["玉米"]),
        ("CS", "玉米淀粉", ["玉米淀粉"]),
        ("RR", "粳米", ["粳米"]),
        ("SR", "白糖", ["白糖"]),
        ("CF", "棉花", ["棉花"]),
        ("CY", "棉纱", ["棉纱"]),
        ("OI", "菜籽油", ["菜油", "菜籽油"]),
        ("RM", "菜籽粕", ["菜粕", "菜籽粕"]),
        ("PK", "花生", ["花生"]),
        ("AP", "苹果", ["苹果"]),
        ("CJ", "红枣", ["红枣"]),
        ("PM", "普麦", ["普麦"]),
        ("WH", "强麦", ["强麦"]),
        ("RI", "早籼稻", ["早籼稻"]),
        ("LR", "晚籼稻", ["晚籼稻"]),
        ("JR", "粳稻", ["粳稻"]),
        ("JD", "鸡蛋", ["鸡蛋"]),
        ("LH", "生猪", ["生猪"]),
        ("SP", "纸浆", ["纸浆"]),
    ],
    "金融期货": [
        ("IF", "沪深300股指", ["IF", "沪深300", "股指"]),
        ("IC", "中证500股指", ["IC", "中证500"]),
        ("IM", "中证1000股指", ["IM", "中证1000"]),
        ("IH", "上证50股指", ["IH", "上证50"]),
        ("TS", "2年期国债", ["2年期国债", "二债"]),
        ("TF", "5年期国债", ["5年期国债", "五债"]),
        ("T", "10年期国债", ["10年期国债", "十债", "国债"]),
        ("TL", "30年期国债", ["30年期国债", "三十债"]),
    ],
    "指数": [
        ("EC", "集装箱运价指数(欧线)", ["集运", "欧线", "集装箱运价指数(欧线)"]),
    ],
}

MACRO_BUCKETS = [
    {
        "bucket_id": "fed_rate",
        "label": "美联储与利率",
        "keywords": ["美联储", "FOMC", "鲍威尔", "降息", "加息", "利率决议", "点阵图", "fed", "rate cut", "rate hike"],
    },
    {
        "bucket_id": "inflation",
        "label": "通胀数据",
        "keywords": ["CPI", "PPI", "PCE", "通胀", "inflation", "核心通胀"],
    },
    {
        "bucket_id": "jobs",
        "label": "就业数据",
        "keywords": ["非农", "失业率", "就业", "初请失业金", "payroll", "jobless", "unemployment"],
    },
    {
        "bucket_id": "usd_fx",
        "label": "美元与汇率",
        "keywords": ["美元指数", "美元", "人民币", "汇率", "dxy", "usdcny", "离岸人民币"],
    },
    {
        "bucket_id": "tariff_trade",
        "label": "关税与贸易",
        "keywords": ["关税", "贸易", "制裁", "出口管制", "tariff", "trade", "sanction"],
    },
    {
        "bucket_id": "geopolitics",
        "label": "地缘政治",
        "keywords": ["地缘", "俄乌", "乌克兰", "中东", "以色列", "伊朗", "霍尔木兹", "战争", "停火", "袭击"],
    },
    {
        "bucket_id": "cb_overseas",
        "label": "海外央行",
        "keywords": ["欧洲央行", "日本央行", "ECB", "BOJ", "英国央行", "加息周期"],
    },
    {
        "bucket_id": "china_econ",
        "label": "中国经济政策",
        "keywords": ["PMI", "社融", "信贷", "政治局", "发改委", "LPR", "降准", "财政", "专项债"],
    },
    {
        "bucket_id": "energy_supply",
        "label": "能源供应",
        "keywords": ["OPEC", "减产", "增产", "EIA", "API", "原油库存", "页岩油"],
    },
]


def _sector(category: str) -> str:
    return {
        "有色金属": "有色",
        "能源化工": "能化",
        "黑色金属": "黑色",
    }.get(category, category)


def build_seed_payload() -> dict[str, Any]:
    assets = []
    for category, rows in FUTURES_CATEGORIES.items():
        for code, name, aliases in rows:
            assets.append(
                {
                    "asset_id": f"FUT-{code}",
                    "asset_class": "futures",
                    "canonical_name": name,
                    "display_name": name,
                    "commodity_code": code,
                    "exchange_code": code,
                    "category": category,
                    "sector": _sector(category),
                    "aliases": sorted(set(aliases + [name])),
                    "framework_ref": {"commodity_code": code},
                    "status": "active",
                }
            )
    return {
        "schema_version": "asset_taxonomy.v1",
        "status": "active",
        "asset_class": "futures",
        "version": "2026-06-17-bootstrap",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "governance": {
            "active_path": "gold/reference_data/assets/futures_assets.v1.json",
            "candidate_path": "agent_workspace/candidates/taxonomy/latest/futures_assets.v1.json",
            "rule": "Agent runtime reads this file; taxonomy changes should be proposed as candidates before promotion.",
        },
        "assets": assets,
        "macro_buckets": MACRO_BUCKETS,
    }


def bootstrap_taxonomy(root: str | Path | None = None, *, force: bool = False) -> Path:
    root_path = quanta_data_root(root)
    output = root_path / "gold" / "reference_data" / "assets" / "futures_assets.v1.json"
    if output.exists() and not force:
        return output
    write_json(output, build_seed_payload())
    return output


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Bootstrap futures asset taxonomy into quanta_data.")
    parser.add_argument("--quanta-root", help="Override GJ_QUANTA_DATA_ROOT.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing taxonomy file.")
    args = parser.parse_args(argv)
    path = bootstrap_taxonomy(args.quanta_root, force=args.force)
    print(f"标准资产 taxonomy 已写入：{path}")


if __name__ == "__main__":
    main()
