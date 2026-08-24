"""全局配置。唯一真源是 .env / 环境变量；测试通过环境变量 + get_settings.cache_clear() 覆盖。"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./demo.db"

    # M0 demo 开关（M5 接入真实审批 interrupt 后 AUTO_APPROVE 下线）
    auto_approve: bool = True
    evidence_threshold: float = 0.7
    max_research_rounds: int = 2
    max_critique_rounds: int = 3

    # LLM provider（M0 stub 节点不调用；M2 接入 SiliconFlow）
    siliconflow_api_key: str = ""
    siliconflow_base_url: str = "https://api.siliconflow.cn/v1"
    llm_model: str = "deepseek-ai/DeepSeek-V3.2"
    embedding_model: str = "BAAI/bge-m3"


@lru_cache
def get_settings() -> Settings:
    return Settings()
