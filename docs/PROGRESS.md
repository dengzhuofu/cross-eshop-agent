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

## ✅ M4 长期记忆双线 + 上下文压缩 + token 硬熔断(已完成,真实 LLM 全链路冒烟通过)

- **记忆存储**:本机 PG 17.9 **没有 pgvector 扩展** → `memories` 表用 JSON embedding 列 + Python 余弦相似度(迁移 0002);换 pgvector 只需改列类型为 VECTOR(1024) + 把排序挪进 SQL,检索契约不变
- **嵌入客户端** `llm/embeddings.py`:BAAI/bge-m3(1024 维,SiliconFlow /embeddings 已真网验证);无 key/网络失败自动降级 `_hash_embedding`(md5 分桶 + L2 归一化,确定性,测试零出网)。**写入与查询必须同引擎**(seed 脚本经 embed_texts 生成,engine 跟随 .env)
- **两个治理工具**:retrieve_memory / record_memory 经 ToolExecutor 全管线;tenant_id 由 ToolContext 注入不可伪造,跨租户检索为空已有单测。工具数 9→11
- **节点接线**:node_supplier 检索 supplier_risk 记忆、按内容命中候选 id/name 附加 memory_hit(选择逻辑仍全确定性);node_retrospective 用 record_memory 真实回写 launch_lesson(source_workflow_id 关联本工作流),失败不阻断收尾。目录里的静态 memory_hit seed 已删除(M3 的演示形态由真实检索替代)
- **token 硬熔断(PRD §17)**:所有 4 处 LLM 调用点统一走 `_llm_available(state)`(key 可用 且 llm_usage.total < llm_hard_budget=80000,alert 50k 先告警);research 步骤 detail 带 llm_budget_cut 标记供 UI 展示
- **上下文压缩接缝(PRD §9)**:`_compress_tool_outputs`(单工具 700 字符/总量 2400)已接 research prompt;后续节点按需复用
- **踩坑①:critic 三轮不收敛复发**——研究痛点"新箱异味"持续诱导生成端换皮输出 odor-free(Odor-Free Fabric/Odorless/minimize odor),prompt 约束压不住,"minimize new-box odor"还被过度追打。修复双管齐下:(a)`CLAIM_HEDGE_MAP` + `_sanitize_llm_copy` 在**生成端**确定性改写(odor-free→low-odor 等,只动 LLM 产物、stub 埋雷不动),改动留痕进 step detail;(b)rubric 补"low-odor/minimize/reduce 等已留余量表述不要报"。修复后 run `e3d237cc` 一轮收敛(critic blocking=0),supplier 记忆降权与复盘回写全部在 trace 可见
- **踩坑②:风险记忆 seed 文案连带误伤**——一条记忆里同时点名 sup_002(违规方)和 sup_001(推荐替代品),节点按 id/name 匹配会把 sup_001 也打标 → 风险记忆只写被标记的供应商
- **可观测性**:critic step detail 现带 blocking_issues 原文(LLM issue 键是 "issue" 不是 "phrase",取值要兜底)
- **测试 46 个全绿**(+6 M4 工具/嵌入单测,+2 sanitizer);ruff 干净

## ✅ M5 真实人工审批 HITL:interrupt/resume + 审批中心(已完成,浏览器端到端验收通过)

- **机制**:node_approval_check 在 manual 模式下调 LangGraph `interrupt(payload)`,图挂起、状态置 `awaiting_approval`,审批快照(margin/主供应商/风险旗标/各平台 Listing 草稿)双写 `workflows.result_json.pending_approval`(检查点只是恢复手段,不是真源——v1.4 §2.3 规则 2);`POST /workflows/{id}/approval` 用 `Command(resume=...)` 唤醒,approve→继续 publish×2→复盘,reject→halted(驳回理由入 halted detail),全程步骤/决策照常落审计
- **检查点**:`AsyncSqliteSaver`(`.localdata/checkpoints.db`,gitignore),FastAPI lifespan 里 `async with from_conn_string` 包住 app 生命周期;thread_id=workflow_id。**resume 会从被 interrupt 的节点头部重跑** → interrupt() 必须放在该节点一切副作用之前
- **按工作流覆盖**:task_input.auto_approve=false 可在全局 AUTO_APPROVE=true 时仍走人工闸门,不用重启服务
- **API**:`GET /api/v1/approvals`(跨工作流过滤 awaiting_approval 队列)+ `POST /workflows/{id}/approval`(404 跨租户/不存在、409 重复审批);审批附言随决策写入审计(human_approval 决策,agent=human_approver)
- **前端审批中心**:导航角标(待审数)、快照卡(利润率/供应商/风险旗标红条/双平台 Listing 预览)、附言输入 + 通过/驳回,409 已处理;创建表单新增「发布前需人工审批」勾选(auto_approve 取反下发)
- **踩坑①:_finalize 曾无条件置 completed**——gate LLM 合规选 revise 导致流程在 listing 前就 halted,复盘不存在却被标"已完成" → _finalize 改为仅当 final_state 有 retrospective 才置 completed,halted/abort/reject 保持 recorder 终态
- **踩坑②:resume 丢 recorder**——`Command(resume)` 的 config 忘带 recorder,恢复段审计静默落 NullRecorder → resume config 必须与首次 ainvoke 同样传 `{"recorder": rec, "thread_id": ...}`
- **踩坑③:AsyncSqliteSaver 连接生命周期**——测试里 suspend 与 resume 必须在同一个 `async with` 连接内(自建 harness contextmanager);服务端 .localdata 目录不存在会启动即挂,lifespan 先 mkdir
- **验收**:pytest 50 绿(+4 HITL 集成:挂起快照/通过发布/驳回取消/双路径 auto_approve);浏览器实测 acme 租户:角标 1→卡片区→附言「利润率与供应商风险均在阈值内」→通过→resume→completed,trace 16 步含 approval_check(manual_pending)→approval_check(human, approved=true, comment 原文)→publish×2,决策含 human_approver→approve;驳回路径此前 API 级已验(cancelled + 理由入 halted + publish 0 次);IDOR 404 / 409 双审均已验
- **已知非缺陷**:决策门 LLM 对弱证据选题可能自评 0.55~0.60 选 revise → 流程在 listing 前合法 halted(系统按设计保守);换选题即可演示

## ✅ M6 客服 RAG:五类知识集合 + 融合铁律(已完成,真实 LLM 全链路冒烟通过)

- **知识库表**:迁移 0003 `knowledge_base`(category/title/content/ref/embedding JSON,租户隔离);五类知识 policy(退换货/退款/保修)/platform_rule(平台售后规则)/product_info(商品说明/尺码/安装)/faq(FAQ/物流时效)/script(客服话术/差评处理),seed 脚本 `scripts/seed_knowledge.py` + 数据模块 `scripts/knowledge_seed_data.py`(22 条,嵌入引擎跟随 .env,零向量探测幂等)
- **两个治理工具**(11→13):`search_knowledge`(RAG 语义检索,category 可选过滤,跨租户不可见)+ `get_order_status`(确定性模拟 OMS 数据源:订单状态/物流轨迹/支付状态/退款资格,未知单号返回 found=false 不抛错)
- **node_support 融合铁律(PRD §7.11)**:订单事实一律 get_order_status 工具、政策引用一律 search_knowledge RAG;LLM 只起草,代码做硬保证——(a)`_etas_in` 抽取草稿中一切时效表述,与工具 eta_text 不一致即判冲突,**整稿弃用回退确定性模板**(工具实时数据不可被知识库覆盖);(b)cited_refs 过滤为 RAG 命中白名单(幻觉引用直接丢弃);(c)草稿过 `_sanitize_llm_copy` 绝对化整形;(d)refund_request 工单强制 escalate(退款必须审批,PRD §14.1)
- **可观测**:support step detail 带 draft_preview/order_status/eta_text/rag_hits/conflict_check/draft_source(llm|template);决策 support_reply(support_agent),reasoning 写明冲突判定
- **踩坑:测试库跨用例泄漏**——conftest 的临时 SQLite 文件整个 pytest 会话共享、`init_db` 只 create_all 不清数据,前一用例 seed 的知识会泄漏进后一用例(同租户断言精确相等就翻车)→ 每个用例用独立租户 id;另 `tenants.name` 有 UNIQUE 约束,ensure_tenant 按 id 判存在,同名不同 id 会 IntegrityError → 租户名带 tag
- **验收**:pytest 58 绿(+6 RAG/订单工具/冲突回退单测,+2 集成:全链路 support 融合 + globex 跨租户不可见);ruff/tsc 干净;真实冒烟 run `09842a44`:support 步骤 engine=llm、rag_hits=3、refs=[POL-RFD-02, POL-EXC-05, FAQ-02] 全部命中白名单、conflict_check 无冲突,决策与双工具审计落 PG
- **演示冲突路径**:FAQ-01 写通用时效 7-10 个工作日而 ord_88123 实时 3-5——LLM 若抄了通用值,`_etas_in` 判冲突弃稿回退模板,单测 `test_support_conflict_falls_back_to_template` 固化该行为

## 后续里程碑速查

M6 客服 RAG ✅(见上) · M7 BadCase 红队 · M8 前端五页打磨(可观测面板三视图 + 审批中心已完成)。

## 验证命令

```bash
bash backend/scripts/dev_postgres.sh          # 起 PG(15433)
cd backend && .venv/Scripts/python -m ruff check src tests scripts
.venv/Scripts/python -m pytest                # 46 个测试(RUN_LLM_SMOKE=1 追加 2 个真网冒烟)
.venv/Scripts/python scripts/reset_demo.py && .venv/Scripts/python scripts/seed_mock_data.py
.venv/Scripts/python -m uvicorn app.api.main:app --port 8000   # cwd=backend
```
