"""M9 集成：客服 agent 的 agentic RAG 检索循环——路由分类 → 查询改写 →
混合检索 → 相关性分级 → 零命中改写重试（≤2 轮），全离线 hermetic。

conftest 清空 SILICONFLOW_API_KEY：无 LLM 时改写/分级走确定性路径、嵌入走 hash
引擎；LLM 场景用 monkeypatch 假客户端（与 M6 单测同手法）。持久化走真实
WorkflowRepository（临时 SQLite），工具调用断言读 ToolExecutor 审计表。
"""

import pytest

from app.graphs.product_launch import nodes as N
from app.graphs.product_launch.agent import graph
from app.graphs.product_launch.nodes import _classify_route, node_support
from app.graphs.product_launch.state import initial_state
from app.llm.embeddings import embed_texts
from app.observability.recorder import RunRecorder
from app.persistence.repositories.workflow_repo import WorkflowRepository

TASK = {
    "product_idea": "可折叠床底收纳箱",
    "marketplaces": ["amazon"],
    "target_market": "US",
}

DOC = {
    "category": "policy",
    "title": "退换货政策",
    "ref": "POL-RTN-07 v2.1",
    "content": (
        "7 天无理由退货（未拆封）。物流延迟处理：订单延误时可选择补偿券或继续等待，"
        "具体时效以订单实时物流轨迹为准。"
    ),
}


async def _harness(tag: str):
    """独立租户 + 独立工作流，返回 (repo, wf, rec, state)。测试库共享文件，
    各用例用独立租户 id 避免知识互串（与 M6 单测 harness 同思路）。"""
    tenant_id = f"t_m9_{tag}"
    repo = WorkflowRepository()
    await repo.ensure_tenant(tenant_id, f"Test Co {tenant_id}")
    wf = await repo.create_workflow(
        tenant_id=tenant_id,
        title=f"m9 support agentic {tag}",
        product_idea=TASK["product_idea"],
        marketplaces=TASK["marketplaces"],
        status="queued",
        input_json=TASK,
    )
    rec = RunRecorder(repo, wf.id, tenant_id)
    state = initial_state(wf.id, tenant_id, TASK)
    return repo, wf, rec, state


async def _seed_doc(tenant_id: str, *, category: str, title: str, ref: str,
                    content: str) -> None:
    repo = WorkflowRepository()
    vectors, _u, _e = await embed_texts([content])
    await repo.insert_knowledge(
        tenant_id=tenant_id,
        category=category,
        title=title,
        content=content,
        embedding=vectors[0],
        ref=ref,
    )


# ---- 1. 路由分类（确定性）----


def test_route_classification_refund_is_dual_and_consult_is_policy_only():
    """退款工单 → realtime+policy 双真；纯咨询 → policy 单真；物流求助按关键词判实时。"""
    refund = _classify_route(
        {"ticket_id": "tk_1", "type": "refund_request", "order_id": "ord_88123"}
    )
    assert refund == {"realtime": True, "policy": True}

    consult = _classify_route(
        {"ticket_id": "tk_2", "type": "policy_question", "question": "你们的退换货政策是怎样的？"}
    )
    assert consult == {"realtime": False, "policy": True}

    logistics = _classify_route(
        {"ticket_id": "tk_3", "type": "general_inquiry", "question": "我的订单到哪了"}
    )
    assert logistics["realtime"] is True


# ---- 2. 全链路（无 LLM）：确定性改写/分级 + hybrid 检索参数落审计 ----


async def test_full_run_offline_uses_deterministic_rewrite_and_hybrid_search():
    tenant_id = "t_m9_fullrun"
    repo = WorkflowRepository()
    await repo.ensure_tenant(tenant_id, f"Test Co {tenant_id}")
    await _seed_doc(
        tenant_id, category=DOC["category"], title=DOC["title"], ref=DOC["ref"],
        content=DOC["content"],
    )
    wf = await repo.create_workflow(
        tenant_id=tenant_id,
        title="m9 full run agentic rag",
        product_idea=TASK["product_idea"],
        marketplaces=TASK["marketplaces"],
        status="queued",
        input_json=TASK,
    )
    rec = RunRecorder(repo, wf.id, tenant_id)
    final_state = await graph.ainvoke(
        initial_state(wf.id, tenant_id, TASK),
        config={"configurable": {"recorder": rec}},
    )

    # 硬事实不回归：订单实时数据仍来自 get_order_status 工具
    assert final_state["support"]["order_status"]
    assert "3-5 个工作日" in final_state["support"]["draft"]
    assert final_state["support"]["refs"] == [DOC["ref"]]

    steps = await repo.steps(tenant_id, wf.id)
    detail = next(s.detail for s in steps if s.node == "support")
    # 无 LLM：改写与分级都走确定性路径
    assert detail["rewrite_source"] == "deterministic"
    assert detail["grade_source"] == "deterministic"
    assert detail["draft_source"] == "template"
    assert detail["conflict_check"]["detected"] is False
    # 路由分类进 detail：demo 工单（查单）需要实时工具 + 政策知识
    assert detail["route"] == {"realtime": True, "policy": True}
    # 检索轨迹：轮数 ≤ 2，每轮留痕 query/hits/relevant_count
    trace = detail["retrieval_trace"]
    assert 1 <= len(trace) <= 2
    assert all({"query", "hits", "relevant_count"} <= set(e) for e in trace)
    assert trace[0]["relevant_count"] == detail["rag_hits"] == 1

    # 审计表：search_knowledge 以新契约参数（mode=hybrid / grade=True）真实发生
    calls = await repo.tool_calls(tenant_id, wf.id)
    hybrid = [
        c for c in calls
        if c.tool == "search_knowledge" and (c.input_summary or {}).get("mode") == "hybrid"
    ]
    assert hybrid, "客服检索应带 mode=hybrid 参数落审计"
    assert all(
        c.input_summary.get("grade") is True and c.input_summary.get("top_k") == 5
        for c in hybrid
    )


# ---- 3. 零命中重试：最多两轮，第二轮换查询 ----


async def test_retry_loop_runs_second_round_with_alternative_query_when_zero_hits():
    repo, wf, rec, state = await _harness("retry")  # 不种任何知识 → 两轮全空
    result = await node_support(state, {"configurable": {"recorder": rec}})

    steps = await repo.steps(wf.tenant_id, wf.id)
    detail = next(s.detail for s in steps if s.node == "support")
    trace = detail["retrieval_trace"]
    assert len(trace) == 2, "零相关命中必须触发改写重试，共 2 轮"
    assert all(e["hits"] == 0 and e["relevant_count"] == 0 for e in trace)
    assert trace[0]["query"] != trace[1]["query"], "重试轮应换查询而非原样复读"

    # 重试仍无果：rag 为空但草稿照常出（工具事实模板兜底）
    assert detail["rag_hits"] == 0
    assert result["support"]["refs"] == []
    assert "3-5 个工作日" in result["support"]["draft"]
    calls = await repo.tool_calls(wf.tenant_id, wf.id)
    assert len([c for c in calls if c.tool == "search_knowledge"]) == 2


# ---- 4. policy=False 路由跳过检索 ----


async def test_policy_false_route_skips_retrieval_entirely(monkeypatch):
    repo, wf, rec, state = await _harness("skip")
    monkeypatch.setattr(
        N,
        "SUPPORT_TICKET",
        {
            "ticket_id": "tk_m9_skip",
            "type": "smalltalk",
            "question": "这个收纳箱防水吗，材质是什么",
            "order_id": "ord_88123",
        },
    )
    await node_support(state, {"configurable": {"recorder": rec}})

    steps = await repo.steps(wf.tenant_id, wf.id)
    detail = next(s.detail for s in steps if s.node == "support")
    assert detail["route"] == {"realtime": False, "policy": False}
    assert detail["retrieval_trace"] == []
    assert detail["rewrite_source"] is None and detail["grade_source"] is None
    calls = await repo.tool_calls(wf.tenant_id, wf.id)
    tools = {c.tool for c in calls}
    assert "search_knowledge" not in tools, "policy=False 不得发起检索"
    assert "get_order_status" in tools, "实时订单事实照常走业务工具"


# ---- 5. 硬保证不回归：ETA 冲突整稿弃用回模板（agentic RAG 并存）----


async def test_eta_conflict_still_discards_draft_with_agentic_rag(monkeypatch):
    repo, wf, rec, state = await _harness("conflict")
    await _seed_doc(
        wf.tenant_id, category=DOC["category"], title=DOC["title"], ref=DOC["ref"],
        content=DOC["content"],
    )

    async def _bad_llm(system, user, **kwargs):
        return (
            {"draft": "您好，订单预计 7-10 个工作日送达。", "cited_refs": [], "escalate": False},
            {"prompt": 100, "completion": 30},
        )

    monkeypatch.setattr(N, "llm_enabled", lambda: True)
    monkeypatch.setattr(N, "_call_llm_json", _bad_llm)

    result = await node_support(state, {"configurable": {"recorder": rec}})
    support = result["support"]
    assert support["eta_text"] == "3-5 个工作日"  # 事实字段始终来自工具
    assert "3-5 个工作日" in support["draft"]  # 草稿回退为工具事实模板
    assert "7-10" not in support["draft"]

    steps = await repo.steps(wf.tenant_id, wf.id)
    detail = next(s.detail for s in steps if s.node == "support")
    assert detail["conflict_check"]["detected"] is True
    assert detail["draft_source"] == "template"
    # 假客户端输出不合改写契约 → 改写降级确定性（兜底路径被真实触发）
    assert detail["rewrite_source"] == "deterministic"


# ---- 6. LLM 分级收窄：与确定性 grade 取交集，refs 白名单同步收窄 ----


async def test_llm_grading_intersects_deterministic_grade_and_narrows_refs(monkeypatch):
    repo, wf, rec, state = await _harness("grade")
    await _seed_doc(
        wf.tenant_id, category="faq", title="物流时效 FAQ", ref="FAQ-01",
        content="标准跨境物流 7-10 个工作日，优先物流 5-7 个工作日。",
    )
    await _seed_doc(
        wf.tenant_id, category=DOC["category"], title=DOC["title"], ref=DOC["ref"],
        content=DOC["content"],
    )

    async def _router_llm(system, user, **kwargs):
        if "查询改写器" in system:
            return {"query": "退换货政策 退款流程"}, {"prompt": 20, "completion": 8}
        if "质检员" in system:
            return {"relevant": ["POL-RTN-07 v2.1"]}, {"prompt": 30, "completion": 6}
        return (
            {
                "draft": "您好，订单 ord_88123 当前为国际运输中，预计 3-5 个工作日送达。",
                "cited_refs": ["POL-RTN-07 v2.1"],
                "escalate": False,
            },
            {"prompt": 100, "completion": 30},
        )

    monkeypatch.setattr(N, "llm_enabled", lambda: True)
    monkeypatch.setattr(N, "_call_llm_json", _router_llm)

    result = await node_support(state, {"configurable": {"recorder": rec}})
    steps = await repo.steps(wf.tenant_id, wf.id)
    detail = next(s.detail for s in steps if s.node == "support")
    assert detail["rewrite_source"] == "llm"
    assert detail["grade_source"] == "llm+deterministic"
    trace = detail["retrieval_trace"]
    assert len(trace) == 1
    assert trace[0]["hits"] == 2  # 两个文档都被检索回来
    assert trace[0]["relevant_count"] == 1  # 判级交集后只剩政策文档
    assert result["support"]["refs"] == [DOC["ref"]]  # 白名单同步收窄
    assert detail["draft_source"] == "llm"  # ETA 与工具一致 → 草稿采纳
    # 辅助调用（改写/判级）计量并入总账：20+30+100 prompt
    assert detail["llm_usage"]["prompt"] == 150


# ---- 7. 与并行交付的 app.rag.rewrite 契约模块对齐（未交付则 skip）----


def test_det_rewrite_matches_parallel_contract_module():
    pytest.importorskip("app.rag.rewrite")
    from app.rag.rewrite import deterministic_rewrite

    src = "你好，我想问一下 退换货政策？？"
    expected = (deterministic_rewrite(src) or src)[:120]
    assert N._det_rewrite(src) == expected
