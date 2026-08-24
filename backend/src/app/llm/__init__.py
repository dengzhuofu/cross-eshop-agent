"""LLM 接入层（M2）：客户端 + 提示词装配。"""

from app.llm.client import (  # noqa: F401
    LlmClient,
    LlmError,
    extract_json,
    get_llm_client,
    llm_enabled,
    reset_llm_client,
)
