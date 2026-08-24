"""一键重置演示数据：drop 业务表后重建（保留数据库本身）。

用法：python scripts/reset_demo.py
"""

import asyncio

from sqlalchemy import text

from app.persistence.db import init_db, session_factory

TABLES_IN_DROP_ORDER = ["agent_decisions", "workflow_steps", "workflows", "tenants"]


async def main() -> None:
    sf = session_factory()
    async with sf() as s:
        for table in TABLES_IN_DROP_ORDER:
            await s.execute(text(f"DROP TABLE IF EXISTS {table}"))
        await s.commit()
    await init_db()
    print("demo data reset done")


if __name__ == "__main__":
    asyncio.run(main())
