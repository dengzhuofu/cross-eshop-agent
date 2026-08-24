"""利润测算工具目录（M3）：确定性商业计算进治理管线。

平台佣金率来自 MarketplaceAdapter 规则（同一真源），节点与 LLM 不做算术——
LLM 只解读结果文本（PRD §7.3：数字必须由确定性代码算出）。
"""

from pydantic import BaseModel, Field

from app.adapters import get_adapter
from app.domain.enums import RiskLevel
from app.tools.context import ToolContext
from app.tools.registry import ToolDefinition, register


class EstimateProfitInput(BaseModel):
    marketplace: str = Field(min_length=1, description="主渠道（决定佣金率）")
    sale_price_usd: float = Field(gt=0, le=10000)
    supplier_price_usd: float = Field(ge=0, le=10000, description="采购价")
    inbound_freight_usd: float = Field(default=3.0, ge=0, description="头程运费/件")
    fulfillment_usd: float = Field(default=4.5, ge=0, description="尾程配送费/件")
    ads_budget_usd: float = Field(default=3.0, ge=0, description="广告预算摊销/件")
    return_rate: float = Field(default=0.04, ge=0, lt=1)


class EstimateProfitOutput(BaseModel):
    marketplace: str
    assumptions: dict
    platform_fee_pct: float
    total_cost_usd: float
    contribution_profit_usd: float
    margin_pct: float
    break_even_price_usd: float


async def _estimate_profit(inp: EstimateProfitInput, ctx: ToolContext) -> dict:
    rules = get_adapter(inp.marketplace).get_rules()
    # adapter 的 referral_fee_pct 是百分数（15.0 = 15%）
    fee_ratio = rules.referral_fee_pct / 100
    landed_cost = inp.supplier_price_usd + inp.inbound_freight_usd
    platform_fee = round(inp.sale_price_usd * fee_ratio, 2)
    # 退货损耗按客单成本比例折算进总成本（简化模型，M4 记忆接入后用类目真实退货率）
    return_loss = round((landed_cost + inp.fulfillment_usd) * inp.return_rate, 2)
    total_cost = round(
        landed_cost + platform_fee + inp.fulfillment_usd + inp.ads_budget_usd + return_loss,
        2,
    )
    contribution = round(inp.sale_price_usd - total_cost, 2)
    margin = round(contribution / inp.sale_price_usd, 4)
    return {
        "marketplace": inp.marketplace,
        "assumptions": {
            "sale_price": inp.sale_price_usd,
            "supplier_price": inp.supplier_price_usd,
            "inbound_freight": inp.inbound_freight_usd,
            "platform_fee": platform_fee,
            "fulfillment": inp.fulfillment_usd,
            "ads_budget": inp.ads_budget_usd,
            "return_loss": return_loss,
            "return_rate": inp.return_rate,
        },
        "platform_fee_pct": rules.referral_fee_pct,
        "total_cost_usd": total_cost,
        "contribution_profit_usd": contribution,
        "margin_pct": margin,
        "break_even_price_usd": round(total_cost / (1 - fee_ratio), 2),
    }


register(
    ToolDefinition(
        name="estimate_profit",
        description="贡献利润测算：平台佣金取自 adapter 规则，输出总成本/贡献利润/盈亏平衡价",
        input_model=EstimateProfitInput,
        output_model=EstimateProfitOutput,
        risk_level=RiskLevel.low,
        timeout_s=5.0,
        handler=_estimate_profit,
    )
)
