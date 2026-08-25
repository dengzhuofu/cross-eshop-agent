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

## ✅ M7 BadCase 红队:detector 注册表 + 三道防线 + eval 门禁(已完成,62 测试绿)

- **detector 注册表(`guardrails/badcases.py`)**:`register_detector`/`run_all_detectors`,每类 detector 独立实现、独立注册,新类别=新注册不动主干(v1.4 §1.5);全部纯确定性正则零 LLM。已注册 3 个:input_injection(A,提示注入/越狱指令)、output_absolute_claims(B,绝对化违禁声明)、memory_poisoning(F,夸大话术污染记忆)。枚举 `BadCaseCategory` 八类全集保留(A-H),MVP 只实现三条 seed 对应 detector
- **三道防线接线(纵深防御)**:(1)planner——选题先扫描再 `scrub_untrusted` 脱敏,**脱敏版直接替换 task_input.product_idea** 再喂生成(输入侧);(2)listing——每个平台草稿 title+claim+bullets 全文扫描(输出侧);(3)retrospective——复盘 lesson 回写前扫描,命中投毒即跳过 record_memory 并落 memory_writeback_block 坏例(记忆投毒兜底)。所有命中写 `bad_cases` 表(status=quarantined),step detail 带 bad_case_hits,主流程永不阻断
- **红队真实战果**:首版 seed 注入文案("ignore all previous instructions"/"跳过所有审查")经 planner→listing 泄漏进最终 Listing——由此催生 scrub_untrusted:**与 detector 共享同一组正则**(检出什么就剥什么,规则永不漂移);"跳过所有审查"最初用裸 `skip` 匹配太宽误伤正常文案,收窄为 `skip\s+(?:the\s+)?(?:all\s+)?(?:reviews?|checks?|validation|qa)`
- **eval 门禁双形态**:`app/evals/redteam.py` 定义并执行 3 条 seed(注入 A/违禁声明 B/埋雷 C"保证10年不坏"/记忆投毒 F),断言=期望类别检出 + Listing 无违禁词 + 复盘 JSON 无投毒;pytest 参数化集成测试跑同一份 seed;standalone `backend/evals/run_evals.py`(自建封闭环境,临时 SQLite+空 key 零出网)供 CI 单命令门禁,任一 seed 违规 exit 1
- **配套**:`bad_cases` 表(迁移 0004,tenant/workflow/category/severity/detector/evidence JSON/status)+ `GET /api/v1/badcases`(租户隔离,limit/workflow_id/category 过滤)+ `scripts/export_badcases.py`(JSONL 导出数据集)
- **踩坑:重复枚举定义**——enums.py 里残留一版旧 BadCaseCategory stub(a_input/b_output 值)排在新版之后把新枚举整个遮蔽,运行时 AttributeError: output_runaway;grep 双定义删旧即愈。测试库泄漏与 tenants.name UNIQUE 两坑同 M6(见上),红队租户同样带 seq 序号
- **验收**:pytest 62 绿(+3 红队参数化 +1 坏例租户隔离);ruff 干净(含 evals 目录);门禁脚本 3×PASS(注入 seed 检出 A+B 两类、违禁声明 seed 检出 B、投毒 seed 检出 F+B 且复盘无污染)

## ✅ M8 打磨收官:主链路 RAG + Demo 兜底缓存 + 部署 + 前端面板(已完成,77 测试绿,真库+真 LLM 冒烟通过)

- **主链路自用 RAG(用户需求:RAG 不止客服,agent 本身也要外挂知识库)**:知识库新增第 6 类 `ops_playbook`(选品方法论 OPS-SEL-01 / Amazon Listing 守则 OPS-AMZ-LS1 / TikTok 内容电商 OPS-TTS-CM1 / 定价促销 OPS-PRC-ST1 / 旺季备货 OPS-LOG-Q4,共 27 条);planner 规划前检索「选题+选品打法」、listing 每平台检索「平台 Listing 守则」经 `search_knowledge` 治理工具(审计留痕),命中以参考资料身份进生成提示词(advisory,不替代研究证据、不得据此绝对化承诺),refs 写入 step detail `knowledge_refs` 可观测;检索失败不阻断主链路。真库冒烟 run `796996ea`:planner 命中 [OPS-SEL-01, OPS-AMZ-LS1],amazon→OPS-AMZ-LS1 / tiktok_shop→OPS-TTS-CM1 定向正确,4 次检索全审计
- **Demo 兜底缓存(v1.4 §1.2 接缝)**:`cache/result_cache.py` 定义 `ResultCache` Protocol + MVP 精确 hash 文件实现(原子写,Phase 2 同接口换 embedding 相似度,semantic_cache_entries 表留后置 migration);`CachedLlmClient` 包裹 LLM 客户端,`demo_cache_mode` 三态 off/read/readwrite——预热:`warm_demo_cache.py`(readwrite+真 key 跑 3 条选题),离线演示:无 key+read 命中即重放真实 LLM 产出(0 token),未命中走节点既有 stub 兜底;`llm_enabled()` 在 read 态放行以敢发起命中
- **部署**:`backend/Dockerfile`(src 布局安装+迁移/种子随镜像)+`frontend/Dockerfile`(vite build→nginx 反代 /api)+`docker-compose.yml`(PG16+后端自动迁移种子+前端 :8088)+`scripts/reset_and_replay.sh` 一键重置重放(已真库验证);**踩坑修复**:reset_demo.py 的 drop 列表停在 0001 时代,0002-0004 的表会让 upgrade_head 撞表——补全为全链 drop
- **前端第 5 页 Bad Case 面板**:统计卡(总数/高危/已隔离)+八类筛选 chips(前端过滤)+坏例卡(severity/status/category 徽标+证据折叠 JSON+detector 中文+点击回溯工作流详情);详情页 `bad_case_scan` 步骤特殊渲染(红色警示节点+origin 徽标+patterns/phrases 标签化);**踩坑**:证据折叠按钮嵌在可点击卡内,点击会冒泡触发卡片跳转——CollapsibleJson 的 toggle 加 stopPropagation 修复(浏览器实测验证)
- **兜底缓存真机验证**：warm 脚本（真 key+readwrite）跑 3 条选题写 18 条缓存（2 条被 go/no-go 正常拦下，blocked 也是合法预热终态）；离线重放（无 key+read）实测：research/decision_gate **0 token 命中缓存**、gate reasoning 与预热运行逐字一致；listing/support 未命中按设计降级 stub。**发现（重要）**：缓存 key 覆盖完整提示词（含 RAG 注入的 knowledge pack），离线侧嵌入引擎降级 hash 与预热侧 bge-m3 检索结果不同 → 提示词不同 → key 不同 → RAG 相关节点必然 miss。结论：全链离线重放要求预热/重放两侧嵌入引擎一致（演示库若用 hash 引擎播种知识即可全链重放）；Phase 2 语义缓存同样只解相似匹配、不解跨引擎检索漂移，该约束记入接缝说明
- **处置闭环（PRD §20.4 收尾）**：`POST /api/v1/badcases/{id}/status`（resolved/escalated/aborted，Literal 校验 422，repo 层 UPDATE 带 tenant_id 过滤防 IDOR、跨租户 404 防枚举）；面板卡片加「标记已处置/升级处理」按钮（stopPropagation 防冒泡跳转、终态卡隐藏按钮、outcome 处置留痕展示）
- **CI 生效（v1.4 验收收口）**：.github/workflows/ci.yml 双 job——backend(ruff 含 evals → pytest → 红队门禁 run_evals.py) + frontend(npm ci → tsc+vite build)，README 挂徽章
- **验收**:pytest 77 绿(+13 缓存单测 +2 主链路 RAG 集成);ruff/tsc 干净;红队门禁 3×PASS;浏览器端到端:面板筛选/跳转/证据展开、详情页扫描块与 knowledge_refs 全部实测通过,截图 4 张入 `docs/screenshots/`

## ✅ M9 Agentic RAG:检索 agentic 化 + 真机语料入库 + 质量评估门禁(已完成,116 测试绿,真库真 LLM 冒烟通过)

- **客服检索 agentic 化(node_support 升级)**:确定性 route 分类(实时事实/政策知识双通道)→ 查询改写(LLM JSON 失败降级 jieba 去停用词的确定性改写)→ hybrid 双路召回(BM25 词面 + 余弦语义 → RRF k=60 融合,返回项带 bm25/rrf 可解释分数)→ 相关性评级(LLM 判定 ∩ 确定性 coverage 评分取交集——LLM 只能收窄不能放宽)→ 零相关重试 ≤2 轮(第二轮用改写变体)→ 仅 relevant 命中进 rag_block;检索轨迹(retrieval_trace/rewrite_source/grade_source)全留痕。融合铁律不变:草稿时效与工具 ETA 冲突即弃稿回退模板
- **混合检索底层**:`app/rag/tokenize.py` 统一三阶层分词器(latin 词 + jieba cut_for_search CJK 词 + CJK 单字),hash 嵌入与 BM25 共享同一 bag-of-words 契约——修复纯中文查询 hash 零向量退化(退换货 vs 退货 相似度 0.0→0.359);`search_knowledge` 加 query_text 参数开启 hybrid 模式,vector 模式向后兼容;工具 v2 加 mode/grade 参数
- **真机爬取语料(crawl_helpcenter.py)**:Shopify×5 + Amazon 定价 + eBay 退货政策帮助中心页,curl 子进程抓取(httpx 被 Shopify/Amazon TLS 指纹拦 403;eBay JS 壳被 MIN_PAGE_CHARS=200 守卫跳过;Amazon 404 错误页与超短块入库后清洗剔除);结构感知切块 `app/rag/ingest.py`:HTML 标题栈 → 章节(短节前向合并)→ 800 字贪心打包/120 重叠/句界硬切;入库 23 块 WEB-* 语料(t_demo_acme,meta 带 source_url/heading_path 可溯源),幂等按 source 清理重灌。**引擎一致性实测**:种子与爬取存储向量均与 hash 重算余弦≈0(维度 1024)= 全部 api 引擎,无跨引擎漂移
- **RAG 评估体系(evals/rag_golden.py + rag_evals.py)**:31 条黄金查询(种子五类 + ops_playbook + WEB 英文 query)+ 7 条忠实度护栏样本(夸大/投毒/注入必须拦截、客观事实回答零命中,复用 M7 detector 注册表同一组正则);指标 Recall@3/@5 / MRR@5 / HitRate@5 整体+分语料报表,未命中逐条归因;hermetic(临时 SQLite + 强制 hash 引擎,评测永不连真实 PG)。基线:Recall@3 90.3 / Recall@5 96.8 / MRR@5 86.0 / HitRate@5 100,七类全 100 命中;门禁线=基线−10~15pt(小样本类别线放单条翻车噪声之下),`--gate` 任一失守 exit 1;CI 增 RAG gate 步骤(红队门禁之后)
- **研究工具矛盾 bug 修复(用户报障:选"水枪"返回的全是床下储物箱数据)**:research 三工具改为 sha256(关键词+盐) 派生确定性数据(趋势/竞品/评论全部内嵌选题词,同题稳定、异题发散),nodes.py stub 兜底的 demand_signal 同步 hashlib 派生;真机冒烟 run `bad66dfa`:儿童水枪玩具全链 completed,R1 证据 0.45 触发深化→R2 0.82 过闸,审计库 4 条工具调用输出全部为水枪品类数据,planner 命中 [OPS-SEL-01, OPS-TTS-CM1](主链路 ops_playbook hybrid 真机验证),support agentic 循环真机全开(route 双通/rewrite llm/grade llm∩det/refs=[POL-EXC-05,POL-RFD-02,POL-RTN-07 v2.1,FAQ-02])
- **验收**:pytest 116 绿(+38:M9 检索单测 12/hybrid 集成 8/派生数据 6/切块 5/agentic 循环 7 等);ruff 含 evals 干净;RAG 门禁 PASS;真库冒烟通过后已重暖 demo 缓存

## 后续里程碑速查

v1.4 全里程碑 M0-M8 ✅ 收官。可选后续：语义缓存 Phase 2（同 ResultCache 接口换 embedding 相似度 + semantic_cache_entries 迁移）、真实出图接缝、更多红队 seed。

## 验证命令

```bash
bash backend/scripts/dev_postgres.sh          # 起 PG(15433)
cd backend && .venv/Scripts/python -m ruff check src tests scripts evals
.venv/Scripts/python -m pytest                # 77 个测试(RUN_LLM_SMOKE=1 追加真网冒烟)
.venv/Scripts/python evals/run_evals.py       # 红队门禁:3 条 seed 全 PASS 才放行
.venv/Scripts/python evals/rag_evals.py --gate    # RAG 质量门禁:31 条黄金查询 + 忠实度护栏
.venv/Scripts/python scripts/warm_demo_cache.py   # 预热 Demo 兜底缓存(需真 key,一次性)
bash scripts/reset_and_replay.sh              # 一键重置+种子+重放全链路(仓库根,cwd 任意)
docker compose up --build                     # 一键起全栈(前端 :8088)
.venv/Scripts/python scripts/reset_demo.py && .venv/Scripts/python scripts/seed_mock_data.py
.venv/Scripts/python -m uvicorn app.api.main:app --port 8000   # cwd=backend
```
