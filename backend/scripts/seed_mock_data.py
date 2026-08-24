"""种子数据：两个演示租户（多租户隔离验证用）。

用法：python scripts/seed_mock_data.py
"""

import asyncio

from app.persistence.db import adispose_database
from app.persistence.migrations import upgrade_head
from app.persistence.repositories.workflow_repo import WorkflowRepository

TENANTS = [
    ("t_demo_acme", "Acme Cross-border"),
    ("t_demo_globex", "Globex Trading"),
]


async def main() -> None:
    await upgrade_head()  # M1 起 schema 由 alembic 保证
    repo = WorkflowRepository()
    for tenant_id, name in TENANTS:
        await repo.ensure_tenant(tenant_id, name)
        print(f"tenant ready: {tenant_id} ({name})")
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
