"""真实 LLM 冒烟（默认跳过）：RUN_LLM_SMOKE=1 pytest 才出网。

用途：本地验证 SiliconFlow 连通性、JSON 输出质量与 rubric 遵从度；
CI 与常规回归不依赖网络。conftest 已强制清空 key，这里显式从 backend/.env 读取。
"""

import os
from pathlib import Path

import pytest
from dotenv import dotenv_values

from app.llm import LlmClient, extract_json

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LLM_SMOKE") != "1",
    reason="需要 RUN_LLM_SMOKE=1 且 backend/.env 配置真实 key",
)

_ENV = dotenv_values(Path(__file__).resolve().parents[2] / ".env")


def _client() -> LlmClient:
    key = _ENV.get("SILICONFLOW_API_KEY") or ""
    if not key.startswith("sk-"):
        pytest.fail("RUN_LLM_SMOKE=1 但 backend/.env 没有有效的 SILICONFLOW_API_KEY")
    return LlmClient(
        api_key=key,
        base_url=_ENV.get("SILICONFLOW_BASE_URL") or "https://api.siliconflow.cn/v1",
        model=_ENV.get("LLM_MODEL") or "deepseek-ai/DeepSeek-V3.2",
    )


async def test_real_llm_returns_valid_json():
    result = await _client().chat(
        [
            {"role": "system", "content": '只输出 JSON：{"ok": true, "hello": "一句话问候"}'},
            {"role": "user", "content": "打个招呼"},
        ],
        max_tokens=100,
    )
    assert result.total_tokens > 0
    assert extract_json(result.content).get("ok") is True


async def test_real_llm_respects_score_rubric():
    """rubric 遵从度：缺竞品/评论维度时评分必须 ≤0.60（研究深化回路的根基）。"""
    from app.graphs.product_launch.nodes import RESEARCH_SYSTEM_PROMPT

    tool_outputs = {"search_market_trends": {"search_trend_pct_90d": 23.0, "sources": ["t1"]}}
    result = await _client().chat(
        [
            {"role": "system", "content": RESEARCH_SYSTEM_PROMPT},
            {"role": "user", "content": f"选题：床底收纳箱（US）\n工具数据：{tool_outputs}"},
        ],
        max_tokens=600,
    )
    score = float(extract_json(result.content)["evidence_score"])
    assert score <= 0.60, f"rubric 被违反：缺维度却给了 {score}"
