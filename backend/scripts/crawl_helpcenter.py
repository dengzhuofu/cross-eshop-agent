"""真实电商客服文档爬取入库（M9 Agentic RAG 语料管道）。

用法（一次性，真机跑）：
  cd backend
  PYTHONUTF8=1 .venv/Scripts/python.exe scripts/crawl_helpcenter.py            # 抓取+入库
  PYTHONUTF8=1 .venv/Scripts/python.exe scripts/crawl_helpcenter.py --dry-run  # 只抓取切块打印统计

语料：Shopify Help Center / eBay Seller Help / Amazon 公开帮助页共 10 页
（Etsy、TikTok Shop 反爬拦截，已探活弃用）。切块经 app.rag.ingest 结构感知
切块，嵌入经 embed_texts（引擎跟随 .env，与运行时检索同引擎），入库
knowledge_base 并带 meta.source="webcrawl"；重跑先删后灌（幂等）。

抓取走 subprocess 系统 curl：httpx 的 TLS 指纹会被 Shopify/Amazon 的反爬
（403）识别，curl 可过；eBay 是 JS 壳页，由短正文守卫自动跳过——单页失败
不炸整批，反爬自愈是管道的显式行为。
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app.llm.embeddings import embed_texts  # noqa: E402
from app.persistence.db import adispose_database  # noqa: E402
from app.persistence.migrations import upgrade_head  # noqa: E402
from app.persistence.repositories.workflow_repo import WorkflowRepository  # noqa: E402
from app.rag.ingest import chunk_sections, html_to_sections  # noqa: E402

TENANT_ID = "t_demo_acme"
SOURCE = "webcrawl"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
MIN_PAGE_CHARS = 200  # 正文过短视为反爬拦截页，跳过

# 已探活（HTTP 200）的真实帮助中心页面；category 按知识分类映射
PAGES = [
    {
        "url": "https://help.shopify.com/en/manual/orders/refunds",
        "category": "policy",
        "slug": "shopify_refunds",
    },
    {
        "url": "https://help.shopify.com/en/manual/checkout-settings/refund-rules",
        "category": "policy",
        "slug": "shopify_refund_rules",
    },
    {
        "url": "https://www.ebay.com/help/selling/managing-return-requests/returns-refunds?id=4097",
        "category": "policy",
        "slug": "ebay_returns_refunds",
    },
    {
        "url": "https://www.amazon.com/gp/help/customer/display.html?nodeId=G201910180",
        "category": "policy",
        "slug": "amazon_help_node",
    },
    {
        "url": "https://help.shopify.com/en/manual/fulfillment/shopify-shipping/shipping-rates",
        "category": "platform_rule",
        "slug": "shopify_shipping_rates",
    },
    {
        "url": "https://help.shopify.com/en/manual/fulfillment/setup/fulfillment-services",
        "category": "platform_rule",
        "slug": "shopify_fulfillment_services",
    },
    {
        "url": "https://help.shopify.com/en/manual/promoting-marketing/analyze-marketing/measurement-track-conversions",
        "category": "platform_rule",
        "slug": "shopify_conversions",
    },
    {
        "url": "https://help.shopify.com/en/manual/selling-online/product-marketplaces/sales-channels",
        "category": "platform_rule",
        "slug": "shopify_sales_channels",
    },
    {
        "url": "https://www.ebay.com/help/selling/posting-items/setting-postage-options?id=4129",
        "category": "platform_rule",
        "slug": "ebay_postage",
    },
    {
        "url": "https://sell.amazon.com/pricing",
        "category": "platform_rule",
        "slug": "amazon_pricing",
    },
]


async def _fetch(url: str) -> str | None:
    """单页抓取（subprocess curl）；非 200/超时返回 None（跳过不炸整批）。"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "curl", "-s", "-L", "--max-time", "30",
            "-A", USER_AGENT,
            "-H", "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "-H", "Accept-Language: en-US,en;q=0.9",
            "-w", "\n%{http_code}",
            url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _err = await proc.communicate()
        text = out.decode("utf-8", errors="replace")
        body, _, code = text.rpartition("\n")
        if code.strip() != "200":
            print(f"  [skip] HTTP {code.strip() or proc.returncode} {url}")
            return None
        return body
    except (OSError, asyncio.TimeoutError) as exc:
        print(f"  [skip] fetch failed: {exc}")
        return None


async def main() -> None:
    parser = argparse.ArgumentParser(description="爬取真实电商客服文档并切块入库")
    parser.add_argument("--dry-run", action="store_true", help="只抓取切块打印统计，不入库")
    args = parser.parse_args()

    await upgrade_head()
    repo = WorkflowRepository()
    if args.dry_run:
        await adispose_database()
        repo = None  # type: ignore[assignment]

    total_chunks = 0
    pages_chunks: list[tuple[dict, list]] = []
    for page in PAGES:
        html = await _fetch(page["url"])
        if html is None:
            pages_chunks.append((page, []))
            continue
        title, sections = html_to_sections(html)
        plain_len = sum(len(s.text) for s in sections)
        if plain_len < MIN_PAGE_CHARS:
            print(f"  [skip] 正文过短({plain_len}字符，疑似拦截/JS壳页) {page['url']}")
            pages_chunks.append((page, []))
            continue
        chunks = chunk_sections(title or page["slug"], sections)
        print(f"  {page['slug']}: {len(sections)} 节 → {len(chunks)} 块 ({plain_len} 字符)")
        pages_chunks.append((page, chunks))
        total_chunks += len(chunks)
        time.sleep(1.0)  # 礼貌抓取

    if args.dry_run:
        print(f"dry-run: 共 {total_chunks} 块（未入库）")
        return

    assert repo is not None
    await repo.ensure_tenant(TENANT_ID, "Acme Cross-border")
    deleted = await repo.delete_knowledge_by_source(tenant_id=TENANT_ID, source=SOURCE)
    print(f"幂等清理: 删除旧 webcrawl 语料 {deleted} 行")

    engine = "hash"
    for page, chunks in pages_chunks:
        if not chunks:
            continue
        texts = [c.content for c in chunks]
        vectors, _usage, engine = await embed_texts(texts)
        slug = page["slug"].upper()
        for chunk, emb in zip(chunks, vectors):
            ref = f"WEB-{slug}-{chunk.index + 1:02d}"
            await repo.insert_knowledge(
                tenant_id=TENANT_ID,
                category=page["category"],
                title=chunk.title,
                content=chunk.content,
                embedding=emb,
                ref=ref,
                meta={
                    "source": SOURCE,
                    "source_url": page["url"],
                    "heading_path": chunk.heading_path,
                    "chunk_index": chunk.index,
                    "lang": "en",
                },
            )
        print(f"  入库 {page['slug']}: {len(chunks)} 块")

    await adispose_database()
    print(f"done: {total_chunks} 块 webcrawl 语料入库（嵌入引擎={engine}，租户={TENANT_ID}）")


if __name__ == "__main__":
    asyncio.run(main())
