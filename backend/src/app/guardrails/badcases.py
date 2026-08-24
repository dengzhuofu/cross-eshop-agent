"""Bad Case detector 注册表（M7，PRD §20）。

注册表模式（v1.4 §1.5）：每个 detector 独立实现、独立注册，新增一类检测 =
新增一个注册项，不动主干。全部为确定性规则（正则/黑名单），零 LLM——护栏的
硬保证不依赖模型自觉。命中即由节点层落 bad_cases 表并进入该节点的处置路径。
"""

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List

from app.domain.enums import BadCaseCategory


@dataclass
class DetectorResult:
    category: BadCaseCategory
    detector: str
    severity: str  # high | medium | low
    summary: str
    evidence: Dict[str, Any] = field(default_factory=dict)


DetectorFn = Callable[[str], List[DetectorResult]]
_REGISTRY: Dict[str, DetectorFn] = {}


def register_detector(name: str, fn: DetectorFn) -> None:
    if name in _REGISTRY:
        raise ValueError(f"detector 重复注册: {name}")
    _REGISTRY[name] = fn


def get_detector(name: str) -> DetectorFn:
    return _REGISTRY[name]


def list_detectors() -> List[str]:
    return sorted(_REGISTRY)


def run_all_detectors(text: str) -> List[DetectorResult]:
    """对一段文本跑全部注册 detector，返回全部命中（无命中返回空列表）。"""
    hits: List[DetectorResult] = []
    for name in sorted(_REGISTRY):
        hits.extend(_REGISTRY[name](text))
    return hits


# ---- A 输入异常：prompt injection（评论/供应商描述/选题里的指令性内容不可信）----

_INJECTION_RE = re.compile(
    r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions?"
    r"|disregard\s+(?:all\s+)?(?:previous|prior|above)"
    r"|忽略(?:之前|以上|前面)的?(?:所有)?(?:指令|指示|设定)"
    r"|(?:跳过|绕过|省去)(?:所有|全部)?(?:审查|校验|审核|复检|检查)"
    r"|skip\s+(?:the\s+)?(?:all\s+)?(?:reviews?|checks?|validation|qa)"
    r"|bypass\s+(?:all\s+)?(?:reviews?|checks?|validation)"
    r"|系统提示(?:词)?[:：]"
    r"|you\s+are\s+now\s+(?:a|an)\s+",
    flags=re.IGNORECASE,
)


def _detect_injection(text: str) -> List[DetectorResult]:
    matches = _INJECTION_RE.findall(text or "")
    if not matches:
        return []
    return [
        DetectorResult(
            category=BadCaseCategory.input_anomaly,
            detector="input_injection",
            severity="high",
            summary=f"检测到 prompt injection 模式 x{len(matches)}，内容按不可信数据处理",
            evidence={"patterns": [m[:60] for m in matches[:5]]},
        )
    ]


register_detector("input_injection", _detect_injection)


# ---- B 输出失控：夸大/违禁声明（Listing、客服草稿等产出物）----

# 与 nodes.BANNED_CLAIM_PHRASES 同源语义；此处含英文变体（detector 独立于生成端整形）
_BANNED_OUTPUT_RE = re.compile(
    r"保证(?:治愈|根治|不坏|正品|通过)|100%|治愈|根治"
    r"|(?:guaranteed?|cures?|100%\s+(?:safe|effective)|miracle)",
    flags=re.IGNORECASE,
)


def _detect_absolute_claims(text: str) -> List[DetectorResult]:
    matches = _BANNED_OUTPUT_RE.findall(text or "")
    if not matches:
        return []
    return [
        DetectorResult(
            category=BadCaseCategory.output_runaway,
            detector="output_absolute_claims",
            severity="high",
            summary=f"产出物含夸大/违禁声明 x{len(matches)}，须隔离重写",
            evidence={"phrases": [m.strip()[:40] for m in matches[:5]]},
        )
    ]


register_detector("output_absolute_claims", _detect_absolute_claims)


# ---- F 记忆异常：记忆投毒（供应商描述/评论里的营销话术不得入记忆）----

_POISON_RE = re.compile(
    r"全网最优|全网最低|全网第一|最低价保证|绝对正品|行业第一|销量冠军"
    r"|(?:best\s+(?:in\s+the\s+world|store)|cheapest\s+guaranteed?|#1\s+rated)",
    flags=re.IGNORECASE,
)


def _detect_memory_poisoning(text: str) -> List[DetectorResult]:
    matches = _POISON_RE.findall(text or "")
    if not matches:
        return []
    return [
        DetectorResult(
            category=BadCaseCategory.memory_anomaly,
            detector="memory_poisoning",
            severity="high",
            summary=f"文本含营销投毒话术 x{len(matches)}，禁止写入长期记忆",
            evidence={"phrases": [m.strip()[:40] for m in matches[:5]]},
        )
    ]


register_detector("memory_poisoning", _detect_memory_poisoning)


def scrub_untrusted(text: str) -> tuple[str, List[str]]:
    """输入脱敏（PRD §20.3 A 类处置「拒绝/脱敏后重试」）：把注入与投毒模式从
    不可信文本中剥离，返回（干净文本, 移除片段）。与 detector 共用同一组正则——
    检出什么就剥什么，两套规则永不漂移。剥不干净也不是问题：内容只被当作
    数据使用，不存在被执行的通道。"""
    out = text or ""
    removed: List[str] = []
    for pattern in (_INJECTION_RE, _POISON_RE):
        for m in pattern.findall(out):
            removed.append(m.strip()[:60])
        out = pattern.sub(" ", out)
    return re.sub(r"\s{2,}", " ", out).strip(), removed
