"""M12 单测：发布 → mock 商城（shopverse）桥接。

立场与主链路一致——商城只是演示出口：未配置/不可达/超时一律静默降级，
发布结果（PublishResult）永不因此失败。HTTP 收口在 _storefront_post 单点，
测试 monkeypatch 它而不必伪造 httpx 客户端。
"""

from types import SimpleNamespace

import pytest

from app.adapters import get_adapter
from app.adapters import marketplaces as M
from app.adapters.base import PublishResult


@pytest.fixture()
def url_on(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        M, "get_settings", lambda: SimpleNamespace(mock_marketplace_url="http://mp.test:8001")
    )


async def test_publish_posts_to_storefront_and_fills_url(url_on, monkeypatch):
    seen: list[tuple[str, dict]] = []

    async def fake_post(url: str, payload: dict) -> dict:
        seen.append((url, payload))
        return {"url": f"/product/{payload['listing_id']}", "duplicated": False}

    monkeypatch.setattr(M, "_storefront_post", fake_post)
    adapter = get_adapter("amazon")
    result = await adapter.publish_listing(
        {
            "title": "Foldable Under-Bed Storage Box",
            "bullets": ["Folds flat in 3s", "Holds 40 lbs", "x", "y", "z"],
            "claim": "Store more.",
            "keywords": ["storage"],
            "workflow_id": "wf_bridge_1",
        },
        "idem_key_bridge_1",
    )
    assert result.status == "published" and result.listing_id.startswith("ama_")
    assert result.url == f"/product/{result.listing_id}"
    url, payload = seen[0]
    assert url == "http://mp.test:8001/api/v1/listings"
    assert payload["marketplace"] == "amazon"
    assert payload["workflow_id"] == "wf_bridge_1"
    assert payload["title"] == "Foldable Under-Bed Storage Box"
    # 幂等重放：同 key 直接回 memo，不再发第二次 HTTP
    again = await adapter.publish_listing({"title": "x"}, "idem_key_bridge_1")
    assert again.listing_id == result.listing_id
    assert len(seen) == 1


async def test_unreachable_storefront_never_breaks_publish(url_on, monkeypatch):
    async def boom(url: str, payload: dict) -> dict:
        raise ConnectionError("refused")

    monkeypatch.setattr(M, "_storefront_post", boom)
    result = await get_adapter("shopify").publish_listing(
        {"title": "可折叠床底收纳箱", "bullets": ["a"], "claim": "b", "keywords": []},
        "idem_key_bridge_2",
    )
    assert result.status == "published" and result.listing_id.startswith("shp_")
    assert result.url == "", "商城不可达时 url 保持空串"


async def test_disabled_url_skips_storefront_entirely(monkeypatch):
    monkeypatch.setattr(M, "get_settings", lambda: SimpleNamespace(mock_marketplace_url=""))

    async def fail(url: str, payload: dict) -> dict:
        raise AssertionError("禁用状态下不得发起任何 HTTP")

    monkeypatch.setattr(M, "_storefront_post", fail)
    result = await get_adapter("tiktok_shop").publish_listing(
        {"title": "Foldable Bed Storage Bin", "bullets": ["a"], "claim": "b", "keywords": []},
        "idem_key_bridge_3",
    )
    assert result.status == "published" and result.url == ""


async def test_storefront_post_real_contract_shape(url_on, monkeypatch):
    """真 HTTP 单点的契约形状：POST JSON → raise_for_status → resp.json()。"""
    recorded: dict = {}

    class _Resp:
        def raise_for_status(self) -> None:
            recorded["raised"] = True

        def json(self) -> dict:
            return {"url": "/product/x", "duplicated": False}

    class _Client:
        def __init__(self, **kwargs):
            recorded["timeout"] = kwargs.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url: str, json: dict):
            recorded["url"], recorded["json"] = url, json
            return _Resp()

    monkeypatch.setattr(M.httpx, "AsyncClient", _Client)
    data = await M._storefront_post(
        "http://mp.test:8001/api/v1/listings", {"listing_id": "ama_x", "title": "t"}
    )
    assert data["url"] == "/product/x"
    assert recorded["timeout"] == 2.0
    assert recorded["json"]["listing_id"] == "ama_x"


def test_publish_result_url_defaults_empty_for_old_contract():
    # 旧代码构造 PublishResult 不带 url 也不炸（向后兼容）
    r = PublishResult(marketplace="amazon", listing_id="ama_x", status="published")
    assert r.url == ""
