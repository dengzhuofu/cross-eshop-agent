"""内存版仓储兜底：仅用于 `langgraph dev` 独立调试图（无 DB）场景。

ToolExecutor 的调用通道保持唯一——没有 Postgres 时用内存实现顶上，
保证"所有工具调用必过 executor"这条铁律在任何运行形态下都不破例。
"""

import math
import uuid
from datetime import datetime, timezone
from typing import Any


def _cosine(a: list[float], b: list[float]) -> float:
    """余弦相似度；零向量/空向量（分母为 0）安全返回 0.0。"""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


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
        self.memories: list[dict[str, Any]] = []

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

    # ---- memories（长期记忆，M4；与 WorkflowRepository 同契约的内存版）----

    async def insert_memory(self, *, tenant_id: str, kind: str, content: str,
                            embedding: list[float], source_workflow_id: str | None = None,
                            meta: dict | None = None) -> str:
        """写入一条记忆，返回 memory_id（uuid4().hex）。"""
        memory_id = uuid.uuid4().hex
        self.memories.append(
            {
                "id": memory_id,
                "tenant_id": tenant_id,
                "kind": kind,
                "content": content,
                "embedding": list(embedding),
                "meta": meta,
                "source_workflow_id": source_workflow_id,
                "created_at": datetime.now(timezone.utc),
            }
        )
        return memory_id

    async def search_memories(self, *, tenant_id: str, kind: str,
                              query_embedding: list[float], top_k: int = 3) -> list[dict]:
        """按租户+kind 取候选后在 Python 里算余弦相似度排序取 top_k。

        返回 [{id, kind, content, similarity(float), source_workflow_id, created_at}]，
        similarity 降序；零向量安全处理（分母为 0 时 similarity=0.0）；
        created_at 输出 isoformat 字符串。查询永远带 tenant_id 过滤（多租户铁律）。
        """
        scored = [
            {
                "id": m["id"],
                "kind": m["kind"],
                "content": m["content"],
                "similarity": _cosine(query_embedding, m["embedding"]),
                "source_workflow_id": m["source_workflow_id"],
                "created_at": m["created_at"].isoformat(),
            }
            for m in self.memories
            if m["tenant_id"] == tenant_id and m["kind"] == kind
        ]
        scored.sort(key=lambda item: item["similarity"], reverse=True)
        return scored[:top_k]
