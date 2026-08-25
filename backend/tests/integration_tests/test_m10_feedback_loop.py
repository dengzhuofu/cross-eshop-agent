"""M10 反馈-分诊-沉淀闭环集成测试：候选知识审批门 / 黄金集落盘 / badcase+记忆沉淀 /
租户隔离 / API 全链路。

hermetic（无 key → 规则分诊 + hash 嵌入 + 模板草稿）；测试库整会话共享，
租户 id/名称必须唯一（同 test_badcase_lifecycle 约定）。
"""

from pathlib import Path

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.api.main import app
from app.config import get_settings
from app.feedback.triage import triage_and_route
from app.persistence.db import session_factory
from app.persistence.models import KnowledgeRecord, MemoryRecord
from app.persistence.repositories.workflow_repo import WorkflowRepository

T_A = "t_fb_loop_a"
T_B = "t_fb_loop_b"


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


_WF_CACHE: dict[str, str] = {}


async def _setup() -> WorkflowRepository:
    """建租户 + 真实工作流（feedback.workflow_id 有 FK，必须用真实主键；
    会话内多测试共享，按模块级缓存只建一次）。"""
    repo = WorkflowRepository()
    await repo.ensure_tenant(T_A, f"{T_A} Co")
    await repo.ensure_tenant(T_B, f"{T_B} Co")
    if T_A not in _WF_CACHE:
        wf = await repo.create_workflow(
            tenant_id=T_A, title="反馈闭环夹具", product_idea="儿童保温杯",
            marketplaces=["amazon"], target_market="US", risk_preference="balanced",
            status="completed", input_json={},
        )
        _WF_CACHE[T_A] = wf.id
    return repo


async def _route(repo: WorkflowRepository, *, tenant_id: str = T_A, verdict: str = "unhelpful",
                 comment: str = "", quote: str = "",
                 target_type: str = "support_draft") -> dict:
    fid = await repo.insert_feedback(
        tenant_id=tenant_id, target_type=target_type, verdict=verdict,
        workflow_id=_WF_CACHE.get(tenant_id),
        comment=comment or None, quote=quote or None,
    )
    triage = await triage_and_route(
        repo, tenant_id=tenant_id, feedback_id=fid,
        workflow_id=_WF_CACHE.get(tenant_id),
        target_type=target_type, verdict=verdict, comment=comment, quote=quote,
    )
    return {"id": fid, **triage}


async def _knowledge_rows(tenant_id: str) -> list[dict]:
    async with session_factory()() as s:
        rows = (
            await s.execute(select(KnowledgeRecord).where(KnowledgeRecord.tenant_id == tenant_id))
        ).scalars().all()
    return [{"ref": r.ref, "meta": r.meta, "title": r.title} for r in rows]


async def test_kb_gap_candidate_requires_approval():
    """知识缺口 → 候选知识默认不可检索；approve 后进检索池，reject 则删除。"""
    repo = await _setup()
    out = await _route(repo, comment="知识库里查不到钛杯的保修年限", quote="钛杯保修几年")
    assert out["category"] == "kb_gap" and out["sink"] == "knowledge_candidate"
    assert out["status"] == "triaged" and out["sink_ref"]
    kid = out["sink_ref"]

    rows = await _knowledge_rows(T_A)
    cand = next(r for r in rows if r["meta"].get("feedback_id") == out["id"])
    assert cand["meta"]["status"] == "candidate"
    assert cand["ref"] == f"FB-{out['id'][:8].upper()}"

    # 未审批：检索池不可见
    hits_before = await repo.search_knowledge(
        tenant_id=T_A, category=None, query_embedding=[0.0] * 1024, top_k=50,
        query_text="钛杯 保修 年限",
    )
    assert all(h["id"] != kid for h in hits_before)

    # 审批通过 → 进检索池
    assert await repo.review_candidate_knowledge(
        tenant_id=T_A, knowledge_id=kid, action="approve")
    hits_after = await repo.search_knowledge(
        tenant_id=T_A, category=None, query_embedding=[0.0] * 1024, top_k=50,
        query_text="钛杯 保修 年限",
    )
    assert any(h["id"] == kid for h in hits_after)


async def test_reject_removes_candidate():
    repo = await _setup()
    out = await _route(repo, comment="知识库里查不到关税计算说明，缺少这块内容", quote="")
    ok = await repo.review_candidate_knowledge(
        tenant_id=T_A, knowledge_id=out["sink_ref"], action="reject")
    assert ok
    refs = [r["ref"] for r in await _knowledge_rows(T_A)]
    assert f"FB-{out['id'][:8].upper()}" not in refs


async def test_formal_corpus_not_reviewable():
    """正式语料（种子）不可经审批通道改动——只认 origin=feedback 的候选行。"""
    repo = await _setup()
    from app.llm.embeddings import embed_texts
    vectors, _u, _e = await embed_texts(["正式种子条目"])
    kid = await repo.insert_knowledge(
        tenant_id=T_A, category="policy", title="正式政策", content="内容",
        embedding=vectors[0], ref="POL-FORMAL-01", meta={"seed": True},
    )
    assert not await repo.review_candidate_knowledge(
        tenant_id=T_A, knowledge_id=kid, action="reject")
    refs = [r["ref"] for r in await _knowledge_rows(T_A)]
    assert "POL-FORMAL-01" in refs


async def test_retrieval_miss_writes_golden(tmp_path, monkeypatch):
    repo = await _setup()
    golden_path = tmp_path / "fg.jsonl"
    monkeypatch.setattr(get_settings(), "feedback_golden_path", str(golden_path))
    out = await _route(repo, quote="物流到美国到底要多少天", comment="答非所问，检索出来的不相关")
    assert out["category"] == "retrieval_miss" and out["sink_ref"] == str(golden_path)
    lines = golden_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1 and out["id"] in lines[0]


async def test_claim_violation_routes_badcase_and_memory():
    repo = await _setup()
    out = await _route(repo, comment="客服草稿居然写保证100%不坏", quote="保证100%不坏")
    assert out["category"] == "claim_violation" and out["sink"] == "badcase_memory"

    cases = await repo.list_bad_cases(tenant_id=T_A, limit=100)
    hit = next(c for c in cases if c["detector"].startswith("feedback_triage:"))
    assert hit["status"] == "quarantined" and hit["workflow_id"] == _WF_CACHE[T_A]
    assert hit["evidence"]["feedback_id"] == out["id"]

    async with session_factory()() as s:
        mems = (
            await s.execute(
                select(MemoryRecord).where(
                    MemoryRecord.tenant_id == T_A, MemoryRecord.kind == "feedback")
            )
        ).scalars().all()
    assert any(out["id"] in json_text(m.meta) for m in mems)


def json_text(meta) -> str:
    import json

    return json.dumps(meta or {}, ensure_ascii=False)


async def test_helpful_dismissed_no_side_effects():
    repo = await _setup()
    cases_before = len(await repo.list_bad_cases(tenant_id=T_A, limit=200))
    out = await _route(repo, verdict="helpful", comment="回复很专业")
    assert out["category"] == "positive" and out["status"] == "dismissed"
    assert out["sink_ref"] is None
    # 正反馈不产生 badcase（负反馈记忆允许存在，这里只断言 badcase 不增）
    assert len(await repo.list_bad_cases(tenant_id=T_A, limit=200)) == cases_before


async def test_tenant_isolation():
    repo = await _setup()
    out = await _route(repo, comment="知识库查不到尺码表")
    # 跨租户审批：False 且候选仍在原租户未被删
    assert not await repo.review_candidate_knowledge(
        tenant_id=T_B, knowledge_id=out["sink_ref"], action="approve")
    assert await repo.review_candidate_knowledge(
        tenant_id=T_A, knowledge_id=out["sink_ref"], action="approve")
    # 列表隔离
    items_b = await repo.list_feedback(tenant_id=T_B, limit=100)
    assert all(i["id"] != out["id"] for i in items_b)


async def test_api_feedback_roundtrip(tmp_path, monkeypatch):
    """API 全链路：POST 反馈 → 分诊结果返回 → GET 可见 → review 审批。"""
    await _setup()
    monkeypatch.setattr(
        get_settings(), "feedback_golden_path", str(Path(tmp_path) / "api_fg.jsonl"))
    async with _client() as client:
        resp = await client.post(
            "/api/v1/feedback",
            headers={"X-Tenant-Id": T_A},
            json={"target_type": "support_draft", "verdict": "unhelpful",
                  "comment": "知识库里查不到这个配件的适配型号", "quote": "适配什么型号"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["category"] in {"kb_gap", "hallucination"}  # 规则命中 kb_gap
        assert body["sink"] == "knowledge_candidate"

        listing = await client.get("/api/v1/feedback", headers={"X-Tenant-Id": T_A})
        ids = [i["id"] for i in listing.json()["items"]]
        assert body["id"] in ids

        ok = await client.post(
            f"/api/v1/knowledge/{body['sink_ref']}/review",
            headers={"X-Tenant-Id": T_A}, json={"action": "approve"},
        )
        assert ok.status_code == 200

        # 跨租户审批 404 防枚举
        cross = await client.post(
            f"/api/v1/knowledge/{body['sink_ref']}/review",
            headers={"X-Tenant-Id": T_B}, json={"action": "reject"},
        )
        assert cross.status_code == 404

        bad = await client.post(
            "/api/v1/feedback", headers={"X-Tenant-Id": T_A},
            json={"target_type": "support_draft", "verdict": "meh"},
        )
        assert bad.status_code == 422
