"""marketplace 工具目录：把 adapters 包装成注册表里的 typed tools。

新增平台能力 = 在 adapter 加方法 + 这里加一行注册；节点与 LLM 只见工具名。
"""

import asyncio

from app.adapters import get_adapter
from app.domain.enums import RiskLevel
from app.tools.context import ToolContext
from app.tools.registry import ToolDefinition, register
from app.tools.schemas import (
    GetMarketplaceRulesInput,
    GetMarketplaceRulesOutput,
    PublishListingInput,
    PublishListingOutput,
    ValidateListingInput,
    ValidateListingOutput,
)


async def _get_rules(inp: GetMarketplaceRulesInput, ctx: ToolContext) -> dict:
    adapter = get_adapter(inp.marketplace)
    return {"marketplace": adapter.name, "rules": adapter.get_rules()}


async def _validate_listing(inp: ValidateListingInput, ctx: ToolContext) -> dict:
    adapter = get_adapter(inp.marketplace)
    errors = await asyncio.to_thread(adapter.validate_listing, inp.listing)
    return {"marketplace": adapter.name, "valid": not errors, "errors": errors}


async def _publish_listing(inp: PublishListingInput, ctx: ToolContext) -> dict:
    adapter = get_adapter(inp.marketplace)
    # 平台规则前置校验失败不算异常：返回 validation_failed 让上层（Critic/人工）处理
    errors = await asyncio.to_thread(adapter.validate_listing, inp.listing)
    if errors:
        return {
            "marketplace": adapter.name,
            "listing_id": "",
            "status": "validation_failed",
            "validation_errors": errors,
        }
    result = await adapter.publish_listing(inp.listing, inp.idempotency_key)
    return {
        "marketplace": result.marketplace,
        "listing_id": result.listing_id,
        "status": result.status,
        "validation_errors": [],
        "url": result.url,
    }


register(
    ToolDefinition(
        name="get_marketplace_rules",
        description="查询平台上架规则（标题长度/卖点数量/违禁词/费率/图片规范）",
        input_model=GetMarketplaceRulesInput,
        output_model=GetMarketplaceRulesOutput,
        risk_level=RiskLevel.low,
        timeout_s=5.0,
        handler=_get_rules,
    )
)
register(
    ToolDefinition(
        name="validate_listing",
        description="按平台规则校验 listing 草稿，返回违规明细",
        input_model=ValidateListingInput,
        output_model=ValidateListingOutput,
        risk_level=RiskLevel.low,
        timeout_s=5.0,
        handler=_validate_listing,
    )
)
register(
    ToolDefinition(
        name="publish_listing",
        description="将 listing 发布到平台。高风险动作，需审批；幂等（同 key 不重复发布）",
        input_model=PublishListingInput,
        output_model=PublishListingOutput,
        risk_level=RiskLevel.high,
        idempotent=True,
        requires_approval=True,
        timeout_s=15.0,
        handler=_publish_listing,
    )
)
