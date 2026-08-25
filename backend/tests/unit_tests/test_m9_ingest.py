"""M9 单测：结构感知切块（HTML 解析 / 分节 / 贪心装块 / 重叠）。全部内联字符串，零网络。"""

from app.rag.ingest import chunk_markdown, chunk_sections, html_to_sections

HTML_SAMPLE = """<html><head><title>Fallback Title</title>
<style>.x{color:red}</style></head><body>
<nav>首页 登录 购物车</nav>
<h1>Refunds</h1>
<p>Shopify lets you refund orders partially or fully.</p>
<h2>How refunds work</h2>
<p>When you refund an order, the customer gets money back.</p>
<ul><li>Refund to original payment method</li><li>Restock items option</li></ul>
<script>trackPageView();</script>
<h2>Refund timing</h2>
<p>It takes 3-5 business days for the bank to process.</p>
</body></html>"""


def test_html_to_sections_structure():
    title, sections = html_to_sections(HTML_SAMPLE)
    assert title == "Refunds"
    paths = [s.heading_path for s in sections]
    assert ["Refunds"] in paths
    assert ["Refunds", "How refunds work"] in paths
    assert ["Refunds", "Refund timing"] in paths
    # script/nav/style 文本必须被剔除
    all_text = "\n".join(s.text for s in sections)
    assert "trackPageView" not in all_text
    assert "购物车" not in all_text
    assert ".x{color:red}" not in all_text
    # li 内容收进段落
    assert "Restock items option" in all_text


def test_html_to_sections_title_fallback():
    html = "<html><body><h2>Only Section</h2><p>Some content here.</p></body></html>"
    title, sections = html_to_sections(html)
    assert title == "Only Section"
    assert sections and sections[0].text


def test_chunk_sections_overlap_and_cap():
    para = "这是一段很长的测试文本。" * 60  # ~600 字
    sections = [
        type(
            "S",
            (),
            {
                "heading_path": ["Doc"],
                "paragraphs": [para, para],
                "text": para + para,
            },
        )()
    ]
    chunks = chunk_sections("Doc", sections, max_chars=800, overlap_chars=120)
    assert len(chunks) >= 2, "超长节应切成多块"
    for chunk in chunks:
        assert len(chunk.content) <= 800 + 130  # 标题行 + 少量余量
    # 相邻块尾部重叠：第二块应包含第一块的尾部片段
    tail = chunks[0].content[-120:]
    assert tail[40:] in chunks[1].content or chunks[1].content.startswith(tail[:40])


def test_short_sections_merge_forward():
    long_text = "这是足够长的一节内容" * 10
    sections = [
        type("S", (), {"heading_path": ["A"], "paragraphs": [long_text], "text": long_text})(),
        type("S", (), {"heading_path": ["B"], "paragraphs": ["短"], "text": "短"})(),
    ]
    chunks = chunk_sections("T", sections, max_chars=800, overlap_chars=0)
    assert len(chunks) == 1, "过短节应并入前一节"
    assert "短" in chunks[0].content and "足够长" in chunks[0].content


def test_chunk_markdown_headings():
    long_a = "第一节正文，内容足够长一些以便独立成块不被合并。" * 3
    long_b = "第二节正文，同样需要足够的长度来通过最短节检查。" * 3
    md = f"""# 主文档
概述段落内容，也需要一定的长度来避免被当作碎片处理。
## 第一节
{long_a}
### 子节
子节正文内容，这里也写长一点保证切块稳定。
## 第二节
{long_b}
"""
    chunks = chunk_markdown(md, max_chars=800, overlap_chars=0)
    assert len(chunks) >= 3
    assert any("第一节" in c.content for c in chunks)
    assert any("子节" in c.content for c in chunks)
    assert any("第二节" in c.content for c in chunks)
