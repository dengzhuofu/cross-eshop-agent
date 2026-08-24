"""M8 Demo 兜底缓存（v1.4 §1.2 的接缝实现）。

v1.4 §1.2 决策：语义缓存不实现，Demo 兜底与缓存合并为同一套机制。本期落的是
「精确 hash」实现（model + messages + temperature + max_tokens 的 canonical JSON
sha256 作 key），Phase 2 在同一个 ResultCache 接口后面换成 embedding 相似度检索，
并落 semantic_cache_entries 表（后置 migration，本期不做表）——调用方零改动。
"""

import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Protocol

from app.config import get_settings

logger = logging.getLogger(__name__)


class ResultCache(Protocol):
    """LLM 结果缓存统一接缝：精确 hash 与未来的 embedding 相似度实现共用。"""

    async def get(self, key: str) -> str | None:
        """命中返回缓存的补全文本，未命中返回 None。"""
        ...

    async def put(self, key: str, value: str) -> None:
        """写入一条缓存；写失败只记日志，绝不影响主流程。"""
        ...

    async def aclose(self) -> None:
        """释放底层资源（文件缓存为 no-op，向量库实现用于关连接）。"""
        ...


def cache_key(model: str, messages: list[dict], temperature: float, max_tokens: int) -> str:
    """对请求四元组做 canonical json（sort_keys、ensure_ascii=False）后取 sha256。"""
    payload = json.dumps(
        {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ExactHashResultCache:
    """JSON 文件存储的精确匹配缓存：懒加载进内存 dict，put 时原子写（临时文件 + os.replace）。

    文件损坏按冷缓存处理；目录不存在在首次 put 时创建。
    """

    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._data: dict[str, str] | None = None  # 懒加载：首次 get/put 才读盘

    def _load(self) -> dict[str, str]:
        if self._data is None:
            if self._path.exists():
                try:
                    loaded = json.loads(self._path.read_text(encoding="utf-8"))
                    self._data = loaded if isinstance(loaded, dict) else {}
                except (OSError, json.JSONDecodeError):
                    logger.warning("demo cache file unreadable, treating as cold: %s", self._path)
                    self._data = {}
            else:
                self._data = {}
        return self._data

    async def get(self, key: str) -> str | None:
        return self._load().get(key)

    async def put(self, key: str, value: str) -> None:
        data = self._load()
        data[key] = value
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=self._path.name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, self._path)  # 同分区替换是原子的，进程中断不会留半个文件
        finally:
            if os.path.exists(tmp):  # replace 成功后 tmp 已不存在，这里只清理失败残留
                os.remove(tmp)
        self._data = data

    @property
    def size(self) -> int:
        """当前条目数（预热脚本打印用）。"""
        return len(self._load())

    async def aclose(self) -> None:
        return None


_cache: ExactHashResultCache | None = None


def get_result_cache() -> ResultCache:
    global _cache
    if _cache is None:
        _cache = ExactHashResultCache(get_settings().demo_cache_path)
    return _cache


def reset_result_cache() -> None:
    """测试用：清空单例（与 reset_llm_client 职责分离，各自 reset，不删文件）。"""
    global _cache
    _cache = None
