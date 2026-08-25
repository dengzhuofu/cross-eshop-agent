"""研究数据源工具目录（M2 建，M9 升级为选题派生数据）：确定性 mock，走完整治理管线。

M9 修复：早期三个 handler 无视入参 keyword、无条件返回「床底收纳箱」的固定
剧情数据——任何其他选题都会拿到与选题矛盾的证据，证据完整度必然低分被闸门
拦下（用户实测：选题「水枪」配到储物箱数据）。现在所有数值/文案由关键词经
sha256 确定性派生：同一选题数据恒定可复现，不同选题数据各归其位。

真实数据源（Helium10/卖家精灵爬虫、Google Trends API 等）按同一 schema 替换
实现即可，节点与 LLM 不感知（PRD §12.5 扩展边界）。LLM 只见工具输出。
"""

import hashlib

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


def _seed(keyword: str, salt: str) -> float:
    """关键词+盐 → 稳定 [0,1) 伪随机（sha256，跨进程可复现，同选题数据恒定）。"""
    digest = hashlib.sha256(f"{salt}:{keyword}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def _pick(keyword: str, salt: str, pool: list[str]) -> str:
    return pool[int(_seed(keyword, salt) * len(pool)) % len(pool)]


_CJK_SEASONALITY = [
    "Q4 旺季（11-12 月搜索量 +40%）",
    "夏季峰值（6-8 月搜索量 +35%）",
    "全年平稳，无明显季节性",
    "春季小高峰（3-4 月 +20%）",
]
_CJK_MODIFIERS = ["大容量", "便携", "家用", "儿童款", "升级款", "折叠", "多功能"]
_LATIN_MODIFIERS = ["with wheels", "for small spaces", "heavy duty", "portable", "foldable"]
_COMPLAINTS = ["做工一般", "包装易损", "尺寸偏小", "有异味", "耐用性差", "色差明显", "配件易丢"]
_QUOTE_TEMPLATES = [
    "买的{kw}用了两周就出了质量问题",
    "收到{kw}的时候包装已经压坏了",
    "{kw}和页面描述的尺寸有出入",
    "这个{kw}用一次就不想用了",
    "{kw}的做工配不上这个价格",
]


def _related_keywords(keyword: str) -> list[str]:
    """相关词：选题词 + 确定性挑选的三个修饰词变体（中英文各一套修饰池）。"""
    is_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in keyword)
    modifiers = _CJK_MODIFIERS if is_cjk else _LATIN_MODIFIERS
    picked: list[str] = []
    for i in range(3):
        pool = [m for m in modifiers if f"{keyword} {m}" not in picked] or modifiers
        variant = f"{keyword} {_pick(keyword, f'rel{i}', pool)}"
        if variant not in picked:
            picked.append(variant)
    return picked


async def _trends(inp: SearchMarketTrendsInput, ctx: ToolContext) -> dict:
    kw = inp.keyword
    digest = hashlib.sha256(kw.encode("utf-8")).hexdigest()
    return {
        "keyword": kw,
        "target_market": inp.target_market,
        "search_trend_pct_90d": round(5.0 + _seed(kw, "trend") * 40.0, 1),
        "category_monthly_searches": int(20000 + _seed(kw, "vol") * 280000),
        "related_keywords": _related_keywords(kw),
        "seasonality": _pick(kw, "season", _CJK_SEASONALITY),
        "sources": [f"trends_mock_{digest[:6]}", f"kw_planner_mock_{digest[6:12]}"],
    }


async def _competitors(inp: SearchCompetitorListingsInput, ctx: ToolContext) -> dict:
    kw = inp.keyword
    brands = ["HomeHero", "MaidMAX", "Lifewit", "SimpleHouseware", "StorageWorks"]
    competitors = []
    for i in range(3):
        competitors.append(
            {
                "name": f"{_pick(kw, f'brand{i}', brands)} {kw}",
                "price_usd": round(15.99 + _seed(kw, f"price{i}") * 30.0, 2),
                "rating": round(3.8 + _seed(kw, f"rating{i}") * 0.9, 1),
                "review_count": int(500 + _seed(kw, f"reviews{i}") * 4500),
                "top_complaint": _pick(kw, f"complaint{i}", _COMPLAINTS),
            }
        )
    return {"keyword": kw, "marketplace": inp.marketplace, "competitors": competitors}


async def _reviews(inp: SearchCustomerReviewsInput, ctx: ToolContext) -> dict:
    kw = inp.keyword
    themes = ["做工一般", "包装易损", "尺寸不符", "异味明显", "耐用性差", "安装不便"]
    shares = [round(15.0 + _seed(kw, f"share{i}") * 25.0, 1) for i in range(3)]
    used_quotes: set[str] = set()
    pain_points = []
    for i in range(3):
        pool = [t for t in _QUOTE_TEMPLATES if t not in used_quotes] or _QUOTE_TEMPLATES
        quote_tpl = _pick(kw, f"quote{i}", pool)
        used_quotes.add(quote_tpl)
        pain_points.append(
            {
                "theme": _pick(kw, f"theme{i}", themes),
                "share_pct": shares[i],
                "sample_quote": quote_tpl.format(kw=kw),
            }
        )
    return {"keyword": kw, "marketplace": inp.marketplace, "pain_points": pain_points}


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
