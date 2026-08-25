"""分诊子 agent：归类归因 + 沉淀路由（M10）。

设计立场与全仓库一致——LLM 只提议，代码做硬保证：
- 分类器是确定性关键词规则（零网络、可单测），LLM 增强只在规则之上收窄归因；
- LLM 返回的 category 不在 TAXONOMY 内 → 整体弃用，回退规则结果；
- 沉淀通道全部走既有治理设施：候选知识要人工审批才进检索池、经验回写先脱敏、
  badcase 走 M7 隔离状态机。反馈永远不可能直接改写线上行为。
"""

import json
import logging
from pathlib import Path

from app.config import get_settings
from app.guardrails.badcases import run_all_detectors, scrub_untrusted
from app.llm import extract_json, get_llm_client, llm_enabled

logger = logging.getLogger(__name__)

# ---- 分诊 taxonomy：类别 → 默认沉淀 sink（代码路由的唯一依据）----
TAXONOMY: dict[str, dict[str, str]] = {
    "positive": {"sink": "none", "desc": "正反馈，统计留痕"},
    "kb_gap": {"sink": "knowledge_candidate", "desc": "知识库缺少该事实/文档"},
    "retrieval_miss": {"sink": "golden_candidate", "desc": "知识库有但没检索到/检索排序不对"},
    "hallucination": {"sink": "badcase_memory", "desc": "生成内容编造或与知识/工具矛盾"},
    "claim_violation": {"sink": "badcase_memory", "desc": "绝对化/违禁承诺"},
    "stale_conflict": {"sink": "badcase_memory", "desc": "时效信息过期或与实时数据冲突(ETA等)"},
    "data_mismatch": {"sink": "badcase_memory", "desc": "工具数据与选题/事实矛盾"},
    "tone_quality": {"sink": "memory_only", "desc": "语气/表达/格式问题，内容本身没错"},
    "other": {"sink": "none", "desc": "无法归类"},
}

# badcase 类 sink 落表用的 BadCaseCategory 映射（M7 八类分类法）
_BADCASE_CATEGORY = {
    "hallucination": "output_runaway",
    "claim_violation": "output_runaway",
    "stale_conflict": "biz_violation",
    "data_mismatch": "tool_failure",
}

# detector 注册表命中 → 分诊类别（护栏优先于一切规则：违禁声明必须进隔离，
# 不管用户吐槽的关键词像什么）
_DETECTOR_CATEGORY = {
    "output_absolute_claims": "claim_violation",
    "memory_poisoning": "claim_violation",
    "input_injection": "other",
}

_RULES: list[tuple[str, tuple[str, ...]]] = [
    # 顺序即优先级：越具体的越先匹配
    ("claim_violation", ("保证", "100%", "治愈", "根治", "全网最低", "guarantee", "miracle")),
    ("stale_conflict", ("过期", "时效不对", "时间不对", "eta", "预计送达", "outdated", "stale")),
    ("data_mismatch", ("数据矛盾", "前后不一", "不一致", "对不上", "mismatch", "contradict")),
    (
        "hallucination",
        ("编造", "瞎编", "虚构", "胡说", "与事实不符", "made up", "fabricat", "hallucin"),
    ),
    (
        "retrieval_miss",
        ("答非所问", "不相关", "跑偏", "离题", "检索错", "irrelevant", "wrong article"),
    ),
    (
        "kb_gap",
        ("查不到", "没有这条", "缺失", "缺少", "未覆盖", "没有相关",
         "知识库没有", "missing", "not covered"),
    ),
    ("tone_quality", ("语气", "态度", "生硬", "太机器", "不礼貌", "tone", "rude", "robotic")),
]


def deterministic_triage(verdict: str, comment: str, quote: str) -> dict:
    """规则分类打底：护栏检测 → 关键词规则 → 兜底 other。零网络。"""
    text = f"{comment or ''} {quote or ''}"
    if verdict == "helpful":
        return _pack("positive", "用户认可产出，留痕统计", ["verdict=helpful"])
    hits = run_all_detectors(text)
    if hits:
        cat = next(
            (_DETECTOR_CATEGORY.get(h.detector, "other") for h in hits
             if h.detector in _DETECTOR_CATEGORY),
            None,
        )
        if cat:
            return _pack(
                cat,
                f"触发 {hits[0].detector} 护栏（{len(hits)} 处），按违禁/不可信处理",
                [h.detector for h in hits],
            )
    lowered = text.lower()
    for category, keywords in _RULES:
        matched = [k for k in keywords if k in lowered]
        if matched:
            return _pack(
                category,
                f"{TAXONOMY[category]['desc']}；命中关键词 {'/'.join(matched[:3])}",
                matched[:5],
            )
    return _pack("other", "负反馈但无强特征，人工复核", [])


def _pack(category: str, root_cause: str, rule_hits: list) -> dict:
    return {
        "category": category,
        "root_cause": root_cause,
        "sink": TAXONOMY[category]["sink"],
        "source": "rule",
        "rule_hits": rule_hits,
    }


TRIAGE_SYSTEM_PROMPT = """你是电商 Agent 平台的用户反馈分诊官。
对用户反馈做归类归因。category 必须且只能从以下枚举选一个：
positive / kb_gap / retrieval_miss / hallucination / claim_violation /
stale_conflict / data_mismatch / tone_quality / other
只输出一个 JSON 对象，schema：
{"category": "<枚举值>", "root_cause": "一句话根因(中文,≤80字)",
 "suggested_fix": "一句话改进建议(中文,≤80字)"}
判定要点：知识库根本没有该信息选 kb_gap；有该信息但没检到/排错序选 retrieval_miss；
内容编造选 hallucination；绝对化承诺选 claim_violation；时效冲突选 stale_conflict。"""


async def llm_enrich_triage(rule_result: dict, comment: str, quote: str,
                            target_type: str) -> dict:
    """LLM 归因增强：只允许在规则结果之上细化 root_cause/suggested_fix/category。
    任何失败（无 key/超时/解析错/越界类别）都静默回退规则结果——分诊永不阻塞。"""
    if not llm_enabled() or not (comment or quote):
        return rule_result
    try:
        s = get_settings()
        result = await get_llm_client().chat(
            [
                {"role": "system", "content": TRIAGE_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(
                    {"target_type": target_type, "comment": (comment or "")[:500],
                     "quote": (quote or "")[:300]}, ensure_ascii=False)},
            ],
            temperature=s.llm_temperature,
            max_tokens=300,
        )
        parsed = extract_json(result.content)
        category = str(parsed.get("category", ""))
        if category not in TAXONOMY:
            return rule_result  # LLM 提议越界 → 弃用，规则结果兜底
        enriched = dict(rule_result)
        enriched.update({
            "category": category,
            "sink": TAXONOMY[category]["sink"],
            "root_cause": str(parsed.get("root_cause") or rule_result["root_cause"])[:120],
            "suggested_fix": str(parsed.get("suggested_fix", ""))[:120],
            "source": "llm",
        })
        return enriched
    except Exception:  # noqa: BLE001 —— 分诊增强失败不阻塞反馈主链路
        logger.exception("triage llm enrich failed; keeping rule result")
        return rule_result


CANDIDATE_DRAFT_PROMPT = """你是客服知识库编辑。根据用户反馈指出的问题，起草一条可补入
知识库的 FAQ 条目（客观事实口吻，不含承诺性措辞）。只输出 JSON：
{"title": "问题式标题(≤40字)", "content": "条目正文(中文,≤400字)"}
正文须基于反馈中给出的事实线索；线索不足时写明「待运营补充：<缺什么>」。"""


async def draft_candidate_knowledge(comment: str, quote: str) -> dict:
    """为 kb_gap 起草候选知识条目。LLM 失败降级确定性模板——草稿质量可以低，
    但闭环不能断；反正 status=candidate 要人工审批后才生效。"""
    fallback = {
        "title": f"[反馈沉淀] {(quote or comment or '知识缺口')[:36]}",
        "content": "待运营补充：用户反馈指出知识缺口。\n"
                   f"反馈评论：{(comment or '(无)')[:300]}\n"
                   f"相关引用：{(quote or '(无)')[:300]}",
    }
    if not llm_enabled() or not (comment or quote):
        return fallback
    try:
        s = get_settings()
        result = await get_llm_client().chat(
            [
                {"role": "system", "content": CANDIDATE_DRAFT_PROMPT},
                {"role": "user", "content": json.dumps(
                    {"comment": (comment or "")[:500], "quote": (quote or "")[:300]},
                    ensure_ascii=False)},
            ],
            temperature=s.llm_temperature,
            max_tokens=600,
        )
        parsed = extract_json(result.content)
        title = str(parsed.get("title", "")).strip()
        content = str(parsed.get("content", "")).strip()
        if not title or not content:
            return fallback
        return {"title": title[:60], "content": content[:600]}
    except Exception:  # noqa: BLE001 —— 模板兜底，闭环不断
        logger.exception("candidate knowledge draft failed; using template")
        return fallback


def append_golden_candidate(feedback_id: str, query: str, note: str,
                            *, path: str | None = None) -> str | None:
    """retrieval_miss → 黄金查询候选集（JSONL）。喂给 evals/rag_evals.py --feedback-report
    做人工复核后转正进 rag_golden.py；文件在 .localdata（gitignore），不污染语料库。
    path 参数供测试注入临时路径。"""
    p = Path(path or get_settings().feedback_golden_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    q = (query or "").strip()
    if not q:
        return None
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "query": q[:200], "expect_refs": [], "note": note[:80],
            "feedback_id": feedback_id, "status": "candidate",
        }, ensure_ascii=False) + "\n")
    return str(p)


async def triage_and_route(repo, *, tenant_id: str, feedback_id: str, workflow_id: str | None,
                           target_type: str, verdict: str, comment: str, quote: str) -> dict:
    """分诊 + 沉淀路由的编排入口（API 层调用）。返回 triage 结果（含 sink_ref）。

    repo 参数用 WorkflowRepository 即可（避免循环 import 不做类型标注）。
    """
    # 反馈文本按不可信输入处理：先脱敏再进任何沉淀通道
    clean_comment, scrubbed_c = scrub_untrusted(comment or "")
    clean_quote, scrubbed_q = scrub_untrusted(quote or "")
    if scrubbed_c or scrubbed_q:
        logger.warning("feedback %s scrubbed %d suspicious fragments",
                       feedback_id, len(scrubbed_c) + len(scrubbed_q))

    triage = deterministic_triage(verdict, clean_comment, clean_quote)
    triage = await llm_enrich_triage(triage, clean_comment, clean_quote, target_type)

    sink_ref: str | None = None
    sink = triage["sink"]

    if sink == "knowledge_candidate":
        draft = await draft_candidate_knowledge(clean_comment, clean_quote)
        from app.llm.embeddings import embed_texts
        vectors, _u, _engine = await embed_texts([f"{draft['title']} {draft['content']}"])
        kid = await repo.insert_knowledge(
            tenant_id=tenant_id, category="faq", title=draft["title"],
            content=draft["content"], embedding=vectors[0],
            ref=f"FB-{feedback_id[:8].upper()}",
            meta={"origin": "feedback", "status": "candidate", "feedback_id": feedback_id},
        )
        sink_ref = kid

    elif sink == "golden_candidate":
        sink_ref = append_golden_candidate(
            feedback_id, clean_quote or clean_comment,
            f"{target_type}:{triage['root_cause'][:50]}",
        )

    elif sink == "badcase_memory":
        await repo.insert_bad_case(
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            category=_BADCASE_CATEGORY.get(triage["category"], "biz_violation"),
            severity="high",
            detector=f"feedback_triage:{triage['category']}",
            summary=triage["root_cause"],
            evidence={"feedback_id": feedback_id,
                      "comment": clean_comment[:300], "quote": clean_quote[:300],
                      "target_type": target_type},
            status="quarantined",
        )

    if verdict == "unhelpful":
        # 全部负反馈沉淀一条经验记忆（已脱敏文本）；检索侧由既有记忆管线消费
        from app.llm.embeddings import embed_texts
        lesson = f"[{triage['category']}] {clean_comment} | 引用: {clean_quote}".strip(" |")
        if lesson.strip("[]| "):
            vectors, _u, _e = await embed_texts([lesson])
            await repo.insert_memory(
                tenant_id=tenant_id, kind="feedback",
                content=lesson[:800], embedding=vectors[0],
                source_workflow_id=workflow_id,
                meta={"origin": "feedback", "feedback_id": feedback_id},
            )

    triage["sink_ref"] = sink_ref
    new_status = "dismissed" if triage["category"] == "positive" else "triaged"
    await repo.update_feedback_triage(tenant_id, feedback_id, triage, new_status)
    return {**triage, "status": new_status}
