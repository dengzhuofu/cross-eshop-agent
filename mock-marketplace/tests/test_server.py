"""Mock 商城服务测试：TestClient 进程内跑全链路（ingest → storefront → PDP →
Seller Central → 搜索/占位图），临时 SQLite，零网络零端口。"""

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import server
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    tmp = Path(tempfile.mkdtemp()) / "market-test.db"
    server.configure(tmp)
    return TestClient(server.app)


PAYLOAD = {
    "listing_id": "ama_test0001",
    "marketplace": "amazon",
    "title": "Foldable Under-Bed Storage Box 2-Pack",
    "brand": "HomeNest",
    "bullets": ["Folds flat in 3 seconds", "Holds 40 lbs"],
    "claim": "Store more in the space you already have.",
    "keywords": ["under bed storage"],
    "price_usd": 29.99,
    "workflow_id": "wf_test_1",
}


def test_healthz(client: TestClient):
    assert client.get("/healthz").json() == {
        "ok": True,
        "service": "shopverse-mock-marketplace",
    }


def test_ingest_is_idempotent_upsert(client: TestClient):
    r1 = client.post("/api/v1/listings", json=PAYLOAD)
    assert r1.status_code == 200
    body = r1.json()
    assert body["listing_id"] == "ama_test0001"
    # url 是绝对地址（按请求 base_url 推导）——前端「在商城查看」外链直接可点
    assert body["duplicated"] is False
    assert body["url"] == "http://testserver/product/ama_test0001"

    # 同 id 重放：覆盖而非新增（适配器幂等重放不会造成重复商品）
    r2 = client.post("/api/v1/listings", json={**PAYLOAD, "title": "Updated Title"})
    assert r2.json()["duplicated"] is True
    rows = client.get("/api/v1/listings").json()["listings"]
    assert len(rows) == 1 and rows[0]["title"] == "Updated Title"


def test_public_base_url_overrides_product_link(client: TestClient, monkeypatch):
    """容器部署：后端经服务别名访问商城，浏览器打不开别名——PUBLIC_BASE_URL 覆盖外链域名。"""
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://localhost:8001")
    body = client.post("/api/v1/listings", json=PAYLOAD).json()
    assert body["url"] == "http://localhost:8001/product/ama_test0001"


def test_storefront_home_search_and_marketplace_filter(client: TestClient):
    client.post("/api/v1/listings", json=PAYLOAD)
    client.post(
        "/api/v1/listings",
        json={
            "listing_id": "shp_test0002",
            "marketplace": "shopify",
            "title": "可折叠床底收纳箱",
        },
    )
    home = client.get("/").text
    assert "Foldable Under-Bed Storage Box 2-Pack" in home
    assert "可折叠床底收纳箱" in home
    # 搜索命中标题
    assert "Foldable Under-Bed" in client.get("/", params={"q": "Foldable"}).text
    assert "可折叠床底收纳箱" not in client.get("/", params={"q": "Foldable"}).text
    # 平台 tab 过滤
    assert "可折叠床底收纳箱" in client.get("/", params={"mp": "shopify"}).text
    assert "Foldable Under-Bed" not in client.get("/", params={"mp": "shopify"}).text
    # 空库搜索给引导文案
    assert "No listings yet" in client.get("/", params={"q": "nothing"}).text


def test_product_page_bullets_and_404(client: TestClient):
    client.post("/api/v1/listings", json=PAYLOAD)
    page = client.get("/product/ama_test0001")
    assert page.status_code == 200
    assert "About this item" in page.text
    assert "Folds flat in 3 seconds" in page.text
    assert "Store more in the space you already have." in page.text
    assert "$29.99" in page.text

    missing = client.get("/product/nope_nope000")
    assert missing.status_code == 404
    assert "does not exist" in missing.text


def test_seller_central_lists_ingested_items(client: TestClient):
    client.post("/api/v1/listings", json=PAYLOAD)
    page = client.get("/seller")
    assert "Seller Central" in page.text
    assert "ama_test0001" in page.text
    assert "wf_test_1" in page.text
    assert "No listings ingested yet" not in page.text


def test_placeholder_svg_is_deterministic(client: TestClient):
    client.post("/api/v1/listings", json=PAYLOAD)
    a = client.get("/img/ama_test0001.svg")
    b = client.get("/img/ama_test0001.svg")
    assert a.status_code == 200
    assert a.headers["content-type"].startswith("image/svg+xml")
    assert a.text == b.text, "同一条目的占位图必须确定性可复现"
    assert "<svg" in a.text
