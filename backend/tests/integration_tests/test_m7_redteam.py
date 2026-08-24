"""M7 红队回归集成测试：三条 seed 打穿全链路，防线失守即失败（CI 门禁的 pytest 形态）。

与 evals/run_evals.py 共用 app/evals/redteam.py 的 seed 与 runner——
脚本与测试永远跑同一套黄金回归集，不会漂移。
"""

import pytest

from app.evals.redteam import REDTEAM_SEEDS, run_redteam_seed
from app.persistence.db import reset_database
from app.persistence.repositories.workflow_repo import WorkflowRepository

_seq = 0


def _next_seq() -> int:
    global _seq
    _seq += 1
    return _seq


@pytest.mark.parametrize("seed", REDTEAM_SEEDS, ids=lambda s: s["key"])
async def test_redteam_seed_guardrails_hold(seed):
    report = await run_redteam_seed(seed, seq=_next_seq())
    assert report["completed"], "红队流程应完整跑通（注入按数据处理，不中断）"
    assert report["violations"] == [], f"红队防线失守: {report['violations']}"


async def test_badcase_records_are_tenant_scoped():
    """bad_cases 列表按租户过滤：红队租户的记录对其他租户不可见。"""
    seed = REDTEAM_SEEDS[1]
    seq = _next_seq()
    report = await run_redteam_seed(seed, seq=seq)
    repo = WorkflowRepository()
    mine = await repo.list_bad_cases(
        tenant_id=f"t_redteam_{seed['key']}_{seq}", limit=100
    )
    assert report["bad_case_count"] == len(mine)
    others = await repo.list_bad_cases(tenant_id="t_redteam_nobody", limit=100)
    assert others == []
    reset_database()
