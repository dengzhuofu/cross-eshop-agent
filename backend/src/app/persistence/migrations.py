"""迁移入口：运行时用 alembic 管理真实库 schema（create_all 只留给测试）。"""

import asyncio
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig

logger = logging.getLogger(__name__)

# 运行时约定 cwd=backend/；允许用 ALEMBIC_CONFIG 覆盖（部署形态变化时不改代码）
_INI = Path(__import__("os").environ.get("ALEMBIC_CONFIG", "alembic.ini"))


async def upgrade_head() -> None:
    """启动时把真实库升到 head。alembic 命令 API 是同步的，放线程池避免阻塞事件循环。"""
    if not _INI.exists():
        raise FileNotFoundError(f"alembic.ini not found at {_INI.resolve()}")
    cfg = AlembicConfig(str(_INI))

    def _upgrade() -> None:
        command.upgrade(cfg, "head")

    await asyncio.to_thread(_upgrade)
    logger.info("alembic upgrade head done")
