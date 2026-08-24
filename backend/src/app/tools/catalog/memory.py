"""长期记忆工具目录（M4）：租户隔离的语义记忆写入与检索。

记忆行落在 workflow 仓储（insert_memory / search_memories 契约见 persistence 层）；
tenant_id 由 ToolContext 注入（PRD §13.2 铁律），工具参数里永远不出现租户——
LLM 无法伪造或跨租户检索。无 API key 时嵌入自动降级确定性 hash 引擎，测试不出网。
"""

from pydantic import BaseModel, Field

from app.domain.enums import RiskLevel
from app.llm.embeddings import embed_texts
from app.tools.context import ToolContext
from app.tools.registry import ToolDefinition, register


class RetrieveMemoryInput(BaseModel):
    kind: str = Field(min_length=1, description="记忆类型，如 insight / failure / preference")
    query_text: str = Field(min_length=1, description="查询文本，按语义相似度检索")
    top_k: int = Field(default=3, ge=1, le=10, description="返回的最相关条数上限")


class MemoryHit(BaseModel):
    content: str
    similarity: float
    source_workflow_id: str | None = None


class RetrieveMemoryOutput(BaseModel):
    kind: str
    results: list[MemoryHit]
    engine: str


class RecordMemoryInput(BaseModel):
    kind: str = Field(min_length=1, description="记忆类型，如 insight / failure / preference")
    content: str = Field(min_length=1, max_length=2000, description="要记住的内容原文（≤2000 字）")
    source_workflow_id: str | None = None


class RecordMemoryOutput(BaseModel):
    memory_id: str
    kind: str
    engine: str


async def _retrieve(inp: RetrieveMemoryInput, ctx: ToolContext) -> dict:
    from app.persistence.repositories.workflow_repo import WorkflowRepository

    vectors, _usage, engine = await embed_texts([inp.query_text])
    repo = WorkflowRepository()
    rows = await repo.search_memories(
        tenant_id=ctx.tenant_id,
        kind=inp.kind,
        query_embedding=vectors[0],
        top_k=inp.top_k,
    )
    return {
        "kind": inp.kind,
        "results": [
            {
                "content": r.get("content", ""),
                "similarity": float(r.get("similarity", 0.0)),
                "source_workflow_id": r.get("source_workflow_id"),
            }
            for r in rows
        ],
        "engine": engine,
    }


async def _record(inp: RecordMemoryInput, ctx: ToolContext) -> dict:
    from app.persistence.repositories.workflow_repo import WorkflowRepository

    vectors, _usage, engine = await embed_texts([inp.content])
    repo = WorkflowRepository()
    memory_id = await repo.insert_memory(
        tenant_id=ctx.tenant_id,
        kind=inp.kind,
        content=inp.content,
        embedding=vectors[0],
        source_workflow_id=inp.source_workflow_id,
    )
    return {"memory_id": memory_id, "kind": inp.kind, "engine": engine}


register(
    ToolDefinition(
        name="retrieve_memory",
        description=(
            "检索当前租户的长期记忆：按记忆类型(kind)与查询文本做语义匹配，"
            "返回最相关的若干条 {content, similarity, source_workflow_id}；"
            "只能看到本租户的记忆（跨租户不可见），无命中时返回空列表而非错误"
        ),
        input_model=RetrieveMemoryInput,
        output_model=RetrieveMemoryOutput,
        risk_level=RiskLevel.low,
        timeout_s=10.0,
        handler=_retrieve,
    )
)
register(
    ToolDefinition(
        name="record_memory",
        description=(
            "把一条经验/结论/教训写入当前租户的长期记忆（≤2000 字），供后续工作流语义复用；"
            "可关联 source_workflow_id 标记出处；写入即生效，同租户后续 retrieve_memory 可召回"
        ),
        input_model=RecordMemoryInput,
        output_model=RecordMemoryOutput,
        risk_level=RiskLevel.low,
        timeout_s=15.0,
        handler=_record,
    )
)
