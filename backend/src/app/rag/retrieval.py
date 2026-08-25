"""混合检索引擎（M9）：BM25 词面路 + 向量余弦语义路 → RRF 融合。

动机（docs/RAG_DESIGN.md §6）：纯向量单路脆弱——兜底 hash 引擎下纯中文查询
曾全零退化；BM25 词面匹配离线确定可用，是演示/测试场景的质量保底。两路
排名经 Reciprocal Rank Fusion 融合，单路退化不拖垮整体。

全部函数离线可运行、确定可复现（分词统一走 app.rag.tokenize）。
"""

import math
from collections import Counter

from app.rag.tokenize import tokenize

BM25_K1 = 1.5
BM25_B = 0.75
RRF_K = 60


def bm25_scores(query: str, corpus: list[str]) -> list[float]:
    """标准 BM25（Okapi）：df/avgdl 按本语料统计，返回每篇文档得分（顺序对应 corpus）。

    空语料返回 []；查询无命中 token 时全 0 分（排名退化但不报错）。
    """
    n = len(corpus)
    if n == 0:
        return []
    q_tokens = tokenize(query)
    doc_tokens = [tokenize(doc) for doc in corpus]
    avgdl = (sum(len(toks) for toks in doc_tokens) / n) or 1.0
    df: Counter[str] = Counter()
    for toks in doc_tokens:
        df.update(set(toks))
    scores: list[float] = []
    for toks in doc_tokens:
        tf = Counter(toks)
        dl = len(toks) or 1  # 空文档按 dl=1 防 0 除（得分为 0，无影响）
        score = 0.0
        for term in q_tokens:
            freq = tf.get(term, 0)
            if freq == 0:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            score += idf * (freq * (BM25_K1 + 1)) / (
                freq + BM25_K1 * (1 - BM25_B + BM25_B * dl / avgdl)
            )
        scores.append(score)
    return scores


def _rank_desc(values: list[float]) -> list[int]:
    """按值降序的下标排名（稳定：同分保持原顺序，保证确定性）。"""
    return sorted(range(len(values)), key=lambda i: -values[i])


def rrf_fuse(rank_lists: list[list[int]], *, k: int = RRF_K) -> list[int]:
    """Reciprocal Rank Fusion：score(d) = Σ 1/(k + rank)，rank 从 1 计。

    输入多路排名（每路元素为文档下标，按该路质量降序），返回融合分降序的
    文档下标；同分按首次达到该分的先后（稳定排序保证确定性）。
    """
    fused: dict[int, float] = {}
    for ranking in rank_lists:
        for pos, doc_idx in enumerate(ranking, start=1):
            fused[doc_idx] = fused.get(doc_idx, 0.0) + 1.0 / (k + pos)
    return sorted(fused, key=lambda d: (-fused[d], d))


def rrf_score_map(rank_lists: list[list[int]], *, k: int = RRF_K) -> dict[int, float]:
    """与 rrf_fuse 同源的每文档融合分（供调用方留痕/解释）。"""
    scores: dict[int, float] = {}
    for ranking in rank_lists:
        for pos, doc_idx in enumerate(ranking, start=1):
            scores[doc_idx] = scores.get(doc_idx, 0.0) + 1.0 / (k + pos)
    return scores


def hybrid_rank(query: str, corpus: list[str], cosine_sims: list[float]) -> list[int]:
    """双路融合：BM25 排名 + 余弦排名 → RRF。返回文档下标（融合质量降序）。

    余弦全零（离线退化场景）时 BM25 独立支撑排序；BM25 全零时余弦兜底。
    """
    if not corpus:
        return []
    bm25 = bm25_scores(query, corpus)
    return rrf_fuse([_rank_desc(bm25), _rank_desc(cosine_sims)])
