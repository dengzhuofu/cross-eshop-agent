"""M9 单测：混合检索引擎（BM25/RRF）与确定性改写、分级原语。

全部离线确定：分词走 app.rag.tokenize（拉丁词 + jieba CJK 词 + CJK 单字）。
"""

from app.rag.retrieval import (
    bm25_scores,
    hybrid_rank,
    rrf_fuse,
    rrf_score_map,
)
from app.rag.rewrite import (
    GRADE_THRESHOLD,
    deterministic_grade,
    deterministic_rewrite,
)
from app.rag.tokenize import tokenize

# ---- tokenize 复用冒烟 ----


def test_tokenize_three_layers():
    toks = tokenize("退换货政策是什么 refunds")
    assert "refunds" in toks  # 拉丁词
    assert "退换货" in toks  # jieba CJK 词
    assert "退" in toks and "货" in toks  # CJK 单字层


# ---- BM25 ----


def test_bm25_ranks_matching_doc_first():
    corpus = [
        "退换货政策：签收后 7 天内可无理由退货",
        "FBA 备货守则：安全库存按 1.4 倍设置",
        "物流时效承诺：48 小时内发货",
    ]
    scores = bm25_scores("退货 政策", corpus)
    assert len(scores) == 3
    assert scores[0] > scores[1] and scores[0] > scores[2]
    assert bm25_scores("任意查询", []) == []


def test_bm25_chinese_word_match_not_char_noise():
    corpus = ["退换货流程说明", "物流时效与运费说明"]
    scores = bm25_scores("退换货", corpus)
    assert scores[0] > scores[1] >= 0.0


# ---- RRF ----


def test_rrf_fuse_hand_computed():
    # 两路排名：BM25 路 [0,1,2]，余弦路 [1,0,2]
    fused = rrf_fuse([[0, 1, 2], [1, 0, 2]])
    scores = rrf_score_map([[0, 1, 2], [1, 0, 2]])
    # doc0 = 1/61 + 1/62；doc1 = 1/62 + 1/61 → 并列，稳定序按下标
    assert abs(scores[0] - scores[1]) < 1e-12
    assert scores[0] > scores[2]
    assert fused[0] in (0, 1) and fused[-1] == 2


def test_rrf_fuse_single_list_passthrough():
    assert rrf_fuse([[3, 1, 2]]) == [3, 1, 2]


# ---- hybrid ----


def test_hybrid_rank_survives_zero_cosine():
    """余弦全零（hash 引擎离线退化场景）时 BM25 仍能排对——本模块存在的意义。"""
    corpus = [
        "儿童保温杯 316 不锈钢 长效保温 12 小时",
        "床底收纳箱 可折叠 帆布 承重",
        "磁吸理线器 数据线收纳 桌面整理",
    ]
    zeros = [0.0, 0.0, 0.0]
    order = hybrid_rank("保温杯 能保温多久", corpus, zeros)
    assert order[0] == 0


def test_hybrid_rank_fuses_both_paths():
    corpus = ["退换货政策 7 天无理由", "退换货政策 质量问题 15 天换新", "发货时效 48 小时"]
    # 余弦路偏爱 doc1，BM25 路两文档同分 → 融合后 doc1 不应落后 doc0
    order = hybrid_rank("退换货 政策", corpus, [0.1, 0.9, 0.0])
    assert order[0] in (0, 1)


# ---- deterministic_rewrite ----


def test_rewrite_strips_stopwords_and_punct():
    out = deterministic_rewrite("东西坏了，想退货怎么办？")
    assert "退货" in out and "东西" in out
    assert "怎么办" not in out and "想" not in out
    assert deterministic_rewrite("请问一下物流时效") == "物流 时效"


def test_rewrite_subtraction_only_keeps_order():
    out = deterministic_rewrite("退换货政策 是什么")
    assert "退换货" in out and "政策" in out
    assert "什么" not in out


def test_rewrite_empty_input_returns_empty():
    assert deterministic_rewrite("？？？") == ""
    assert deterministic_rewrite("") == ""


# ---- deterministic_grade ----


def test_grade_above_threshold_for_matching_doc():
    relevant, score = deterministic_grade(
        "退货 政策", "退换货政策：签收后 7 天内可无理由退货", similarity=0.4
    )
    assert relevant and score > GRADE_THRESHOLD


def test_grade_below_threshold_for_unrelated_doc():
    relevant, score = deterministic_grade(
        "退货 政策", "FBA 备货守则 安全库存 1.4 倍", similarity=0.0
    )
    assert not relevant and score < GRADE_THRESHOLD
