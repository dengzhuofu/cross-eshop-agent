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

    # LLM provider（无 key 时节点自动走确定性 stub 路径，测试/CI 不依赖网络）
    siliconflow_api_key: str = ""
    siliconflow_base_url: str = "https://api.siliconflow.cn/v1"
    llm_model: str = "deepseek-ai/DeepSeek-V3.2"
    embedding_model: str = "BAAI/bge-m3"
    llm_temperature: float = 0.4
    llm_timeout_s: float = 60.0
    # PRD §17 计量接缝：alert 阈值先告警；hard_budget 必须高于 alert（先看见再熔断），
    # 超限后本工作流后续 LLM 调用一律降级 stub（M4 硬熔断）——预算是租户级保护，
    # 宁可用确定性兜底也不烧穿成本。正常全链路 LLM run 约耗 2~4 万 token。
    token_alert_threshold: int = 50000
    llm_hard_budget: int = 80000


@lru_cache
def get_settings() -> Settings:
    return Settings()
