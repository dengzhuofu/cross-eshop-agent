"""种子数据：两个演示租户 + Acme 的供应商风险长期记忆。

用法：python scripts/seed_mock_data.py

风险记忆只给 t_demo_acme：globex 检索不到同一条记忆，直观演示记忆的
租户隔离（PRD §13.2）。嵌入经 embed_texts 生成——有 SILICONFLOW_API_KEY
用真实向量，无 key 自动降级确定性 hash，与运行时 retrieve_memory 始终
同一引擎，保证写入/查询两侧相似度可比。
"""

import asyncio

from app.persistence.db import adispose_database
from app.persistence.migrations import upgrade_head
from app.persistence.repositories.workflow_repo import WorkflowRepository

TENANTS = [
    ("t_demo_acme", "Acme Cross-border"),
    ("t_demo_globex", "Globex Trading"),
]

SUPPLIER_RISK_SEEDS: dict[str, list[str]] = {
    "t_demo_acme": [
        # 风险记忆只点名被标记的供应商：节点按 id/name 匹配降权，
        # 推荐替代品不要写进同一条记忆，否则会被误伤标记
        (
            "sup_002 Yiwu General Trading 在 wf_seed_2026_07 批次抽检缺陷率 12%"
            "（阈值 3%），已标记高风险并暂停新订单。"
        ),
    ],
}


async def main() -> None:
    await upgrade_head()  # M1 起 schema 由 alembic 保证
    repo = WorkflowRepository()
    for tenant_id, name in TENANTS:
        await repo.ensure_tenant(tenant_id, name)
        print(f"tenant ready: {tenant_id} ({name})")

    from app.llm.embeddings import EMBEDDING_DIM, embed_texts

    for tenant_id, contents in SUPPLIER_RISK_SEEDS.items():
        # 零向量探测做幂等：该租户已有 supplier_risk 记忆则跳过（重复执行不翻倍）
        existing = await repo.search_memories(
            tenant_id=tenant_id,
            kind="supplier_risk",
            query_embedding=[0.0] * EMBEDDING_DIM,
            top_k=1,
        )
        if existing:
            print(f"memory seed skipped (tenant={tenant_id}): supplier_risk 已存在")
            continue
        vectors, _usage, engine = await embed_texts(contents)
        for content, emb in zip(contents, vectors):
            memory_id = await repo.insert_memory(
                tenant_id=tenant_id,
                kind="supplier_risk",
                content=content,
                embedding=emb,
                meta={"seed": True},
            )
            print(f"memory seeded (tenant={tenant_id}, engine={engine}): {memory_id}")
    await adispose_database()

    print()
    print("创建工作流示例：")
    print(
        'curl -X POST http://127.0.0.1:8000/api/v1/workflows '
        '-H "Content-Type: application/json" -H "X-Tenant-Id: t_demo_acme" '
        '-d \'{"product_idea":"可折叠床底收纳箱","marketplaces":["amazon","tiktok_shop"]}\''
    )


if __name__ == "__main__":
    asyncio.run(main())
