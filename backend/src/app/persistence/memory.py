"""内存版仓储兜底：仅用于 `langgraph dev` 独立调试图（无 DB）场景。

ToolExecutor 的调用通道保持唯一——没有 Postgres 时用内存实现顶上，
保证"所有工具调用必过 executor"这条铁律在任何运行形态下都不破例。
"""

from typing import Any


class _PassRef:
    """跨租户引用检测的放行对象（dev 模式没有真实 workflow 行）。"""

    tenant_id: str = ""
    id: str = ""

    def __init__(self, tenant_id: str, wid: str) -> None:
        self.tenant_id = tenant_id
        self.id = wid


class MemoryWorkflowRepository:
    """只实现 executor 依赖的三个方法；审计行存内存，进程退出即弃。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def get(self, tenant_id: str, workflow_id: str):
        return _PassRef(tenant_id, workflow_id)

    async def find_tool_output_by_idempotency(
        self, tenant_id: str, idempotency_key: str
    ) -> dict | None:
        for c in reversed(self.calls):
            if (
                c["tenant_id"] == tenant_id
                and c.get("idempotency_key") == idempotency_key
                and c["status"] == "ok"
            ):
                return c.get("output_summary")
        return None

    async def record_tool_call(
        self,
        tenant_id: str,
        tool: str,
        status: str,
        call_id: str,
        workflow_id: str | None = None,
        risk_level: str = "low",
        idempotency_key: str | None = None,
        input_summary: dict | None = None,
        output_summary: dict | None = None,
        error: str | None = None,
        latency_ms: int | None = None,
    ) -> None:
        self.calls.append(
            {
                "id": call_id,
                "tenant_id": tenant_id,
                "workflow_id": workflow_id,
                "tool": tool,
                "risk_level": risk_level,
                "idempotency_key": idempotency_key,
                "input_summary": input_summary,
                "output_summary": output_summary,
                "status": status,
                "error": error,
                "latency_ms": latency_ms,
            }
        )
