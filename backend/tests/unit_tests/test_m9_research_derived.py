"""M9 单测：研究工具的选题派生确定性数据（用户实测 bug 回归）。

背景：早期 handler 无视 keyword 硬编码「床底收纳箱」数据，任何其他选题
都拿到矛盾证据（证据完整度 0.45 被闸门拦）。修复后所有数据由关键词
sha256 确定性派生：同选题恒定、异选题各异、选题词必须出现在数据里。
"""

from app.tools.catalog.research import (
    SearchCompetitorListingsInput,
    SearchCustomerReviewsInput,
    SearchMarketTrendsInput,
    _competitors,
    _related_keywords,
    _reviews,
    _trends,
)
from app.tools.context import ToolContext

CTX = ToolContext(tenant_id="t_test", workflow_id="wf_test")


async def test_trends_derived_from_keyword():
    out = await _trends(SearchMarketTrendsInput(keyword="水枪"), CTX)
    assert 5.0 <= out["search_trend_pct_90d"] <= 45.0
    assert 20000 <= out["category_monthly_searches"] <= 300000
    assert all("水枪" in rk for rk in out["related_keywords"])
    assert out["seasonality"]


async def test_same_keyword_same_data():
    a = await _trends(SearchMarketTrendsInput(keyword="儿童保温杯"), CTX)
    b = await _trends(SearchMarketTrendsInput(keyword="儿童保温杯"), CTX)
    assert a == b, "同选题数据必须恒定（演示可复现）"


async def test_different_keywords_diverge():
    a = await _trends(SearchMarketTrendsInput(keyword="水枪"), CTX)
    b = await _trends(SearchMarketTrendsInput(keyword="床底收纳箱"), CTX)
    assert a["category_monthly_searches"] != b["category_monthly_searches"]
    assert a["related_keywords"] != b["related_keywords"]


async def test_competitors_carry_keyword_and_determinism():
    a = await _competitors(SearchCompetitorListingsInput(keyword="磁吸理线器"), CTX)
    b = await _competitors(SearchCompetitorListingsInput(keyword="磁吸理线器"), CTX)
    assert a == b
    assert len(a["competitors"]) == 3
    for comp in a["competitors"]:
        assert "磁吸理线器" in comp["name"], "竞品名必须含选题词（数据与选题一致）"
        assert 15.99 <= comp["price_usd"] <= 45.99
        assert 3.8 <= comp["rating"] <= 4.7


async def test_reviews_quotes_embed_keyword():
    out = await _reviews(SearchCustomerReviewsInput(keyword="水枪"), CTX)
    assert len(out["pain_points"]) == 3
    for pp in out["pain_points"]:
        assert "水枪" in pp["sample_quote"], "评论原声必须提及选题词"
        assert pp["share_pct"] > 0


async def test_related_keywords_no_duplicates():
    rks = _related_keywords("可折叠床底收纳箱")
    assert len(rks) == len(set(rks)) == 3
