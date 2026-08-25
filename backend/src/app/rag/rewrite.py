"""确定性查询改写与相关性分级原语（M9）。

定位：agentic RAG 循环里 LLM 增强（改写/判级）不可用（无 key / 调用失败）
时的确定性兜底——只做减法、不改语义，行为完全可复现、可解释。
LLM 可用时节点层会与这里的分级取交集（LLM 只能收紧、不能放宽）。
"""

import re

from app.rag.tokenize import tokenize

# 高频口语虚词/客套词（精选，避免误伤实义词；改写只做整词剔除的减法）
STOPWORDS = {
    "请问", "一下", "我想", "想要", "帮忙", "麻烦", "谢谢",
    "怎么", "怎样", "怎么样", "怎么办", "如何", "为什么", "什么", "哪些", "是不是",
    "的", "了", "吗", "呢", "啊", "吧", "呀", "哈",
    "这个", "那个", "这些", "那些", "大家", "你们", "我们",
    "可以", "应该", "需要", "想", "想问", "咨询", "了解", "看看",
}

# 中英文标点（改写时剔除）
_PUNCT = re.compile(r"[，。？！、；：,.?!;:'\"“”‘’（）()\[\]【】\s]+")
_WORDISH = re.compile(r"[a-z0-9\u4e00-\u9fff]+")

GRADE_THRESHOLD = 0.15
# 合成权重：词面覆盖率 0.7 + 归一化余弦 0.3（离线 hash 余弦弱，词面为主；
# 在线 bge-m3 下余弦可信，权重仍偏词面以保可解释性）
_COVER_WEIGHT = 0.7
_COS_WEIGHT = 0.3


def deterministic_rewrite(query: str) -> str:
    """口语查询 → 检索友好查询：jieba 词级切分后剔除停用词与标点，保序拼接。

    只做减法不改语义；结果可能为空串（全停用词的极端输入），调用方应回退原查询。
    """
    try:
        import jieba
    except ImportError:  # pragma: no cover - pyproject 已声明 jieba，此分支仅防御
        jieba = None
    if jieba is not None:
        words = [w for w in jieba.cut(query) if _WORDISH.fullmatch(w)]
    else:
        words = _WORDISH.findall(query)
    kept = [w for w in words if w not in STOPWORDS]
    return " ".join(kept)


def deterministic_grade(query: str, content: str, *, similarity: float = 0.0) -> tuple[bool, float]:
    """确定性相关性分级，返回 (是否相关, 分数)。

    分数 = 0.7 × 查询 token 在内容 token 中的覆盖率 + 0.3 × max(0, 余弦)，
    阈值 GRADE_THRESHOLD=0.15。纯词面可解释：命中了哪些查询词一目了然，
    与 LLM 判级取交集时不引入黑盒。
    """
    q_tokens = set(tokenize(query))
    if not q_tokens:
        return False, 0.0
    c_tokens = set(tokenize(content))
    coverage = len(q_tokens & c_tokens) / len(q_tokens)
    score = round(_COVER_WEIGHT * coverage + _COS_WEIGHT * max(0.0, similarity), 4)
    return score >= GRADE_THRESHOLD, score
