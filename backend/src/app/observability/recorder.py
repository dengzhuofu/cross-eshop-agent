"""运行期记录器：graph 节点与持久层之间的唯一写入口。

- RunRecorder：绑定 (tenant_id, workflow_id)，落 WorkflowStep / 状态流转 / AgentDecision；
- NullRecorder：`langgraph dev` 独立调试图、无 DB 时使用，接口不变。

节点通过 config["configurable"]["recorder"] 拿到实例（见 api/main.py 注入），
从而保证节点函数本身不接触会话工厂、可在纯内存下测试。
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


class RunRecorder:
    def __init__(self, repo, workflow_id: str, tenant_id: str) -> None:
        self._repo = repo
        self.workflow_id = workflow_id
        self.tenant_id = tenant_id

    async def status(
        self,
        status: str,
        current_node: str | None = None,
        error: str | None = None,
    ) -> None:
        try:
            await self._repo.update_status(
                self.tenant_id, self.workflow_id, status, current_node=current_node, error=error
            )
        except Exception:  # noqa: BLE001 —— trace 失败不应打断主流程，但要留痕
            logger.exception("recorder.status failed (workflow=%s)", self.workflow_id)

    async def step(
        self,
        node: str,
        status: str = "completed",
        detail: dict | None = None,
        latency_ms: int | None = None,
    ) -> None:
        try:
            await self._repo.add_step(
                self.tenant_id,
                self.workflow_id,
                node=node,
                status=status,
                detail=detail,
                latency_ms=latency_ms,
            )
        except Exception:  # noqa: BLE001
            logger.exception("recorder.step failed (workflow=%s node=%s)", self.workflow_id, node)

    async def decision(
        self,
        agent: str,
        decision_type: str,
        reasoning: str,
        chosen_option: str,
        alternatives: list | None = None,
    ) -> None:
        try:
            await self._repo.add_decision(
                self.tenant_id,
                self.workflow_id,
                agent=agent,
                decision_type=decision_type,
                reasoning=reasoning,
                chosen_option=chosen_option,
                alternatives=alternatives or [],
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "recorder.decision failed (workflow=%s type=%s)", self.workflow_id, decision_type
            )


class NullRecorder:
    """与 RunRecorder 同接口的空实现。"""

    workflow_id = "null"
    tenant_id = "null"

    async def status(self, *args: Any, **kwargs: Any) -> None: ...

    async def step(self, *args: Any, **kwargs: Any) -> None: ...

    async def decision(self, *args: Any, **kwargs: Any) -> None: ...


def recorder_from_config(config: dict | None):
    configurable = (config or {}).get("configurable") or {}
    return configurable.get("recorder") or NullRecorder()
