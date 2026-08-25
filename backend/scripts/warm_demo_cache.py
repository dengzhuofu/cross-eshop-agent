"""M8 Demo 兜底缓存预热脚本（v1.4 §1.2）。

用法（一次性，需要真 key——key 只放 backend/.env，本脚本不硬编码任何密钥）：
  cd backend
  PYTHONUTF8=1 .venv/Scripts/python.exe scripts/warm_demo_cache.py

流程：强制 DEMO_CACHE_MODE=readwrite（必须在任何 get_settings/get_llm_client 调用之前
设置环境变量，Settings 是 lru_cache 单例、导入链随时可能触发定格）；然后复用
app.api.main 的现成工作流启动机制（ensure_tenant 建租户 + run_workflow 跑图），
把固定演示选题跑到终态。LLM 产出按 (model, messages, temperature, max_tokens) 精确
hash 写入 settings.demo_cache_path。

预热完成后，演示环境把 .env 改成 SILICONFLOW_API_KEY=（留空）且 DEMO_CACHE_MODE=read，
重启后端即可完全离线重放出与预热时相同的 LLM 产出；未命中的调用由各节点确定性
stub 兜底，主链路不断。
"""

import asyncio
import os
from pathlib import Path

# 必须先设环境变量再 import app.*：pydantic-settings 的 lru_cache 在首次 get_settings()
# 时定格，而任何 app 模块的导入都可能触发它。AUTO_APPROVE=true 保证跑到 completed 终态，
# 不被人工审批挂起（预热要的是全链路 LLM 产出）。
os.environ["DEMO_CACHE_MODE"] = "readwrite"
os.environ["AUTO_APPROVE"] = "true"

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver  # noqa: E402

import app.api.main as api_main  # noqa: E402
from app.cache import get_result_cache  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.graphs.product_launch.agent import build_graph  # noqa: E402
from app.llm import get_llm_client, llm_enabled  # noqa: E402
from app.persistence.db import adispose_database  # noqa: E402
from app.persistence.migrations import upgrade_head  # noqa: E402
from app.persistence.repositories.workflow_repo import WorkflowRepository  # noqa: E402

# 固定演示选题：离线重放的正是这几条的完整 LLM 产出
DEMO_IDEAS = ["可折叠床底收纳箱", "儿童保温杯", "磁吸理线器"]
TENANT_ID = "t_demo_acme"


async def main() -> int:
    if not llm_enabled():
        print(
            "错误：未检测到 SILICONFLOW_API_KEY，无法产生新的缓存条目。\n"
            "请先在 backend/.env 配好真 key 再跑预热；若缓存已预热过，"
            "直接把 .env 改成 DEMO_CACHE_MODE=read 即可离线重放。"
        )
        return 1

    await upgrade_head()
    repo = WorkflowRepository()
    await repo.ensure_tenant(TENANT_ID, "Acme Cross-border")

    settings = get_settings()
    Path(settings.checkpoint_db_path).parent.mkdir(parents=True, exist_ok=True)

    # 复用 api.main 的运行器：与 lifespan 同款做法——带 checkpointer 的图替换模块级 _GRAPH，
    # run_workflow 内部每次引用的都是最新 _GRAPH（interrupt/resume 才有断点可用）
    async with AsyncSqliteSaver.from_conn_string(settings.checkpoint_db_path) as saver:
        api_main._GRAPH = build_graph(checkpointer=saver)
        finals = []
        for idea in DEMO_IDEAS:
            wf = await repo.create_workflow(
                tenant_id=TENANT_ID,
                title=f"[warm] {idea}",
                product_idea=idea,
                marketplaces=["amazon", "shopify", "tiktok_shop"],
                target_market="US",
                risk_preference="balanced",
                status="queued",
                input_json={"product_idea": idea},
            )
            await api_main.run_workflow(wf.id, TENANT_ID)
            final = await repo.get(TENANT_ID, wf.id)
            finals.append(final)
            print(f"warm: id={final.id} status={final.status} idea={idea}")

    await get_llm_client().aclose()  # CachedLlmClient 透传关掉 inner 的 httpx
    await adispose_database()

    cache = get_result_cache()
    print(f"cache: {cache.size} entries -> {Path(settings.demo_cache_path).resolve()}")
    print(
        "下一步：把 .env 改成 SILICONFLOW_API_KEY=（留空）+ DEMO_CACHE_MODE=read，\n"
        "即可在无网/零 token 环境重放同样的 LLM 产出。"
    )
    # blocked/cancelled 也是合法预热终态（go/no-go 闸门拦下的选题同样预热了前段链路）；
    # 只有非终态（超时未跑完）才算失败
    return 0 if all(f.status in ("completed", "blocked", "cancelled") for f in finals) else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
