"""一键重置演示数据：drop 全部业务表 + alembic_version，再 upgrade head 重建。

M1 起 schema 归 alembic 管；本脚本保证真实库永远从迁移链干净重建。
用法：python scripts/reset_demo.py
"""

import asyncio

from sqlalchemy import text

from app.persistence.db import adispose_database, session_factory
from app.persistence.migrations import upgrade_head

# 与 migrations/versions/0001 的 downgrade 顺序一致：先子表后父表
TABLES_IN_DROP_ORDER = [
    "tool_calls",
    "agent_decisions",
    "workflow_steps",
    "workflows",
    "tenants",
]


async def main() -> None:
    sf = session_factory()
    async with sf() as s:
        for table in TABLES_IN_DROP_ORDER:
            await s.execute(text(f"DROP TABLE IF EXISTS {table}"))
        await s.execute(text("DROP TABLE IF EXISTS alembic_version"))
        await s.commit()
    await adispose_database()  # 正确关闭旧引擎的 asyncpg 连接后再走迁移
    await upgrade_head()
    print("demo data reset done (schema via alembic head)")


if __name__ == "__main__":
    asyncio.run(main())
