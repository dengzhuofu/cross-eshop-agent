"""SiliconFlow（OpenAI 兼容协议）异步客户端。

M2 接缝落地点：
- llm_enabled() 为 False（无 key 且缓存关闭）时节点走确定性 stub 路径——测试与 CI 永不依赖网络；
- 所有调用返回 token usage（PRD §17 计量接缝），由节点累计进 state.llm_usage；
- extract_json 对 LLM 输出做防御式解析（裸 JSON / ```json 围栏 / 混杂文本均可）。

M8 增加 CachedLlmClient（v1.4 §1.2）：Demo 兜底缓存包裹层，接口与 LlmClient 同形，
节点零改动。行为矩阵见 CachedLlmClient docstring。
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass

import httpx

from app.cache.result_cache import ResultCache, cache_key, get_result_cache
from app.config import get_settings

logger = logging.getLogger(__name__)


class LlmError(Exception):
    pass


@dataclass(frozen=True)
class LlmResult:
    content: str
    model: str
    prompt_tokens: int
    completion_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def llm_enabled() -> bool:
    """节点发起 LLM 调用的总开关（无参，调用方众多，签名保持不变）。

    有 key → 在线路径；无 key 但 demo 缓存处于 read/readwrite → 离线演示模式：
    节点敢于发起调用从而命中预热缓存；未命中的调用由 inner 鉴权失败抛 LlmError，
    经节点既有 except LlmError 降级为确定性 stub。
    """
    s = get_settings()
    return bool(s.siliconflow_api_key) or s.demo_cache_mode in ("read", "readwrite")


def extract_json(text: str) -> dict:
    """防御式 JSON 提取：优先围栏块，其次全文，最后首个平衡大括号块。"""
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidates = []
    if fence:
        candidates.append(fence.group(1))
    candidates.append(text)
    start = text.find("{")
    if start != -1:
        depth = 0
        for i, ch in enumerate(text[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start : i + 1])
                    break
    for c in candidates:
        try:
            obj = json.loads(c)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    raise LlmError(f"no valid JSON object in llm output: {text[:200]!r}")


class LlmClient:
    def __init__(self, api_key: str, base_url: str, model: str, timeout_s: float = 60.0) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout_s = timeout_s
        self._client: httpx.AsyncClient | None = None

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=self._timeout_s,
            )
        return self._client

    async def chat(
        self,
        messages: list[dict],
        *,
        temperature: float = 0.4,
        max_tokens: int = 1500,
        retries: int = 2,
    ) -> LlmResult:
        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            try:
                http = await self._http()
                resp = await http.post("/chat/completions", json=payload)
                if resp.status_code in (429, 500, 502, 503, 504):
                    raise LlmError(f"transient status {resp.status_code}: {resp.text[:200]}")
                resp.raise_for_status()
                data = resp.json()
                usage = data.get("usage") or {}
                return LlmResult(
                    content=data["choices"][0]["message"]["content"] or "",
                    model=data.get("model", self._model),
                    prompt_tokens=int(usage.get("prompt_tokens", 0)),
                    completion_tokens=int(usage.get("completion_tokens", 0)),
                )
            except (httpx.HTTPError, LlmError, KeyError, ValueError) as exc:
                last_exc = exc
                if attempt < retries:
                    await asyncio.sleep(1.5 * (attempt + 1))
        raise LlmError(f"chat failed after {retries + 1} attempts: {last_exc}") from last_exc

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()


class CachedLlmClient:
    """Demo 兜底缓存包裹层（v1.4 §1.2）：chat/aclose 与 LlmClient 同形，节点零改动。

    行为矩阵（demo_cache_mode × 是否配置 siliconflow_api_key）：
    - off + 有 key：直通 inner，真实 LLM（与 M2 行为一致）。
    - off + 无 key：节点不发起调用（llm_enabled()=False → 确定性 stub）。
    - read / readwrite + 有 key：命中缓存直接返回（model=demo-cache、零 token）；
      未命中打真实 LLM；readwrite 额外把结果写入缓存供日后离线重放，read 不写。
      read 与 readwrite 的唯一差别就是是否写缓存。
    - read / readwrite + 无 key（离线兜底演示）：llm_enabled() 为 True，节点照常发起
      调用；命中缓存 → 返回预热的 LLM 产出；未命中 → inner 因鉴权失败抛 LlmError，
      原样上抛，由各节点既有 except LlmError 的确定性 stub 兜底——主链路不断。

    inner 抛出的 LlmError 一律原样上抛，本层不做重试/吞异常（重试是 inner 的职责，
    stub 降级是节点的职责，缓存层只管命中与否）。
    """

    def __init__(self, inner: LlmClient, cache: ResultCache) -> None:
        self._inner = inner
        self._cache = cache

    async def chat(
        self,
        messages: list[dict],
        *,
        temperature: float = 0.4,
        max_tokens: int = 1500,
        retries: int = 2,
    ) -> LlmResult:
        key = cache_key(get_settings().llm_model, messages, temperature, max_tokens)
        cached = await self._cache.get(key)
        if cached is not None:
            return LlmResult(
                content=cached, model="demo-cache", prompt_tokens=0, completion_tokens=0
            )
        result = await self._inner.chat(
            messages, temperature=temperature, max_tokens=max_tokens, retries=retries
        )
        await self._cache.put(key, result.content)
        return result

    async def aclose(self) -> None:
        # 透传关掉 inner 的 httpx 客户端；文件缓存的 aclose 是 no-op（职责分离，
        # reset_llm_client 不连带 reset_result_cache）
        await self._inner.aclose()
        await self._cache.aclose()


_client: LlmClient | CachedLlmClient | None = None


def get_llm_client() -> LlmClient | CachedLlmClient:
    global _client
    if _client is None:
        s = get_settings()
        inner = LlmClient(
            api_key=s.siliconflow_api_key,
            base_url=s.siliconflow_base_url,
            model=s.llm_model,
            timeout_s=s.llm_timeout_s,
        )
        if s.demo_cache_mode in ("read", "readwrite"):
            _client = CachedLlmClient(inner=inner, cache=get_result_cache())
        else:
            _client = inner
    return _client


def reset_llm_client() -> None:
    """测试用：清空单例。"""
    global _client
    _client = None
