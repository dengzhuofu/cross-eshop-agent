"""SiliconFlow（OpenAI 兼容协议）嵌入客户端 + 确定性 hash 兜底引擎。

M4 长期记忆接缝：
- embedding_enabled() 为 False（无 key）时全部走本地 hash 引擎——测试与 CI 永不依赖网络；
- API 失败（网络/HTTP 错/解析错）时按 allow_fallback 降级 hash 或抛 LlmError，
  调用方（工具 handler）只感知 (向量, usage, engine) 三元组；
- hash 引擎是确定性词袋：跨进程稳定（md5 分桶），同一文本向量恒等，可离线复现检索。
"""

import hashlib
import logging
import math
import re

import httpx

from app.config import get_settings
from app.llm.client import LlmError

logger = logging.getLogger(__name__)

EMBEDDING_DIM = 1024


def embedding_enabled() -> bool:
    return bool(get_settings().siliconflow_api_key)


async def embed_texts(
    texts: list[str], *, allow_fallback: bool = True
) -> tuple[list[list[float]], dict, str]:
    """批量嵌入文本，返回 (向量列表, usage{"prompt_tokens":int}, engine:"api"|"hash")。

    - 已启用 API：POST {base_url}/embeddings（Bearer key，模型取 settings.embedding_model），
      失败时 allow_fallback=True 转 hash 引擎并告警，否则抛 LlmError；
    - 未启用：allow_fallback=True 全部走 hash 引擎，False 直接抛 LlmError。
    """
    if not embedding_enabled():
        if not allow_fallback:
            raise LlmError("embedding api disabled: SILICONFLOW_API_KEY is empty")
        logger.warning("embedding api disabled, falling back to hash engine (%d texts)", len(texts))
        return [_hash_embedding(t) for t in texts], {"prompt_tokens": 0}, "hash"

    s = get_settings()
    payload = {"model": s.embedding_model, "input": texts}
    try:
        async with httpx.AsyncClient(
            base_url=s.siliconflow_base_url,
            headers={"Authorization": f"Bearer {s.siliconflow_api_key}"},
            timeout=30.0,
        ) as http:
            resp = await http.post("/embeddings", json=payload)
            resp.raise_for_status()
            data = resp.json()
            items = sorted(data["data"], key=lambda d: d.get("index", 0))
            vectors = [list(item["embedding"]) for item in items]
            usage_raw = data.get("usage") or {}
            usage = {"prompt_tokens": int(usage_raw.get("prompt_tokens", 0))}
            if len(vectors) != len(texts):
                raise ValueError(f"embedding count mismatch: {len(vectors)} != {len(texts)}")
            return vectors, usage, "api"
    except (httpx.HTTPError, LlmError, KeyError, IndexError, TypeError, ValueError) as exc:
        if not allow_fallback:
            raise LlmError(f"embeddings failed: {exc}") from exc
        logger.warning("embeddings api failed (%s), falling back to hash engine", exc)
        return [_hash_embedding(t) for t in texts], {"prompt_tokens": 0}, "hash"


def _hash_tokens(text: str) -> list[str]:
    """hash 引擎分词：与检索侧共用 app.rag.tokenize（拉丁词 + jieba CJK 词 + 单字）。

    修复史：早期只认 [a-z0-9]+，纯中文文本全部落成零向量，离线检索退化成
    任意序——中文是本平台主语料，CJK 必须进词袋。单字层保证「退换货/退货」
    这类词面不同但字面重叠的说法仍有召回。rag 层缺席时退化为本地等价实现。
    """
    try:
        from app.rag.tokenize import tokenize

        return tokenize(text)
    except ImportError:  # pragma: no cover - 防御：rag 模块缺席时的等价兜底
        tokens = re.findall(r"[a-z0-9]+", text.lower())
        try:
            import jieba

            tokens.extend(w for w in jieba.cut_for_search(text) if re.search(r"[\u4e00-\u9fff]", w))
        except ImportError:  # pragma: no cover
            pass
        tokens.extend(re.findall(r"[\u4e00-\u9fff]", text))
        return tokens


def _hash_embedding(text: str) -> list[float]:
    """确定性本地嵌入：分词（拉丁词 + CJK 词）后每词经 md5 哈希分桶计数再 L2 归一化。

    内置 hash() 按进程加盐不可复现——必须用 hashlib.md5 把词转成稳定整数再对维度取模。
    空文本（或不含可分词内容）返回全零向量。
    """
    counts = [0.0] * EMBEDDING_DIM
    for token in _hash_tokens(text):
        digest = hashlib.md5(token.encode("utf-8")).hexdigest()
        bucket = int(digest, 16) % EMBEDDING_DIM
        counts[bucket] += 1.0
    norm = math.sqrt(sum(v * v for v in counts))
    if norm == 0.0:
        return [0.0] * EMBEDDING_DIM
    return [v / norm for v in counts]
