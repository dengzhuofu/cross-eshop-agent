"""供应商数据源工具目录（M3）：确定性 mock 目录 + 历史风险记忆 seed。

memory_hit 演示"跨工作流风险记忆降权"的展示形态；M4 接 pgvector 后由
retrieve_memory 实时检索替换 seed（PRD §7.4 / §9）。
"""

from pydantic import BaseModel, Field

from app.domain.enums import RiskLevel
from app.tools.context import ToolContext
from app.tools.registry import ToolDefinition, register


class SearchSuppliersInput(BaseModel):
    keyword: str = Field(min_length=1)
    max_results: int = Field(default=5, ge=1, le=20)


class SupplierCandidate(BaseModel):
    id: str
    name: str
    price_usd: float
    moq: int
    lead_time_days: int
    quality_score: int = Field(ge=0, le=100)
    risk: str = "low"
    memory_hit: dict | None = None


class SearchSuppliersOutput(BaseModel):
    keyword: str
    candidates: list[SupplierCandidate]


# 1688/阿里巴巴国际站爬虫的 mock 替身：同 schema 换实现即可
_SUPPLIER_CATALOG: list[dict] = [
    {
        "id": "sup_001",
        "name": "Ningbo Foldable Factory",
        "price_usd": 6.80,
        "moq": 500,
        "lead_time_days": 25,
        "quality_score": 86,
        "risk": "low",
    },
    {
        "id": "sup_002",
        "name": "Yiwu General Trading",
        "price_usd": 5.90,
        "moq": 300,
        "lead_time_days": 35,
        "quality_score": 41,
        "risk": "high",
        "memory_hit": {
            "source_workflow_id": "wf_seed_2026_07",
            "reason": "历史缺陷率 12% 超标被标记",
        },
    },
]


async def _search_suppliers(inp: SearchSuppliersInput, ctx: ToolContext) -> dict:
    return {
        "keyword": inp.keyword,
        "candidates": _SUPPLIER_CATALOG[: inp.max_results],
    }


register(
    ToolDefinition(
        name="search_suppliers",
        description="搜索供应商目录：报价/起订量/交期/质检分/历史风险记忆命中",
        input_model=SearchSuppliersInput,
        output_model=SearchSuppliersOutput,
        risk_level=RiskLevel.low,
        timeout_s=5.0,
        handler=_search_suppliers,
    )
)
