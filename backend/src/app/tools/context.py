"""工具执行上下文。

铁律（PRD §13.2）：tenant_id 只能由系统注入此处，工具 handler 与工具参数里
永远不出现 tenant_id——LLM/调用方无法伪造租户。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolContext:
    tenant_id: str
    workflow_id: str | None = None
    actor_id: str = "system"  # agent 名或人工审批人；M5 起区分 machine/human
    approved: bool = False  # 高风险工具的审批凭据（由审批环节写入）
