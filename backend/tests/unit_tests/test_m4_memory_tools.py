"""M4 长期记忆单测：hash 嵌入确定性、降级引擎、工具治理链路与跨租户隔离。

全程不出网（conftest 清空 key → embed_texts 自动走 hash 引擎）；
持久化契约方法用 monkeypatch 打成内存假实现——测试只依赖假实现，不连真库。
"""

import math

import pytest

from app.llm import EMBEDDING_DIM, LlmError, embed_texts, embedding_enabled
from app.llm.embeddings import _hash_embedding
from app.persistence.memory import MemoryWorkflowRepository
from app.persistence.repositories.workflow_repo import WorkflowRepository

# 注册副作用：import 即把 retrieve_memory / record_memory 登记进 registry
from app.tools.catalog import memory as _memory_catalog  # noqa: F401
from app.tools.context import ToolContext
from app.tools.executor import execute_tool
from app.tools.registry import list_tools

# ---- 1. hash 嵌入：确定性与归一化 ----


def test_hash_embedding_deterministic_and_normalized():
    v1 = _hash_embedding("Under Bed Storage 床底收纳箱")
    v2 = _hash_embedding("Under Bed Storage 床底收纳箱")
    assert v1 == v2  # 同一文本两次结果一致（md5 分桶，跨进程稳定）
    assert len(v1) == EMBEDDING_DIM
    assert abs(sum(x * x for x in v1) - 1.0) < 1e-9  # L2 归一化后平方和 ≈ 1
    other = _hash_embedding("pet grooming glove 宠物手套")
    assert other != v1  # 不同文本向量不同


def test_hash_embedding_empty_text_is_zero_vector():
    z = _hash_embedding("")
    assert len(z) == EMBEDDING_DIM
    assert all(v == 0.0 for v in z)
    # 纯标点同样切不出词，也是零向量
    assert all(v == 0.0 for v in _hash_embedding("!!!???"))


async def test_embed_texts_hash_engine_without_key():
    """无 key 时自动走 hash 引擎；维度恒为 EMBEDDING_DIM。"""
    assert embedding_enabled() is False  # conftest 已清空 key
    vectors, usage, engine = await embed_texts(["床底收纳箱 under bed storage"])
    assert engine == "hash"
    assert len(vectors) == 1 and len(vectors[0]) == EMBEDDING_DIM
    assert usage["prompt_tokens"] == 0
    # allow_fallback=False 且未启用：必须抛错而不是静默降级
    with pytest.raises(LlmError):
        await embed_texts(["x"], allow_fallback=False)


# ---- 2. 治理链路：内存假仓储 + execute_tool 全通道 ----


def _cosine(a: list[float], b: list[float]) -> float:
    da = math.sqrt(sum(x * x for x in a))
    db = math.sqrt(sum(y * y for y in b))
    if da == 0.0 or db == 0.0:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / (da * db)


class _FakeMemoryStore:
    """WorkflowRepository.insert_memory / search_memories 的内存假实现。

    按 tenant_id+kind 过滤的 dict 存储 + 余弦相似度排序，签名与持久化契约一致。
    """

    def __init__(self) -> None:
        self.rows: list[dict] = []

    async def insert_memory(
        self,
        *,
        tenant_id: str,
        kind: str,
        content: str,
        embedding: list[float],
        source_workflow_id: str | None = None,
        meta: dict | None = None,
    ) -> str:
        memory_id = f"mem_{len(self.rows) + 1:04d}"
        self.rows.append(
            {
                "memory_id": memory_id,
                "tenant_id": tenant_id,
                "kind": kind,
                "content": content,
                "embedding": embedding,
                "source_workflow_id": source_workflow_id,
                "meta": meta,
            }
        )
        return memory_id

    async def search_memories(
        self, *, tenant_id: str, kind: str, query_embedding: list[float], top_k: int = 3
    ) -> list[dict]:
        hits = [
            (_cosine(query_embedding, row["embedding"]), row)
            for row in self.rows
            if row["tenant_id"] == tenant_id and row["kind"] == kind
        ]
        hits.sort(key=lambda pair: pair[0], reverse=True)
        return [
            {
                "content": row["content"],
                "similarity": sim,
                "source_workflow_id": row["source_workflow_id"],
            }
            for sim, row in hits[:top_k]
        ]


@pytest.fixture()
def fake_store(monkeypatch):
    store = _FakeMemoryStore()
    # raising=False：并行代理落地真实契约方法前后，本套测试都只依赖这份假实现
    monkeypatch.setattr(
        WorkflowRepository, "insert_memory", store.insert_memory, raising=False
    )
    monkeypatch.setattr(
        WorkflowRepository, "search_memories", store.search_memories, raising=False
    )
    return store


async def test_record_then_retrieve_roundtrip_via_executor(fake_store):
    ctx_a = ToolContext(tenant_id="t_acme")
    content = "亚马逊listing标题要前置核心关键词，否则自然流量掉一半"

    rec = await execute_tool(
        "record_memory",
        {"kind": "insight", "content": content, "source_workflow_id": "wf_001"},
        ctx_a,
        MemoryWorkflowRepository(),  # 审计走内存仓储
    )
    assert rec.ok is True
    assert rec.output["engine"] == "hash"
    assert rec.output["kind"] == "insight"
    assert rec.output["memory_id"] == fake_store.rows[0]["memory_id"]

    ret = await execute_tool(
        "retrieve_memory",
        {"kind": "insight", "query_text": content},
        ToolContext(tenant_id="t_acme"),
        MemoryWorkflowRepository(),
    )
    assert ret.ok is True
    out = ret.output
    assert out["engine"] == "hash" and len(out["results"]) >= 1
    top = out["results"][0]
    assert top["content"] == content  # top1 就是刚写入的内容
    assert top["similarity"] > 0.99  # 同文本 hash 向量恒等 → 余弦≈1
    assert top["source_workflow_id"] == "wf_001"


async def test_retrieve_is_tenant_isolated(fake_store):
    """治理铁律：tenant_id 只能由系统注入，换租户检索必须拿不到他租户记忆。"""
    ctx_owner = ToolContext(tenant_id="t_owner")
    secret = "Shopify 独立站弃购挽回邮件在 1 小时内发送转化率最高"

    rec = await execute_tool(
        "record_memory",
        {"kind": "preference", "content": secret},
        ctx_owner,
        MemoryWorkflowRepository(),
    )
    assert rec.ok is True

    ret = await execute_tool(
        "retrieve_memory",
        {"kind": "preference", "query_text": secret},
        ToolContext(tenant_id="t_other"),  # 另一个租户查同样的文本
        MemoryWorkflowRepository(),
    )
    assert ret.ok is True
    assert ret.output["results"] == []  # 跨租户一律不可见

    # 同 kind 但不同租户也隔离；同租户同 kind 才能命中
    miss_kind = await execute_tool(
        "retrieve_memory",
        {"kind": "insight", "query_text": secret},
        ToolContext(tenant_id="t_owner"),
        MemoryWorkflowRepository(),
    )
    assert miss_kind.output["results"] == []
    hit = await execute_tool(
        "retrieve_memory",
        {"kind": "preference", "query_text": secret},
        ctx_owner,
        MemoryWorkflowRepository(),
    )
    assert hit.output["results"][0]["similarity"] > 0.99


def test_m4_tools_registered():
    names = {t.name for t in list_tools()}
    assert {"retrieve_memory", "record_memory"} <= names
