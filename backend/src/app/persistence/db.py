"""数据库引擎与会话工厂。

M0 默认 sqlite+aiosqlite（零依赖可跑）；docker-compose 起 postgres 后用 DATABASE_URL 切换，
代码不感知方言差异。建表 M0 用 metadata.create_all，M1 引入 alembic 迁移。
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_engine = None
_factory: async_sessionmaker[AsyncSession] | None = None


def _build(url: str):
    global _engine, _factory
    _engine = create_async_engine(url, echo=False, future=True)
    _factory = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


def set_database_url(url: str) -> None:
    """测试专用：在任何会话创建前切换数据库 URL。"""
    global _engine, _factory
    if _factory is not None:  # 已有连接时禁止热切换，避免半初始化状态
        raise RuntimeError("database already initialized; call reset_database() first")
    _build(url)


def reset_database() -> None:
    global _engine, _factory
    if _engine is not None:
        _engine.sync_engine.dispose()
    _engine = None
    _factory = None


def session_factory() -> async_sessionmaker[AsyncSession]:
    if _factory is None:
        from app.config import get_settings

        _build(get_settings().database_url)
    return _factory


async def init_db() -> None:
    """开发/测试用建表。生产迁移走 alembic（M1）。"""
    from app.persistence.models import Base

    session_factory()  # 确保引擎按当前配置构建
    assert _engine is not None
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
