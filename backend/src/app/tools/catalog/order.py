"""订单数据源工具目录（M6）：确定性 mock 数据，但走完整工具治理管线。

真实 OMS（订单管理系统）API 按同一 schema 替换实现即可，
节点与 LLM 不感知（PRD §12.5 扩展边界）。LLM 只见工具输出，不接触数据实现。
"""

from pydantic import BaseModel, Field

from app.domain.enums import RiskLevel
from app.tools.context import ToolContext
from app.tools.registry import ToolDefinition, register

# 确定性模拟订单库：真实环境替换为 OMS API 查询，schema 保持不变
_KNOWN_ORDERS: dict[str, dict] = {
    "ord_88123": {
        "status": "国际运输中",
        "eta_text": "3-5 个工作日",
        "payment_status": "已支付",
        "refund_eligible": True,
        "logistics": [
            {"ts": "2026-08-18 10:24", "event": "包裹已揽收", "location": "深圳转运中心"},
            {"ts": "2026-08-20 16:02", "event": "干线运输离港", "location": "盐田港"},
            {"ts": "2026-08-23 09:41", "event": "清关中", "location": "洛杉矶口岸"},
        ],
    },
    "ord_76501": {
        "status": "已签收",
        "eta_text": None,
        "payment_status": "已支付",
        "refund_eligible": False,
        "logistics": [
            {"ts": "2026-08-05 11:30", "event": "包裹已揽收", "location": "广州白云仓"},
            {"ts": "2026-08-09 08:15", "event": "国际运输抵达", "location": "悉尼口岸"},
            {"ts": "2026-08-12 14:50", "event": "末端派送完成，已签收", "location": "悉尼"},
        ],
    },
    "ord_90234": {
        "status": "已发货",
        "eta_text": "7-10 个工作日",
        "payment_status": "已支付",
        "refund_eligible": True,
        "logistics": [
            {"ts": "2026-08-21 09:05", "event": "订单出库发货", "location": "杭州保税仓"},
            {"ts": "2026-08-22 21:37", "event": "航班起飞", "location": "浦东国际机场"},
        ],
    },
    "ord_64877": {
        "status": "待支付",
        "eta_text": None,
        "payment_status": "未支付",
        "refund_eligible": None,
        "logistics": [],
    },
}


class GetOrderStatusInput(BaseModel):
    order_id: str = Field(min_length=3)


class LogisticsEvent(BaseModel):
    ts: str
    event: str
    location: str


class GetOrderStatusOutput(BaseModel):
    order_id: str
    found: bool
    status: str | None = None
    eta_text: str | None = None
    payment_status: str | None = None
    refund_eligible: bool | None = None
    logistics: list[LogisticsEvent] = []


async def _status(inp: GetOrderStatusInput, ctx: ToolContext) -> dict:
    record = _KNOWN_ORDERS.get(inp.order_id)
    if record is None:
        return {"order_id": inp.order_id, "found": False}
    return {
        "order_id": inp.order_id,
        "found": True,
        "status": record["status"],
        "eta_text": record["eta_text"],
        "payment_status": record["payment_status"],
        "refund_eligible": record["refund_eligible"],
        "logistics": [dict(e) for e in record["logistics"]],
    }


register(
    ToolDefinition(
        name="get_order_status",
        description=(
            "查询订单实时状态/物流轨迹/支付状态/退款资格（当前为模拟 OMS 数据源）；"
            "客服场景中订单事实一律以本工具返回为准，知识库/政策文档不得覆盖其返回的实时数据"
        ),
        input_model=GetOrderStatusInput,
        output_model=GetOrderStatusOutput,
        risk_level=RiskLevel.low,
        timeout_s=5.0,
        handler=_status,
    )
)
