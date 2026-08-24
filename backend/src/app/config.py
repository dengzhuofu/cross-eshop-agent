"""全局配置。唯一真源是 .env / 环境变量；测试通过环境变量 + get_settings.cache_clear() 覆盖。"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./demo.db"

    # M0 demo 开关（M5 起支持工作流级 auto_approve=false 覆盖，走 interrupt 人工审批）
    auto_approve: bool = True
    # M5 interrupt/resume 的断点存储（checkpointer 只管恢复，不是状态真源——v1.4 §2.3 规则2；
    # 独立 sqlite 文件避免与业务库耦合，进程重启后待审工作流仍可批）
    checkpoint_db_path: str = ".localdata/checkpoints.db"
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

    # M8 Demo 兜底缓存（v1.4 §1.2：语义缓存不实现，精确-hash 缓存兼做离线演示兜底）。
    # 三态开关：
    #   off       —— 直连 LLM，行为与 M2 完全一致（默认）；
    #   read      —— 只读：命中缓存即离线重放预热的 LLM 产出（配合 key 留空可全离线演示，
    #                未命中时节点走确定性 stub 兜底）；
    #   readwrite —— 读 + 写：预热脚本用它把真实 LLM 产出落盘，供日后 read 模式重放。
    demo_cache_mode: str = "off"
    demo_cache_path: str = ".localdata/demo_cache.json"


@lru_cache
def get_settings() -> Settings:
    return Settings()
