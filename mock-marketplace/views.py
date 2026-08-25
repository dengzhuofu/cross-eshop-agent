"""Mock 商城的 HTML/SVG 渲染（M12）。

刻意不引模板引擎：f-string + html.escape 足够，且让「商城长什么样」集中在一个
文件里可读可改。视觉基调对齐 Amazon：深色导航（#131921）、橙色行动按钮（#f90）、
浅灰底白卡片。所有动态文本必须过 esc()，占位图是确定性 SVG（无外网图片依赖）。
"""

import hashlib
import html
import random
from datetime import datetime
from pathlib import Path

STYLE = (Path(__file__).parent / "static" / "style.css").read_text(encoding="utf-8")

# 平台展示名与徽标配色（未登记的平台回退灰底）
MP_META = {
    "amazon": {"label": "Amazon", "color": "#232f3e"},
    "shopify": {"label": "Shopify", "color": "#5e8e3e"},
    "tiktok_shop": {"label": "TikTok Shop", "color": "#ee1d52"},
}
MPS = ["amazon", "shopify", "tiktok_shop"]


def esc(text: object) -> str:
    return html.escape(str(text if text is not None else ""))


def mp_label(marketplace: str) -> str:
    return MP_META.get(marketplace, {}).get("label", marketplace.replace("_", " ").title())


def mp_color(marketplace: str) -> str:
    return MP_META.get(marketplace, {}).get("color", "#6b7280")


def _seed(text: str) -> random.Random:
    return random.Random(int(hashlib.md5(text.encode("utf-8")).hexdigest(), 16))


def display_price(listing: dict) -> str:
    price = listing.get("price_usd")
    if not price:
        # 未带价格的上架物按 id 稳定派生一个演示价（同一条目每次打开一致）
        price = round(19.99 + (_seed(str(listing.get("listing_id"))).random() * 20), 2)
    return f"${float(price):.2f}"


def stars(listing: dict) -> str:
    rng = _seed(str(listing.get("listing_id")))
    rating = round(rng.uniform(4.1, 4.9), 1)
    reviews = rng.randrange(52, 2400)
    full = round(rating)
    bar = "★" * full + "☆" * (5 - full)
    return (
        f'<span class="stars"><b class="star-ico">{bar}</b> {rating}</span> '
        f'<span class="muted">({reviews:,})</span>'
    )


def badge(listing: dict) -> str:
    mp = str(listing.get("marketplace") or "")
    return (
        f'<span class="badge" style="background:{mp_color(mp)}">{esc(mp_label(mp))}</span>'
    )


def placeholder_svg(listing: dict) -> str:
    """确定性占位主图：标题 hash → 双色渐变 + 品牌首字母。离线可复现、零外链。"""
    rng = _seed(str(listing.get("listing_id")) + str(listing.get("title")))
    h1, h2 = rng.randrange(0, 360), (rng.randrange(0, 360) + 40) % 360
    text = esc(str(listing.get("brand") or listing.get("title") or "?")[:2].upper())
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="460" height="460" '
        'viewBox="0 0 460 460" role="img" aria-label="product image">'
        f'<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0%" stop-color="hsl({h1},55%,72%)"/>'
        f'<stop offset="100%" stop-color="hsl({h2},60%,52%)"/>'
        "</linearGradient></defs>"
        '<rect width="460" height="460" fill="url(#g)"/>'
        '<rect x="24" y="24" width="412" height="412" fill="white" opacity="0.82" rx="18"/>'
        f'<text x="230" y="262" font-size="150" font-family="Arial, sans-serif" '
        f'font-weight="bold" fill="hsl({h1},45%,38%)" text-anchor="middle">{text}</text>'
        "</svg>"
    )


def _card(listing: dict) -> str:
    lid = esc(listing.get("listing_id"))
    img = f"/img/{lid}.svg"
    return (
        f'<a class="card" href="/product/{lid}">'
        f'<div class="card-img"><img src="{img}" alt="{esc(listing.get("title"))}" loading="lazy"/></div>'
        f'<div class="card-body">{badge(listing)}'
        f'<div class="card-title">{esc(listing.get("title"))}</div>'
        f"<div>{stars(listing)}</div>"
        f'<div class="price">{display_price(listing)}</div>'
        f'<div class="muted small">{esc(listing.get("brand"))}</div>'
        "</div></a>"
    )


def _nav(q: str = "", mp: str = "all") -> str:
    tabs = "".join(
        f'<a class="tab{" on" if mp == m else ""}" href="/?mp={m}">{esc(mp_label(m))}</a>'
        for m in MPS
    )
    return (
        f'<div class="nav">'
        f'<a class="logo" href="/">shopverse<span class="logo-dot">.</span></a>'
        f'<form class="search" action="/" method="get">'
        f'<input type="hidden" name="mp" value="{esc(mp)}"/>'
        f'<input name="q" value="{esc(q)}" placeholder="Search products" aria-label="search"/>'
        f'<button type="submit">🔍</button></form>'
        f'<a class="seller-link" href="/seller">Seller Central</a>'
        f"</div>"
        f'<div class="subnav"><a class="tab{" on" if mp == "all" else ""}" href="/?mp=all">All</a>{tabs}</div>'
    )


def _page(title: str, body: str) -> str:
    return (
        f"<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"/>"
        f'<meta name="viewport" content="width=device-width, initial-scale=1"/>'
        f"<title>{esc(title)} · shopverse</title>"
        f"<style>{STYLE}</style></head><body>{body}"
        f'<div class="footer">shopverse — demo marketplace seeded by cross-eshop-agent · '
        f'<a href="/seller">Seller Central</a></div></body></html>'
    )


def render_home(listings: list[dict], q: str = "", mp: str = "all") -> str:
    cards = "".join(_card(l) for l in listings) or (
        '<div class="empty">No listings yet — publish from cross-eshop-agent, '
        'or run <code>python server.py --demo</code> to seed samples.</div>'
    )
    heading = f'Results for “{esc(q)}”' if q else f"{esc(mp_label(mp) if mp != 'all' else 'All marketplaces')}"
    return _page(
        "shopverse",
        _nav(q, mp)
        + f'<div class="wrap"><h1 class="h">{heading} <span class="muted small">({len(listings)})</span></h1>'
        f'<div class="grid">{cards}</div></div>',
    )


def render_product(listing: dict) -> str:
    bullets = "".join(
        f'<li class="bullet">{esc(b)}</li>' for b in (listing.get("bullets") or [])
    )
    keywords = " ".join(
        f'<span class="kw">{esc(k)}</span>' for k in (listing.get("keywords") or [])
    )
    claim = esc(listing.get("claim") or "")
    lid = esc(listing.get("listing_id"))
    created = esc(_fmt_dt(listing.get("created_at")))
    return _page(
        str(listing.get("title")),
        _nav()
        + f'<div class="wrap pdp">'
        f'<div class="crumbs muted small">{"Shopverse › " + esc(mp_label(str(listing.get("marketplace"))))} · {lid}</div>'
        f'<div class="pdp-grid">'
        f'<div class="pdp-img"><img src="/img/{lid}.svg" alt="{esc(listing.get("title"))}"/></div>'
        f'<div class="pdp-info">'
        f"<h1>{esc(listing.get('title'))}</h1>"
        f"<div>{badge(listing)} {stars(listing)}</div>"
        f'<div class="price big">{display_price(listing)}</div>'
        f'<div class="stock">In Stock · Ships from Shopverse Fulfillment</div>'
        f"<div class=\"claim\">“{claim}”</div>"
        f"<h2>About this item</h2>"
        f'<ul class="bullets">{bullets}</ul>'
        f"<div>{keywords}</div>"
        f"</div>"
        f'<div class="buybox">'
        f'<div class="price">{display_price(listing)}</div>'
        f'<div class="stock">In Stock</div>'
        f'<div class="muted small">Sold by <b>{esc(listing.get("brand") or "Shopverse Seller")}</b></div>'
        f'<div class="muted small">Listed {created}</div>'
        f'<button class="btn" disabled>Add to Cart</button>'
        f'<button class="btn alt" disabled>Buy Now</button>'
        f"</div>"
        f"</div>"
        f'<div class="desc"><h2>Product description</h2><p>{esc(listing.get("description"))}</p></div>'
        f"</div>",
    )


def render_seller(listings: list[dict]) -> str:
    rows = "".join(
        "<tr>"
        f'<td><code>{esc(l.get("listing_id"))}</code></td>'
        f"<td>{badge(l)}</td>"
        f'<td><a href="/product/{esc(l.get("listing_id"))}">{esc(l.get("title"))}</a></td>'
        f'<td>{display_price(l)}</td>'
        f'<td><span class="pill">{esc(l.get("status") or "live")}</span></td>'
        f"<td class='muted'>{esc(_fmt_dt(l.get('created_at')))}</td>"
        f"<td class='muted small'>{esc(l.get('workflow_id'))}</td>"
        "</tr>"
        for l in listings
    ) or '<tr><td colspan="7" class="empty">No listings ingested yet.</td></tr>'
    return _page(
        "Seller Central",
        _nav()
        + '<div class="wrap"><h1 class="h">Seller Central · Inventory</h1>'
        '<table class="tbl"><thead><tr><th>Listing ID</th><th>Marketplace</th>'
        "<th>Title</th><th>Price</th><th>Status</th><th>Listed</th><th>Workflow</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></div>",
    )


def render_not_found(listing_id: str) -> str:
    return _page(
        "Not found",
        _nav()
        + f'<div class="wrap"><div class="empty">Listing <code>{esc(listing_id)}</code> '
        "does not exist (or was not ingested by this demo marketplace).</div></div>",
    )


def _fmt_dt(value: object) -> str:
    if not value:
        return ""
    try:
        return datetime.fromisoformat(str(value)).strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return str(value)
