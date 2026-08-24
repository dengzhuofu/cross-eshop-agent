"""租户上下文。

铁律（PRD §13.6/§19.4）：tenant_id 由系统注入，不接受 LLM/客户端在业务参数里传入。
M0 通过 X-Tenant-Id 头注入（dev 模式）；接真实鉴权后改为令牌解析，接口不变。
"""

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Optional


class TenantContextError(Exception):
    pass


@dataclass(frozen=True)
class TenantContext:
    tenant_id: str
    actor_id: Optional[str] = None


_current: ContextVar[Optional[TenantContext]] = ContextVar("tenant_context", default=None)


def current_tenant() -> TenantContext:
    ctx = _current.get()
    if ctx is None:
        raise TenantContextError("tenant context 未注入：拒绝执行任何数据访问")
    return ctx


def try_current_tenant() -> Optional[TenantContext]:
    return _current.get()


def set_current_tenant(ctx: TenantContext):
    """请求入口（API 依赖/中间件）调用；返回 token 供 finally 里 reset。"""
    return _current.set(ctx)


def reset_current_tenant(token) -> None:
    _current.reset(token)
