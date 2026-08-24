"""ToolExecutor：所有工具调用的唯一通道（PRD §7.2 工具治理 + §13.2 多租户铁律）。

执行管线（顺序即语义，不可调换）：
  1. 输入 schema 校验        —— LLM 产出的参数先验模型再执行
  2. 跨租户引用检测          —— ctx.workflow_id 必须属于 ctx.tenant_id，否则拒绝并落审计
  3. 审批门                  —— 高风险工具未带审批凭据时拒绝（AUTO_APPROVE 仅限 dev 演示）
  4. 幂等回放                —— 同 idempotency_key 的成功调用直接返回缓存输出，不重复执行
  5. 超时控制 + 执行 handler
  6. 输出 schema 校验
  7. ToolCall 审计           —— 成功/失败/回放全部留痕

任何一步失败都会尝试写审计行后抛出对应 ToolError。
"""

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from app.config import get_settings
from app.domain.enums import RiskLevel
from app.persistence.repositories.workflow_repo import WorkflowRepository
from app.tools.context import ToolContext
from app.tools.registry import ToolDefinition, get_tool

logger = logging.getLogger(__name__)


class ToolError(Exception):
    """工具层错误基类。"""


class UnknownToolError(ToolError):
    pass


class SchemaValidationError(ToolError):
    def __init__(self, errors: list[dict[str, Any]]) -> None:
        self.errors = errors
        detail = json.dumps(errors, ensure_ascii=False)
        super().__init__(f"input schema validation failed: {detail}")


class CrossTenantReferenceError(ToolError):
    pass


class ApprovalRequiredError(ToolError):
    pass


class ToolTimeoutError(ToolError):
    pass


class OutputValidationError(ToolError):
    pass


@dataclass(frozen=True)
class ToolExecutionResult:
    tool: str
    ok: bool
    output: dict[str, Any] | None
    replayed: bool = False
    error: str | None = None
    latency_ms: int = 0
    tool_call_id: str | None = None


def _summarize(payload: Any) -> dict[str, Any]:
    """审计摘要：listing 等大对象只留规模信息，避免审计表膨胀。"""
    if not isinstance(payload, dict):
        return {"raw": str(payload)[:200]}
    out: dict[str, Any] = {}
    for k, v in payload.items():
        if isinstance(v, dict) and len(json.dumps(v)) > 500:
            out[k] = f"<{len(v)} keys omitted>"
        else:
            out[k] = v
    return out


async def execute_tool(
    name: str,
    payload: dict[str, Any],
    ctx: ToolContext,
    repo: WorkflowRepository,
) -> ToolExecutionResult:
    t0 = time.perf_counter()
    started = uuid.uuid4().hex

    try:
        tool: ToolDefinition = get_tool(name)
    except KeyError:
        raise UnknownToolError(name) from None

    async def _audit(status: str, *, input_summary=None, output_summary=None, error=None) -> str:
        await repo.record_tool_call(
            tenant_id=ctx.tenant_id,
            workflow_id=ctx.workflow_id,
            call_id=started,
            tool=name,
            risk_level=tool.risk_level.value
            if isinstance(tool.risk_level, RiskLevel)
            else str(tool.risk_level),
            idempotency_key=str(payload.get("idempotency_key") or "") or None,
            input_summary=input_summary,
            output_summary=output_summary,
            status=status,
            error=error,
            latency_ms=int((time.perf_counter() - t0) * 1000),
        )
        return started

    # ---- 1. 输入 schema 校验 ----
    try:
        validated: BaseModel = tool.input_model.model_validate(payload)
    except ValidationError as exc:
        errors = exc.errors(include_url=False, include_input=False)
        await _audit("error", input_summary=_summarize(payload), error="input_schema_invalid")
        raise SchemaValidationError(errors) from None

    # ---- 2. 跨租户引用检测 ----
    wf_id_in_payload = getattr(validated, "workflow_id", None) or ctx.workflow_id
    if wf_id_in_payload is not None:
        owner = await repo.get(ctx.tenant_id, str(wf_id_in_payload))
        if owner is None:
            await _audit(
                "error",
                input_summary=_summarize(payload),
                error="cross_tenant_reference",
            )
            raise CrossTenantReferenceError(
                f"workflow {wf_id_in_payload} 不存在或不属于当前租户"
            )

    # ---- 3. 审批门 ----
    needs_approval = tool.requires_approval and not ctx.approved
    if needs_approval and not get_settings().auto_approve:
        await _audit("error", input_summary=_summarize(payload), error="approval_required")
        raise ApprovalRequiredError(f"tool {name} 是高风险动作，需人工审批")

    # ---- 4. 幂等回放 ----
    idem_key = getattr(validated, "idempotency_key", None)
    if tool.idempotent and idem_key:
        prior = await repo.find_tool_output_by_idempotency(ctx.tenant_id, str(idem_key))
        if prior is not None:
            await _audit("replayed", input_summary=_summarize(payload))
            logger.info("tool %s replayed via idempotency_key=%s", name, idem_key)
            return ToolExecutionResult(
                tool=name,
                ok=True,
                output=prior,
                replayed=True,
                latency_ms=int((time.perf_counter() - t0) * 1000),
                tool_call_id=started,
            )

    # ---- 5/6. 执行 + 输出校验 ----
    try:
        raw = await asyncio.wait_for(tool.handler(validated, ctx), timeout=tool.timeout_s)
        output = tool.output_model.model_validate(raw)
    except TimeoutError:
        await _audit("error", input_summary=_summarize(payload), error="timeout")
        raise ToolTimeoutError(f"{name} 超过 {tool.timeout_s}s") from None
    except ValidationError as exc:
        errors = exc.errors(include_url=False, include_input=False)
        await _audit("error", input_summary=_summarize(payload), error="output_schema_invalid")
        raise OutputValidationError(errors) from None

    out_dict = output.model_dump(mode="json")
    await _audit("ok", input_summary=_summarize(payload), output_summary=out_dict)
    return ToolExecutionResult(
        tool=name,
        ok=True,
        output=out_dict,
        latency_ms=int((time.perf_counter() - t0) * 1000),
        tool_call_id=started,
    )
