# Cross Eshop Agent —— 跨境电商全链路 Agent 平台

面向中小跨境卖家的多租户 Agent 运营平台：选品研究 → 利润测算 → 供应商评估 → go/no-go 决策闸门 → 多平台 Listing 生成（生成-评审-重写闭环）→ 人工审批 → 模拟发布 → 运营监控 → 客服售后 → 复盘回流。

> 当前进度：**M1 已完成**。M0 十三步行走骨架 + M1 真实工具层（MarketplaceAdapter ×3、ToolExecutor 治理管线、ToolCall 审计、alembic 迁移）均已在真实 PostgreSQL 上端到端验证。Agent 节点仍为确定性 stub（零 LLM），M2 起逐个替换为真实 LLM。

## 快速开始

```bash
cd backend

# 1) 环境
py -3 -m venv .venv                       # Windows；Linux/macOS 用 python3 -m venv .venv
source .venv/Scripts/activate             # Windows Git Bash；cmd 用 .venv\Scripts\activate.bat
pip install -e ".[dev]"

# 2) 真实 PostgreSQL（无需 Docker：用本机 PG 二进制初始化专属实例，端口 15433）
bash scripts/dev_postgres.sh              # 输出 DATABASE_URL
cp .env.example .env                      # 把上面输出的 DATABASE_URL 填进去

# 3) 种子数据 + 启动 API
python scripts/seed_mock_data.py          # 两个演示租户：t_demo_acme / t_demo_globex
python -m uvicorn app.api.main:app --port 8000
```

创建并观察一条完整工作流：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/workflows \
  -H "Content-Type: application/json" -H "X-Tenant-Id: t_demo_acme" \
  -d '{"product_idea":"可折叠床底收纳箱","marketplaces":["amazon","tiktok_shop"]}'

# 稍候两秒后：
curl -H "X-Tenant-Id: t_demo_acme" http://127.0.0.1:8000/api/v1/workflows/<id>
curl -H "X-Tenant-Id: t_demo_acme" http://127.0.0.1:8000/api/v1/workflows/<id>/trace
```

`trace` 里能看到：planner 规划 → 研究证据不足 **自主触发第二轮深化** → 利润/供应商 → **go/no-go = proceed** → Listing 首轮含违规声明被 Critic 打回 → **带约束重写后通过** → dev 自动放行 → 发布（幂等键）→ 运营建议（高风险标记）→ 客服草稿 → 复盘（记忆回写接缝）。

LangGraph Studio 本地调试图定义（与 FastAPI 共用同一份 `graph` 导出）：

```bash
python -m langgraph dev   # 需要 langgraph-cli[inmem]
```

重置演示数据：`python scripts/reset_demo.py`

## 架构速览

```
backend/src/app/
├── graphs/product_launch/     # LangGraph 应用部分（官方形制）
│   ├── agent.py               #   构建并导出 graph 变量 ← langgraph.json 指向这里
│   ├── state.py               #   图状态 + scratchpad reducer
│   ├── nodes.py               #   十三个执行器节点（现为 stub）
│   └── edges.py               #   纯路由 + 循环硬上限
├── api/                       # FastAPI 壳层（workflows CRUD + trace + 租户注入）
├── adapters/                  # MarketplaceAdapter 协议：同接口不同平台规则（amazon/shopify/tiktok mock）
├── tools/                     # typed tools：注册中心 + ToolExecutor 唯一调用通道
│   ├── registry.py            #   ToolDefinition（schema/风险/幂等/审批/超时）
│   ├── executor.py            #   校验→跨租户检测→审批门→幂等回放→超时→审计
│   └── catalog/               #   按领域注册（marketplace；后续 profit/supplier…）
├── persistence/               # SQLAlchemy 模型与仓储 —— workflow 状态唯一真源
│   └── migrations/            # alembic（运行时 schema 管理入口 upgrade_head）
├── observability/recorder.py  # RunRecorder：节点→WorkflowStep/AgentDecision 的唯一写入口
├── multitenancy/              # TenantContext 注入（铁律：tenant_id 永远系统注入）
├── domain/                    # 业务枚举（状态机/风险等级/决策类型/BadCase 八类）
└── config.py                  # pydantic-settings，.env 唯一配置真源
```

四条结构性规则（详见 docs/v1.4 修订案 §2.3）：

1. **三分规则**：`domain/`=内部对象、`schemas/`=wire DTO、工具 IO schema 独立。
2. **双真源规则**：workflow 状态唯一真源是 repositories（PostgreSQL）；LangGraph checkpoint 只做断点恢复。
3. **不引入 BaseStore**：长期记忆走自建 pgvector 表（M4）。
4. **单一定义两种运行时**：图只在 `agent.py` 定义一次，`langgraph dev` 与 FastAPI 共用。

## 测试

```bash
pytest -q        # 单测（路由护栏上限）+ 集成（全链路对库跑通、跨租户 IDOR 阻断）
ruff check src tests scripts
```

测试使用临时 SQLite 保持封闭；运行时默认连接真实 PostgreSQL（`.env` 的 `DATABASE_URL`）。

## 里程碑路线图

| 里程碑 | 内容 | 状态 |
| --- | --- | --- |
| M0 | walking skeleton：十三步 stub 链路 + 状态机持久化 + 决策时间线 + 租户隔离 + 循环护栏 | ✅ |
| M1 | 工具治理层：MarketplaceAdapter 协议 + 三平台差异化规则 + ToolExecutor（schema 校验/跨租户引用检测/审批门/幂等回放/审计）+ ToolCall 审计表 + alembic 迁移 | ✅ |
| M2 | 三角真身(上)：SiliconFlow 接入 + research/profit 真实现 + go/no-go LLM 决策 | ⬜ |
| M3 | 三角真身(下)：listing/critic 真实现 + generate_image_brief | ⬜ |
| M4 | 记忆双线(pgvector) + 上下文压缩(summarization/tool-output) + token 计量 | ⬜ |
| M5 | 真实人工审批：LangGraph interrupt/resume + Approval Center | ⬜ |
| M6 | Support Agent + RAG 五类知识集合 | ⬜ |
| M7 | Bad Case 三条红队 seed + detector 注册表 + eval CI 门禁 | ⬜ |
| M8 | Demo 兜底缓存 + 前端五页 + 打磨 | ⬜ |

## 设计文档

- `docs/跨境电商全链路Agent平台-PRD-v2.md`（v1.3 全量 PRD）
- `docs/跨境电商全链路Agent平台-v1.4修订-MVP裁剪与LangGraph结构.md`（范围裁剪 + 结构基准，本仓库按此执行）
