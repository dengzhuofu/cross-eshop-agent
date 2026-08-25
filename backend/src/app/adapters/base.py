"""MarketplaceAdapter 协议与平台规则模型（PRD §12.3）。

三个 mock 实现同一协议、内部规则不同；未来接真实平台（Shopify Admin GraphQL 等）
时按同协议替换实现即可，调用方不感知（PRD §12.5 的扩展边界）。
"""

from typing import Protocol, runtime_checkable

from pydantic import BaseModel


class ImageSpec(BaseModel):
    """平台图片规范（生图 Phase 2 接缝，v1.4 §1.1；MVP 仅作为规则数据存在）。"""

    main_count: int = 1
    main_background: str = "white"  # white | free
    allow_watermark: bool = False


class MarketplaceRules(BaseModel):
    marketplace: str
    title_max_length: int
    bullets_min: int
    bullets_max: int
    required_fields: list[str]
    prohibited_phrases: list[str]
    referral_fee_pct: float
    image_spec: ImageSpec


class PublishResult(BaseModel):
    marketplace: str
    listing_id: str
    status: str = "published"
    # M12：mock 商城商品页 URL（商城不可达/禁用时为空串，契约向后兼容）
    url: str = ""


def validate_against_rules(listing: dict, rules: MarketplaceRules) -> list[str]:
    """通用校验器：各 adapter 用自己的规则实例调用，保证“接口相同、规则不同”。"""
    errors: list[str] = []
    title = str(listing.get("title") or "")
    bullets = [str(b) for b in (listing.get("bullets") or [])]
    if len(title) > rules.title_max_length:
        errors.append(f"title exceeds {rules.title_max_length} chars ({len(title)})")
    if len(bullets) < rules.bullets_min:
        errors.append(f"bullets below minimum ({len(bullets)} < {rules.bullets_min})")
    if len(bullets) > rules.bullets_max:
        errors.append(f"bullets above maximum ({len(bullets)} > {rules.bullets_max})")
    for field in rules.required_fields:
        if not listing.get(field):
            errors.append(f"missing required field: {field}")
    text = " ".join([title, *bullets, str(listing.get("claim") or "")])
    for phrase in rules.prohibited_phrases:
        if phrase in text:
            errors.append(f"prohibited phrase: {phrase}")
    return errors


@runtime_checkable
class MarketplaceAdapter(Protocol):
    name: str

    def get_rules(self) -> MarketplaceRules: ...

    def validate_listing(self, listing: dict) -> list[str]: ...

    async def publish_listing(self, listing: dict, idempotency_key: str) -> PublishResult: ...

    async def get_orders(self, filters: dict | None = None) -> list[dict]: ...

    async def get_performance(self, listing_id: str) -> dict: ...
