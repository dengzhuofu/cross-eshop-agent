"""适配器单元测试：同一接口、不同平台规则（PRD §12.3-12.4）。"""

import pytest

from app.adapters import ADAPTERS, UnknownMarketplaceError, get_adapter


def _listing(
    marketplace: str, *, bullets=3, title="Foldable Storage Box", claim="实验室测试承重 40kg"
):
    return {
        "marketplace": marketplace,
        "title": title,
        "bullets": [f"bullet {i}" for i in range(bullets)],
        "claim": claim,
    }


def test_amazon_requires_exactly_five_bullets():
    ama = get_adapter("amazon")
    assert ama.validate_listing(_listing("amazon", bullets=3))  # 3 条违规
    errors = ama.validate_listing(_listing("amazon", bullets=6))
    assert any("above maximum" in e for e in errors)
    assert ama.validate_listing(_listing("amazon", bullets=5)) == []


def test_platform_rules_differ():
    rules = {name: get_adapter(name).get_rules() for name in ADAPTERS}
    # 费率梯度：shopify 最便宜，amazon 抽成最重
    assert (
        rules["shopify"].referral_fee_pct < rules["tiktok_shop"].referral_fee_pct <
        rules["amazon"].referral_fee_pct
    )
    # 图片规范：amazon 强制白底单主图；tiktok 多图自由背景
    assert rules["amazon"].image_spec.main_background == "white"
    assert rules["tiktok_shop"].image_spec.main_background == "free"
    # 标题上限各不相同
    assert (
        rules["tiktok_shop"].title_max_length <
        rules["amazon"].title_max_length <
        rules["shopify"].title_max_length
    )


def test_prohibited_phrases_differ_by_marketplace():
    risky = _listing("tiktok_shop", bullets=2, claim="全网最佳收纳神器，销量第一")
    tt = get_adapter("tiktok_shop")
    errors = tt.validate_listing(risky)
    assert any("最佳" in e for e in errors)
    # 同样的文案在 shopify 合法（宽松派黑名单更短）
    shp = get_adapter("shopify")
    ok = dict(risky, marketplace="shopify", bullets=risky["bullets"])
    assert shp.validate_listing(ok) == []


def test_title_over_limit_is_rejected():
    ama = get_adapter("amazon")
    long_title = "x" * 201
    errors = ama.validate_listing(_listing("amazon", title=long_title))
    assert any("title exceeds" in e for e in errors)


def test_unknown_marketplace_raises():
    with pytest.raises(UnknownMarketplaceError):
        get_adapter("wish")


async def test_adapter_publish_is_idempotent():
    ada = get_adapter("amazon")
    first = await ada.publish_listing({"title": "t"}, "key-001")
    second = await ada.publish_listing({"title": "t"}, "key-001")
    other = await ada.publish_listing({"title": "t"}, "key-002")
    assert first.listing_id == second.listing_id
    assert other.listing_id != first.listing_id
    assert first.listing_id.startswith("ama_")
