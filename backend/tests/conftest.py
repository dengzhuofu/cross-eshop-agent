"""测试环境：在任何 app 模块导入前固定环境变量，保证封闭性。

- 测试库用临时 SQLite（每个测试独立引擎、互不污染）；
- 运行时默认连真实 PostgreSQL（backend/.env 的 DATABASE_URL），
  真实链路由 API 冒烟与部署环境验证。
"""

import os
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="cesa-test-")).as_posix()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp}/test.db"
os.environ["AUTO_APPROVE"] = "true"
os.environ["EVIDENCE_THRESHOLD"] = "0.7"
os.environ["MAX_RESEARCH_ROUNDS"] = "2"
os.environ["MAX_CRITIQUE_ROUNDS"] = "3"

import pytest  # noqa: E402

from app.persistence.db import init_db, reset_database  # noqa: E402


@pytest.fixture(autouse=True)
async def _fresh_db():
    await init_db()
    yield
    # 释放连接池，避免跨事件循环复用连接（同步清理，引擎内部自行处理）
    reset_database()
