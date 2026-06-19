from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

from quanta_agents.core.config import first_env, quanta_data_root
from quanta_agents.core.io import read_json


DEFAULT_ACTIVE_RELATIVE = "gold/reference_data/assets/futures_assets.v1.json"
DEFAULT_CANDIDATE_RELATIVE = "agent_workspace/candidates/taxonomy/latest/futures_assets.v1.json"


@dataclass(frozen=True)
class TaxonomyHit:
    key: str
    label: str
    kind: str
    asset_id: str | None = None


class AssetTaxonomy:
    def __init__(self, payload: dict[str, Any], source_path: Path | None = None):
        self.payload = payload
        self.source_path = source_path
        self.assets: list[dict[str, Any]] = [
            item for item in payload.get("assets", []) if isinstance(item, dict)
        ]
        self.macro_buckets: list[dict[str, Any]] = [
            item for item in payload.get("macro_buckets", []) if isinstance(item, dict)
        ]
        self._asset_by_name = {
            str(asset.get("canonical_name")): asset
            for asset in self.assets
            if asset.get("canonical_name")
        }
        self._alias_to_asset: dict[str, dict[str, Any]] = {}
        for asset in self.assets:
            canonical = str(asset.get("canonical_name") or "").strip()
            if canonical:
                self._alias_to_asset[canonical] = asset
                if not _is_case_sensitive_short_code(canonical):
                    self._alias_to_asset[canonical.lower()] = asset
            for alias in asset.get("aliases") or []:
                text = str(alias).strip()
                if text:
                    self._alias_to_asset[text] = asset
                    if not _is_case_sensitive_short_code(text):
                        self._alias_to_asset[text.lower()] = asset

    @property
    def version(self) -> str:
        return str(self.payload.get("version") or "")

    def names(self) -> list[str]:
        return sorted(self._asset_by_name, key=len, reverse=True)

    def category_for(self, name: str) -> str:
        asset = self._asset_by_name.get(name)
        if not asset:
            asset = self._alias_to_asset.get(name) or self._alias_to_asset.get(name.lower())
        return str((asset or {}).get("category") or "其他")

    def sector_for(self, name: str) -> str:
        asset = self._asset_by_name.get(name)
        if not asset:
            asset = self._alias_to_asset.get(name) or self._alias_to_asset.get(name.lower())
        return str((asset or {}).get("sector") or "")

    def canonical_name(self, raw_name: str) -> str | None:
        raw = raw_name.strip()
        asset = self._asset_by_name.get(raw) or self._alias_to_asset.get(raw) or self._alias_to_asset.get(raw.lower())
        if not asset:
            return None
        return str(asset.get("canonical_name") or "").strip() or None

    def prompt_asset_list(self) -> str:
        lines = []
        for asset in sorted(self.assets, key=lambda item: str(item.get("canonical_name") or "")):
            name = str(asset.get("canonical_name") or "").strip()
            if not name:
                continue
            aliases = ", ".join(str(alias) for alias in asset.get("aliases", [])[:8])
            category = asset.get("category") or "其他"
            code = asset.get("exchange_code") or asset.get("commodity_code") or ""
            lines.append(f"- {name} ({code}, {category}) aliases: {aliases}")
        return "\n".join(lines)

    def classify(self, text: str) -> list[TaxonomyHit]:
        hits: dict[str, TaxonomyHit] = {}
        low = text.lower()
        for alias, asset in self._alias_to_asset.items():
            alias_text = str(alias)
            if not alias_text:
                continue
            alias_low = alias_text.lower()
            if not _contains_alias(text, low, alias_text, alias_low):
                continue
            name = str(asset.get("canonical_name") or "").strip()
            if not name:
                continue
            asset_id = str(asset.get("asset_id") or "")
            hits[f"V::{name}"] = TaxonomyHit(f"V::{name}", name, "variety", asset_id)
            sector = str(asset.get("sector") or "").strip()
            if sector:
                hits[f"S::{sector}"] = TaxonomyHit(f"S::{sector}", sector, "sector")
            category = str(asset.get("category") or "").strip()
            if category and category != sector:
                hits[f"C::{category}"] = TaxonomyHit(f"C::{category}", category, "category")

        for bucket in self.macro_buckets:
            bucket_id = str(bucket.get("bucket_id") or bucket.get("key") or "").strip()
            label = str(bucket.get("label") or "").strip()
            keywords = [str(item).strip() for item in bucket.get("keywords", []) if str(item).strip()]
            if bucket_id and label and any(
                _macro_keyword_hit(bucket_id, keyword, text, low) for keyword in keywords
            ):
                hits[f"M::{bucket_id}"] = TaxonomyHit(f"M::{bucket_id}", label, "macro")

        return list(hits.values())


def _contains_alias(text: str, low_text: str, alias_text: str, alias_low: str) -> bool:
    if _blocked_ambiguous_alias(alias_text, text):
        return False
    if _is_ascii_code(alias_text):
        flags = 0 if len(alias_text) <= 2 else re.IGNORECASE
        pattern = rf"(?<![A-Za-z0-9]){re.escape(alias_text)}(?![A-Za-z0-9])"
        return bool(re.search(pattern, text, flags))
    return alias_text in text or alias_low in low_text


def _is_ascii_code(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]*", value))


def _is_case_sensitive_short_code(value: str) -> bool:
    return _is_ascii_code(value) and len(value) <= 2


def _blocked_ambiguous_alias(alias_text: str, text: str) -> bool:
    alias = alias_text.strip()
    if alias == "苹果":
        commodity_context = (
            "苹果期货",
            "苹果主力",
            "果农",
            "果价",
            "冷库",
            "套袋",
            "产区",
            "销区",
            "客商",
            "走货",
            "新季",
            "替代水果",
        )
        company_context = (
            "AAPL",
            "iPhone",
            "iPad",
            "Mac",
            "AirPods",
            "iOS",
            "库克",
            "微软",
            "亚马逊",
            "特斯拉",
            "SpaceX",
            "应用商店",
            "App Store",
            "苹果公司",
        )
        return any(word in text for word in company_context) and not any(
            word in text for word in commodity_context
        )
    if alias == "玻璃":
        commodity_context = (
            "玻璃期货",
            "玻璃主力",
            "浮法",
            "沙河",
            "平板玻璃",
            "玻璃库存",
            "玻璃现货",
            "玻璃厂",
            "冷修",
            "纯碱",
        )
        tech_context = (
            "玻璃基板",
            "玻璃纤维",
            "AMOLED",
            "OLED",
            "TGV",
            "芯片封装",
            "半导体",
            "显示屏",
            "光模块",
            "二氧化硅",
            "信义玻璃",
            "旗滨集团",
        )
        return any(word in text for word in tech_context) and not any(
            word in text for word in commodity_context
        )
    if alias == "铅":
        commodity_context = (
            "沪铅",
            "铅期货",
            "铅价",
            "铅锭",
            "铅矿",
            "铅库存",
            "再生铅",
            "精铅",
            "LME",
            "伦敦金属交易所",
        )
        false_context = ("铅笔",)
        return any(word in text for word in false_context) and not any(
            word in text for word in commodity_context
        )
    if alias == "锡":
        commodity_context = (
            "沪锡",
            "锡期货",
            "锡价",
            "锡矿",
            "锡锭",
            "锡库存",
            "LME",
            "伦敦金属交易所",
        )
        false_context = ("无锡",)
        return any(word in text for word in false_context) and not any(
            word in text for word in commodity_context
        )
    return False


def _macro_keyword_hit(bucket_id: str, keyword: str, text: str, low_text: str) -> bool:
    keyword_low = keyword.lower()
    if bucket_id == "usd_fx":
        return _usd_fx_hit(keyword, keyword_low, text, low_text)
    if bucket_id == "fed_rate":
        return _fed_rate_hit(keyword, keyword_low, text, low_text)
    return keyword in text or keyword_low in low_text


def _usd_fx_hit(keyword: str, keyword_low: str, text: str, low_text: str) -> bool:
    direct_keywords = (
        "美元指数",
        "汇率",
        "人民币",
        "离岸人民币",
        "usdcny",
        "dxy",
    )
    if keyword in direct_keywords or keyword_low in direct_keywords:
        return keyword in text or keyword_low in low_text
    if keyword not in text and keyword_low not in low_text:
        return False
    currency_context = (
        "美元兑",
        "兑美元",
        "美债",
        "收益率",
        "外汇",
        "汇市",
        "美元走强",
        "美元走弱",
        "美元回落",
        "美元反弹",
        "美元指数",
        "usd/jpy",
        "usd/cnh",
        "usd/cny",
        "eur/usd",
        "gbp/usd",
    )
    commodity_units = ("美元/桶", "美元/盎司", "美元/吨", "美元/磅", "美元/百万英热")
    return any(item in text or item in low_text for item in currency_context) and not any(
        item in text for item in commodity_units
    )


def _fed_rate_hit(keyword: str, keyword_low: str, text: str, low_text: str) -> bool:
    if keyword not in text and keyword_low not in low_text:
        return False
    fed_context = (
        "美联储",
        "fomc",
        "fed",
        "鲍威尔",
        "沃什",
        "点阵图",
        "美国联邦基金",
    )
    generic_policy_words = ("降息", "加息", "利率决议", "rate cut", "rate hike")
    if keyword in generic_policy_words or keyword_low in generic_policy_words:
        return any(item in text or item in low_text for item in fed_context)
    return True


def taxonomy_candidates(root: str | Path | None = None) -> list[Path]:
    explicit = first_env("GJ_ASSET_TAXONOMY_PATH", "QUANTA_ASSET_TAXONOMY_PATH")
    candidates = [Path(explicit).expanduser()] if explicit else []
    base = quanta_data_root(root)
    candidates.extend(
        [
            base / DEFAULT_ACTIVE_RELATIVE,
            base / DEFAULT_CANDIDATE_RELATIVE,
            base / "gold/reference_data/asset_taxonomy/commodity_assets.v1.json",
        ]
    )
    return candidates


def load_asset_taxonomy(root: str | Path | None = None, *, required: bool = False) -> AssetTaxonomy:
    for path in taxonomy_candidates(root):
        if not path.exists():
            continue
        payload = read_json(path)
        if not isinstance(payload, dict):
            continue
        return AssetTaxonomy(payload, path)
    if required:
        searched = ", ".join(str(path) for path in taxonomy_candidates(root))
        raise FileNotFoundError(f"未找到标准资产 taxonomy 文件。已查找：{searched}")
    return AssetTaxonomy({"schema_version": "asset_taxonomy.v1", "assets": [], "macro_buckets": []}, None)
