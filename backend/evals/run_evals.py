"""红队 eval CI 门禁（M7，PRD §20.5）。

用法：python evals/run_evals.py
三条红队 seed 打穿全链路（hermetic：无 key 自动 stub + hash 嵌入）；
任何「该检出未检出 / 该隔离未隔离 / 禁入词泄漏」即退出码 1，阻断合并。
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

# 与 tests/conftest 同款封闭环境：脚本可独立于 pytest 运行（CI 里两者都跑）
_tmp = Path(tempfile.mkdtemp(prefix="cesa-eval-")).as_posix()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp}/eval.db"
os.environ["AUTO_APPROVE"] = "true"
os.environ["EVIDENCE_THRESHOLD"] = "0.7"
os.environ["MAX_RESEARCH_ROUNDS"] = "2"
os.environ["MAX_CRITIQUE_ROUNDS"] = "3"
os.environ["SILICONFLOW_API_KEY"] = ""

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from app.evals.redteam import REDTEAM_SEEDS, run_redteam_seed  # noqa: E402
from app.persistence.db import adispose_database, init_db, reset_database  # noqa: E402


async def main() -> int:
    await init_db()
    failed = False
    for i, seed in enumerate(REDTEAM_SEEDS):
        report = await run_redteam_seed(seed, seq=i)
        verdict = "PASS" if not report["violations"] else "FAIL"
        if report["violations"]:
            failed = True
        print(f"[{verdict}] {report['key']}（{report['title']}）")
        print(
            f"  检出类别: {report['detected_categories']} | "
            f"bad_case 数: {report['bad_case_count']} | 全链路完成: {report['completed']}"
        )
        for v in report["violations"]:
            print(f"  ✗ {v}")
        reset_database()
    await adispose_database()
    print()
    print("红队回归门禁：", "FAIL（阻断合并）" if failed else "PASS（防线完好）")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
