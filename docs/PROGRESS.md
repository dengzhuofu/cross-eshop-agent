# 开发进度快照(供上下文压缩后恢复)

> 每完成一个里程碑更新本文件。设计依据:`docs/跨境电商全链路Agent平台-PRD-v2.md` + `docs/跨境电商全链路Agent平台-v1.4修订-MVP裁剪与LangGraph结构.md`

## 环境事实(踩坑记录,勿重复排查)

- 仓库本地路径:`C:\Users\Yinsen\WorkBuddy\cross-eshop-agent`,远端 `https://github.com/dengzhuofu/cross-eshop-agent.git`(main 分支)
- Python 用 `py -3` 调用(WindowsApps stub 会静默失败);venv 在 `backend/.venv`
- **真实 PostgreSQL**:本机二进制在 `/f/postgresql/bin`(注意不是 C 盘),专用集群数据目录 `backend/.localdata/pgdata`,端口 **15433**(5433 被别的实例占用),用户 `cesa/cesa_secret`,库 `crosseshop`
- 启动 PG:`bash backend/scripts/dev_postgres.sh`(幂等);连接串在 `backend/.env`(已 gitignore)
- Git Bash 下 curl 发中文 JSON 会乱码 → 先用 python 写 UTF-8 文件再 `--data-binary @file`
- 测试用 hermetic sqlite(conftest 里先设 env 再 import app),**运行时必须真实 PG**

## 架构铁律(v1.4 §2.3)

1. domain / persistence(schemas) / tools-schemas 三层分离
2. Postgres repository 是 workflow 状态唯一真相;checkpointer 只管 resume
3. 不用 LangGraph BaseStore
4. 单一 graph 定义同时服务 `langgraph dev` 和 FastAPI 运行时
5. 多租户:tenant_id 只由系统注入(X-Tenant-Id header),IDOR 返回 404,工具函数永不接收 tenant_id 参数

## ✅ M0 行走骨架(commit 14fce5a,已在真实 PG 上端到端验证)

- LangGraph 官方标准结构(src layout + langgraph.json 指向 `./src/app/graphs/product_launch/agent.py:graph`)
- 13 个 stub 节点 + 条件边:research 加深(≤2轮)、go/no-go 门、critique 重写(≤3轮)、审批(interrupt seam 留给 M5)
- 全部决策写 AgentDecision 审计表;RunRecorder 吞 DB 异常只打日志
- API:POST/GET /api/v1/workflows、/{id}、/{id}/trace;X-Tenant-Id 注入;跨租户读返回 404 已验证
- 测试 10 个全过(ruff 干净);E2E 见证:0.55<0.7 加深→0.82 过门,critic 抓到"保证/100%"违禁词→重写→pass,15 steps/6 decisions

## ✅ M1 真实工具层(已完成,真实 PG 冒烟验证通过)

- **adapters/**:MarketplaceAdapter Protocol + 三平台差异化 mock —— Amazon(bullets 恰好5条/标题≤200/15%佣金/白底/ama_)、Shopify(0-10条/≤255/2.9%/shp_)、TikTok(1-3条/≤100/禁"最佳第一"/5%/tts_);adapter 内 memo 做第二层发布幂等
- **tools/**:ToolDefinition 注册中心(schema/风险等级/幂等/审批/超时);ToolExecutor 七步管线 = 输入schema校验→跨租户引用检测→审批门(AUTO_APPROVE 仅 dev)→幂等回放(按 tenant+idempotency_key 查历史 ok 输出)→wait_for 超时→输出schema校验→ToolCall 审计;ToolContext 注入租户(铁律:工具永不收 tenant_id 参数);无 DB 的 langgraph dev 场景由 persistence/memory.py 内存仓储兜底,调用通道仍唯一
- **persistence**:tool_calls 表(ix_tool_calls_tenant_idem 幂等索引)+ repo 三方法;**alembic** 0001 迁移由 autogenerate 对照 models 生成(索引名与 create_all 一致),api lifespan 启动即 upgrade_head;reset_demo = drop 全部表含 alembic_version 再迁移重建
- **节点接线**:listing 经 executor 查规则并按 bullets_min/max 整形卖点、截断标题(平台差异不再硬编码);publish 全部经 executor(审批凭据来自 state.approved),产物带 validation_errors/replayed 字段
- **API**:trace 增加 tool_calls 段
- **测试 22 个全绿**:适配器差异 6 + executor 集成 6(幂等回放同 listing_id/跨租户引用拒绝并落审计/AUTO_APPROVE=false 时审批门拒绝、带凭据放行/validation_failed 业务路径/schema 校验/未知工具)
- **冒烟 E2E(真库)**:completed 15步6决策;审计链 rules×4 + publish×2(high/ok/幂等键);research×2、listing→critic×2 循环可见;跨租户读 404

### M2 待办(下一步)

SiliconFlow 接入:research/listing 节点换真 LLM(typed tools: search_market_trends 等 stub 化数据源)、go/no-go 由 Planner 结合 rubric 决策、决策 reasoning 用 LLM 生成但仍写 AgentDecision 表。注意 SILICONFLOW_API_KEY 只进 .env,永不入库入 git。

## 后续里程碑速查

M3 利润/供应商真实现 · M4 记忆双线(pgvector)+上下文压缩+token计量 · M5 HITL interrupt · M6 Support RAG · M7 BadCase 红队 · M8 前端五页打磨。前端可视化面板(frontend/,Vite+React)已另行开工:列表/创建/详情三视图,展示步骤时间线+决策卡片流+工具审计表,vite proxy 到 :8000。

## 验证命令

```bash
bash backend/scripts/dev_postgres.sh          # 起 PG(15433)
cd backend && .venv/Scripts/python -m ruff check src tests scripts
.venv/Scripts/python -m pytest                # 22 个测试
.venv/Scripts/python scripts/reset_demo.py && .venv/Scripts/python scripts/seed_mock_data.py
.venv/Scripts/python -m uvicorn app.api.main:app --port 8000   # cwd=backend
```
