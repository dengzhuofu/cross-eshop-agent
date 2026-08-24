"""typed tools 层。

import 即完成工具注册（catalog 副作用）；executor 是所有调用的唯一通道。
"""

# 注册副作用必须放在最后：先定义再登记
from app.tools.catalog import marketplace as _marketplace_catalog  # noqa: F401,E402
from app.tools.context import ToolContext  # noqa: F401
from app.tools.executor import (  # noqa: F401
    ApprovalRequiredError,
    CrossTenantReferenceError,
    SchemaValidationError,
    ToolError,
    ToolExecutionResult,
    ToolTimeoutError,
    UnknownToolError,
    execute_tool,
)
from app.tools.registry import ToolDefinition, get_tool, list_tools  # noqa: F401
