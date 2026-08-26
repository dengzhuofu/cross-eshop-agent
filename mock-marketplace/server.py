"""shopverse —— Mock 商城（M12）：给「铺货效果」一个看得见的去处。

真实亚马逊/Shopify/TikTok Shop 需要 API 与店铺资质，本服务在本地 8001 端口
扮演一个最小但像样的电商站：cross-eshop-agent 的 publish_listing 适配器把
上架物 POST 进来（幂等 upsert），这里以 storefront / 商品详情 / Seller Central
三个页面呈现。零外网依赖：占位主图是确定性 SVG，样式本地内置。

运行（backend venv 即可，无新依赖）：
    python mock-marketplace/server.py --demo   # 顺带种 3 条演示商品
    python mock-marketplace/server.py          # 空库启动
测试：pytest mock-marketplace -q（TestClient 进程内，不占端口）
"""

import argparse
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
import views
from fastapi import FastAPI, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

DB_PATH = Path(__file__).resolve().parent / ".localdata" / "market.db"
_write_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

app = FastAPI(title="shopverse mock marketplace", docs_url=None, redoc_url=None)


# ---- 存储：stdlib sqlite3 单连接 + 写锁（演示量级足够，不引 ORM）----


def configure(db_path: Path) -> None:
    """测试注入临时库路径用；生产路径由模块常量给定。"""
    global _conn
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _conn = sqlite3.connect(db_path, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn.execute(
        """
        CREATE TABLE IF NOT EXISTS listings (
            listing_id  TEXT PRIMARY KEY,
            marketplace TEXT NOT NULL,
            title       TEXT NOT NULL,
            brand       TEXT NOT NULL DEFAULT '',
            bullets     TEXT NOT NULL DEFAULT '[]',
            claim       TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            keywords    TEXT NOT NULL DEFAULT '[]',
            price_usd   REAL,
            currency    TEXT NOT NULL DEFAULT 'USD',
            sku         TEXT NOT NULL DEFAULT '',
            status      TEXT NOT NULL DEFAULT 'live',
            workflow_id TEXT NOT NULL DEFAULT '',
            created_at  TEXT NOT NULL
        )
        """
    )
    _conn.commit()


def _row_to_listing(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["bullets"] = json.loads(d.get("bullets") or "[]")
    d["keywords"] = json.loads(d.get("keywords") or "[]")
    return d


def upsert_listing(payload: dict) -> bool:
    """按 listing_id 幂等写入；返回是否为新条目（False=重放覆盖）。"""
    assert _conn is not None
    with _write_lock:
        cur = _conn.execute(
            "SELECT 1 FROM listings WHERE listing_id = ?", (payload["listing_id"],)
        )
        existed = cur.fetchone() is not None
        _conn.execute(
            """
            INSERT INTO listings
            (listing_id, marketplace, title, brand, bullets, claim, description,
             keywords, price_usd, currency, sku, status, workflow_id, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(listing_id) DO UPDATE SET
                marketplace=excluded.marketplace, title=excluded.title,
                brand=excluded.brand, bullets=excluded.bullets, claim=excluded.claim,
                description=excluded.description, keywords=excluded.keywords,
                price_usd=excluded.price_usd, currency=excluded.currency,
                sku=excluded.sku, status=excluded.status,
                workflow_id=excluded.workflow_id
            """,
            (
                payload["listing_id"],
                payload.get("marketplace") or "amazon",
                payload.get("title") or "(untitled)",
                payload.get("brand") or "",
                json.dumps(payload.get("bullets") or [], ensure_ascii=False),
                payload.get("claim") or "",
                payload.get("description") or "",
                json.dumps(payload.get("keywords") or [], ensure_ascii=False),
                payload.get("price_usd"),
                payload.get("currency") or "USD",
                payload.get("sku") or "",
                payload.get("status") or "live",
                payload.get("workflow_id") or "",
                payload.get("created_at")
                or datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        )
        _conn.commit()
    return not existed


def query_listings(marketplace: str | None = None, q: str = "") -> list[dict]:
    assert _conn is not None
    sql = "SELECT * FROM listings WHERE 1=1"
    args: list = []
    if marketplace and marketplace != "all":
        sql += " AND marketplace = ?"
        args.append(marketplace)
    if q:
        sql += " AND (title LIKE ? OR keywords LIKE ? OR brand LIKE ?)"
        like = f"%{q}%"
        args += [like, like, like]
    sql += " ORDER BY created_at DESC, listing_id"
    with _write_lock:
        rows = _conn.execute(sql, args).fetchall()
    return [_row_to_listing(r) for r in rows]


def get_listing(listing_id: str) -> dict | None:
    assert _conn is not None
    with _write_lock:
        row = _conn.execute(
            "SELECT * FROM listings WHERE listing_id = ?", (listing_id,)
        ).fetchone()
    return _row_to_listing(row) if row else None


# ---- 契约模型 ----


class ListingIn(BaseModel):
    listing_id: str = Field(min_length=3, max_length=64)
    marketplace: str = Field(min_length=1, max_length=32)
    title: str = Field(min_length=1, max_length=300)
    brand: str = Field(default="", max_length=120)
    bullets: list[str] = Field(default_factory=list, max_length=10)
    claim: str = Field(default="", max_length=500)
    description: str = Field(default="", max_length=5000)
    keywords: list[str] = Field(default_factory=list, max_length=15)
    price_usd: float | None = Field(default=None, gt=0, le=100000)
    currency: str = Field(default="USD", max_length=8)
    sku: str = Field(default="", max_length=64)
    status: str = Field(default="live", max_length=24)
    workflow_id: str = Field(default="", max_length=64)
    created_at: str = Field(default="", max_length=40)


# ---- API（供 agent 适配器回写与测试断言）----


def _product_url(request: Request, listing_id: str) -> str:
    """商品页绝对地址：默认按请求的 base_url 推导（本地 dev 即 http://127.0.0.1:8001）。
    容器部署时后端经服务网别名访问商城，浏览器打不开该别名——用 PUBLIC_BASE_URL 覆盖。"""
    public = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
    base = public or str(request.base_url).rstrip("/")
    return f"{base}/product/{listing_id}"


@app.post("/api/v1/listings")
def api_ingest(listing: ListingIn, request: Request) -> JSONResponse:
    created = upsert_listing(listing.model_dump())
    return JSONResponse(
        {
            "listing_id": listing.listing_id,
            "marketplace": listing.marketplace,
            "status": listing.status,
            # 绝对地址：前端「在商城查看」外链直接可点（相对路径会落到前端自己的域名上）
            "url": _product_url(request, listing.listing_id),
            "duplicated": not created,
        }
    )


@app.get("/api/v1/listings")
def api_list(
    marketplace: str | None = None, q: str = Query(default="")
) -> JSONResponse:
    return JSONResponse({"listings": query_listings(marketplace, q)})


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "service": "shopverse-mock-marketplace"}


# ---- 页面（storefront / PDP / Seller Central）----


@app.get("/", response_class=HTMLResponse)
def home(q: str = Query(default=""), mp: str = Query(default="all")) -> str:
    return views.render_home(query_listings(mp, q), q=q, mp=mp)


@app.get("/seller", response_class=HTMLResponse)
def seller() -> str:
    return views.render_seller(query_listings())


@app.get("/product/{listing_id}", response_class=HTMLResponse)
def product(listing_id: str) -> str:
    listing = get_listing(listing_id)
    if listing is None:
        return HTMLResponse(views.render_not_found(listing_id), status_code=404)
    return views.render_product(listing)


@app.get("/img/{listing_id}.svg")
def product_image(listing_id: str) -> Response:
    listing = get_listing(listing_id) or {"listing_id": listing_id, "title": listing_id}
    return Response(
        views.placeholder_svg(listing), media_type="image/svg+xml"
    )


@app.get("/static/style.css")
def style() -> Response:
    return Response(views.STYLE, media_type="text/css")


# ---- 启动 ----

DEMO_SEEDS = [
    {
        "listing_id": "ama_demo0001",
        "marketplace": "amazon",
        "title": "Foldable Under-Bed Storage Box, 2-Pack | Sturdy Oxford Fabric, Tool-Free Assembly",
        "brand": "HomeNest",
        "bullets": [
            "FOLDS FLAT in 3 seconds — slides under beds 6in+ clearance",
            "Holds up to 40 lbs of seasonal clothing and bedding",
            "Reinforced bottom board keeps shape when fully loaded",
            "Breathable oxford fabric — low-odor, no chemical smell",
            "Clear front window so you can find items without unzipping",
        ],
        "claim": "Store more in the space you already have.",
        "keywords": ["under bed storage", "foldable storage bin", "clothing organizer"],
        "price_usd": 29.99,
        "workflow_id": "demo_seed",
    },
    {
        "listing_id": "shp_demo0002",
        "marketplace": "shopify",
        "title": "HomeNest 可折叠床底收纳箱（两只装）— 免工具安装，承重 40 磅",
        "brand": "HomeNest",
        "bullets": [
            "3 秒折叠，塞进 15cm 以上床底空隙",
            "加固底板，满载不塌陷",
            "透气牛津布，低气味无异味",
        ],
        "claim": "把已有的空间，变成更多的储物。",
        "keywords": ["床底收纳", "折叠收纳箱", "衣物整理"],
        "price_usd": 25.99,
        "workflow_id": "demo_seed",
    },
    {
        "listing_id": "tts_demo0003",
        "marketplace": "tiktok_shop",
        "title": "Foldable Bed Storage Bin 2-Pack",
        "brand": "HomeNest",
        "bullets": ["Folds flat in 3s", "40 lbs capacity", "Low-odor fabric"],
        "claim": "More storage, zero assembly.",
        "keywords": ["storage hack", "home organize"],
        "price_usd": 22.99,
        "workflow_id": "demo_seed",
    },
]


def seed_demo() -> None:
    for s in DEMO_SEEDS:
        upsert_listing(dict(s))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="shopverse mock marketplace")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--demo", action="store_true", help="seed 3 demo listings")
    args = parser.parse_args()
    configure(DB_PATH)
    if args.demo:
        seed_demo()
    print(f"shopverse mock marketplace → http://{args.host}:{args.port}/")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
