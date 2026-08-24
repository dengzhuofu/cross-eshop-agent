"""M6 种子数据：Acme 的 RAG 五类知识集合（policy/platform_rule/product_info/faq/script）。

用法：python scripts/seed_knowledge.py

知识只给 t_demo_acme：globex 检索不到同一条知识，直观演示 RAG 的租户隔离
（PRD §13.2）。文档原文在 scripts/knowledge_seed_data.py（PRD §7.11 五类集合），
嵌入经 embed_texts 生成，引擎跟随 .env，与运行时 search_knowledge 同引擎，
保证写入/查询两侧相似度可比。
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from knowledge_seed_data import KNOWLEDGE_SEEDS  # noqa: E402

from app.persistence.db import adispose_database  # noqa: E402
from app.persistence.migrations import upgrade_head  # noqa: E402
from app.persistence.repositories.workflow_repo import WorkflowRepository  # noqa: E402


async def main() -> None:
    await upgrade_head()  # 0003 起含 knowledge_base 表
    repo = WorkflowRepository()

    tenant_id = KNOWLEDGE_SEEDS[0]["tenant_id"]
    await repo.ensure_tenant(tenant_id, "Acme Cross-border")

    from app.llm.embeddings import EMBEDDING_DIM, embed_texts

    # 零向量探测做幂等：该租户已有知识文档则跳过（重复执行不翻倍）
    existing = await repo.search_knowledge(
        tenant_id=tenant_id,
        category=None,
        query_embedding=[0.0] * EMBEDDING_DIM,
        top_k=1,
    )
    if existing:
        print(f"knowledge seed skipped (tenant={tenant_id}): 知识库已存在")
        await adispose_database()
        return

    texts = [f"{s['title']} {s['content']}" for s in KNOWLEDGE_SEEDS]
    vectors, _usage, engine = await embed_texts(texts)
    for seed, emb in zip(KNOWLEDGE_SEEDS, vectors):
        kid = await repo.insert_knowledge(
            tenant_id=tenant_id,
            category=seed["category"],
            title=seed["title"],
            content=seed["content"],
            embedding=emb,
            ref=seed.get("ref"),
            meta={"seed": True},
        )
        print(f"knowledge seeded (engine={engine}): [{seed['category']}] {seed['ref']} -> {kid}")
    await adispose_database()
    print(f"done: {len(KNOWLEDGE_SEEDS)} docs for {tenant_id}")


if __name__ == "__main__":
    asyncio.run(main())
