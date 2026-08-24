"""RAG 知识库工具目录（M6）：租户隔离的语义知识检索。

知识行落在 workflow 仓储（search_knowledge 契约见 persistence 层）；
tenant_id 由 ToolContext 注入（PRD §13.2 铁律），工具参数里永远不出现租户——
LLM 无法伪造或跨租户检索。无 API key 时嵌入自动降级确定性 hash 引擎，测试不出网。
"""

from pydantic import BaseModel, Field

from app.domain.enums import RiskLevel
from app.llm.embeddings import embed_texts
from app.tools.context import ToolContext
from app.tools.registry import ToolDefinition, register


class SearchKnowledgeInput(BaseModel):
    query_text: str = Field(min_length=1, description="查询文本，按语义相似度检索知识库")
    category: str | None = Field(
        default=None,
        description=(
            "知识分类过滤：policy/platform_rule/product_info/faq/script/ops_playbook"
            "（运营打法，主链路 planner/listing 自用）；缺省查全部"
        ),
    )
    top_k: int = Field(default=3, ge=1, le=8)


class KnowledgeHit(BaseModel):
    category: str
    title: str
    content: str
    similarity: float
    ref: str | None = None


class SearchKnowledgeOutput(BaseModel):
    results: list[KnowledgeHit]
    engine: str


async def _search(inp: SearchKnowledgeInput, ctx: ToolContext) -> dict:
    from app.persistence.repositories.workflow_repo import WorkflowRepository

    vectors, _usage, engine = await embed_texts([inp.query_text])
    repo = WorkflowRepository()
    rows = await repo.search_knowledge(
        tenant_id=ctx.tenant_id,
        category=inp.category,
        query_embedding=vectors[0],
        top_k=inp.top_k,
    )
    return {
        "results": [
            {
                "category": r.get("category", ""),
                "title": r.get("title", ""),
                "content": r.get("content", ""),
                "similarity": float(r.get("similarity", 0.0)),
                "ref": r.get("ref"),
            }
            for r in rows
        ],
        "engine": engine,
    }


register(
    ToolDefinition(
        name="search_knowledge",
        description=(
            "检索当前租户的 RAG 知识库，覆盖五类知识：policy 退换货政策、"
            "platform_rule 平台规则、product_info 商品说明、faq 常见问题、"
            "script 客服话术；按查询文本做语义匹配，返回最相关的若干条 "
            "{category, title, content, similarity, ref}；"
            "只能看到本租户的知识（跨租户不可见），无命中时返回空列表而非错误；"
            "事实类问题应优先检索本工具；订单状态/物流轨迹等实时数据必须走业务工具查询，"
            "不得用知识库内容替代（PRD §7.11 融合铁律）"
        ),
        input_model=SearchKnowledgeInput,
        output_model=SearchKnowledgeOutput,
        risk_level=RiskLevel.low,
        timeout_s=10.0,
        handler=_search,
    )
)
