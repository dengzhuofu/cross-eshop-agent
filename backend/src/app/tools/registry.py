"""工具注册中心。

每个工具一条 ToolDefinition：schema、风险等级、是否幂等、是否需审批、超时。
executor 只认注册表——节点/LLM 不能绕过定义直接调 handler（PRD §7.2 工具治理）。
"""

from dataclasses import dataclass, field
from typing import Awaitable, Callable

from pydantic import BaseModel

from app.domain.enums import RiskLevel
from app.tools.context import ToolContext


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    risk_level: RiskLevel = RiskLevel.low
    idempotent: bool = False
    requires_approval: bool = False
    timeout_s: float = 10.0
    # handler(validated_input, ctx) -> dict（将交给 output_model 校验）
    handler: Callable[[BaseModel, "ToolContext"], Awaitable[dict]] = field(kw_only=True)


_REGISTRY: dict[str, ToolDefinition] = {}


def register(tool: ToolDefinition) -> None:
    if tool.name in _REGISTRY:
        raise ValueError(f"tool already registered: {tool.name}")
    _REGISTRY[tool.name] = tool


def get_tool(name: str) -> ToolDefinition:
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        raise KeyError(f"unknown tool: {name}") from exc


def list_tools() -> list[ToolDefinition]:
    return list(_REGISTRY.values())


def reset_registry() -> None:
    """仅测试用：清空注册表。"""
    _REGISTRY.clear()
