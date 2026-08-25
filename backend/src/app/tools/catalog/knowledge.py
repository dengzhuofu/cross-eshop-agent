"""RAG 知识库工具目录（M6 建，M9 升级混合检索）：租户隔离的知识检索。

知识行落在 workflow 仓储（search_knowledge 契约见 persistence 层）；
tenant_id 由 ToolContext 注入（PRD §13.2 铁律），工具参数里永远不出现租户——
LLM 无法伪造或跨租户检索。无 API key 时嵌入自动降级确定性 hash 引擎，测试不出网。

M9：mode="hybrid"（默认）走 BM25+余弦 RRF 双路融合，grade=True 时对每个命中
追加确定性相关性分级（词面覆盖率 × 余弦合成，见 app.rag.rewrite）——分级是
agentic RAG 循环（客服节点改写→检索→分级→重试）的兜底判级原语。
"""

from typing import Literal

from pydantic import BaseModel, Field

from app.domain.enums import RiskLevel
from app.llm.embeddings import embed_texts
from app.rag.rewrite import deterministic_grade
from app.tools.context import ToolContext
from app.tools.registry import ToolDefinition, register


class SearchKnowledgeInput(BaseModel):
    query_text: str = Field(min_length=1, description="查询文本，按语义+词面混合检索知识库")
    category: str | None = Field(
        default=None,
        description=(
            "知识分类过滤：policy/platform_rule/product_info/faq/script/ops_playbook"
            "（运营打法，主链路 planner/listing 自用）；缺省查全部"
        ),
    )
    top_k: int = Field(default=3, ge=1, le=8)
    mode: Literal["vector", "hybrid"] = Field(
        default="hybrid",
        description=(
            "检索模式：hybrid（默认）BM25 词面+向量余弦双路 RRF 融合，"
            "离线/中文查询下质量显著更稳；vector 纯向量余弦（旧行为）"
        ),
    )
    grade: bool = Field(
        default=False,
        description="是否对每个命中做确定性相关性分级（返回 grade/grade_score 字段）",
    )


class KnowledgeHit(BaseModel):
    category: str
    title: str
    content: str
    similarity: float
    ref: str | None = None
    bm25: float | None = None
    rrf: float | None = None
    grade: bool | None = None
    grade_score: float | None = None


class SearchKnowledgeOutput(BaseModel):
    results: list[KnowledgeHit]
    engine: str
    mode: str


async def _search(inp: SearchKnowledgeInput, ctx: ToolContext) -> dict:
    from app.persistence.repositories.workflow_repo import WorkflowRepository

    vectors, _usage, engine = await embed_texts([inp.query_text])
    repo = WorkflowRepository()
    rows = await repo.search_knowledge(
        tenant_id=ctx.tenant_id,
        category=inp.category,
        query_embedding=vectors[0],
        top_k=inp.top_k,
        query_text=inp.query_text if inp.mode == "hybrid" else None,
    )
    results = []
    for r in rows:
        item = {
            "category": r.get("category", ""),
            "title": r.get("title", ""),
            "content": r.get("content", ""),
            "similarity": float(r.get("similarity", 0.0)),
            "ref": r.get("ref"),
            "bm25": r.get("bm25"),
            "rrf": r.get("rrf"),
        }
        if inp.grade:
            relevant, score = deterministic_grade(
                inp.query_text, item["content"], similarity=item["similarity"]
            )
            item["grade"] = relevant
            item["grade_score"] = score
        results.append(item)
    return {"results": results, "engine": engine, "mode": inp.mode}


register(
    ToolDefinition(
        name="search_knowledge",
        description=(
            "检索当前租户的 RAG 知识库，覆盖五类知识：policy 退换货政策、"
            "platform_rule 平台规则、product_info 商品说明、faq 常见问题、"
            "script 客服话术；默认 BM25+向量混合检索（中文/离线场景质量更稳），"
            "返回最相关的若干条 {category, title, content, similarity, ref[, bm25, rrf, grade]}；"
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
