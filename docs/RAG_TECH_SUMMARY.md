# 客服 Agent RAG 技术方案总结（数据清洗 → 切块 → 检索 → 评估 → 数据飞轮）

> 分析对象：本仓库客服 Agent（node_support）的 Agentic RAG 子系统 + 主链路自用 RAG（planner/listing）。
> 代码事实核对至 commit `7cac373`；相关模块：`scripts/crawl_helpcenter.py`、`src/app/rag/*`、
> `src/app/tools/catalog/knowledge.py`、`graphs/product_launch/nodes.py`（node_support）、
> `evals/rag_golden.py + rag_evals.py`、`src/app/feedback/triage.py`。设计细节另见 `docs/RAG_DESIGN.md`。

## 0. 全链路一图流

```
语料层   结构化种子(49条) ─┐
         真机爬取帮助中心 ──┤→ 清洗(反爬自愈/HTML去噪) → 结构感知切块(800/120重叠)
         用户反馈候选(M10) ─┘         │
                                      ▼
索引层   bge-m3 1024维向量 + BM25共享分词 + meta(source_url/heading_path/status)
         ※ 候选知识 status=candidate 不进检索池，人工审批后才可见
                                      ▼
检索层   策略规划(M11:direct原句/rewrite改写/hyde假设文档;规则打底LLM提议越界弃用)
         → hybrid双路召回 top5(hyde 假设文档只进语义路,BM25 词面保持用户原词)
         BM25(k1=1.5,b=0.75) + 余弦(max(主查询,假设文档)) → RRF(k=60) 融合粗排
                                      ▼
精排层   确定性分级(0.7×词面覆盖+0.3×余弦,阈值0.15) ∩ LLM pointwise判级(只收窄)
         零相关 → ESCALATION 阶梯 direct→rewrite→hyde 换策略重试 ≤2轮
                                      ▼
生成层   rag_block(仅relevant命中) + 融合铁律(与工具实时ETA冲突→整稿弃用)
                                      ▼
飞轮层   用户反馈 → 分诊子agent归类归因 → 候选知识/黄金查询/坏例 三路沉淀 → 人工把关回流
```

## 1. 数据清洗

**三层清洗管道（爬取层 → 解析层 → 治理层）：**

- **爬取层（反爬自愈）**：subprocess 系统 curl 抓取（httpx 的 TLS 指纹被 Shopify/Amazon 反爬识别返回 403，curl 可过）；HTTP 状态码校验非 200 跳过；`MIN_PAGE_CHARS=200` 正文下限守卫自动识别 JS 壳页/拦截页（eBay 壳页被此守卫剔除）；单页失败不炸整批；1s 礼貌间隔；按 `source` 先删后灌幂等重跑。
- **解析层（HTML 去噪）**：stdlib `HTMLParser` 流式解析，整棵剔除 `script/style/nav/header/footer/aside/noscript/svg` 子树；只收集块级文本（`p/li/blockquote/td/dd`）；charref 解码、逐段 strip 归一空白；h1-h4 标题栈维护分节归属；主标题优先取第一个 h1、回退 `<title>`。
- **治理层（质量事件闭环）**：Amazon 404 错误页壳（"We're Sorry."）曾混过守卫入库 → 事后从 PG 清洗剔除并从语料 fixture 排除；M10 起反馈沉淀的候选知识带 `status=candidate`，`search_knowledge` 过滤掉候选行——未审批语料永远不进检索池。

**评价**：对当前规模（约 72 条语料：49 条结构化种子 + 23 块爬取）这套清洗足够且全部确定性可测。已知边界：未做块级近似去重（同一政策多页转载会重复）、HTML→文本丢失列表/表格结构（表格类政策页信息有损）。

## 2. Chunk 切割

**当前方案：结构感知两级切块（`app/rag/ingest.py`），不是固定长度、不是递归分隔符、不是 LLM 语义切割。**

1. **第一级按文档结构切节**：HTML 标题栈（h1→h4）分节，节是语义完整单位；`<40` 字的碎节并入上一节、同标题节合并，避免碎片块稀释向量。
2. **第二级节内贪心装块**：段落贪心打包到 800 字上限；超长单段先按句边界（`。！？!?` + 换行）硬切、切不动再按字符兜底。
3. **重叠 overlap=120 字**：仅在节内打包溢出时，相邻块携带尾部 120 字重叠（约 1-2 句），跨块引用（"详见下文"类）不断裂。
4. **上下文前缀**：每块 content 以 `heading_path`（如 `Refunds > How refunds work`）开头，块自带主题上下文，独立嵌入/独立参与 BM25 也不失焦。

**三种方案对比与选型理由：**

| 方案 | 优点 | 缺点 | 适用 |
| --- | --- | --- | --- |
| 固定长度滑窗 | 最简单、块均匀 | 割裂条款/语义，政策文档重灾区 | 均质长文本 |
| 递归分隔符（langchain RecursiveCharacterTextSplitter） | 通用、段落优先 | 不懂 HTML 标题层级，节边界仍可能切断 | 无结构标记的纯文本 |
| LLM 语义切割 | 主题边界最准 | 贵、慢、结果不确定、不可回归测试 | 一次离线建库的超长文档 |
| **结构感知（本系统）** | 语义完整 + 确定性零成本 + 可单测进 CI | 依赖文档有标题结构 | 帮助中心/政策/FAQ 类文档 |

**overlap 的取舍**：overlap 是对"切割边界切断语义"的保险，按结构分节后节内语义已完整，所以只在长段打包溢出时给 120 字兜底，而不是全量滑窗重叠（那会让语料膨胀、BM25 统计重复计权）。若换成纯固定长度方案，则需要 15-20% 比例的重叠才够。

**结论：当前方案对本系统就是最优档**——语料以短政策/FAQ/帮助文档为主、中英混合、要求 CI 确定性可回归，结构感知切块同时满足这四个约束。LLM 语义切割只有在引入无结构的超长文档（>5000 字连续正文）时才值得上。

## 3. 父子 chunk 索引（small-to-big）：当前没必要，接缝已留

- **现状**：未实现独立父子索引；但 `meta` 已存 `source_url + heading_path + chunk_index`，"检索小块、返回父节/父文档"随时可以按 meta 分组重组实现，**不需要新建索引结构**。
- **判断**：当前语料块上限 800 字且自带标题前缀，块本身已是"够用的上下文"；帮助中心类文档一节很少超过 800 字。父子索引的收益场景是"小块精准命中、但回答需要大上下文"——在短政策文档语料上收益趋近于零，反而引入父子一致性维护成本（父节更新要级联子块）。
- **触发条件**：引入 >5000 字长文档（如完整卖家协议）时再启用：子块 200-300 字精准检索，命中后按 `source_url+heading_path` 拼回父节进提示词。

## 4. 元数据与向量化

**meta 字段及用途**（全部随块入库、可观测可溯源）：

| 字段 | 用途 |
| --- | --- |
| `source`（seed/webcrawl/feedback） | 语料来源；幂等清理按它先删后灌 |
| `source_url` / `heading_path` / `chunk_index` / `lang` | 爬取块溯源：原文链接、标题栈路径、块序、语言 |
| `origin:feedback` + `status:candidate/approved` + `feedback_id` | M10 治理：候选知识审批门（candidate 行被检索层过滤） |
| `seed`（正式语料标记） | 正式语料不可被审批接口误动 |
| 顶层列 `category` / `ref` | 六类知识分类（policy/platform_rule/product_info/faq/script/ops_playbook）+ 可引用编号（引用白名单校验用） |

**向量化**：

- 模型 **BAAI/bge-m3（1024 维）**，SiliconFlow Embedding API；入库文本 = `title + content`（种子/反馈候选），爬取块用含标题前缀的 `content`；查询侧只 embed 查询文本。
- **确定性 hash 降级引擎同接口**：无 API key 时自动切换，测试/CI 零出网、行为可复现；接口统一意味着业务代码对引擎无感。
- **引擎一致性是硬约束**：入库与检索必须同引擎（实测跨引擎余弦≈0）；评测（hash）与线上（bge-m3）各自内部一致，评测永不连真实库。
- **存储**：PostgreSQL JSON 列 + Python 侧余弦（未用 pgvector）；pgvector 升级接缝已在 `docs/RAG_DESIGN.md` §10 预留（当前语料量全表扫描 + 内存计算 <5ms，无必要）。

## 5. 检索链路：混合检索 → RRF 粗排 → 双重判级精排

**工具层**：`search_knowledge` 治理工具（13 个工具之一，走 ToolExecutor 七步管线、审计留痕）。参数：`top_k` 默认 3（1-8）、`mode` 默认 hybrid、`grade` 可选确定性分级。

**混合检索（粗排）**：

- **BM25 路**（Okapi，k1=1.5 / b=0.75，df/avgdl 按本语料统计）× **余弦语义路** → **RRF（k=60）** 融合排序，返回项带 `bm25 / rrf / similarity` 三元可解释分数。
- 分词器是两路共享的底座：三阶层（latin 词 + jieba cut_for_search 词 + CJK 单字）——修复了纯中文查询 hash 零向量退化（"退换货" vs "退货" 相似度 0.0→0.359）。
- 离线/降级场景余弦全零时，BM25 独立支撑排序——这正是双路设计的动机（任一路失效不塌方）。

**top_k 策略（分场景）**：

| 场景 | top_k | 理由 |
| --- | --- | --- |
| 客服 agentic 循环 | **5** | 先召回保 Recall，靠判级过滤收 Precision |
| planner/listing 主链路（ops_playbook） | **2** | 注入生成提示词，控 token 成本 |
| 评估口径 | 3 / 5 | Recall@3、Recall@5、MRR@5、HitRate@5 |

**精排：过滤器而非重排器**：

- **确定性分级**打底：`0.7 × 查询词面覆盖率 + 0.3 × max(0, 余弦)`，阈值 0.15——纯词面可解释（命中哪些查询词一目了然）。
- **LLM pointwise 判级**与其取**交集**：LLM 只能收窄不能放宽；LLM 不可用/输出违约时整体回退确定性分级（分级永不阻塞）。
- **零相关重试 ≤2 轮**：零相关命中沿 **ESCALATION 升级阶梯换策略**再检索（direct→rewrite→hyde；hyde 重试换角度重新生成假设文档防复读），全程 `retrieval_trace`（每轮 round/query/strategy/hyde/hits/relevant_count + rewrite_source/grade_source/strategy_source）留痕。
- **融合铁律**（M6）：检索知识永远覆盖不了工具实时事实——草稿时效表述与 OMS 工具 ETA 冲突即整稿弃用回退模板。

**M11 策略自适应：查询侧增强方案按问题自主决策**：

- **为什么需要**：一律先改写对含订单号/型号的短查询是伤害（精确词面被稀释），对长而模糊的口语问题又泛化不动——「选哪种增强」本身应是 agent 决策点。
- **三策略**：`direct` 原句直检 / `rewrite` 查询改写 / `hyde` 假设性回答（HyDE）。确定性规则打底（零网络可单测）：≤14 字且含 `[A-Za-z0-9]{4,}` 精确信号 → direct；≥40 字问题式无信号 → hyde；默认 rewrite。LLM 可用时单次调用同时提议策略+改写短语，**枚举外提议整体弃用回退规则结果**（与全仓库「LLM 只提议、代码做硬保证」同构）。
- **HyDE 的作用域硬约束**：假设文档由独立生成器产出（客观政策口吻、禁止编造具体数字），经 `search_knowledge` 新参 `hyde_text` **只进余弦语义路**——repo 层对每篇文档取 `max(cos 主查询, cos 假设文档)`；BM25 词面永远用用户原词，LLM 生成的文本不污染词面匹配。这是「不可信产物限定作用域」（同 scrub_untrusted 思想）在检索侧的投影。
- **降级闭环**：无 LLM/生成失败时 hyde 自动降级 rewrite（假设文档无从生成但检索不断）；策略规划失败降级确定性规则；任何环节都不阻塞检索主链路。

**Cross-Encoder rerank：未使用（有意取舍）**：

- 当前"精排"是**过滤式**（剔除不相关）而非**重排式**（Cross-Encoder 对 query-doc 对精打分重排）。在 ~72 块语料上，RRF 已在全集上排序，Cross-Encoder 的重排增益趋近于零，却要加一次 API 往返（+200-400ms）和一个外部依赖。
- **升级触发条件**：语料 >1k 块、或 RRF top-N 出现噪声抬头时，在 RRF 与判级之间插一层 rerank（SiliconFlow 提供 `BAAI/bge-reranker-v2-m3` 端点，工具契约零改动即可插入）。这是有接缝的演进项，不是缺失。

**召回准确率手段清单**：

已做：双路混合召回、**策略自适应查询增强（M11：direct/rewrite/hyde 按问题形态自主决策，LLM 提议规则兜底）**、HyDE 假设文档（只进语义路的 max 余弦）、查询改写（LLM + 确定性兜底）与阶梯变体重试 ≤2 轮、三阶层分词器、结构切块 + 标题前缀（提升块主题性）、120 字重叠、top-5 召回优先再判级、category 定向过滤（route 分类后只查对应类别）、反馈飞轮持续补语料。
未做（按收益排序）：Cross-Encoder rerank（语料大了再加）、多路多向量（ColBERT 类，运维重）。

## 6. 评估：自建黄金集门禁（CI）+ 忠实度护栏；ragas 留作离线互补

**自建评估体系（`evals/rag_golden.py + rag_evals.py`）**：

- 31 条黄金查询（覆盖种子五类 + ops_playbook + 英文爬取语料）+ 7 条忠实度护栏样本（夸大/投毒/注入必须拦截、客观事实回答零命中——复用 M7 detector 同一组正则）。
- 指标：**Recall@3 / Recall@5 / MRR@5 / HitRate@5**，整体 + 分语料报表，未命中逐条归因。
- **hermetic 设计**：临时 SQLite + 强制 hash 嵌入引擎，评测零出网、永不连真实 PG，全程 <30s。
- **CI 门禁 `--gate`**：阈值 = 基线 −10~15pt（小样本类别线刻意放单条翻车噪声之下防误杀），任一失守 exit 1。基线：**Recall@3 90.3 / Recall@5 96.8 / MRR@5 0.860 / HitRate@5 100**。
- **`--feedback-report`**（M10）：打印反馈沉淀的候选黄金查询 top5 命中，供人工复核转正。

**deepeval vs ragas——结论：CI 门禁用自建更合适；要补离线深度评估则选 ragas。**

- 自建检索指标评估（Recall/MRR 类）：确定性、零出网、秒级、免费——这是 CI 门禁的硬要求；ragas/deepeval 的核心指标（faithfulness、answer relevancy）依赖 LLM-as-judge，出网、按次计费、结果非确定，放进 CI 会让门禁本身不可靠。
- **ragas 更适合做离线深度评估**（若要加）：其指标体系与 RAG 三元组一一对应（faithfulness 忠实度 / answer relevancy 答案相关性 / context precision & recall 上下文精确率与召回率），是 RAG 评估的事实标准，且能评"生成端"——这正是自建体系不覆盖的半边（自建评检索、护栏评拦截，不评答案质量）。
- deepeval 更偏通用 LLM 评估框架（非 RAG 场景也覆盖），pytest 集成较重；本系统已有 pytest 门禁形态，重复建设价值低。
- 三者关系：**自建门禁（每次提交）→ ragas 深评（每周/发版前，LLM judge 评答案质量）→ 互补不互替**。

## 7. 数据飞轮：反馈 → 分诊 → 沉淀 → 人工把关 → 回流

```
用户反馈(👍/👎+评论)
   │ scrub_untrusted 脱敏(与M7 detector同组正则)
   ▼
分诊子agent: 确定性规则打底(M7护栏detector > 关键词规则) + LLM归因只收窄(越界类别弃用)
   │ 9类taxonomy, 每类绑定唯一sink
   ▼
三路沉淀 ──┬─ kb_gap → 候选知识(status=candidate, 人工approve才进检索池/reject删除)
           ├─ retrieval_miss → 黄金查询候选集(JSONL → rag_evals --feedback-report 复核转正)
           └─ 幻觉/违禁/时效/数据矛盾 → M7 badcase隔离 + 经验记忆回写(kind=feedback)
   ▼
人工把关(硬门槛) → 检索池更全 / 评估集更准 / 隔离区可归因 → 下一次回答更准
```

- **真机闭环案例**（commit 7cac373 验证）：客服步骤收到反馈「知识库里查不到水枪玩具的CE认证要求，缺少这条安全合规文档」→ 分诊 `kb_gap`（source=llm，rule_hits=[查不到,缺少]）→ LLM 起草候选条目 → 面板人工审批入库 → hybrid 检索「水枪玩具需要什么安全认证 CE」该条 **top1（相似度 0.821**，原生种子条目仅 ~0.34）。
- **安全边界**：反馈文本先脱敏再进任何通道；候选知识 `status=candidate` 永不直接进检索池；正式语料（seed 标记）不可被审批接口误动——**闭环越转越准，但永远没有"反馈直改线上"的捷径**。

## 8. 已知边界与演进路线

| 项 | 现状 | 触发条件 | 方案 |
| --- | --- | --- | --- |
| pgvector | PG JSON 列 + 内存余弦（<5ms） | 语料 >1 万块 | 迁移 0006 建 ivfflat/hnsw 索引（接缝已留 RAG_DESIGN §10） |
| Cross-Encoder rerank | 未用（LLM∩确定性判级过滤） | 语料 >1k 块 | SiliconFlow bge-reranker-v2-m3 插 RRF 与判级之间 |
| 父子块 small-to-big | meta 可回溯父节 | 引入 >5000 字长文档 | 按 source_url+heading_path 重组父节，无需新索引 |
| ragas 离线深评 | 未接（自建门禁已覆盖检索侧） | 需要评答案质量（忠实度/相关性） | 每周离线跑 ragas，LLM judge |
| 块级去重 | 未做 | 转载重复造成召回污染 | minhash/simhash 近似去重 |
| 语义缓存 Phase 2 | 精确 hash 缓存已上线 | 高频相似查询 | 同 ResultCache 接口换 embedding 相似度 |
