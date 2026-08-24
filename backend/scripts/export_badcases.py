"""M7 BadCase 数据集导出：bad_cases 表 → JSONL（PRD §20.5 沉淀/回归闭环的导出接缝）。

用法：
  python scripts/export_badcases.py                    # 全租户导出到 stdout
  python scripts/export_badcases.py --tenant t_demo_acme --out badcases.jsonl
  python scripts/export_badcases.py --workflow <wf_id> --tenant t_demo_acme

导出行即 BadCaseDataset 的黄金回归集条目；eval 门禁（evals/run_evals.py）
跑的是同源 seed，防回归不漂移。
"""

import asyncio
import json
import sys

from app.persistence.db import adispose_database, init_db
from app.persistence.repositories.workflow_repo import WorkflowRepository


async def main() -> int:
    args = sys.argv[1:]
    tenant_id = args[args.index("--tenant") + 1] if "--tenant" in args else None
    workflow_id = args[args.index("--workflow") + 1] if "--workflow" in args else None
    out_path = args[args.index("--out") + 1] if "--out" in args else None

    await init_db()
    repo = WorkflowRepository()
    if tenant_id:
        tenants = [tenant_id]
    else:
        from sqlalchemy import select

        from app.persistence.db import session_factory
        from app.persistence.models import Tenant

        async with session_factory()() as s:
            rows = (await s.execute(select(Tenant))).scalars().all()
        tenants = [t.id for t in rows]

    count = 0
    fh = open(out_path, "w", encoding="utf-8") if out_path else None
    try:
        for tid in tenants:
            items = await repo.list_bad_cases(tenant_id=tid, workflow_id=workflow_id, limit=500)
            for item in items:
                line = json.dumps(item, ensure_ascii=False)
                if fh:
                    fh.write(line + "\n")
                else:
                    print(line)
                count += 1
    finally:
        if fh:
            fh.close()
        await adispose_database()
    print(f"exported {count} bad cases"
          + (f" -> {out_path}" if out_path else ""), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
