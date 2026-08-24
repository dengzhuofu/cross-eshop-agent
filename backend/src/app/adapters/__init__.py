"""adapter 注册表：按名称取平台实现。新增平台 = 新增一个类 + 一行注册。"""

from app.adapters.base import MarketplaceAdapter
from app.adapters.marketplaces import (
    MockAmazonAdapter,
    MockShopifyAdapter,
    MockTikTokShopAdapter,
)


class UnknownMarketplaceError(Exception):
    pass


ADAPTERS: dict[str, MarketplaceAdapter] = {
    MockAmazonAdapter.name: MockAmazonAdapter(),
    MockShopifyAdapter.name: MockShopifyAdapter(),
    MockTikTokShopAdapter.name: MockTikTokShopAdapter(),
}


def get_adapter(name: str) -> MarketplaceAdapter:
    try:
        return ADAPTERS[name]
    except KeyError as exc:
        raise UnknownMarketplaceError(f"unknown marketplace: {name}") from exc


__all__ = ["ADAPTERS", "MarketplaceAdapter", "UnknownMarketplaceError", "get_adapter"]
