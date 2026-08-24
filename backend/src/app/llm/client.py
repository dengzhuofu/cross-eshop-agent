"""SiliconFlow（OpenAI 兼容协议）异步客户端。

M2 接缝落地点：
- llm_enabled() 为 False（无 key）时节点走确定性 stub 路径——测试与 CI 永不依赖网络；
- 所有调用返回 token usage（PRD §17 计量接缝），由节点累计进 state.llm_usage；
- extract_json 对 LLM 输出做防御式解析（裸 JSON / ```json 围栏 / 混杂文本均可）。
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass

import httpx

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
    return bool(get_settings().siliconflow_api_key)


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


_client: LlmClient | None = None


def get_llm_client() -> LlmClient:
    global _client
    if _client is None:
        s = get_settings()
        _client = LlmClient(
            api_key=s.siliconflow_api_key,
            base_url=s.siliconflow_base_url,
            model=s.llm_model,
            timeout_s=s.llm_timeout_s,
        )
    return _client


def reset_llm_client() -> None:
    """测试用：清空单例。"""
    global _client
    _client = None
