"""LLM 客户端与节点降级行为的单元测试（全程不出网）。"""

import pytest

from app.graphs.product_launch.nodes import _merge_llm_usage, node_research
from app.llm import LlmError, extract_json
from app.observability.recorder import NullRecorder


def test_extract_json_plain():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_fenced():
    text = "前置说明\n```json\n{\"score\": 0.8, \"items\": [\"x\"]}\n```\n后置说明"
    assert extract_json(text) == {"score": 0.8, "items": ["x"]}


def test_extract_json_embedded_in_prose():
    text = '结论如下：{"chosen": "proceed", "reasoning": "达标"} 以上。'
    assert extract_json(text)["chosen"] == "proceed"


def test_extract_json_invalid_raises():
    with pytest.raises(LlmError):
        extract_json("完全没有 JSON 的输出")


def test_merge_llm_usage_accumulates():
    state = {"llm_usage": {"calls": 1, "prompt_tokens": 100, "completion_tokens": 20}}
    u = _merge_llm_usage(state, 50, 30)
    assert u == {"calls": 2, "prompt_tokens": 150, "completion_tokens": 50, "total_tokens": 200}


async def test_research_node_falls_back_to_stub_when_llm_fails(monkeypatch):
    """LLM 故障时 research 节点必须降级 stub，主链路不中断（弹性要求）。"""
    from app.graphs.product_launch import nodes as N

    async def _boom(*a, **k):
        raise LlmError("simulated outage")

    monkeypatch.setattr(N, "llm_enabled", lambda: True)
    monkeypatch.setattr(N, "_research_via_llm", _boom)

    state = {
        "workflow_id": "wf_x",
        "tenant_id": "t_x",
        "task_input": {"product_idea": "测试品", "marketplaces": ["amazon"]},
        "research_rounds": 0,
        "scratchpad": {"artifacts": {}},
    }
    update = await node_research(state, {"configurable": {"recorder": NullRecorder()}})
    assert update["research_evidence_score"] == 0.55  # stub 首轮分数
    assert update["research_rounds"] == 1
    assert "llm_usage" not in update  # 降级路径不产生计量


async def test_research_node_llm_path_uses_tool_data(monkeypatch):
    """LLM 路径：数据来自工具输出，评分经 rubric 约束，usage 计量进 state。"""
    from app.graphs.product_launch import nodes as N

    captured = {}

    async def _fake_research(state, config, idea, market, rounds):
        captured["idea"] = idea
        return (
            {
                "round": 1,
                "evidence_score": 0.55,
                "demand_signal": "趋势上行",
                "competitor_gap": "无折叠款",
                "review_pain_points": [],
                "evidence_refs": ["trends_mock_001"],
                "reasoning": "缺少评论与竞品维度",
            },
            {"prompt": 120, "completion": 40},
        )

    monkeypatch.setattr(N, "llm_enabled", lambda: True)
    monkeypatch.setattr(N, "_research_via_llm", _fake_research)

    state = {
        "workflow_id": "wf_x",
        "tenant_id": "t_x",
        "task_input": {"product_idea": "磁吸理线器", "target_market": "US"},
        "research_rounds": 0,
        "scratchpad": {"artifacts": {}},
    }
    update = await node_research(state, {"configurable": {"recorder": NullRecorder()}})
    assert captured["idea"] == "磁吸理线器"
    assert update["research_evidence_score"] == 0.55
    assert update["llm_usage"]["total_tokens"] == 160
    assert update["llm_usage"]["calls"] == 1
