"""结构感知切块（M9）：HTML/Markdown → 语义节 → 检索块。

切块原则（docs/RAG_DESIGN.md §4）：
- 按文档结构（标题栈）切节，不按固定窗口硬切——节是语义完整单位；
- 节内按段落贪心装块，超长单段按句边界硬切；
- 相邻块携带尾部重叠，跨块引用（"详见下文"类）不断裂；
- 过短节并入下一节，避免碎片块稀释向量。
"""

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

_MAX_CHARS_DEFAULT = 800
_OVERLAP_DEFAULT = 120
_MIN_SECTION_CHARS = 40
# 超长单段的硬切边界（句号/问叹号/换行后）
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?\n])")


@dataclass
class Section:
    """一个标题下的正文段集合（heading_path 为 h1→h4 标题栈）。"""

    heading_path: list[str] = field(default_factory=list)
    paragraphs: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(self.paragraphs)


@dataclass
class Chunk:
    """入库最小单元：独立嵌入、独立参与 BM25。"""

    title: str
    heading_path: list[str]
    content: str
    index: int


class _SectionExtractor(HTMLParser):
    """stdlib HTML → (主标题, 分节正文)。剔除脚本/样式与导航类子树。"""

    _SKIP_TAGS = {"script", "style", "nav", "header", "footer", "aside", "noscript", "svg"}
    _HEADINGS = {"h1", "h2", "h3", "h4"}
    _BLOCKS = {"p", "li", "blockquote", "td", "dd"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._title_tag_seen = False
        self.sections: list[Section] = []
        self._heading_stack: list[str] = []
        self._skip_depth = 0
        self._buf: list[str] = []
        self._in_block = False

    # -- 标签生命周期 ---------------------------------------------------------

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str]]) -> None:
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag in self._HEADINGS:
            self._flush_block()
            self._heading_stack = self._heading_stack[: len(self._heading_stack)]
            level = int(tag[1])
            # 同级标题弹出栈内更深层级（h3 出现在 h2 下 → 栈 [h1, h2]）
            self._heading_stack = self._heading_stack[: level - 1]
            self._pending_heading = True
        elif tag in self._BLOCKS:
            self._flush_block()
            self._in_block = True
        elif tag == "title":
            self._title_tag_seen = True

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag in self._HEADINGS:
            self._flush_block()
        elif tag in self._BLOCKS:
            self._flush_block()
            self._in_block = False
        elif tag == "title":
            self._title_tag_seen = False

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = data.strip()
        if not text:
            return
        if self._title_tag_seen and not self.title:
            self.title = text
            return
        if getattr(self, "_pending_heading", False):
            self._heading_stack.append(text)
            self._pending_heading = False
            self.sections.append(Section(heading_path=list(self._heading_stack)))
            return
        if self._in_block or self.sections:
            if not self.sections:
                self.sections.append(Section(heading_path=[]))
            self._buf.append(text)

    # -- 缓冲 ---------------------------------------------------------------

    def _flush_block(self) -> None:
        if self._buf and self.sections:
            self.sections[-1].paragraphs.append(" ".join(self._buf))
        self._buf = []


def html_to_sections(html: str) -> tuple[str, list[Section]]:
    """HTML → (页面主标题, 分节正文)。主标题优先取第一个 h1，缺省回退 <title>。"""
    extractor = _SectionExtractor()
    extractor.feed(html)
    h1 = next((s.heading_path[-1] for s in extractor.sections if s.heading_path), "")
    title = h1 or extractor.title
    # 丢掉完全无正文的节
    sections = [s for s in extractor.sections if s.text.strip()]
    return title, sections


def _hard_split_long_paragraph(paragraph: str, max_chars: int) -> list[str]:
    """超长单段按句边界硬切（句号/换行后），切不动再按字符硬切。"""
    if len(paragraph) <= max_chars:
        return [paragraph]
    pieces: list[str] = []
    for sentence in _SENTENCE_SPLIT.split(paragraph):
        while len(sentence) > max_chars:
            pieces.append(sentence[:max_chars])
            sentence = sentence[max_chars:]
        if sentence.strip():
            pieces.append(sentence)
    return pieces


def _pack_paragraphs(paragraphs: list[str], max_chars: int, overlap_chars: int) -> list[str]:
    """段落贪心装块 + 相邻块尾部重叠。返回块文本列表。"""
    blocks: list[str] = []
    current = ""
    for para in paragraphs:
        for piece in _hard_split_long_paragraph(para, max_chars):
            candidate = f"{current}\n{piece}" if current else piece
            if len(candidate) <= max_chars:
                current = candidate
                continue
            if current:
                blocks.append(current)
            tail = current[-overlap_chars:] if overlap_chars and current else ""
            current = f"{tail}\n{piece}" if tail else piece
            # 单 piece 仍可能超长（已在 _hard_split 兜底，这里再保一次）
            while len(current) > max_chars:
                blocks.append(current[:max_chars])
                current = current[max_chars:]
    if current.strip():
        blocks.append(current)
    return blocks


def chunk_sections(
    title: str,
    sections: list[Section],
    *,
    max_chars: int = _MAX_CHARS_DEFAULT,
    overlap_chars: int = _OVERLAP_DEFAULT,
) -> list[Chunk]:
    """分节切块：过短节并入下一节，节内贪心装块，块间尾部重叠。"""
    merged: list[Section] = []
    for section in sections:
        if merged and len(section.text) < _MIN_SECTION_CHARS:
            merged[-1].paragraphs.extend(section.paragraphs)
            continue
        if merged and merged[-1].heading_path == section.heading_path:
            merged[-1].paragraphs.extend(section.paragraphs)
            continue
        merged.append(section)

    chunks: list[Chunk] = []
    for section in merged:
        heading = " > ".join(section.heading_path) if section.heading_path else title
        for block in _pack_paragraphs(section.paragraphs, max_chars, overlap_chars):
            chunks.append(
                Chunk(
                    title=title,
                    heading_path=list(section.heading_path),
                    content=f"{heading}\n{block}".strip(),
                    index=len(chunks),
                )
            )
    return chunks


def chunk_markdown(
    text: str,
    *,
    max_chars: int = _MAX_CHARS_DEFAULT,
    overlap_chars: int = _OVERLAP_DEFAULT,
) -> list[Chunk]:
    """markdown 变体：#/##/### 标题行切节后走 chunk_sections（给本地文档用）。"""
    title = ""
    sections: list[Section] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        heading = re.match(r"^(#{1,4})\s+(.*)$", line)
        if heading:
            level, text_part = len(heading.group(1)), heading.group(2).strip()
            if level == 1 and not title:
                title = text_part
            stack = sections[-1].heading_path if sections else []
            sections.append(Section(heading_path=[*stack[: level - 1], text_part]))
        elif line.strip():
            if not sections:
                sections.append(Section(heading_path=[]))
            sections[-1].paragraphs.append(line.strip())
    if not title:
        first = sections[0].heading_path if sections else []
        title = first[-1] if first else "document"
    return chunk_sections(title, sections, max_chars=max_chars, overlap_chars=overlap_chars)
