"""工具 IO schema（typed tools，PRD §7.1）。

铁律：工具的输入/输出必须过 pydantic 模型校验——LLM 产出的参数先验 schema 再执行，
输出同样校验后才允许进入状态与审计。工具永不接收 tenant_id 参数（租户由 executor 注入）。
"""

from pydantic import BaseModel, Field

from app.adapters.base import MarketplaceRules


class GetMarketplaceRulesInput(BaseModel):
    marketplace: str = Field(min_length=1)


class GetMarketplaceRulesOutput(BaseModel):
    marketplace: str
    rules: MarketplaceRules


class ValidateListingInput(BaseModel):
    marketplace: str = Field(min_length=1)
    listing: dict


class ValidateListingOutput(BaseModel):
    marketplace: str
    valid: bool
    errors: list[str]


class PublishListingInput(BaseModel):
    marketplace: str = Field(min_length=1)
    workflow_id: str = Field(min_length=1)
    listing: dict
    idempotency_key: str = Field(min_length=8)


class PublishListingOutput(BaseModel):
    marketplace: str
    listing_id: str = ""
    status: str = "published"  # published | validation_failed
    validation_errors: list[str] = []
    url: str = ""  # M12：mock 商城商品页 URL（未桥接/失败时为空）
