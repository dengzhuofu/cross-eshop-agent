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

## ✅ M2 三角真身(上):LLM 接入(已完成,真实 LLM 全链路冒烟通过)

- **llm/client.py**:OpenAI 兼容客户端(SiliconFlow,DeepSeek-V3.2);429/5xx 指数退避重试;`extract_json`(fenced→全文→首个平衡括号);`llm_enabled()` 门控——无 key 走 stub,**conftest 强制清空 key 保证测试永不出网**;`RUN_LLM_SMOKE=1` 才跑真网冒烟(2 用例:连通性+rubric 遵从)
- **研究数据源工具**:tools/catalog/research.py 注册 search_market_trends / search_competitor_listings / search_customer_reviews(mock 数据,床底收纳域);LLM 数据一律来自受治理工具,只做综合与评分
- **节点 LLM 化(research/decision_gate/listing)**:
  - research:第一轮只有趋势(代码保证 score≤0.60)→ deepen → 第二轮补竞品+评论 → 0.75~0.95;rubric 违规由代码硬封顶(LLM 曾给趋势轮打 0.70)
  - decision_gate:evidence_summary 显式下发 `supplier_risk.primary_risk/has_backup`(曾因 LLM 从 flags 文本误读主供应商风险导致 revise/proceed 摇摆);GATE rubric 改为**优先级序**(abort 条件→proceed 条件→revise),命中即停,次要风险只准写进 reasoning
  - listing:LLM 生成三平台文案后仍走确定性整形(bullets 数量/标题长度按平台规则截断),critic 约束回注重写路径不变;listing step detail 增加 titles(UI 直接可见 LLM 文案)
  - **设计原则:LLM 只提议,代码做硬保证**(评分封顶/规则整形/rubric 优先级全是确定性兜底);任何 LLM 异常降级 stub 主链路不断
- **计量接缝(PRD §17)**:`_merge_llm_usage` 累计 tokens 进 state.llm_usage,超阈值告警日志(step detail 带 llm_usage);硬熔断留给 M4
- **测试 29 个全绿**(新增 extract_json×4、usage 累计、降级 stub、fake-LLM 路径);ruff 干净
- **真实 LLM 冒烟(DeepSeek-V3.2 实测)**:showcase run `7a7d671f` 全链路 completed——research 0.60→deepen→0.85→gate proceed(reasoning 正确引用 rubric 且把供应商历史风险列为参考)→listing×3 平台英文文案(engine=llm,titles 进 step detail UI 可见)→critic pass→publish×3 幂等键→复盘;UI 详情页三区块渲染正常

## ✅ 前端可观测面板(已提前落地,浏览器实测通过)

- `frontend/`:Vite + React + TS,零组件库,手写深色主题(#0d1117);vite proxy `/api`、`/healthz` → 127.0.0.1:8000(前端代码只用相对路径)
- 三视图:列表(表格+刷新+新建)、创建表单(idea/三渠道复选/市场/风险偏好)、详情(状态徽章+非终态 1.5s 轮询(setTimeout 链式防堆积)+步骤时间线(detail 可折叠 JSON)+决策卡片流(类型色条+最终选择 vs 备选)+工具审计表(风险/状态/幂等键))
- 顶部租户切换器(切换即重建 ApiClient 回列表)+ /healthz 在线指示灯;`src/labels.ts` 集中维护 21 状态/13 节点/9 决策类型的中文映射
- 浏览器实测:列表渲染真数据✅、详情三区块✅、表单创建「磁吸式桌面理线器」→轮询到 completed✅(新审计行幂等键正确)、切 Globex 看到空列表(隔离可视化)✅
- 启动:`cd frontend && npm run dev` → http://localhost:5173;构建 `npm run build`(tsc 零错误)
- 注意:Playwright locator click 在该 IAB 上会超时(fill 正常),浏览器操作用 dom_cua 节点路径或 cua 坐标(截图坐标是缩放过的,需按视口换算)

## ✅ M3 三角真身(下):治理工具补全 + critic LLM 审查(已完成,真实 LLM 全链路冒烟通过)

- **新工具目录(共 9 个治理工具)**:
  - estimate_profit:佣金率取自 MarketplaceAdapter 规则(**注意 referral_fee_pct 是百分数 15.0,不是 0.15**——初版没除 100 被冒烟抓出),含退货损耗项;margin 25.41% vs 旧 stub 27.31%
  - search_suppliers:供应商目录 + 历史风险记忆 seed(sup_002 memory_hit);节点内确定性选择(low-risk 优先→质量分降序→价格升序)
  - generate_image_brief:按平台 ImageSpec 出结构化拍摄 brief(主图规范/分镜/合规注意);listing 节点替换硬编码 image_brief
- **critic 两层审查**:第一层确定性违禁词扫描(红线,零成本)不变;第二层 LLM 语义审查(_critic_via_llm,失败降级纯确定性)。**分级拦截:high 才触发重写,medium 只记录进 scratchpad**——否则审美级意见无限打回直到循环上限(真实冒烟曾 7→3→6 三轮耗尽)
- **踩坑:critic rubric 的范畴错误**——初版把"宣称解决品类差评点"当"与证据矛盾"追打(承重30kg回应"易塌陷"被打回)。修正后语义:品类痛点≠本品矛盾,解决方案型卖点是正当营销;硬违规只限绝对化措辞(odor-free 这类,应写 low-odor)与本品自身事实矛盾。修正后离线复现验证:承重卖点不报、Odor-free 正确报 high
- **executor 加固**:handler 内非 ToolError 异常(如未知渠道名)原先会穿透节点的 except ToolError 兜底 → 新增 ToolHandlerError 统一包装并落审计
- **测试 38 个全绿**(+7 工具单测:数学/adapter 费率驱动/memory_hit/ImageSpec/executor 通道一致性;+2 critic 分级拦截);ruff 干净
- **真实 LLM 冒烟**:run `117912aa` 一次通过(critic pass blocking=0,15 治理工具调用,publish×3 幂等键);此前 run `9144c2c8` 验证了重写闭环全形态(27 条审计含 image_brief×8)

## 后续里程碑速查

M4 记忆双线(pgvector)+上下文压缩+token硬熔断 · M5 HITL interrupt · M6 Support RAG · M7 BadCase 红队 · M8 前端五页打磨(可观测面板三视图已完成)。

## 验证命令

```bash
bash backend/scripts/dev_postgres.sh          # 起 PG(15433)
cd backend && .venv/Scripts/python -m ruff check src tests scripts
.venv/Scripts/python -m pytest                # 38 个测试(RUN_LLM_SMOKE=1 追加 2 个真网冒烟)
.venv/Scripts/python scripts/reset_demo.py && .venv/Scripts/python scripts/seed_mock_data.py
.venv/Scripts/python -m uvicorn app.api.main:app --port 8000   # cwd=backend
```
