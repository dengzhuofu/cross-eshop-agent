"""检索与 hash 嵌入共用的规范分词器（M9）。

三层词袋，全部确定可复现：
1. 拉丁词：``[a-z0-9]+``（小写化后），覆盖英文文档与 SKU/单号等；
2. CJK 词：jieba ``cut_for_search`` 搜索粒度，中文主语料的主语义单位；
3. CJK 单字：保证「退换货 / 退货」这类词面不同但字面重叠的说法仍有召回。

写入侧（hash 嵌入）与查询侧（BM25 / hash 查询向量）必须用同一分词器，
两侧词袋才可比——这是离线检索质量的根基。
"""

import re

_CJK = re.compile(r"[\u4e00-\u9fff]")


def tokenize(text: str) -> list[str]:
    """文本 → token 列表（拉丁词 + CJK 词 + CJK 单字；顺序保留，不去重）。"""
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    try:
        import jieba
    except ImportError:  # pragma: no cover - pyproject 已声明 jieba，此分支仅防御
        jieba = None
    if jieba is not None:
        tokens.extend(w for w in jieba.cut_for_search(text) if _CJK.search(w))
        cjk_chars = "".join(w for w in jieba.cut_for_search(text) if _CJK.search(w))
    else:
        cjk_chars = "".join(_CJK.findall(text))
    tokens.extend(_CJK.findall(cjk_chars))
    return tokens
