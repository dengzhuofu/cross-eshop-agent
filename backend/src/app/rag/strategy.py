"""检索增强策略选择（M11）：用户问题 → {direct | rewrite | hyde} → 检索执行。

立场与全仓库一致——LLM 只提议，代码做硬保证：
- 策略枚举与零命中升级阶梯是代码常量；LLM 提议越界一律弃用、回退规则结果；
- 规则选择零网络、可单测：短且带精确信号（单号/型号/SKU）直检保真——改写或
  假设文档都会稀释精确词面；长而口语的问题式查询走 HyDE 做语义泛化；默认改写；
- HyDE 的假设文档只进语义路（向量余弦与原查询向量逐文档取 max），词面路
  （BM25）永远用用户原词——假设文档是 LLM 生成的，不能污染词面匹配；
- 无 LLM 时 hyde 自动降级 rewrite（假设文档无从生成），闭环不断。
"""

import re

# 策略枚举：direct=原句直检 / rewrite=查询改写 / hyde=假设性文档增强语义路
STRATEGIES: tuple[str, ...] = ("direct", "rewrite", "hyde")

# 零相关命中时的升级阶梯（每轮沿阶梯上移一档；hyde 重试换角度重新生成）
ESCALATION: dict[str, str] = {"direct": "rewrite", "rewrite": "hyde", "hyde": "hyde"}

# 精确信号：单号/型号/英文 SKU 片段——这类查询的词面本身就是最强检索特征
_HIGH_SIGNAL = re.compile(r"[A-Za-z0-9]{4,}")

# 问题式句子的标志词（命中即视为需要语义泛化的开放式提问）
_QUESTION_WORDS = (
    "怎么", "怎样", "如何", "为什么", "什么", "哪些", "能否", "是不是",
    "能不能", "可以吗", "how", "what", "why", "can i", "does",
)

_DIRECT_MAX_CHARS = 14  # 短且带精确信号 → 直检
_HYDE_MIN_CHARS = 40    # 长且问题式、无精确信号 → HyDE


def deterministic_strategy(question: str) -> str:
    """规则策略选择（零网络，兜底与离线唯一真源）：
    短+精确信号 → direct；长+问题式+无精确信号 → hyde；其余 → rewrite。"""
    text = (question or "").strip()
    if not text:
        return "rewrite"
    has_signal = bool(_HIGH_SIGNAL.search(text))
    if has_signal and len(text) <= _DIRECT_MAX_CHARS:
        return "direct"
    lowered = text.lower()
    if (
        len(text) >= _HYDE_MIN_CHARS
        and not has_signal
        and any(w in lowered for w in _QUESTION_WORDS)
    ):
        return "hyde"
    return "rewrite"


def normalize_proposal(parsed: dict | None, fallback: dict) -> dict:
    """LLM 策略提议校验（LLM 只能收窄不能放宽的同类约束）：
    strategy 不在枚举内或缺失 → 整体弃用，原样返回规则结果；
    合法提议只覆盖 strategy/strategy_source/reason 三个字段，其余键不动。"""
    s = str((parsed or {}).get("strategy") or "").strip().lower()
    if s not in STRATEGIES:
        return dict(fallback)
    out = dict(fallback)
    out.update(
        {
            "strategy": s,
            "strategy_source": "llm",
            "reason": str((parsed or {}).get("reason") or "").strip()[:120],
        }
    )
    return out
