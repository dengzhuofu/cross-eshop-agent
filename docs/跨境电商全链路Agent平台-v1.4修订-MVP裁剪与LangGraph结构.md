# v1.4 修订案：MVP 裁剪（含拓展点）+ LangGraph 结构调整

日期：2026-08-24
性质：对 PRD v1.3 的范围修订与工程结构修正，**不推翻 v1.3 的任何架构决策**，只做"砍实现、留接缝"。
适用：单人开发、以求职作品集为目标、需要在有限时间内拿到可深度讲解的可运行 Demo。

---

## 0. 三条修订原则

1. **砍实现不砍接口**：每一项裁剪都必须留下类型化接缝（协议/枚举/字段/注册表），Phase 2 按原 PRD 设计直接插入，不需要返工。
2. **垂直切片优先**：先用 stub 打通 §24 的 13 步 Demo 骨架，再逐里程碑把 stub 换成真器官。任何时刻仓库里都有一个能跑的东西。
3. **确定性优先不变**：数学、评分公式、状态机、隔离校验全部确定性实现，LLM 只做生成与解释。

---

## 1. MVP 裁剪清单（每项：砍什么 / 留什么接缝 / 何时恢复）

### 1.1 生图整块移出 MVP（PRD §7.5 图片部分）

| | 内容 |
| --- | --- |
| **砍** | `generate_listing_images` 工具实现；Vision `verify_image_compliance`；图文一致性校验；图片集合三类验收项；生图成本口径 |
| **留接缝** | ① `ImageProvider` / `VisionProvider` 协议 + 占位实现（返回预置素材或 NotAvailable）；② adapter 的 `get_image_spec` 保留；③ ListingDraft 模型保留 `images` JSON 字段与 `generated` 标记约定；④ Critic rubric 预留 image_compliance 维度开关（默认 off）；⑤ **`generate_image_brief` 文字 brief 保留在 MVP**（零成本体现工具边界思维） |
| **恢复** | Phase 2 接入文生图 API + Vision 校验，预计 1–2 周 |

### 1.2 语义缓存不实现，与 Demo 兜底合并（PRD §10.6 + §21.2）

| | 内容 |
| --- | --- |
| **砍** | embedding 相似度检索缓存、TTL/命中频次衰减、命中率看板 |
| **留接缝** | 定义统一 `ResultCache` 接口：MVP 用精确-hash 实现（服务 Demo 兜底预生成），Phase 2 在同接口后换 embedding 相似度实现；`semantic_cache_entries` 表放到独立的后置 migration |
| **理由** | Demo 场景每个流程只跑一两次，语义缓存命中率趋近于零；§21.2 与 §10.6 本质是同一问题的两个机制，合并为一个接口下的两种实现 |

### 1.3 Procedural 记忆与巩固批处理降级（PRD §9）

| | 内容 |
| --- | --- |
| **砍** | procedural 类型的实际读写路径；低峰离线巩固批处理系统；相关性衰减遗忘算法 |
| **留接缝** | ① `memory_type` 枚举保留三值（episodic/semantic/procedural）；② `consolidate_memory` 工具保留接口，MVP 实现为"工作流结束触发的增量去重脚本"；③ archive 软删除保留 |
| **MVP 主线** | 只保两条记忆线打穿：**供应商风险**、**类目表现**（episodic 写入 → 跨工作流检索命中并引用 source_workflow_id）。这是 §23 验收的核心证据 |

### 1.4 多租户十层隔离 → 六层全量实现 + 三层降级（PRD §13.4）

| 层 | MVP 处理 |
| --- | --- |
| #1 数据 / #2 记忆 / #3 向量 / #4 上下文 / #5 RAG / #6 工具 / #7 Trace | **全量实现**。这些层是"跨租户访问被阻断并告警"这一验收证据的直接来源 |
| #8 密钥 | 降级：env 单租户占位，但 key 规范按 `tenant/{tenant_id}/marketplace/{marketplace}` 设计，`TenantSecretRef` 表放后置 migration |
| #9 队列隔离 | 降级：共享队列 + 每租户并发数 DB count 检查；worker 无跨租户可变状态的编码纪律照旧 |
| #10 配额 | 降级：`TenantQuota` 接口保留，DB count 实现；Redis 原子计数 Phase 2 替换 |

留接缝：`isolation_mode` 字段（shared_db，预留 schema_per_tenant）；quota/secret 的接口签名与 Phase 2 完全一致。

### 1.5 Bad Case 八类检测 → 三条红队 seed 打穿全链路（PRD §20）

| | 内容 |
| --- | --- |
| **砍** | 八类的专用探测设施：A 类注入分类器简化为规则匹配、F 类记忆访问监控、G 类 token 驱逐联动等 |
| **MVP 三条 seed** | ① 评论夹带 prompt injection（input guardrail 拦截）；② Listing 夸大声明（Critic 拦截）；③ 跨租户 supplier_id 引用（防越权拦截） |
| **不做砍的部分** | B 类 output schema 校验、H 类确定性规则校验器本来就要做，不算额外成本；**检测→隔离→沉定→CI 回归门禁主线完整保留**（这是核心卖点） |
| **留接缝** | detector 注册表模式：`BadCaseCategory` 八类枚举保留，每个 detector 独立实现、独立注册，新增一类 = 新增一个注册项，不动主干 |

### 1.6 前端九页 → 五页半（PRD §17.1）

- 全力做：**Workflow Detail（决策时间线）**、**Approval Center**、**Observability（记忆面板 + 压缩日志 + Bad Case 面板）**、**Dashboard**。
- 简化做：Listing Workspace（草稿 + critique diff 即可）。
- 合并为最小表格页：Operations Monitor + Support Desk。
- 砍掉：Tenant Admin（配额用量并入 Dashboard 一张卡片）。
- 留接缝：Next.js 路由按九页规划，未实现的页面留占位路由，Phase 2 直接填内容。

### 1.7 Token 预算驱逐 → 计量 + 告警（PRD §10.4/§10.5）

- **砍**：预算驱动的多级驱逐优先级策略（先压摘要→再裁记忆→最后裁工具结果）。
- **MVP 做**：summarization 节点 + `compress_tool_output` + token 计量进 trace + 超预算告警。
- **留接缝**：`token_budget` 字段进入节点元数据；eviction 策略 Phase 2 作为 ContextAssembler 的一个策略对象插入。

---

## 2. LangGraph 项目结构：以官方标准为基准的对齐

> 本章基于 2026-08 官方资料核对：
> - 官方文档《Application structure》（docs.langchain.com/oss/python/langgraph/application-structure）
> - 官方脚手架仓库 langchain-ai/new-langgraph-project（其 langgraph.json 与 pyproject.toml 为权威样例）

### 2.0 官方标准是什么（原文归纳）

一个 LangGraph 应用 = 一或多个 graph + 一个配置文件 `langgraph.json` + 一个依赖声明文件 + 可选 `.env`。

**官方推荐的 Python 目录形态（文档原文）：**

```text
my-app/
├── my_agent/              # all project code lies within here（所有项目代码都在这一个包里）
│   ├── utils/             # utilities for your graph
│   │   ├── __init__.py
│   │   ├── tools.py       # tools for your graph
│   │   ├── nodes.py       # node functions for your graph
│   │   └── state.py       # state definition of your graph
│   ├── __init__.py
│   └── agent.py           # code for constructing your graph（构建图的地方）
├── .env                   # environment variables
├── requirements.txt       # 或 pyproject.toml
└── langgraph.json         # configuration file for LangGraph
```

**官方脚手架模板（new-langgraph-project）的实际形态（src 布局变体）：**

```text
├── .env.example
├── langgraph.json
├── pyproject.toml         # setuptools src 布局映射，使包可安装
├── src/
│   └── agent/
│       ├── __init__.py
│       └── graph.py       # 定义并导出编译图变量 graph
├── tests/
│   ├── conftest.py
│   ├── unit_tests/
│   └── integration_tests/
└── static/
```

**官方 langgraph.json 形制（模板原文）：**

```json
{
  "$schema": "https://langgra.ph/schema.json",
  "dependencies": ["."],
  "graphs": {
    "agent": "./src/agent/graph.py:graph"
  },
  "env": ".env",
  "image_distro": "wolfi"
}
```

要点：

1. **所有项目代码在一个可安装的本地包里**，`dependencies: ["."]` 让 CLI/部署端把该包当普通 pip 依赖装进运行镜像（src 布局靠 pyproject 的 setuptools `package-dir` 映射实现）。
2. **每个 graph 是一个模块，导出一个编译图变量**，`graphs` 键把名字映射到 `./路径/文件.py:变量名`。
3. **tools/nodes/state 是图包内的常规组织单元**（文档用 `utils/` 子包演示）。
4. **测试在根级**，模板命名为 `unit_tests` / `integration_tests`。
5. 官方默认由 **LangGraph Server**（本地 `langgraph dev` / LangSmith Deployment）托管运行这些图。

### 2.1 对照官方标准，原 §18.2 的真实偏差

1. **langgraph.json 形制缺失**：无 `$schema`、未写明 `dependencies` 键、图路径没有落到"文件:导出变量"约定。它是应用的真正入口，不是装饰品。
2. **包不可安装**：官方要求代码位于一个可 pip 安装的本地包（src 布局 + setuptools 映射），否则 `dependencies: ["."]` 不成立，`langgraph dev` 和未来的 LangSmith Deployment 都无法加载。原树只是目录堆叠，没有任何可安装性声明。
3. **图的组织偏离官方惯例**：官方是"一图一模块（agent.py 导出编译图变量）+ 包内 tools/nodes/state"。原版的 `graph.py/state.py/edges.py/nodes/` 接近但没有确立"导出变量"约定，也没有 tools 的归属位置。
4. **运行时边界从未交代**：官方默认 LangGraph Server 托管；本项目主运行时是自建 FastAPI + worker 在进程内调用图。两者必须共用同一个包导出的同一份图定义，这条关系原版完全没写。
5. **测试布局**：原版 `tests/{unit,contract,integration,e2e}` 位置正确，命名改为官方式 `*_tests` 并补 conftest.py 约定。
6. （承袭 v1.4 已有判定）`models/` 命名冲突 → `providers/`；`persistence/store.py`（BaseStore 第二套记忆抽象）→ 移除；三角原语需要跨图共享落点 → `orchestration/`；approvals/badcases 需要显式模块。

### 2.2 对齐后的结构（`[改名]` `[新增]` `[移除]` `[降级]` 标注变更点）

```text
repo-root/
├── backend/
│   ├── .env.example                  # SILICONFLOW_API_KEY 等；真实 .env 不入库（langgraph.json env 指向它）
│   ├── Dockerfile
│   ├── docker-compose.yml            # postgres+pgvector / redis(可选) / api / worker / frontend
│   ├── langgraph.json                # 官方形制（见下方示例）；MVP 只注册 product_launch
│   ├── pyproject.toml                # 唯一依赖真源；setuptools src 布局映射使 app 包可安装（官方模板同款）
│   ├── alembic.ini
│   ├── migrations/                   # v1.4 子集建表；预留表（semantic_cache_entries 等）走后置 migration
│   ├── scripts/
│   │   ├── seed_mock_data.py         # 版本化演示数据 + 红队 seed（§21.3）
│   │   ├── reset_demo.py             # 一键 reset
│   │   ├── gen_demo_cache.py         # 关键路径预生成输出（ResultCache 的精确-hash 实现）
│   │   ├── consolidate_memory.py     # 记忆增量巩固（工作流结束触发调用）
│   │   ├── isolation_scan.py         # CI：扫描无 tenant_id 过滤的查询（§21.1）
│   │   └── export_badcase_dataset.py
│   ├── evals/
│   │   ├── datasets/                 # 黄金场景 5–10 个 + BadCaseDataset 回归导出物
│   │   ├── evaluators/
│   │   └── run_evals.py              # CI 门禁：分数阈值 + 旧 bad case 复现即失败
│   ├── tests/                        # 官方模板同款根级布局，命名 *_tests
│   │   ├── conftest.py
│   │   ├── unit_tests/
│   │   ├── contract_tests/           # [我们扩展] 工具 schema 与 adapter 协议契约
│   │   ├── integration_tests/
│   │   ├── isolation_tests/          # [我们扩展] 跨租户越权专项，CI 必跑
│   │   └── e2e_tests/                # Playwright
│   └── src/
│       └── app/                      # 唯一可安装本地包（官方："all project code lies within here"）
│           ├── __init__.py
│           │
│           │   ── LangGraph 应用部分（官方结构管辖范围）──
│           ├── graphs/
│           │   ├── product_launch/
│           │   │   ├── __init__.py
│           │   │   ├── agent.py      # 构建 StateGraph 并导出变量 graph（langgraph.json 指向 :graph）
│           │   │   ├── state.py      # 图状态 schema + scratchpad reducer（对应官方 utils/state.py）
│           │   │   ├── nodes.py      # research/profit/supplier/listing 领域执行器节点（utils/nodes.py）
│           │   │   ├── edges.py      # 纯路由函数 + 循环次数约束 [我们扩展]
│           │   │   └── tools.py      # 本图工具装配，引用顶层 tools/catalog（utils/tools.py）
│           │   ├── operations/…      # 同构：agent/state/nodes/edges/tools
│           │   ├── support/…
│           │   └── retrospective/…
│           ├── orchestration/        # [新增] 跨图共享三角原语，被各图 agent.py 组装复用
│           │   ├── planner.py        # 规划节点工厂 + 决策窗口（最近 N=6 个 AgentDecision 全量）
│           │   ├── critic.py         # 评审节点工厂 + 结构化 critique 输出
│           │   ├── decision_gate.py  # go/no-go 闸门
│           │   ├── critique_loop.py  # 循环计数/约束下发/升级判定（≤3 轮硬上限）
│           │   ├── timeouts.py       # 节点双层超时 + 兜底返回（PRD §8.6）
│           │   └── scratchpad.py     # 共享工作内存 schema 与 reducer
│           │
│           │   ── 平台壳层（FastAPI/多租户/治理；LangGraph 标准之外的自有部分）──
│           ├── api/                  # FastAPI：app.py / dependencies.py（令牌→TenantContext）/ routes/
│           ├── multitenancy/         # context(injection 中间件)/guards(IDOR·跨租户引用)/quotas[降级]
│           ├── providers/            # [改名：原 models/] router(small/main 分档)/llm/embeddings/reranker
│           │   ├── vision.py         # [Phase 2] 协议 + NotAvailable stub
│           │   └── imagegen.py       # [Phase 2] 协议 + 占位实现
│           ├── tools/                # registry（风险/幂等/超时/审批标记）/ executor（租户注入·权限·审批门·
│           │   ├── catalog/          #   重试·审计·压缩·BadCase 统一入口）/ schemas（工具 IO）
│           ├── approvals/            # [新增] ApprovalRequest + LangGraph interrupt/resume 编排对接
│           ├── guardrails/           # input/output/pii/injection 检测（detector 注册表）
│           ├── badcases/             # [新增] detector 注册表 + quarantine + dataset 导出
│           ├── memory/               # writer/retriever(pgvector+tenant 强制过滤)/access_log/lifecycle[降级]
│           ├── context/              # assembler/summarizer/compressor/tokens(计量+告警)[驱逐 Phase 2]
│           ├── rag/                  # retriever(metadata 强制 tenant 过滤→向量→rerank)/collections 声明
│           ├── adapters/             # MarketplaceAdapter 协议 + MockAmazon/Shopify/TikTokShop
│           ├── observability/        # trace/decision/memory-access/compression/badcase recorders + 查询
│           ├── services/             # 确定性利润计算/供应商评分公式/ResultCache 实现
│           ├── domain/               # 内部业务实体/枚举/值对象
│           ├── schemas/              # API wire DTO
│           ├── prompts/              # 版本化；few-shot 回流落点
│           ├── mock_data/
│           ├── persistence/          # repositories（workflow 状态唯一真源）+ checkpointer 封装
│           │                         # [移除 store.py：不用 LangGraph BaseStore]
│           └── runtime/              # container.py（构建期 DI）/ context.py
├── frontend/                         # [建议提为仓库根同级] Next.js
└── README.md
```

**backend/langgraph.json（MVP 版）：**

```json
{
  "$schema": "https://langgra.ph/schema.json",
  "dependencies": ["."],
  "graphs": {
    "product_launch": "./src/app/graphs/product_launch/agent.py:graph"
  },
  "env": ".env"
}
```

其余 operations/support/retrospective 三张图完成后逐个追加到 `graphs` 键。

**pyproject.toml 关键片段（使 `dependencies: ["."]` 成立，官方模板同款手法）：**

```toml
[build-system]
requires = ["setuptools>=83.0.0", "wheel"]
build-backend = "setuptools.build_meta"

[tool.setuptools]
packages = ["app"]

[tool.setuptools.package-dir]
"app" = "src/app"

[dependency-groups]
dev = ["pytest", "ruff", "mypy", "langgraph-cli[inmem]"]   # langgraph dev 本地调试
```

### 2.3 四条必须写进 README 和代码注释的结构性规则

1. **三分规则**：`domain/`=内部业务对象，`schemas/`=API wire DTO，`tools/schemas.py`=工具 IO。三者不得互相混放（wire DTO 可从 domain 投影，反向禁止）。
2. **双真源规则**：workflow 状态的唯一真源是 `persistence/repositories`（Postgres）；checkpointer 只负责图的中断恢复；两者以存于 graph state 的 `workflow_id` 关联。任何"当前状态"查询一律走 repositories，不读 checkpoint。
3. **不引入 LangGraph BaseStore**：跨 thread 长期记忆只走自建 `memory_records`（pgvector），避免第二套记忆抽象。
4. **单一定义、两种运行时**：四张图只在 `graphs/*/agent.py` 定义一次并导出编译图变量。`langgraph dev`（本地调试）与 FastAPI+worker（Demo 主运行时）都 import 这同一份定义；禁止在任何地方出现第二份图装配。这样保留了未来整体迁去 LangSmith Deployment 的选项（届时 API 层改调 LangGraph Server SDK 即可）。

---

## 3. 里程碑重排（walking skeleton 优先）

| 里程碑 | 内容 | 出口标准 |
| --- | --- | --- |
| **M0 骨架（新增，最先做）** | stub 化 Agent 打通 §24 十三步 + 状态机持久化 + trace 最小闭环 + Workflow Detail 最小版 | 第一周结束就有一个从头跑到尾的 Demo |
| M1 基座 | tenant 注入 + 六层隔离 + 工具注册中心/executor + 3 个 mock adapter + migrations（v1.4 子集） | 跨租户读取被阻断可演示 |
| M2 三角真身（上） | orchestration/ 原语 + research/profit 真实现 + go/no-go 闸门 + 研究深化（≤2 轮） | 决策时间线出现真实 AgentDecision |
| M3 三角真身（下） | supplier + listing + CritiqueLoop（≤3 轮）+ localize + generate_image_brief | critique diff 可证明应用了约束 |
| M4 记忆双线 + 上下文压缩 | 供应商风险/类目表现写入与跨工作流命中 + summarization/compress_tool_output + token 计量 | 第一次标记→第二次自动命中并引用来源 |
| M5 审批与执行 | approvals interrupt/resume + mock 发布幂等 + 审计 | 高风险动作无法绕过审批；中断恢复可用 |
| M6 运营/客服/RAG | 模拟运营数据 + Support Agent + RAG 五类集合 + 退款审批 | 回复带来源引用；RAG 与工具冲突时以工具为准 |
| M7 Bad Case + eval 门禁 | 三条 seed 打穿 + detector 注册表 + dataset 导出 + CI 门禁 + 隔离扫描 + 可观测面板完善 | 红队 seed 进回归集且 CI 生效 |
| M8 打磨 | Demo 兜底缓存 + docker-compose + README + 截图 + 面试讲解要点 | 一键 reset & replay |

顺序说明：记忆放在 M4（M0–M3 期间相关步骤由 stub 承担）；若时间紧张，把"供应商风险记忆"单独提前到 M3 并入 supplier 节点即可，其余不动。
补充：M0 起就按 §2.2 结构落包（可安装包 + langgraph.json 注册 product_launch），后续里程碑只往既有骨架里填实现，不再挪目录。

---

## 4. 对 PRD §23 验收标准的增删

**删**（随 §1.1 生图裁剪）：三类图片集合、`generated` 标记、图文一致性校验相关条目。
**换**：`generate_image_brief` 输出结构化 brief 且字段符合 `get_image_spec`。
**增**（接缝验收）：
- `ImageProvider` / `VisionProvider` 协议存在且可在 runtime/container 中注入 mock；
- `ResultCache` 接口存在，Demo 兜底输出走该接口；
- `tests/isolation_tests/` 在 CI 独立执行并通过；
- `langgraph dev` 能加载并运行 product_launch 图（验证包可安装、图定义符合官方形制）。

## 5. 对两份配套设计文档的标注建议

- 《技术设计》§2.7 语义缓存 → 标注"MVP 仅设计，实现在 Phase 2（ResultCache 的 embedding 实现）"；§3.5 peer 反馈 → 降为可选（时间富余再做）。
- 《多租户隔离与 BadCase 处理设计》§3.8 密钥 / §3.9 队列 / §3.10 成本 → 标注"MVP 按 v1.4 §1.4 降级实现，接口不变"。

## 6. PRD §26 待确认问题在本版的落定

pgvector ✓；Docker Compose ✓；shared_db + 强制过滤 ✓；巩固 = 工作流结束触发增量脚本（离线批处理 Phase 2）✓；遗忘 = TTL+衰减设计保留、MVP 只实现 archive 软删除 ✓；CritiqueLoop 升级即人工 ✓；Bad Case 数据集默认租户私有 ✓。
