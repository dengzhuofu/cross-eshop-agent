"""M3 新工具单测：estimate_profit / search_suppliers / generate_image_brief。

直接调用 handler（绕开 executor 的审计/幂等管线）验证业务数学与数据形状；
executor 通道另有一条冒烟断言（内存仓储）。
"""

from app.adapters import get_adapter
from app.persistence.memory import MemoryWorkflowRepository
from app.tools.catalog.media import GenerateImageBriefInput, _generate_image_brief
from app.tools.catalog.profit import EstimateProfitInput, _estimate_profit
from app.tools.catalog.supplier import SearchSuppliersInput, _search_suppliers
from app.tools.context import ToolContext
from app.tools.executor import execute_tool
from app.tools.registry import list_tools

_CTX = ToolContext(tenant_id="t_test")


async def test_estimate_profit_math_amazon():
    out = await _estimate_profit(
        EstimateProfitInput(marketplace="amazon", sale_price_usd=29.99, supplier_price_usd=6.80),
        _CTX,
    )
    # amazon 佣金 15% → fee=4.50；return_loss=(6.80+3.00+4.50)*0.04≈0.57；margin≈0.2541
    assert out["assumptions"]["platform_fee"] == 4.50
    assert abs(out["assumptions"]["return_loss"] - 0.57) < 0.01
    assert 0.253 <= out["margin_pct"] <= 0.255
    assert out["contribution_profit_usd"] == round(29.99 - out["total_cost_usd"], 2)
    # 盈亏平衡价 = 总成本/(1-费率)，有正利润时必然低于售价
    assert 0 < out["break_even_price_usd"] < 29.99


async def test_estimate_profit_fee_comes_from_adapter_rules():
    """同参数换渠道费率必须跟着 adapter 规则走，证明没有硬编码。"""
    amazon = await _estimate_profit(
        EstimateProfitInput(marketplace="amazon", sale_price_usd=29.99, supplier_price_usd=6.80),
        _CTX,
    )
    shopify = await _estimate_profit(
        EstimateProfitInput(marketplace="shopify", sale_price_usd=29.99, supplier_price_usd=6.80),
        _CTX,
    )
    assert amazon["platform_fee_pct"] == get_adapter("amazon").get_rules().referral_fee_pct
    assert shopify["platform_fee_pct"] == get_adapter("shopify").get_rules().referral_fee_pct
    assert shopify["margin_pct"] > amazon["margin_pct"]  # shopify 2.9% 佣金更轻


async def test_estimate_profit_return_rate_sensitivity():
    base = await _estimate_profit(
        EstimateProfitInput(marketplace="amazon", sale_price_usd=29.99, supplier_price_usd=6.80),
        _CTX,
    )
    doubled = await _estimate_profit(
        EstimateProfitInput(
            marketplace="amazon", sale_price_usd=29.99, supplier_price_usd=6.80, return_rate=0.08
        ),
        _CTX,
    )
    assert doubled["total_cost_usd"] > base["total_cost_usd"]
    assert doubled["margin_pct"] < base["margin_pct"]


async def test_search_suppliers_shape():
    out = await _search_suppliers(SearchSuppliersInput(keyword="床底收纳箱"), _CTX)
    assert len(out["candidates"]) == 2
    by_id = {c["id"]: c for c in out["candidates"]}
    assert by_id["sup_001"]["risk"] == "low"
    sup2 = by_id["sup_002"]
    assert sup2["risk"] == "high"
    # M4 起 memory_hit 不在目录里——由节点检索 supplier_risk 记忆后动态附加
    assert sup2.get("memory_hit") is None
    truncated = await _search_suppliers(SearchSuppliersInput(keyword="x", max_results=1), _CTX)
    assert len(truncated["candidates"]) == 1


async def test_generate_image_brief_follows_platform_spec():
    out = await _generate_image_brief(
        GenerateImageBriefInput(
            marketplace="amazon", product_idea="床底收纳箱", listing_title="Under Bed Storage"
        ),
        _CTX,
    )
    spec_amazon = get_adapter("amazon").get_rules().image_spec
    assert out["main_background"] == spec_amazon.main_background == "white"
    assert out["allow_watermark"] is False
    assert any("水印" in n for n in out["compliance_notes"])
    assert out["shot_list"] and "主图" in out["shot_list"][0]

    tiktok = await _generate_image_brief(
        GenerateImageBriefInput(marketplace="tiktok_shop", product_idea="床底收纳箱"),
        _CTX,
    )
    assert tiktok["main_background"] != "white"  # tiktok 允许场景化背景


def test_m3_tools_registered():
    names = {t.name for t in list_tools()}
    assert {"estimate_profit", "search_suppliers", "generate_image_brief"} <= names


async def test_estimate_profit_via_executor_channel():
    """治理通道冒烟：经 execute_tool 调用结果与直调 handler 一致。"""
    repo = MemoryWorkflowRepository()
    res = await execute_tool(
        "estimate_profit",
        {"marketplace": "amazon", "sale_price_usd": 29.99, "supplier_price_usd": 6.80},
        _CTX,
        repo,
    )
    direct = await _estimate_profit(
        EstimateProfitInput(marketplace="amazon", sale_price_usd=29.99, supplier_price_usd=6.80),
        _CTX,
    )
    assert res.ok is True and not res.replayed
    assert res.output["margin_pct"] == direct["margin_pct"]
