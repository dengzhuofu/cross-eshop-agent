"""M8 Demo 兜底缓存单测：精确 hash 缓存、CachedLlmClient 命中/未命中、模式开关。

全程不出网（inner 一律用假客户端；Settings 有 lru_cache，本模块 autouse fixture
在前后 get_settings.cache_clear() 并重置 llm/cache 单例，避免污染其他用例）。
"""

import json

import pytest

from app.cache import (
    ExactHashResultCache,
    cache_key,
    reset_result_cache,
)
from app.config import get_settings
from app.llm import LlmClient, LlmError, llm_enabled, reset_llm_client
from app.llm.client import CachedLlmClient, LlmResult, get_llm_client

_MSGS = [{"role": "system", "content": "s"}, {"role": "user", "content": "你好"}]


@pytest.fixture(autouse=True)
def _isolated_demo_cache_env(tmp_path, monkeypatch):
    """本模块所有用例都可能改 DEMO_CACHE_*：固定封闭环境并隔离 Settings 缓存与单例。"""
    monkeypatch.setenv("SILICONFLOW_API_KEY", "")
    monkeypatch.setenv("DEMO_CACHE_MODE", "off")
    monkeypatch.setenv("DEMO_CACHE_PATH", str(tmp_path / "demo_cache.json"))
    get_settings.cache_clear()
    reset_llm_client()
    reset_result_cache()
    yield
    get_settings.cache_clear()
    reset_llm_client()
    reset_result_cache()


class _FakeInner:
    """记录调用次数的假 LlmClient：零网络。"""

    def __init__(self, content: str = '{"ok": true}') -> None:
        self.calls = 0
        self.content = content
        self.closed = False

    async def chat(self, messages, *, temperature=0.4, max_tokens=1500, retries=2) -> LlmResult:
        self.calls += 1
        self.last_messages = messages
        return LlmResult(
            content=self.content, model="fake-model", prompt_tokens=11, completion_tokens=7
        )

    async def aclose(self) -> None:
        self.closed = True


# ---- ExactHashResultCache ----


async def test_exact_hash_roundtrip_and_miss(tmp_path):
    cache = ExactHashResultCache(str(tmp_path / "c.json"))
    key = cache_key("m", _MSGS, 0.4, 1500)
    assert await cache.get(key) is None  # 冷缓存未命中
    await cache.put(key, '{"answer": 1}')
    assert await cache.get(key) == '{"answer": 1}'
    assert await cache.get("no-such-key") is None


def test_cache_key_distinguishes_request_dimensions():
    base = cache_key("m", _MSGS, 0.4, 1500)
    assert base == cache_key("m", _MSGS, 0.4, 1500)  # 同请求同 key（确定性）
    assert base != cache_key("other-model", _MSGS, 0.4, 1500)
    assert base != cache_key("m", [{"role": "user", "content": "别的输入"}], 0.4, 1500)
    assert base != cache_key("m", _MSGS, 0.7, 1500)  # temperature 参与哈希
    assert base != cache_key("m", _MSGS, 0.4, 800)


async def test_cache_persists_to_file_and_reloads(tmp_path):
    path = tmp_path / "sub" / "c.json"  # put 需自动建父目录
    cache = ExactHashResultCache(str(path))
    key = cache_key("m", _MSGS, 0.4, 1500)
    await cache.put(key, "cached-content")
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw[key] == "cached-content"
    # 新实例从盘上懒加载读回（模拟进程重启）
    reopened = ExactHashResultCache(str(path))
    assert await reopened.get(key) == "cached-content"
    assert reopened.size == 1


async def test_corrupt_cache_file_treated_as_cold(tmp_path):
    path = tmp_path / "c.json"
    path.write_text("{broken json", encoding="utf-8")
    cache = ExactHashResultCache(str(path))
    assert await cache.get(cache_key("m", _MSGS, 0.4, 1500)) is None


# ---- CachedLlmClient ----


async def test_cached_client_hit_does_not_call_inner(tmp_path):
    inner = _FakeInner(content='{"n": 2}')
    client = CachedLlmClient(inner=inner, cache=ExactHashResultCache(str(tmp_path / "c.json")))
    first = await client.chat(_MSGS)
    assert inner.calls == 1 and first.model == "fake-model"
    second = await client.chat(_MSGS)  # 同请求第二次必须命中缓存
    assert inner.calls == 1  # 未打 inner
    assert second.content == first.content
    assert second.model == "demo-cache"
    assert second.prompt_tokens == 0 and second.completion_tokens == 0
    assert second.total_tokens == 0


async def test_cached_client_miss_calls_inner_and_populates_cache(tmp_path):
    inner = _FakeInner()
    cache = ExactHashResultCache(str(tmp_path / "c.json"))
    client = CachedLlmClient(inner=inner, cache=cache)
    result = await client.chat(_MSGS, temperature=0.2, max_tokens=900)
    assert inner.calls == 1
    assert result.model == "fake-model"
    key = cache_key(get_settings().llm_model, _MSGS, 0.2, 900)
    assert await cache.get(key) == '{"ok": true}'  # 内存可见
    assert json.loads((tmp_path / "c.json").read_text(encoding="utf-8"))[key] == '{"ok": true}'
    # 落盘后新实例也能命中
    assert await ExactHashResultCache(str(tmp_path / "c.json")).get(key) == '{"ok": true}'


async def test_cached_client_inner_error_propagates_without_caching(tmp_path):
    class _Boom(_FakeInner):
        async def chat(self, messages, **kwargs):
            self.calls += 1
            raise LlmError("simulated outage")

    inner = _Boom()
    cache = ExactHashResultCache(str(tmp_path / "c.json"))
    with pytest.raises(LlmError):
        await CachedLlmClient(inner=inner, cache=cache).chat(_MSGS)
    assert inner.calls == 1
    assert cache.size == 0  # 失败结果绝不进缓存


async def test_aclose_transparently_closes_inner(tmp_path):
    inner = _FakeInner()
    client = CachedLlmClient(inner=inner, cache=ExactHashResultCache(str(tmp_path / "c.json")))
    await client.aclose()
    assert inner.closed


# ---- 模式开关与装配 ----


@pytest.mark.parametrize(
    ("mode", "expect_enabled"),
    [("off", False), ("read", True), ("readwrite", True)],
)
def test_llm_enabled_matrix_with_empty_key(monkeypatch, mode, expect_enabled):
    """key 为空时：off → False（stub）；read/readwrite → True（敢发起调用以命中缓存）。"""
    monkeypatch.setenv("DEMO_CACHE_MODE", mode)
    monkeypatch.setenv("SILICONFLOW_API_KEY", "")
    get_settings.cache_clear()
    assert llm_enabled() is expect_enabled


def test_llm_enabled_true_when_key_present_even_off(monkeypatch):
    monkeypatch.setenv("DEMO_CACHE_MODE", "off")
    monkeypatch.setenv("SILICONFLOW_API_KEY", "dummy-not-real")
    get_settings.cache_clear()
    assert llm_enabled() is True


def test_get_llm_client_wiring_follows_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("DEMO_CACHE_MODE", "readwrite")
    monkeypatch.setenv("DEMO_CACHE_PATH", str(tmp_path / "wired.json"))
    get_settings.cache_clear()
    reset_llm_client()
    reset_result_cache()
    wrapped = get_llm_client()
    assert isinstance(wrapped, CachedLlmClient)
    assert get_llm_client() is wrapped  # 单例
    monkeypatch.setenv("DEMO_CACHE_MODE", "off")
    get_settings.cache_clear()
    reset_llm_client()
    assert type(get_llm_client()) is LlmClient  # off 直通裸客户端
