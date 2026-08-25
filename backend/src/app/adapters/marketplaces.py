"""三个 Mock 平台适配器：接口相同、规则不同（PRD §5.4 / §12.4）。

差异点体现真实工程复杂度：字段约束、声明黑名单、佣金模型、图片规范各不相同。
发布幂等在 adapter 内部再做一层 memo（executor 层的 ToolCall 重放是第一道）。
M12 起发布成功后会把上架物推送到 mock 商城（shopverse），铺货效果看得见；
商城只是演示出口——不可达/禁用一律静默降级，绝不阻塞发布主链路。
"""

import logging
import secrets

import httpx

from app.adapters.base import (
    ImageSpec,
    MarketplaceRules,
    PublishResult,
    validate_against_rules,
)
from app.config import get_settings

logger = logging.getLogger(__name__)


async def _storefront_post(url: str, payload: dict) -> dict:
    """桥接 HTTP 单点：测试 monkeypatch 本函数即可，不必伪造 httpx 客户端。"""
    async with httpx.AsyncClient(timeout=2.0) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        return resp.json()


class _MockAdapterBase:
    name: str = "mock"
    _rules: MarketplaceRules
    _id_prefix: str = "mck"

    def __init__(self) -> None:
        self._memo: dict[str, PublishResult] = {}

    def get_rules(self) -> MarketplaceRules:
        return self._rules

    def validate_listing(self, listing: dict) -> list[str]:
        return validate_against_rules(listing, self._rules)

    async def publish_listing(self, listing: dict, idempotency_key: str) -> PublishResult:
        if idempotency_key in self._memo:  # 幂等重放：同 key 返回同一 listing_id
            return self._memo[idempotency_key]
        result = PublishResult(
            marketplace=self.name,
            listing_id=f"{self._id_prefix}_{secrets.token_hex(4)}",
            status="published",
        )
        self._memo[idempotency_key] = result
        await self._notify_storefront(listing, result)
        return result

    async def _notify_storefront(self, listing: dict, result: PublishResult) -> None:
        """M12：上架物 POST 到 mock 商城（幂等 upsert），成功则回填商品页 URL。
        未配置（空串）/商城不在线/超时 → 静默跳过，url 保持空串。"""
        base = get_settings().mock_marketplace_url
        if not base:
            return
        payload = {
            "listing_id": result.listing_id,
            "marketplace": self.name,
            "title": str(listing.get("title") or ""),
            "brand": str(listing.get("brand") or ""),
            "bullets": [str(b) for b in (listing.get("bullets") or [])],
            "claim": str(listing.get("claim") or ""),
            "description": str(listing.get("description") or ""),
            "keywords": [str(k) for k in (listing.get("keywords") or [])],
            "price_usd": listing.get("price_usd"),
            "sku": str(listing.get("sku") or ""),
            "workflow_id": str(listing.get("workflow_id") or ""),
        }
        try:
            data = await _storefront_post(f"{base.rstrip('/')}/api/v1/listings", payload)
            result.url = str((data or {}).get("url") or "")
        except Exception:  # noqa: BLE001 —— 演示出口的任何故障都不影响发布主链路
            logger.warning("mock marketplace unreachable (%s); skip storefront sync", base)

    async def get_orders(self, filters: dict | None = None) -> list[dict]:
        # M6 接缝：客服/运营的真实订单查询走这里；当前返回确定性演示数据
        return [
            {"order_id": "ord_88123", "status": "shipped", "days_in_transit": 9, "sku": "UBS-001"},
            {"order_id": "ord_88124", "status": "delivered", "days_in_transit": 6},
        ]

    async def get_performance(self, listing_id: str) -> dict:
        return {
            "listing_id": listing_id,
            "impressions": 12400,
            "clicks": 512,
            "conversion": 0.018,
            "orders": 9,
        }


class MockAmazonAdapter(_MockAdapterBase):
    """严格派：标题长度上限、五点描述必须恰好 5 条、白底主图、15% 佣金。"""

    name = "amazon"
    _id_prefix = "ama"
    _rules = MarketplaceRules(
        marketplace="amazon",
        title_max_length=200,
        bullets_min=5,
        bullets_max=5,
        required_fields=["title", "bullets", "claim"],
        prohibited_phrases=["保证", "100%", "治愈", "根治"],
        referral_fee_pct=15.0,
        image_spec=ImageSpec(main_count=1, main_background="white"),
    )


class MockShopifyAdapter(_MockAdapterBase):
    """宽松派：自有站，长标题可用、卖点数量自由、SEO 友好、费率最低。"""

    name = "shopify"
    _id_prefix = "shp"
    _rules = MarketplaceRules(
        marketplace="shopify",
        title_max_length=255,
        bullets_min=0,
        bullets_max=10,
        required_fields=["title"],
        prohibited_phrases=["治愈", "根治"],
        referral_fee_pct=2.9,
        image_spec=ImageSpec(main_background="free"),
    )


class MockTikTokShopAdapter(_MockAdapterBase):
    """内容派：短标题、最多 3 条卖点、营销声明检查最严格。"""

    name = "tiktok_shop"
    _id_prefix = "tts"
    _rules = MarketplaceRules(
        marketplace="tiktok_shop",
        title_max_length=100,
        bullets_min=1,
        bullets_max=3,
        required_fields=["title", "claim"],
        prohibited_phrases=["保证", "100%", "治愈", "根治", "最佳", "第一"],
        referral_fee_pct=5.0,
        image_spec=ImageSpec(main_count=3, main_background="free"),
    )
