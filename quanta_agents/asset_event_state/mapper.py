from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from quanta_agents.asset_event_state.models import clean_text, normalize_asset, normalize_event_type


DEFAULT_ASSET_SCHEMAS: dict[str, dict[str, Any]] = {
    "Copper": {
        "nodes": {
            "Macro": ["美元", "dxy", "fed", "美联储", "利率", "通胀", "汇率", "macro"],
            "Supply": ["铜矿", "矿山", "冶炼", "tc", "精炼铜", "罢工", "减产", "供应", "supply"],
            "Demand": ["需求", "消费", "订单", "地产", "电网", "新能源", "制造业", "demand"],
            "Inventory": ["库存", "仓单", "lme库存", "comex库存", "上期所库存", "去库", "累库"],
            "China demand": ["中国需求", "国内需求", "社融", "基建", "地产", "家电"],
            "Policy": ["政策", "关税", "进口", "出口", "制裁", "tariff", "policy"],
            "Sentiment": ["情绪", "资金", "多头", "空头", "持仓", "升水", "贴水", "sentiment"],
            "Data": ["库存", "价格", "持仓", "升贴水", "tc", "产量", "开工率", "数据", "指标", "data"],
            "Research": ["研报", "观点", "预期", "判断", "测算", "research", "report"],
        },
        "event_type_nodes": {
            "policy": "Policy",
            "macro": "Macro",
            "supply": "Supply",
            "demand": "Demand",
            "sentiment": "Sentiment",
            "data": "Data",
            "research": "Research",
        },
    },
    "Gold": {
        "nodes": {
            "Macro": ["美元", "dxy", "fed", "美联储", "利率", "通胀", "实际利率", "macro"],
            "Policy": ["央行", "政策", "财政", "地缘", "制裁"],
            "Demand": ["金饰", "央行购金", "etf", "避险需求", "需求"],
            "Supply": ["矿产金", "回收金", "供应"],
            "Sentiment": ["避险", "情绪", "多头", "空头", "持仓"],
            "Data": ["库存", "etf", "持仓", "价格", "数据", "指标"],
            "Research": ["研报", "观点", "预期", "判断", "research", "report"],
        },
        "event_type_nodes": {
            "policy": "Policy",
            "macro": "Macro",
            "supply": "Supply",
            "demand": "Demand",
            "sentiment": "Sentiment",
            "data": "Data",
            "research": "Research",
        },
    },
    "Oil": {
        "nodes": {
            "Macro": ["美元", "利率", "通胀", "经济", "macro"],
            "Supply": ["opec", "欧佩克", "减产", "增产", "库存", "页岩油", "供应"],
            "Demand": ["需求", "炼厂", "出行", "航空", "消费"],
            "Policy": ["制裁", "政策", "关税", "出口限制"],
            "Sentiment": ["风险偏好", "情绪", "基金持仓", "多头", "空头"],
            "Data": ["库存", "产量", "钻机", "出口", "价格", "数据", "指标"],
            "Research": ["研报", "观点", "预期", "判断", "research", "report"],
        },
        "event_type_nodes": {
            "policy": "Policy",
            "macro": "Macro",
            "supply": "Supply",
            "demand": "Demand",
            "sentiment": "Sentiment",
            "data": "Data",
            "research": "Research",
        },
    },
    "Macro": {
        "nodes": {
            "Liquidity": ["流动性", "降息", "加息", "货币", "财政", "liquidity"],
            "Growth": ["增长", "衰退", "就业", "pmi", "gdp", "消费"],
            "Inflation": ["通胀", "cpi", "ppi", "油价", "工资"],
            "Policy": ["政策", "央行", "美联储", "财政", "监管"],
            "Sentiment": ["风险偏好", "波动率", "避险", "情绪"],
            "Data": ["数据", "指标", "cpi", "ppi", "pmi", "gdp", "就业"],
            "Research": ["研报", "观点", "预期", "判断", "research", "report"],
        },
        "event_type_nodes": {
            "policy": "Policy",
            "macro": "Liquidity",
            "supply": "Growth",
            "demand": "Growth",
            "sentiment": "Sentiment",
            "data": "Data",
            "research": "Research",
        },
    },
    "Other": {
        "nodes": {
            "General": [],
            "Policy": ["政策", "监管", "policy"],
            "Sentiment": ["情绪", "资金", "sentiment"],
            "Data": ["数据", "指标", "data"],
            "Research": ["研报", "观点", "research", "report"],
        },
        "event_type_nodes": {
            "policy": "Policy",
            "macro": "General",
            "supply": "General",
            "demand": "General",
            "sentiment": "Sentiment",
            "data": "Data",
            "research": "Research",
        },
    },
}

ASSET_KEYWORDS = {
    "Copper": ["铜", "沪铜", "伦铜", "lme铜", "comex铜", "美铜", "copper", "cu", "hg"],
    "Gold": ["黄金", "金价", "comex金", "gold", "xau", "au"],
    "Oil": ["原油", "布伦特", "brent", "wti", "crude", "oil", "燃油"],
    "Macro": ["美联储", "美元", "利率", "通胀", "cpi", "pmi", "gdp", "宏观", "fed"],
}

EVENT_TYPE_KEYWORDS = {
    "policy": ["政策", "关税", "监管", "制裁", "出口限制", "进口", "tariff", "sanction", "policy"],
    "macro": ["美联储", "美元", "利率", "通胀", "cpi", "pmi", "gdp", "就业", "macro", "fed"],
    "supply": ["供应", "减产", "增产", "矿山", "冶炼", "罢工", "停产", "库存", "supply", "production"],
    "demand": ["需求", "消费", "订单", "补库", "地产", "基建", "制造业", "demand", "consumption"],
    "sentiment": [
        "情绪",
        "风险偏好",
        "上涨",
        "下跌",
        "升水",
        "贴水",
        "持仓",
        "多头",
        "空头",
        "净多",
        "净空",
        "仓位",
        "sentiment",
        "positioning",
    ],
    "data": ["数据", "指标", "库存", "价格", "持仓", "升贴水", "开工率", "产量", "data", "indicator"],
    "research": ["研报", "报告", "观点", "预期", "判断", "测算", "research", "report"],
}


class EventMapper:
    def __init__(self, quanta_root: str | Path | None = None):
        self.quanta_root = Path(quanta_root).expanduser() if quanta_root else None
        self.asset_schemas = self._load_asset_schemas()

    def classify_asset(self, text: Any, hint: Any = "") -> str:
        hinted = normalize_asset(hint)
        if hinted != "Other":
            return hinted
        normalized_text = clean_text(text).lower()
        for asset, keywords in ASSET_KEYWORDS.items():
            if any(keyword.lower() in normalized_text for keyword in keywords):
                return asset
        return "Other"

    def classify_event_type(self, text: Any, hint: Any = "") -> str:
        raw_hint = clean_text(hint).lower()
        if raw_hint in EVENT_TYPE_KEYWORDS:
            return normalize_event_type(raw_hint)
        normalized_text = clean_text(text).lower()
        scores = {
            event_type: sum(1 for keyword in keywords if keyword.lower() in normalized_text)
            for event_type, keywords in EVENT_TYPE_KEYWORDS.items()
        }
        best_type, best_score = max(scores.items(), key=lambda item: item[1])
        return best_type if best_score > 0 else "sentiment"

    def map_nodes(self, *, asset: str, event_type: str, text: Any) -> list[dict[str, Any]]:
        asset = normalize_asset(asset)
        event_type = normalize_event_type(event_type)
        schema = self.asset_schemas.get(asset) or self.asset_schemas["Other"]
        normalized_text = clean_text(text).lower()
        matches: list[dict[str, Any]] = []

        for node_name, keywords in schema.get("nodes", {}).items():
            hit_terms = [keyword for keyword in keywords if str(keyword).lower() in normalized_text]
            if not hit_terms:
                continue
            matches.append(
                {
                    "node": self.node_id(asset, node_name),
                    "node_name": node_name,
                    "confidence": round(min(1.0, 0.55 + len(hit_terms) * 0.1), 4),
                    "matched_terms": hit_terms[:8],
                }
            )

        fallback_name = schema.get("event_type_nodes", {}).get(event_type) or "General"
        fallback_node = self.node_id(asset, fallback_name)
        if not matches:
            return [
                {
                    "node": fallback_node,
                    "node_name": fallback_name,
                    "confidence": 0.5,
                    "matched_terms": [],
                }
            ]
        if all(item["node"] != fallback_node for item in matches):
            matches.append(
                {
                    "node": fallback_node,
                    "node_name": fallback_name,
                    "confidence": 0.45,
                    "matched_terms": [],
                }
            )
        return sorted(matches, key=lambda item: item["confidence"], reverse=True)[:5]

    def node_id(self, asset: str, node_name: str) -> str:
        normalized_node = re.sub(r"[^A-Za-z0-9]+", "", str(node_name).title()) or "General"
        return f"{normalize_asset(asset)}.schema.{normalized_node}"

    def describe_schema(self) -> dict[str, Any]:
        return {
            "schema_source": self.schema_source,
            "assets": self.asset_schemas,
            "asset_keywords": ASSET_KEYWORDS,
            "event_type_keywords": EVENT_TYPE_KEYWORDS,
        }

    def _load_asset_schemas(self) -> dict[str, dict[str, Any]]:
        self.schema_source = "builtin"
        schemas = json.loads(json.dumps(DEFAULT_ASSET_SCHEMAS, ensure_ascii=False))
        if not self.quanta_root:
            return schemas
        config_path = self.quanta_root / "configs" / "asset_event_schemas.v1.json"
        if not config_path.exists():
            return schemas
        try:
            external = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return schemas
        if not isinstance(external, dict):
            return schemas
        for asset, config in external.items():
            if not isinstance(config, dict):
                continue
            base = schemas.setdefault(str(asset), {"nodes": {}, "event_type_nodes": {}})
            if isinstance(config.get("nodes"), dict):
                base.setdefault("nodes", {}).update(config["nodes"])
            if isinstance(config.get("event_type_nodes"), dict):
                base.setdefault("event_type_nodes", {}).update(config["event_type_nodes"])
        self.schema_source = str(config_path)
        return schemas
