"""缓存接缝层（M8）：Demo 兜底缓存与未来语义缓存共用的 ResultCache 接口。"""

from app.cache.result_cache import (  # noqa: F401
    ExactHashResultCache,
    ResultCache,
    cache_key,
    get_result_cache,
    reset_result_cache,
)
