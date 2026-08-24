"""研究数据源工具目录（M2）：确定性 mock 数据，但走完整工具治理管线。

真实数据源（Helium10/卖家精灵爬虫、Google Trends API 等）按同一 schema 替换实现即可，
节点与 LLM 不感知（PRD §12.5 扩展边界）。LLM 只见工具输出，不接触数据实现。
"""

from pydantic import BaseModel, Field

from app.domain.enums import RiskLevel
from app.tools.context import ToolContext
from app.tools.registry import ToolDefinition, register


class SearchMarketTrendsInput(BaseModel):
    keyword: str = Field(min_length=1)
    target_market: str = "US"


class SearchMarketTrendsOutput(BaseModel):
    keyword: str
    target_market: str
    search_trend_pct_90d: float
    category_monthly_searches: int
    related_keywords: list[str]
    seasonality: str
    sources: list[str]


class SearchCompetitorListingsInput(BaseModel):
    keyword: str = Field(min_length=1)
    marketplace: str = "amazon"


class Competitor(BaseModel):
    name: str
    price_usd: float
    rating: float
    review_count: int
    top_complaint: str


class SearchCompetitorListingsOutput(BaseModel):
    keyword: str
    marketplace: str
    competitors: list[Competitor]


class SearchCustomerReviewsInput(BaseModel):
    keyword: str = Field(min_length=1)
    marketplace: str = "amazon"


class PainPoint(BaseModel):
    theme: str
    share_pct: float
    sample_quote: str


class SearchCustomerReviewsOutput(BaseModel):
    keyword: str
    marketplace: str
    pain_points: list[PainPoint]


async def _trends(inp: SearchMarketTrendsInput, ctx: ToolContext) -> dict:
    return {
        "keyword": inp.keyword,
        "target_market": inp.target_market,
        "search_trend_pct_90d": 23.0,
        "category_monthly_searches": 148000,
        "related_keywords": [
            "under bed storage with wheels",
            "foldable storage bin queen bed",
            "low profile under bed drawer",
        ],
        "seasonality": "Q4 旺季（11-12 月搜索量 +40%）",
        "sources": ["trends_mock_001", "kw_planner_mock_002"],
    }


async def _competitors(inp: SearchCompetitorListingsInput, ctx: ToolContext) -> dict:
    return {
        "keyword": inp.keyword,
        "marketplace": inp.marketplace,
        "competitors": [
            {
                "name": "StorageWorks Under Bed Drawer",
                "price_usd": 32.99,
                "rating": 4.4,
                "review_count": 3210,
                "top_complaint": "拉链易坏",
            },
            {
                "name": "SimpleHouseware Foldable Bag",
                "price_usd": 25.99,
                "rating": 4.2,
                "review_count": 1870,
                "top_complaint": "无支撑易塌陷",
            },
            {
                "name": "Generic Under Bed Box",
                "price_usd": 21.99,
                "rating": 3.9,
                "review_count": 640,
                "top_complaint": "异味明显",
            },
        ],
    }


async def _reviews(inp: SearchCustomerReviewsInput, ctx: ToolContext) -> dict:
    return {
        "keyword": inp.keyword,
        "marketplace": inp.marketplace,
        "pain_points": [
            {
                "theme": "中部易塌陷",
                "share_pct": 34.0,
                "sample_quote": "装两床被子中间就陷下去了",
            },
            {
                "theme": "新箱异味",
                "share_pct": 21.0,
                "sample_quote": "拆开味道很大，晾了三天",
            },
            {
                "theme": "拉链损坏",
                "share_pct": 17.0,
                "sample_quote": "第二周拉链头就掉了",
            },
        ],
    }


register(
    ToolDefinition(
        name="search_market_trends",
        description="搜索品类趋势：90 天搜索环比、月搜索量、相关关键词、季节性",
        input_model=SearchMarketTrendsInput,
        output_model=SearchMarketTrendsOutput,
        risk_level=RiskLevel.low,
        timeout_s=5.0,
        handler=_trends,
    )
)
register(
    ToolDefinition(
        name="search_competitor_listings",
        description="搜索竞品 listing：价格/评分/评论数/主要差评点",
        input_model=SearchCompetitorListingsInput,
        output_model=SearchCompetitorListingsOutput,
        risk_level=RiskLevel.low,
        timeout_s=5.0,
        handler=_competitors,
    )
)
register(
    ToolDefinition(
        name="search_customer_reviews",
        description="聚合客户评论痛点：主题/占比/原声引用",
        input_model=SearchCustomerReviewsInput,
        output_model=SearchCustomerReviewsOutput,
        risk_level=RiskLevel.low,
        timeout_s=5.0,
        handler=_reviews,
    )
)
