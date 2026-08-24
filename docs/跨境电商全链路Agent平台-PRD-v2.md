# PRD：跨境电商全链路 Agent 平台

版本：1.3（自主决策 + 多租户隔离 + Bad Case 处理 + 成本优化与效果迭代 + Listing 生图能力）  
状态：MVP 设计重构  
日期：2026-08-23  
主要目标：打造一个体现**自主决策、记忆管理、上下文压缩、多 Agent 协作、多租户隔离、Bad Case 处理**的工程化 Agent 作品集项目

> 本版相对 v0.2 的核心变化：
>
> 1. 编排模型从"模糊的 Supervisor + 固定状态机"重构为显式的 **Planner + Executor + Critic 三角回路**。
> 2. 新增**自主决策能力清单**：顶层 go/no-go 闸门、Listing 自批判 loop、可选研究深化、失败重规划。
> 3. 新增整章**记忆管理**（episodic / semantic / procedural，含生命周期与跨工作流学习）。
> 4. 新增整章**上下文管理**（summarization 节点、滑动窗口、工具输出压缩、token 预算）。
> 5. 新增整章**多 Agent 协作**（生成-评审-重写回路、共享工作记忆、peer 反馈）。
> 6. 新增整章**多租户隔离**（十层隔离矩阵、tenant_id 注入铁律、防越权、资源配额）。
> 7. 新增整章**Bad Case 处理**（八类分类法、纵深检测、处理策略、沉定回归闭环、红队 seed）。
> 8. 保留并强化 v0.2 已有的强项：工具边界、Marketplace Adapter、风险分级与审批、评估体系、Trace 与审计。
> 9. MVP 范围裁剪为"最小惊艳集"；补 CI/CD 与 eval 门禁、Demo 健壮性（响应缓存）、成本预算。
> 10. 新增**成本优化手段**：语义缓存（§10.6）、模型分级调度（§18.3.1）、token 精细化管控（§10.3/§10.5）。
> 11. 新增**效果迭代闭环**：Bad Case → Few-Shot 示例 / 微调数据集（§20.5）；明确 **RAG+Agent 融合优先级铁律**（§7.11、§18.4）。

---

## 1. 项目概述

本项目是一个面向中小跨境卖家的全链路电商 Agent 平台。MVP 阶段采用"多平台模拟适配层"，暂不直接接入真实 Amazon、Shopify、TikTok Shop 卖家账号，但整体架构按未来真实平台接入的要求设计。

平台覆盖从选品机会发现、利润测算、供应商评估、多平台 Listing 生成、本地化、风险审核、人工审批、模拟铺货、运营监控、客服售后到复盘优化的完整链路。

本版的重点不是"做一个跑流程的脚本"，而是构建一个**能学习、能重规划、能在长链路中维持上下文、且每一步都受护栏约束、并能在多租户下安全隔离运行、对 Bad Case 可沉淀可防回归的自主 Agent 系统**。Agent 只能通过受控工具执行动作，高风险操作必须人工审批，每一步决策都有证据链、记忆引用、评估结果和审计记录，并且工作流可以回放和复现。

### 1.1 三层自主模型

- **执行层（已有）**：工具层、权限、审批、确定性计算。Agent 不直接访问数据库或外部系统，只通过 typed tools 执行动作。
- **决策层（本版新增）**：Planner 出方案与 go/no-go 决策、Executor 调工具、Critic 评审并决定放行/重写/升级。真实循环而非单向流水线。
- **记忆与自改进层（本版新增）**：记忆管理（跨工作流学习）+ 上下文压缩（长链路续航）+ Trace/Eval 回流闭环（每次 run 的评估自动沉淀为下一轮的约束与记忆）。

### 1.2 多租户与安全底座

平台按多租户组织：每个商家组织是一个 Tenant，其用户、店铺、工作流、记忆、知识库、凭证与审计严格隔离。tenant_id 永远由系统注入，不接受 LLM/客户端传入（详见 §13、§19）。

---

## 2. 产品定位

### 2.1 产品名称

暂定名称：Cross-Border CommerceOps Agent  
中文名：跨境电商智能运营 Agent 平台

### 2.2 一句话介绍

一个面向跨境卖家的、具备自主决策与记忆能力的多租户 Agent 化运营平台：它能跨工作流学习、在长链路中维持上下文、通过多 Agent 协作完成从选品到复盘的全链路工作，对所有高风险动作锁在人工审批与确定性护栏之内，并对 Bad Case 可隔离、可沉定、可回归。

### 2.3 目标用户

- 同时运营多个跨境平台的中小卖家。
- 缺少专门选品、运营、客服、供应链团队的小型商家。
- 希望将重复性运营流程标准化、自动化的电商团队。
- 想用 AI 辅助业务决策，但必须保留人工审批和风险控制的团队。

### 2.4 求职作品集目标

本版要重点展示以下能力（前 6 项为本版强化重点）：

- **自主决策**：go/no-go 判断、带约束重写、自主深化研究、失败重规划。
- **记忆管理**：跨工作流记忆，含写入/检索/巩固/遗忘生命周期。
- **上下文压缩**：长链路分段摘要、工具输出压缩、滑动窗口。
- **多 Agent 协作**：Planner/Executor/Critic 三角回路、生成-评审-重写闭环。
- **多租户隔离**：十层隔离矩阵、tenant_id 注入铁律、防越权、资源配额。
- **Bad Case 处理**：八类分类法、纵深检测、沉定回归闭环、红队 seed。
- **成本优化**：语义缓存、模型分级调度、token 精细化管控。
- **效果迭代**：全链路埋点、Bad Case 回流 Few-Shot / 微调、RAG+Agent 融合优先级。
- 工具调用、工具注册、权限控制和风险分级。
- 多平台适配层设计，隔离不同平台规则。
- Human-in-the-loop 人工审批机制。
- Agent 可观测性：trace、工具调用、token 成本、失败原因、延迟、决策轨迹。
- Agent 评估体系：Listing 质量、证据完整性、客服安全性、流程成功率、自主决策质量。
- 生产化意识：异步任务、状态机、幂等性、重试、数据隔离、审计日志、版本管理。

---

## 3. 背景与问题

跨境电商卖家通常要同时处理选品、竞品分析、供应商比较、利润测算、商品上架、本地化、库存监控、订单处理、客服售后和复盘优化。这些流程重复性强，但风险也很高。

如果自动化做得不好，可能会出现：

- 发布夸大或违规的商品描述。
- 错误定价导致亏损。
- 选择交付风险高的供应商。
- 在没有人工确认的情况下错误退款。
- 根据不充分证据做出选品或运营判断。
- 不同平台规则混在一起，导致上架失败。
- 不同商家的数据、记忆、凭证互相串用（多租户越权）。
- 同一类错误反复出现却没人沉淀（缺 Bad Case 闭环）。

普通 AI 聊天工具可以生成文案或建议，但通常缺少：

- 自主决策能力、记忆、上下文续航、真实多 Agent 协作。
- 多租户隔离与防越权。
- 对 Bad Case 的检测、隔离、沉定与回归。
- 平台级工具边界、可追踪证据链、高风险动作审批、工具权限与审计、可重复执行的 Agent 工作流、能判断输出质量的评估体系。

本项目的核心思路是：**把一个具备自主决策与记忆能力的 Agent，放进一个受控、多租户隔离、对 Bad Case 可沉淀的电商运营系统里——让它会想办法，但不让它乱来。**

---

## 4. 目标与非目标

### 4.1 MVP 目标

- 打通一条完整的跨境电商 Agent 工作流，且**在工作流中体现自主决策**（go/no-go、自批判、研究深化、重规划）。
- 实现**记忆管理**：至少供应商风险记忆和类目表现记忆跨工作流生效。
- 实现**上下文压缩**：长链路分段摘要 + 工具输出压缩，单条工作流内 token 用量受控。
- 实现**多 Agent 协作**：Planner/Executor/Critic 三角 + Listing 生成-评审-重写闭环。
- 实现**多租户隔离**：至少两个种子租户，记忆/数据/RAG/凭证隔离可验证。
- 实现**Bad Case 处理闭环**：检测→隔离→沉定→CI 回归门禁可跑通。
- 支持多个模拟平台适配器，体现不同平台的字段、规则和费用差异。
- 让 Agent 通过受控工具完成研究、计算、生成、校验、发布、查询订单和客服回复等动作。
- 对高风险操作强制人工审批。
- 记录 Agent trace、决策轨迹、记忆访问、上下文压缩、工具调用、审批记录、评估结果和审计日志。
- 提供前端界面，用于启动工作流、查看进度、审批动作、查看决策时间线、记忆面板和调试 Agent。
- 架构上预留未来接入真实平台的空间。

### 4.2 MVP 非目标

- 第一版不直接连接真实卖家账号、不执行真实资金操作。
- 不做完整 ERP、WMS、CRM 或广告投放系统。
- 不支持所有国家、语言、类目和平台规则。
- 不让 Agent 替代人类做最终高风险商业决策（自主决策只在低中风险区间生效，高风险一律人工）。
- 不做多租户计费与企业级团队权限（预留）。

### 4.3 后续目标

- 接入真实平台 API、真实商品/订单/库存/评论/广告数据。
- 支持 CSV / Excel / ERP 导入。
- 多租户计费、团队协作、角色化策略、组织级审计导出。
- 记忆巩固与遗忘的离线批处理、记忆治理。
- 事件驱动的持续监控（订单、库存、差评、转化率、退款异常）。
- 高敏感租户切独立 schema/库。

---

## 5. MVP 范围（已裁剪）

### 5.1 裁剪原则

v0.2 把 9 个专职 Agent + 3 adapter + 7 页前端 + 全量可观测全部塞进 MVP，单人无法完成。本版按"最小惊艳集"裁剪：保留能体现自主/记忆/协作/隔离/坏例的核心，把可合并项合并、把可后移项后移。

### 5.2 MVP 必做集（最小惊艳 Demo）

Agent（收敛为 6 个核心角色 + 1 个 Critic）：

- **Planner**：顶层决策、go/no-go、研究深化触发、重规划路由。
- **Product Research Agent**：选品机会分析 + 证据评分。
- **Profit Analyst Agent**：确定性利润计算 + 解释。
- **Supplier Agent**：供应商评分（读取记忆中的供应商历史风险）。
- **Listing Agent**：多平台 Listing 生成（支持被 Critic 打回后带约束重写）。
- **Critic（即 Risk & Review Agent）**：评审与放行/重写/升级决策。
- **Customer Support Agent**：客服回复草稿（RAG + 记忆）。

可合并/简化：Localization 并入 Listing Agent；Ops 简化为"运营数据生成 + 单个总结节点"；Supervisor 被 Planner 取代。

### 5.3 MVP 主工作流

1. 用户创建一个跨境选品和铺货任务。
2. **Planner** 拆解任务、加载相关记忆、规划执行路径。
3. Product Research Agent 分析模拟趋势/竞品/评论/价格带，并给出证据完整度评分。
4. **Planner 顶层 go/no-go**：决策"继续/深化研究/放弃"，证据不足时自主触发第二轮研究（最多 2 轮）。
5. Profit Analyst Agent 计算到岸成本、毛利、敏感性、盈亏平衡。
6. Supplier Agent 评分候选供应商（读取**供应商风险记忆**，历史被标红的供应商自动降权）。
7. Listing Agent 为不同平台生成 Listing 草稿。
8. **Critic 评审**：被阻断的 Listing 进入**生成-评审-重写闭环**（最多 3 轮）。
9. 人工审核 Listing 和发布动作。
10. Marketplace Adapter 执行模拟发布。
11. Ops（简化节点）监控模拟运营数据。
12. Customer Support Agent 为模拟工单生成有依据的回复草稿。
13. **复盘**：生成复盘报告，并将本次评估结果与关键决策**回流写入记忆**，供后续工作流使用。

### 5.4 MVP 支持的模拟平台

- MockAmazonAdapter：严格标题长度、五点描述、类目属性、平台佣金、FBA 类履约假设。
- MockShopifyAdapter：灵活商品页、SEO 字段、库存位置、内容区块。
- MockTikTokShopAdapter：短内容、视频卖点、更严格的营销声明检查。

三个适配器实现相同接口，内部规则不同。

### 5.5 MVP 商品类目

建议只支持 2-3 个相对安全类目：家居收纳类、宠物配件类、健身或健康生活配件类。避免食品、保健品、医疗器械、儿童安全用品、化妆品、电池等高合规风险类目。

---

## 6. 用户角色与使用场景

### 6.1 商家运营人员

目标：快速判断一个商品是否值得做，并生成可执行的铺货方案。  
关注点：市场需求、利润、供应商可靠性、Listing 质量、是否值得进入下一步。  
核心操作：创建选品任务、查看机会评分与利润、比较供应商、审核 Listing、查看模拟运营数据。

### 6.2 电商运营负责人

目标：掌握流程质量和风险。  
关注点：Agent 是否按流程执行、高风险动作是否审批、失败是否可恢复、业务判断是否有证据与记忆支撑、能否复盘、Bad Case 是否在沉淀。  
核心操作：查看所有工作流、查看 Agent 决策与工具调用、审批发布/改价/退款、查看错误与审计日志、对比不同工作流的评估分数、查看记忆库与 Bad Case 数据集。

### 6.3 客服人员

目标：用 Agent 辅助处理订单、物流、退款和差评，保留人工确认。  
核心操作：打开工单、让 Agent 查询订单与物流、查看回复草稿、审核退款或补偿建议、修改后发送。

### 6.4 技术面试官

面试官希望确认这不是简单 Prompt Demo 或固定流程脚本，而是一个具备自主决策能力的真实 Agent 工程。  
项目需要能展示：

- 清晰的 **Planner/Executor/Critic 三角**编排图与真实循环。
- 自主决策点清单（go/no-go、自批判、研究深化、重规划）及其决策轨迹。
- 记忆管理层与跨工作流学习证据。
- 上下文压缩策略与 token 预算。
- **多租户十层隔离矩阵与防越权证据**。
- **Bad Case 处理闭环与 CI 回归门禁**。
- Typed tools 与工具权限、多平台 adapter 抽象。
- 审批中心与审计日志、可观测性面板（含决策时间线、记忆访问、上下文压缩日志）。
- Agent 评估体系（含自主决策质量维度）。
- 失败恢复、重规划与工作流回放。

---

## 7. 产品需求

### 7.1 工作流创建

用户创建一个跨境商品启动任务，输入：商品想法或关键词、目标平台、目标国家或语言地区、商品类目、目标价格区间、可选供应商信息、风险偏好（保守/平衡/激进）。

验收标准：

- 系统创建唯一 workflow_id。
- 工作流进入可见状态机。
- Planner 在启动时加载相关记忆（供应商历史、类目表现）并记录"记忆命中"到 trace；记忆命中必须属于本租户。
- 工作流中断后可恢复，恢复时重新装配上下文摘要。

### 7.2 选品机会分析（含自主研究深化）

系统分析模拟趋势数据、竞品数据、价格分布和评论痛点，并给出**证据完整度评分**。

输出：机会评分、需求判断依据、价格带分析、竞品总结、评论痛点、推荐定位、证据引用、**证据完整度评分**。

**自主决策点**：当证据完整度评分低于阈值时，Planner 自主决定是否触发第二轮研究（补充竞品/评论/关键词），最多 2 轮。决策记为 AgentDecision。

验收标准：

- 每个重要建议都链接到 mock evidence 记录。
- Agent 能区分事实、推断和假设，数据缺失时明确说明。
- 研究深化触发与放弃都有明确理由并记录到 trace。

### 7.3 利润测算

系统计算商品预计盈利能力。  
输入：供应商单价、MOQ、国际运费、关税与税费、平台佣金、支付手续费、履约费用、广告成本假设、退货率假设。  
输出：到岸成本、毛利率、贡献利润、盈亏平衡售价、广告成本与退货率敏感性分析、利润风险评分。

验收标准：

- 数学计算由确定性服务完成，不由 LLM 在文本里自由计算。
- Agent 可以用业务语言解释结果。
- 所有假设都被保存并可查看，并写入 episodic 记忆供复盘。

### 7.4 供应商评估（含跨工作流记忆）

系统对候选供应商评分，并读取**供应商风险记忆**：历史被标记为高风险的供应商自动降权并附"历史风险来源"引用。

输入：单价、MOQ、交期、质量评分、历史缺陷率、响应速度、所在地区、认证情况。  
输出：供应商排序、风险说明、推荐供应商、备选供应商、下单前追问清单。

验收标准：

- 供应商评分由透明公式计算。
- 历史风险记忆命中必须显式引用来源 workflow_id，且属本租户。
- 高风险供应商必须被标记；首次标记的高风险供应商会被写入记忆。

### 7.5 Listing 生成（含自批判 loop 与图文生成）

系统为不同平台生成 Listing 草稿与配套图文素材。每个平台输出：标题、简短描述、五点/卖点、长描述、SEO 关键词、商品属性、变体结构、图片需求 brief、生成的图片集合（主图/场景图/卖点信息图）、合规提示、本地化说明。

**图片生成（生图）是 Listing 的正式产出，不是仅出 brief**：

- Listing Agent 在文本草稿定稿后，按 `generate_image_brief` 调用 `generate_listing_images` 工具，按平台图片规范（见 §12.3 `get_image_spec`）生成图片集合：主图（白底/纯背景，符合平台主图政策）、场景图（使用场景/人群）、卖点信息图（功能标注/尺寸对比/合规标识）。
- **图片来源优先级（RAG + Agent 融合）**：卖家已有实拍图/品牌素材（来自租户素材库 RAG 检索）优先用于主图与场景图；纯生成图仅用于卖点信息图与补充场景，不得冒充实拍主图。生成图在 trace 中标记 `generated=true`，与实拍素材区分。
- **图文一致性校验**：生成图后做"文字声明 vs 图片"一致性检查（如宣称 waterproof 须在图中体现、尺寸标注须与变体结构一致），不一致在 Critic 评审中记高风险。
- **图片合规自检**：生成图经 Vision 模型 `verify_image_compliance` 校验主图白底/无违规水印/无侵权元素，不通过的图不进入图片集合、触发重生成。

**多 Agent 协作点（生成-评审-重写闭环）**：

1. Listing Agent 按 Critic 下发的约束集生成文本草稿与图片 brief。
2. 文本定稿后调用生图工具产出图片集合，并经 Vision 自检。
3. Critic 评审：夸大声明、缺失字段、平台规则冲突、本地化风险、**图片合规（主图政策/水印/误导性/侵权）、图文一致性**。
4. 若被阻断，Critic 输出结构化 critique（问题清单 + 必须满足的约束），Listing Agent 带约束重写文本或重新生图，最多 3 轮。
5. 仍不通过则升级人工，并记录 CritiqueLoop 迭代轨迹。

验收标准：

- Listing 草稿与图片集合符合对应 adapter 字段与图片规范。
- 不同平台生成的 Listing 结构与图片集合不同（主图/场景图数量随平台而异）。
- 图片集合含主图/场景图/卖点信息图三类，且生成图标记 `generated`。
- 每轮 critique 与重写（含重生成）都记录到 CritiqueLoop 与 trace。
- 重写必须可证明"应用了 Critic 的约束"，而非随机重试。

### 7.6 本地化（并入 Listing Agent）

本地化作为 Listing Agent 的一个能力节点：语言适配、尺寸/重量/单位换算、表达语气调整、地区用语、声明与免责检查。

验收标准：

- 本地化内容关联原始 Listing 版本。
- 风险声明在审批前高亮。
- 本地化改动可查看 diff。

### 7.7 风险审核与 Guardrails

风险检查项：利润率低于阈值、退货率假设过高、无证据声明、平台必填属性缺失、价格策略过激、供应商质量风险、类目限制、商标/IP 风险、退款/补偿超阈值。

验收标准：

- 每个工作流都有风险报告。
- 高风险发现阻断自动执行并触发 Critic 决策（重写/升级）。
- 用户能看到为什么需要审批。

### 7.8 人工审批

必须显式审批的操作：发布 Listing、修改价格、应用促销/优惠券、发起退款、发放补偿、下架商品、修改库存预留。

审批记录包括：请求动作、请求来源、涉及工具与 adapter、输入 payload、风险等级、Agent 理由、审批人决策、时间戳。

验收标准：

- 高风险动作没有审批不能执行。
- 被拒绝动作记录拒绝原因，并可触发 Planner 重规划。
- 已批准动作进入不可变审计记录。

### 7.9 模拟铺货

系统通过 mock adapter 发布已批准的 Listing。  
行为：校验 payload、返回模拟 listing_id、记录发布状态、模拟字段错误/限流/临时失败。

验收标准：

- 同一发布请求可通过 idempotency_key 安全重试。
- 校验错误对用户和 Agent 可见。
- 系统可从临时失败恢复；持续失败触发 Planner 重规划（如跳过该平台）。

### 7.10 运营监控（MVP 简化）

发布后监控模拟表现：曝光、点击、转化率、订单、收入、库存、评论评分、退货信号、客服工单量。  
输出：表现总结、异常检测、下一步建议、改价/优化/补货/客服策略建议。

验收标准：

- 建议必须引用已观测指标。
- 高风险优化动作必须审批。
- 监控结果与 Listing 版本关联，并写入类目表现记忆。

### 7.11 客服售后（RAG + 记忆）

系统基于订单、物流、商品和政策数据生成客服回复草稿，并读取客户历史工单记忆与差评处理记忆。

支持工单类型：订单在哪里、商品不符合预期、退货请求、退款请求、差评回复。

RAG 适用知识（静态/半静态）：店铺退换货政策、平台售后规则、商品说明书、尺码表/材质/安装说明、FAQ、物流时效、保修政策、多语言客服话术、差评处理规范。

必须走业务工具的数据：订单状态、物流轨迹、支付状态、退款金额、库存状态、优惠券状态、客户历史工单。

客服 Agent 回答流程：

1. 识别问题类型。
2. 查询订单、物流、退款等实时工具。
3. 检索 RAG 知识库（metadata 限定租户/平台/语言/商品）。
4. 读取客户历史工单记忆（同客户是否反复投诉、是否有补偿记录）。
5. 组合实时数据与检索证据生成回复草稿。
6. Critic 检查编造物流、过度承诺、违规补偿、政策冲突。  
   **RAG 与工具融合优先级铁律**：事实类、知识类查询（政策、商品说明、FAQ、尺码/材质）优先走 RAG 检索；操作类、计算类、实时数据类任务（订单、物流、支付、退款、库存、利润计算）优先走业务工具调用。当 RAG 知识与工具返回的实时业务数据冲突时，一律以工具实时数据为准，RAG 知识仅作补充说明，不得覆盖实时事实；以知识库覆盖实时数据的产出在 Critic 评审中记为高风险。
7. 中高风险回复进入人工审核。

验收标准：

- 回复必须基于订单和政策数据。
- 退款和补偿必须审批。
- Agent 不能编造物流状态。
- 检索不到依据时必须说明缺少依据并请求人工处理。
- 涉及政策/商品说明/FAQ 的回复必须带来源引用。
- 客户历史记忆命中必须显式引用来源且属本租户。

### 7.12 复盘报告与记忆回流

工作流结束后生成复盘报告：选品机会总结、最终决策与理由、已发布 Listing ID、关键风险、发布后表现、后续优化建议、下一轮实验建议、**自主决策复盘**、**Bad Case 复盘**。

**记忆回流（自改进闭环）**：本次评估结果、关键决策、供应商风险标记、类目表现均写入记忆；低质量决策被标记为"待改进"并沉淀为 LearningRule；高价值 Bad Case 进 BadCaseDataset。

验收标准：

- 复盘基于已保存的工作流状态生成。
- 报告包含 trace、证据、审批、评估、决策轨迹、记忆引用、Bad Case 链接。
- 评估结果确实写入了记忆（可通过下一个工作流命中验证）。

### 7.13 顶层 go/no-go 决策闸门（新增）

在研究+利润+供应商综合产出后、进入 Listing 生成前，Planner 必须做一次显式 go/no-go 决策：

- **proceed**：进入 Listing 生成。
- **revise**：打回某个上游步骤（如要求换供应商、调整价格假设）。
- **abort**：终止本次启动并记录原因。

决策必须输出：综合证据摘要、风险评估、chosen_option、alternatives_considered、理由，并记为 AgentDecision。

验收标准：

- 没有 go/no-go 决策记录，工作流不能进入 drafting_listings 状态。
- abort 必须给出理由并可被复盘。
- revise 必须指明打回的目标步骤与约束。

---

## 8. Agent 系统设计

### 8.1 编排模型：Planner + Executor + Critic 三角

本版明确采用**显式三角回路**，取代 v0.2 的"模糊 Supervisor + 固定状态机"。三角在一条**确定性主干状态机**上运行：主干定义宏观阶段顺序与护栏边界，三角在每个阶段内部提供真实决策与循环能力。这样既保留可控性，又获得自主性。

- **Planner（规划者）**：任务拆解、记忆加载、执行路径规划、go/no-go、研究深化触发、失败重规划路由。它不直接调业务工具，只调规划/决策/记忆工具。
- **Executor（执行者，由专业 Agent 充当）**：Research/Profit/Supplier/Listing/Support 等。它们调用业务工具完成具体动作，返回结构化结果与证据。
- **Critic（评审者，即 Risk & Review Agent）**：评审 Executor 产出，给出风险等级与决策（放行/重写/升级人工）。它是 CritiqueLoop 与 Bad Case 检测的驱动方。

三角协作回路（以 Listing 为例）：

```
Planner ──下发任务+约束──▶ Listing Agent (Executor)
                                │ 产出草稿
                                ▼
                            Critic ──评审──┐
                              │放行          │重写(critique+约束)
                              ▼              ▼
                          下一步        Listing Agent 重写
```

### 8.2 Agent 角色与职责边界

Planner：拆解任务、加载记忆、规划路径；顶层 go/no-go、研究深化触发、重规划路由；不直接访问业务数据，只调规划/决策/记忆工具。

Product Research Agent：发现选品机会，总结需求、竞品缺口、评论痛点；输出附带证据 ID 与证据完整度评分。

Profit Analyst Agent：调用确定性计算工具，解释利润与风险；不在自然语言里做不可追踪计算。

Supplier Agent：评估供应商，读取供应商风险记忆，标记供应链风险；推荐主/备供应商，首次标记的高风险供应商写入记忆。

Listing Agent：生成平台特定 Listing，避免无证据声明；支持带 Critic 约束重写；本地化作为其能力节点；重写必须可证明应用了 Critic 的约束。

Customer Support Agent：生成客服回复草稿，查询订单/物流/政策工具，读取客户历史记忆；对风险工单升级。

Critic（Risk & Review Agent）：评估输出与动作风险，给出风险等级与放行/重写/升级决策；驱动 CritiqueLoop；触发 Bad Case 记录与隔离。

### 8.3 自主决策能力清单（新增，明确化）

为避免"看起来自主、实际是脚本"的质疑，本版显式列出系统中的自主决策点，每个都有 AgentDecision 记录：

| 决策点         | 触发条件           | 决策选项                  | 边界         |
| ----------- | -------------- | --------------------- | ---------- |
| 证据深化        | 证据完整度 < 阈值     | 继续研究/放弃               | 最多 2 轮深化   |
| 顶层 go/no-go | 研究+利润+供应商综合产出后 | proceed/revise/abort  | 必须有理由与备选   |
| Listing 自批判 | Critic 评审不通过   | 重写/升级人工               | 最多 3 轮重写   |
| 供应商重选       | 供应商质量评分 < 阈值   | 换主供应商/降级/保留并标注        | 记录候选对比     |
| 失败重规划       | 工具持续失败         | 重试/换路径/跳过该平台/升级人工     | 受最大重规划次数约束 |
| 运营优化建议      | 运营指标异常         | 改价/优化 Listing/补货/客服策略 | 高风险一律人工    |
| Bad Case 处理 | 检测命中           | 隔离/降级/重试/重规划/拒绝/升级/回滚 | 高风险一律人工    |

验收：每个自主决策点都能在 trace 与决策时间线中找到对应 AgentDecision 记录，含理由与备选。

### 8.4 工作流状态机（加入循环边与决策分支）

状态：draft / queued / planning / researching / decision_gate / analyzing_profit / evaluating_suppliers / drafting_listings / critique_loop / localizing / reviewing_risk / awaiting_approval / executing / monitoring / handling_support / retrospective / completed / failed / cancelled / blocked / reroute / **quarantined（Bad Case 隔离）**。

新增边（循环与决策）：

- researching → researching（自主深化，最多 2 次）。
- researching → decision_gate → drafting_listings（proceed）/ → researching（revise）/ → cancelled（abort）。
- drafting_listings → reviewing_risk → critique_loop → drafting_listings（重写，最多 3 次）/ → awaiting_approval（放行）。
- executing → reroute → 上游某步（重规划）/ → blocked（需人工）。
- 任意节点 → quarantined（检测到 Bad Case）/ → resolved（处理后回到正常或终止）。

每次状态变化显式持久化；循环次数与决策原因必须记录，防止死循环。

### 8.5 失败处理与重规划

失败类型：工具参数校验失败、adapter 返回错误、LLM 输出不符合 schema、缺少证据、缺少计算输入、审批被拒绝、队列超时、模型/外部服务限流。

恢复策略：

- 临时错误：指数退避重试。
- schema 错误：让 Agent 修复结构化输出。
- 缺少业务输入：暂停并请求用户补充（进入 blocked）。
- 审批拒绝：进入 blocked 或 reroute，由 Planner 决定下一步。
- **持续失败（新增）**：触发 Planner 重规划，从"换路径/跳过平台/升级人工"中选一个，记为 AgentDecision，而非无限重试。
- **Bad Case（新增）**：检测命中后进隔离态，按 §20 处理。
- 保留已有中间结果、trace、记忆引用与错误信息。

### 8.6 子 Agent 执行超时与兜底中断（新增）

为避免单个 Executor 子 Agent 因模型挂起、外部限流或长任务导致编排层干等甚至链路卡死，本版为每个子 Agent 节点设定显式执行超时阈值，并配套强制中断 + 兜底返回：

- **超时阈值（双层，注册中心显式声明，§12.1）**：
  - 节点 LLM 调用超时：默认 60s（结构化/规划类 30s），超时即中断当前 LLM 调用。
  - 节点整体执行超时：默认 120s（含工具调用与重试），超时即强制中断整个节点。
  - 阈值可按节点类型覆盖。
- **强制中断**：超时由 ToolExecutor / 编排运行层（runtime）触发，不依赖子 Agent 自觉；中断后该节点标记为 `timeout`，释放资源，不阻塞兄弟节点与后续可并行阶段。
- **兜底返回（fallback）**：超时节点必须返回一个结构化兜底结果，而非抛错挂起：
  - 优先：返回该节点上一次成功结果的缓存（若有且未过期）；
  - 否则：返回确定性降级结果或"能力暂不可用"的结构化提示（含 `reason=timeout`），由上游 Planner/Critic 决定跳过、换路径或升级人工；
  - 不得返回半成品或残缺 schema 输出。
- **记录与联动**：每次超时写 trace（节点、超时类型、阈值、已用时间），并记 Bad Case（归入 D 工具失败 / E 流程异常，§20.1）；按 §20.3 触发重试 / 重规划 / 降级；超时不计入"正常完成"，不写入语义记忆。
- **与状态机衔接**：节点 `timeout` → 进入 `blocked` 或 `reroute`，由 Planner 在护栏内决策下一步；同一节点多次超时触发重规划而非无限等待。区别于 §20.1 E 类的"状态机卡死流程级看门狗"——本节的超时针对**单节点执行耗时**，E 类看门狗针对**循环/状态机整体停滞**。

---

## 9. 记忆管理（新增章）

本章把记忆从 v0.2 的"基础设施级（checkpointer/store）"提升为一等公民。

### 9.1 记忆类型

- **Episodic（情景记忆）**：过往工作流的事实——某次选品决策、某次供应商评分、某次 Listing 表现、某次客服处理。原始事件，按 workflow_id/step 索引。
- **Semantic（语义记忆）**：从情景记忆巩固出的抽象知识——"供应商 X 历史高风险""类目 Y 在 TikTok 转化低""平台 Z 禁止某声明"。结构化键值 + 向量。
- **Procedural（程序记忆）**：什么做法有效——某 prompt 版本在 Listing 生成上得分更高、某工具组合在低利润场景更稳。供 Planner 规划时参考。

### 9.2 记忆生命周期

- **写入（Write）**：关键节点自动写入（供应商评分、Listing 发布结果、客服处理结果、复盘评估）。通过 `write_memory` 工具，带 tenant_id、entity_type、source_workflow_id。
- **检索（Retrieve）**：决策前 Planner/Agent 调 `retrieve_memory`，语义检索 + metadata 过滤（entity_type、tenant_id、marketplace、category）。
- **巩固（Consolidate）**：离线/低峰任务把同类 episodic 记忆去重、抽象成 semantic 记忆与 LearningRule，标注来源。
- **遗忘（Forget）**：基于 TTL、访问频次衰减与相关性分数；遗忘是软删除（archived），保留审计可追溯，不物理删除高风险记忆。

### 9.3 跨工作流学习（核心卖点）

- 供应商风险记忆：被标记高风险的供应商在后续所有工作流中自动降权并引用历史来源。
- 类目表现记忆：某类目在某平台的历史转化/退货/利润表现，影响后续 go/no-go 与定价建议。
- 决策模式记忆：重复出现的有效/无效决策模式沉淀为 LearningRule，供 Planner 参考。
- 客户记忆：同客户反复投诉或补偿历史影响客服回复策略。

验收：可在两个连续工作流中验证记忆生效（第一次标记→第二次自动命中并引用来源）。

### 9.4 记忆检索协议

检索请求必须包含：tenant_id、query 向量、metadata 过滤、top_k、min_score。  
检索结果返回：memory_id、content、source_workflow_id、relevance_score、created_at、access_count。  
每次检索与访问写入"记忆访问日志"（谁、何时、命中哪些记忆），用于审计与相关性校准。

### 9.5 记忆隔离与隐私

- 记忆严格按 tenant_id 隔离，工具层强制校验，不依赖 LLM 自觉（详见 §13 隔离矩阵）。
- PII 脱敏后再入记忆；客户身份信息不进 semantic 记忆的 content。
- 记忆访问全部进审计日志。
- 防止"记忆投毒"：外部数据（供应商描述、评论、客服消息）不得直接作为可信记忆写入，须先经 Critic 评审与脱敏。

---

## 10. 上下文管理（新增章）

长多 Agent 链路必爆上下文。本章给出压缩与续航策略。

### 10.1 上下文组装模型

每个 Agent 节点收到的"工作上下文"由 ContextAssembler 组装，包含：当前任务与目标、**压缩后的前序上下文**（decision brief，而非原始全量输出）、检索到的记忆（限定条数）、当前节点需要的工具结果（按需压缩）、Critic 下发的约束（对 Executor 节点）。

### 10.2 Summarization 节点

在产出大段结果的节点（Research、Profit、Supplier）之后插入 summarization 节点：输入节点原始输出 + 证据 ID；输出结构化 decision brief（关键事实、数字、风险、证据引用），而非全文；原始全量保留在存储里供回放与审计。

### 10.3 工具输出压缩

长工具输出（竞品列表、评论 dump、订单列表）由 `compress_tool_output` 压缩为"关键事实 + 证据 ID"，全量保留在 ToolCall 记录里供回放与审计。

### 10.4 滑动窗口与语义摘要

采用"最新窗口保留原始 + 早期历史语义压缩"的通用上下文策略，避免长链路 token 爆量又丢关键证据：

- **最新窗口保留原始**：每个 Agent 节点默认保留最近 N 轮（N 可配，默认 6）原始上下文（当前任务输入输出、最近几条工具结果、最近几次 Critic 评审），不加压缩，保证近端推理精度。
- **早期历史语义压缩**：窗口之外的早期上下文由 `summarize_context` 压成结构化 `decision_brief`（关键事实 + 证据 ID），丢弃冗余原文但保留证据链；原始全量仍存存储供回放/审计（§10.2）。
- **token 预算驱动驱逐**：当窗口 + 摘要超出节点 `token_budget`（§10.5）时，优先驱逐最旧的非关键内容（先压更早摘要、再裁记忆条数、最后裁工具结果），压缩前后 token 记入 trace（§10.5）。
- **Planner 决策窗口（特殊处理）**：Planner 额外维护"决策上下文窗口"：最近 N=6 个 `AgentDecision` 保留全量，更早仅保留 `decision_brief`，保证长链路里 Planner 不失忆、不自相矛盾。

### 10.5 Token 预算与成本

- 每个节点有 token 预算，超预算触发额外压缩或告警。
- 每条工作流记录总 token、各节点 token、压缩节省的 token、估算成本。
- 上下文压缩操作本身记入 trace（压缩前/后 token、保留的 evidence ID）。

### 10.6 语义缓存（新增）

在上下文压缩之外，额外引入**语义缓存层**直接复用历史执行结果，进一步压低重复推理成本。注意它与 §9 记忆的区别：记忆是"跨工作流沉淀的知识"，语义缓存是"相同或高度相似请求直接复用产出以省 token"，二者互补、不替代。

- **命中逻辑**：对进入 Agent 的请求（问题/任务描述 + 关键参数）做 embedding，与缓存库按余弦相似度检索；相似度 ≥ 阈值（默认 0.92）视为命中，直接返回缓存的结构化产出，跳过本轮 LLM 推理与工具调用。
- **回填与失效**：未命中则正常执行，完成后将（请求 embedding、产出摘要、关键证据 ID、命中模型档位）写入缓存；缓存按 TTL + 命中频次衰减；价格/库存/订单等强时效数据不参与缓存或 TTL 极短。
- **安全与隔离**：缓存按 tenant_id 隔离（与 §13 一致），跨租户不命中；命中结果带来源 workflow_id 可追溯；高风险动作（发布/退款/改价等）一律不缓存，强制走真实审批链路。
- **可观测**：每次命中/未命中写 trace（相似度、命中缓存 id、节省 token/成本），Dashboard 展示缓存命中率与节省成本。

---

## 11. 多 Agent 协作（新增章）

### 11.1 协作模式：三角回路

见 8.1。Planner/Executor/Critic 三角在每个阶段内部形成"规划→执行→评审→(重写/放行)"的真实循环，而非单向交接。

### 11.2 生成-评审-重写回路

最典型的协作回路（Listing）：

1. Planner 给 Listing Agent 下发任务 + Critic 的硬约束集。
2. Listing Agent 生成草稿。
3. Critic 评审，输出结构化 critique（问题 + 约束）。
4. 若 critique 非空，Listing Agent 带约束重写，回到步骤 3。
5. 通过则放行；超 3 轮则升级人工。

验收：重写必须可证明"应用了 Critic 的约束"（diff 对比），不是随机重试。

### 11.3 共享工作记忆

- 三角之间通过 LangGraph state 的"共享 scratchpad"交换结构化中间产物（而非整段上下文透传）。
- scratchpad 字段：current_task、constraints、artifacts（listing_draft 等）、critique、decision_brief。
- 这样减少上下文膨胀，也让协作可追溯。

### 11.4 Peer 反馈

Executor 之间可发起反馈请求（经 Planner 协调），例如 Supplier Agent 请求 Research Agent 补充竞品交付数据。这是有界的 peer-to-peer 交互，不构成自由对话，避免失控。

### 11.5 协作边界

- Critic 只评审、不直接执行业务写操作。
- Planner 不直接调业务工具，只调规划/决策/记忆工具。
- 任何 peer 反馈都记录到 trace。
- 自主协作只在低中风险区间生效；高风险一律人工。

---

## 12. 工具与平台适配层架构

### 12.1 工具注册中心

每个工具定义包含：工具名称、描述、输入/输出 schema、风险等级、所需权限、是否需要幂等、超时、重试策略、审计策略、是否需要人工审批、**是否产出可压缩输出、是否产出可隔离产出物**。其中"超时"为节点/工具级显式阈值（LLM 调用超时、节点整体执行超时），由 ToolExecutor / runtime 强制中断并执行兜底返回，而非仅作标记（见 §8.6）。

### 12.2 核心工具分类

研究类：search_market_trends / get_competitor_products / get_review_pain_points / get_keyword_metrics。

利润类：estimate_landed_cost / calculate_marketplace_fees / calculate_break_even_price / run_margin_sensitivity。

供应商类：list_supplier_candidates / score_supplier / compare_suppliers。

Listing 类：create_listing_draft / validate_listing / localize_listing / generate_image_brief / generate_listing_images / verify_image_compliance。

平台类：publish_listing / update_price / update_inventory / get_orders / get_listing_performance。

客服类：get_order_details / get_shipping_status / get_return_policy / draft_support_response / request_refund_approval / issue_refund。

可观测性类：record_trace_event / record_evaluation_result / replay_workflow。

**记忆类（新增）**：write_memory / retrieve_memory / consolidate_memory / list_memory / archive_memory。

**上下文类（新增）**：summarize_context / get_decision_brief / compress_tool_output / record_token_usage。

**决策类（新增）**：record_decision / request_research_deepening / request_rewrite / request_reroute。

**Guardrail 类（新增）**：validate_input / validate_output / detect_prompt_injection / detect_pii。

**Bad Case 类（新增）**：record_bad_case / quarantine_artifact / escalate_to_human。

### 12.3 Marketplace Adapter 接口

```text
MarketplaceAdapter
  validate_listing(payload) -> ValidationResult
  get_image_spec(marketplace) -> ImageSpec   # 平台图片规范：主图背景/尺寸/数量/水印/信息图要求
  create_listing_draft(payload) -> ListingDraftResult
  publish_listing(payload, idempotency_key) -> PublishResult
  update_price(listing_id, price, idempotency_key) -> ActionResult
  update_inventory(listing_id, quantity, idempotency_key) -> ActionResult
  get_orders(filters) -> OrderList
  get_inventory(sku) -> InventorySnapshot
  get_performance(listing_id, date_range) -> PerformanceSnapshot
  issue_refund(order_id, amount, reason, idempotency_key) -> RefundResult
```

### 12.4 Mock Adapter 要求

Mock adapter 模拟：平台字段规则、类目必填属性、平台费用模型、Listing 校验错误、发布成功与失败、限流/临时故障、订单与运营数据、退款策略限制。这样即使不接真实平台也能体现真实工程复杂度。

### 12.5 未来真实平台接入要求

凭证存 secret manager（按租户维度）、OAuth/官方 token、不暴露原始凭证给 LLM、优先 sandbox、真实写操作继续强制审批、reconciliation job 对账、webhook 处理事件。

---

## 13. 数据模型与多租户隔离

### 13.1 核心实体（保留 v0.2）

Tenant / User / MarketplaceConnection / Workflow / WorkflowStep / ToolCall / AgentTrace / ApprovalRequest / ProductIdea / Supplier / ListingDraft / PublishedListing / Order / InventorySnapshot / SupportTicket / EvaluationResult / AuditLog。

### 13.2 新增实体（自主/记忆/协作/隔离/Bad Case）

- **TenantQuota**：tenant_id, max_concurrent_workflows, daily_token_limit, daily_cost_limit, max_memory_records, max_rag_docs, used{token_today,cost_today,workflows_running}。
- **TenantSecretRef**：tenant_id, marketplace, secret_key（secret manager 引用，非明文）, status, last_rotated_at。
- **MemoryRecord**：id, tenant_id, memory_type(episodic/semantic/procedural), entity_type, entity_id, content, embedding, metadata{marketplace,category,locale,...}, source_workflow_id, created_at, last_accessed, access_count, relevance_score, status。
- **ContextSummary**：id, workflow_id, step, summary_text, source_refs, token_before, token_after, created_at。
- **AgentDecision**：id, workflow_id, agent, decision_type, input_context_ref, reasoning, chosen_option, alternatives_considered(json), trace_id, timestamp。
- **CritiqueLoop**：id, workflow_id, target_artifact, iteration, critic_findings(json), applied_constraints(json), resolved(bool), trace_id, timestamp。
- **LearningRule**：id, tenant_id, rule_text, derived_from, confidence, active, created_at。
- **MemoryAccessLog**：id, tenant_id, workflow_id, agent, operation, query, hit_memory_ids(json), occurred_at。
- **BadCase**：bad_case_id, tenant_id, workflow_id, category(A-H), trigger, context_ref, severity, handling, outcome, artifact_quarantined, occurred_at, resolved_at。
- **BadCaseDataset**：dataset_id, name, cases[], tags[], active。
- **QuarantinedArtifact**：artifact_id, tenant_id, artifact_type, reason, source_bad_case_id, status, created_at。
- **SemanticCacheEntry**：cache_id, tenant_id, request_embedding, response_summary, source_workflow_id, model_tier(small/main), hit_count, ttl_until, created_at。

### 13.3 建议数据库表（在 v0.2 基础上新增）

新增：tenant_quotas、tenant_secret_refs、memory_records、context_summaries、agent_decisions、critique_loops、learning_rules、memory_access_logs、decision_briefs、bad_cases、bad_case_datasets、bad_case_dataset_items、quarantined_artifacts、semantic_cache_entries。

### 13.4 多租户隔离矩阵（十层）

| #  | 隔离对象     | 实现方式                                                                  | 校验点                              | 不可信任来源                     |
| -- | -------- | --------------------------------------------------------------------- | -------------------------------- | -------------------------- |
| 1  | 业务数据     | 所有业务表含 tenant_id；repository 层强制 WHERE tenant_id                       | 复合索引含 tenant_id；查询缺 tenant_id 报错 | LLM 不得传入 tenant_id         |
| 2  | 记忆       | memory_records.tenant_id；retrieve_memory metadata_filter 强制 tenant_id | 跨租户记忆命中即安全事件                     | LLM 传入的 tenant_id 忽略       |
| 3  | 向量库      | pgvector 单表 + tenant_id 过滤 + HNSW；检索必带 tenant_id                      | 检索 SQL 缺 tenant_id 拒绝            | 向量检索绕过过滤层                  |
| 4  | 上下文      | AgentContext 只装配同租户记忆/工具结果；scratchpad 按 workflow_id 隔离                | context 组装前校验 memory_hits 归属     | 上游残留的跨租户 context           |
| 5  | RAG 知识   | 知识集合 metadata 带 tenant_id/marketplace/locale；检索 metadata_filter       | 跨租户文档不得进同一结果                     | 文档导入漏标 tenant_id           |
| 6  | 工具       | ToolExecutor 注入 tenant_id/actor_id；工具入参 schema 不接受 tenant_id          | 工具实现里 tenant_id 取自上下文            | 工具自己读 payload 里的 tenant_id |
| 7  | Trace/审计 | trace 与 audit_log 带 tenant_id；可观测性读权限按租户过滤                            | observer 跨租户读 trace 拒绝           | 面板透传 tenant_id 到查询         |
| 8  | 密钥/凭证    | secret manager 按 tenant 维度存；credential ref 不进 LLM context             | 真实 adapter 用凭证不经 LLM             | LLM 输出里出现凭证                |
| 9  | 执行/队列    | 每租户队列优先级与并发上限；worker 无跨租户共享可变状态                                       | 同租户多 workflow 不串数据               | worker 缓存跨租户对象             |
| 10 | 成本/限流    | 每租户 token/cost 配额与限流；成本按租户归因                                          | 超限触发隔离而非全局限流连坐                   | 单租户打满拖垮全平台                 |

**铁律**：tenant_id 永远由系统注入，不接受 LLM/客户端传入的值覆盖。任何工具/查询若 tenant_id 来自不可信来源，一律拒绝并记审计。

### 13.5 租户与资源配额

- 租户实体含 `isolation_mode`（MVP=shared_db，预留 schema_per_tenant / db_per_tenant）。
- 每租户配额：并发 workflow、单日 token、单日成本、记忆条数、RAG 文档数。
- 超配额：新任务入队等待或拒绝并告警，不静默失败；配额计数原子化（Redis incr）。

### 13.6 防越权

- **tenant_id 注入而非传入**：LLM/客户端提供的 tenant_id 一律忽略；以令牌解析出的 tenant_id 为准。API 网关鉴权后覆盖 `Tenant-Context`，下游只信任该 header。
- **IDOR 防护**：所有按 id 查询的接口必须同时校验 `tenant_id` 匹配，不匹配返回 404（避免枚举）。
- **跨租户引用检测**：工具/Agent 若入参引用了别的租户的 entity_id（如 supplier_id、memory_id），ToolExecutor 检测到引用对象不属于当前租户时拒绝，记 Bad Case。
- 记忆检索结果在进上下文前再校验一次归属。

### 13.7 向量库多租户实现

MVP：单表 `memory_records(embedding vector(1024))` + `tenant_id`：

```sql
SELECT memory_id, content, embedding <=> $1 AS dist
FROM memory_records
WHERE tenant_id = $2 AND status IN ('active','consolidated')
ORDER BY embedding <=> $1 LIMIT $3;
```

- HNSW 索引 on `embedding`；`tenant_id` 过滤在索引后做（MVP 数据量可接受）。
- 后续高敏感租户切独立 collection 或 `schema_per_tenant`，避免召回受跨租户数据影响。
- 禁止出现不带 `tenant_id` 的向量检索路径。

---

## 14. 审批与风险策略

### 14.1 风险等级

低风险：读取数据、总结趋势、生成草稿、计算利润、提出建议。  
中风险：创建 Listing 草稿、生成客服回复草稿、推荐价格、推荐供应商、**自主研究深化、Listing 自批判重写、Bad Case 隔离与降级**（需记录但可自动执行）。  
高风险：发布 Listing、修改价格、应用促销、发送客服回复、预留库存。  
关键风险：发起退款、发放补偿、下架商品、推荐大额供应商订单、未来接触真实凭证/真实平台写操作。

### 14.2 审批规则

- 低风险动作可自动执行。
- 中风险动作可执行，必须记录并可见（含自主决策与 Bad Case 记录）。
- 高风险动作执行前必须审批。
- 关键风险动作必须审批并要求更强理由。
- 被拒绝动作必须停止或改走其他路径，可触发 Planner 重规划。

### 14.3 自主决策的护栏（新增）

- 自主决策只在低中风险区间生效；任何高风险一律人工。
- 所有循环（研究深化、自批判、重规划、Bad Case 重试）有最大次数硬上限，防死循环。
- abort/reroute 必须有理由并记为 AgentDecision。
- 自主决策不得绕过 Critic 评审与审批。

### 14.4 策略示例

- 贡献利润率 < 15%：阻断发布并要求人工审核。
- Listing 含健康/安全/医疗/保证结果声明：阻断发布。
- 退款金额超阈值：必须审批。
- 客服工单情绪极端或含法律威胁：必须升级。
- 供应商质量评分 < 阈值：必须管理者审批。
- 同一 CritiqueLoop 超 3 轮：升级人工。
- 研究深化超 2 轮仍证据不足：建议 abort。
- 检测到 prompt injection 或记忆投毒：隔离 + 记 Bad Case。

---

## 15. 评估体系

### 15.1 评估维度（保留并新增自主/Bad Case 维度）

选品研究：证据覆盖度、竞品相关性、评论痛点提取质量、机会评分一致性、**证据深化决策质量**。  
利润分析：计算正确性、假设可见性、敏感性分析完整度。  
Listing 生成：平台规则符合度、SEO 关键词、声明安全性、本地化质量、必填字段完整度、**自批判重写后是否真的改善了分数**。  
客服售后：是否基于订单和政策数据、语气质量、退款安全性、升级判断正确性、**客户记忆命中准确性**。  
工作流整体：工具调用成功率、人工审批正确性、完成耗时、单次 workflow 成本、从模拟失败中恢复的能力。  
**自主决策（新增）**：go/no-go 是否合理、自批判是否收敛、重规划是否有效、记忆命中是否相关、上下文压缩是否丢关键信息。  
**Bad Case（新增）**：检测召回率、处理决策正确性、隔离有效性、沉定转化率、回归集是否防住回归。  
**多租户隔离（新增）**：跨租户访问被阻断率、越权尝试告警率。

### 15.2 评估实现

- 结构化字段：规则校验器（确定性优先）。
- 确定性计算：单元测试。
- LLM-as-judge：仅评软维度（语气、Listing 可读性），带固定 rubric + few-shot，降低波动。
- 黄金测试场景：5–10 个。
- Agent 工作流回归测试。
- **BadCaseDataset 作为 CI 回归门禁**：新代码导致旧 Bad Case 复现即阻断合并。
- 评估结果展示页面，含自主决策评分。
- **评估回流**：评估结果写入记忆与 LearningRule，形成自改进闭环。

### 15.3 成功指标

- 用户可完成完整商品启动工作流。
- 至少使用三个 mock marketplace adapter。
- 高风险动作无法绕过审批。
- 主要 Agent 动作都有 trace，自主决策都有 AgentDecision。
- Listing 有校验分数与评估分数，且自批判后分数可证明上升。
- 工具失败可重试或清晰展示，持续失败可重规划。
- 系统可回放一次保存过的 workflow。
- **跨工作流记忆可验证生效**（第一次标记→第二次自动命中）。
- **多租户隔离可验证**：跨租户读取/检索被阻断并告警。
- **Bad Case 可沉淀可回归**：至少一个 Bad Case 进入数据集并在 CI 跑通。

---

## 16. 可观测性与审计

### 16.1 Trace 要求

每次 workflow run 记录：workflow_id、trace_id、parent_trace_id、Agent 名称、模型名称、prompt 版本、工具版本、输入/输出摘要、token 用量、成本估算、延迟、工具调用、错误、审批状态、**决策类型与理由、记忆命中、上下文压缩记录、CritiqueLoop 迭代、Bad Case 记录、隔离动作**。所有记录带 tenant_id。

### 16.2 可观测性页面

- 工作流时间线（含循环边与决策分支）。
- **决策时间线**：按时间排列的 AgentDecision，含理由与备选。
- **记忆面板**：本工作流写入/检索了哪些记忆、来源、相关性分数。
- **上下文压缩日志**：每步压缩前后 token、保留的 evidence ID。
- **Bad Case 面板**：检测/隔离/处理/沉定记录。
- 工具调用表、审批历史、评估结果、成本与延迟汇总、错误与重试历史、Workflow 回放。

### 16.3 审计日志规则

审计日志 append-only，带 tenant_id。敏感 payload 摘要化或脱敏。高风险动作保存足够信息让审核者理解发生了什么，但不暴露凭证或不必要隐私。记忆访问全部进审计。跨租户访问尝试记安全事件。

---

## 17. 前端需求

### 17.1 主要页面

Dashboard：活跃工作流、待审批动作、最近错误、成本汇总、平台健康状态。  
Workflow Builder：商品想法输入、平台选择、目标市场、风险偏好、可选供应商。  
Workflow Detail：状态机进度（含循环边）、Agent 输出、证据链接、工具调用、评估分数、重试控制、**决策时间线**。  
Approval Center：待审批高风险动作、Listing 发布/价格修改 diff、Agent 理由、风险报告、批准/拒绝/要求修改。  
Listing Workspace：多平台草稿、校验结果、本地化视图、**CritiqueLoop 迭代 diff**、版本历史。  
Operations Monitor：模拟运营指标、库存、评论、优化建议。  
Support Desk：模拟客服工单、订单详情、回复草稿、退款审批流程、**客户记忆引用**。  
Observability：Trace、工具调用、评估结果、成本与延迟、Workflow 回放、**记忆面板、上下文压缩日志、Bad Case 面板**。  
Tenant Admin：租户配额与用量、凭证状态、成员与角色、审计导出。

### 17.2 交互原则

- 第一屏是可工作的运营 Dashboard，不是营销首页。
- Agent 进度用结构化工作流 + 决策时间线展示，不只聊天记录。
- 审批动作明显可理解；证据、风险信号、记忆引用靠近 Agent 建议。
- 循环与决策分支在状态机视图里可见。
- 失败状态明确，并提供恢复/重规划方式。

---

## 18. 后端需求

### 18.1 推荐技术栈

- 后端 API：Python + FastAPI。
- Agent 编排：LangGraph（本版选定 LangGraph，理由见 18.2）。
- 数据库：PostgreSQL。
- 向量检索：pgvector（减少基础设施数量，记忆与 RAG 共用）。
- 队列：Celery / Dramatiq / RQ + Redis。
- 前端：Next.js（全栈，减少边界开销）。
- 可观测性：先自建 trace/决策/记忆/Bad Case 表，后续接 LangSmith 或 OpenTelemetry。
- 测试：pytest、Playwright、工作流回归 fixtures。

### 18.2 LangGraph 项目结构（更新）

LangGraph 官方部署规范要求应用至少包含一个或多个 graph、`langgraph.json`、依赖声明（`pyproject.toml`）、可选 `.env`。本版采用"官方基础结构 + 多工作流包"，每个工作流 graph 内部实现 Planner/Executor/Critic 三角与 CritiqueLoop。

```text
backend/
├── .env.example
├── Dockerfile
├── docker-compose.yml
├── langgraph.json
├── pyproject.toml
├── migrations/
├── scripts/
├── src/app/
│   ├── api/{app.py,dependencies.py,routes/}
│   ├── adapters/          # Marketplace/物流/支付协议与 mock 实现
│   ├── domain/            # 业务实体、枚举、值对象
│   ├── guardrails/        # 输入/输出/检索/敏感信息/Bad Case 防护
│   ├── memory/            # 记忆写入/检索/巩固/遗忘
│   ├── context/           # 上下文组装/summarization/压缩
│   ├── multitenancy/      # tenant 注入/配额/防越权（新增）
│   ├── mock_data/         # 版本化演示数据
│   ├── models/            # LLM/Embedding/Reranker/Vision/ImageGen provider
│   ├── observability/     # Trace/决策/记忆/压缩/Bad Case recorder
│   ├── persistence/{checkpointer.py,store.py,repositories/}
│   ├── prompts/
│   ├── rag/support/
│   ├── runtime/{container.py,context.py}
│   ├── schemas/
│   ├── services/
│   ├── tools/
│   │   ├── catalog/
│   │   ├── executor.py    # 权限/审批/幂等/重试/审计/记忆/压缩/Bad Case 统一入口
│   │   ├── registry.py
│   │   └── schemas.py
│   └── workflows/
│       ├── product_launch/
│       │   ├── graph.py      # 装配三角 + CritiqueLoop + 决策闸门 + Bad Case 隔离边
│       │   ├── state.py       # 含 shared scratchpad
│       │   ├── edges.py       # 纯路由，含循环边
│       │   └── nodes/
│       ├── operations/
│       ├── support/
│       └── retrospective/
├── evals/{datasets/,evaluators/,run_evals.py}
└── tests/{unit/,contract/,integration/,e2e/}
```

目录职责约束：

- `graph.py` 只负责 StateGraph 装配/编译/导出，不承载业务计算。
- `state.py` 只保存可序列化业务状态与 reducer，含 shared scratchpad；不存客户端/密钥。
- `edges.py` 无副作用纯路由；循环次数由 state 字段计数约束。
- `nodes/` 通过构建期依赖注入调 service/tool，每节点只返回 state 增量。
- 短期 thread 状态由 checkpointer 管理；跨 thread 长期记忆由 store + memory 模块管理；审批、审计、记忆访问日志、Bad Case 独立持久化。
- 所有外部副作用经 `ToolExecutor`，统一执行租户隔离、权限、审批、schema、幂等、重试、记忆、压缩、Bad Case 与脱敏 trace。
- `langgraph.json` 导出产品发布、运营、客服、复盘四张业务图。
- `pyproject.toml` 是唯一依赖真源；测试按 unit/contract/integration/e2e 分层，质量门禁在 `evals/`。

### 18.3 大模型与 RAG 模型配置

MVP 暂用硅基流动 SiliconFlow，不硬编码 API key。

- LLM provider：siliconflow，base_url：<https://api.siliconflow.cn/v1，model：deepseek-ai/DeepSeek-V3.2，OpenAI-compatible。>
- Embedding：BAAI/bge-m3，dim 1024。
- Reranker：BAAI/bge-reranker-v2-m3。
- Vision：Qwen/Qwen3-VL-32B-Instruct（预留，用于图片合规自检 `verify_image_compliance`）。
- 文生图（ImageGen）：可接硅基流动等价文生图/外部 diffusion（如 Kolors / FLUX），按"张"计费而非 token；MVP 可先用占位生成器或 mock 返回预置素材，保持接口与成本口径一致。

### 18.3.1 模型分级调度（新增）

差异化控制 Token 成本：按任务复杂度把推理路由到不同档位模型，而非全程用主力大模型。

- **小模型档（轻量）**：简单问答、客服回复草稿初稿、工具结果摘要、低风险的单轮解释。用便宜、低延迟的小模型（如 Qwen2.5-7B-Instruct / 硅基流动等价小模型）。
- **主力大模型档**：Planner 任务规划与 go/no-go、多工具编排决策、Critic 评审与放行/重写/升级、Listing 生成与自批判重写等需要强推理与多步规划的任务。使用 DeepSeek-V3.2。
- **路由规则（model router）**：由 `ModelRouter` 按"任务类型 + 风险等级 + 是否多工具"决定档位；默认低中风险单域任务走小模型，规划/评审/多工具/高风险一律走主力模型；§20.3 的降级"切便宜模型"与本调度共用小模型档。
- **成本差异**：小模型档 token 单价与延迟显著低于主力档；Dashboard 按档位展示成本占比。
- **生图成本档（独立计费）**：图片生成为文生图模型调用，按"每张/每套"计费，不计入 LLM token；在成本看板中与 LLM 档位分离展示，单次 Listing 生图成本纳入 §21.4 的 per-task 预算。

环境变量：SILICONFLOW_API_KEY / SILICONFLOW_BASE_URL / LLM_MODEL / EMBEDDING_MODEL / RERANKER_MODEL / VISION_MODEL。  
配置要求：API key 只放 .env / secret manager；PRD/README/trace 不得出现明文 key；统一配置；测试默认 mock provider。

建议参数：LLM temperature 普通解释 0.7、结构化/风险/规划 0.2–0.3；max_tokens 默认 4096；RAG initial_top_k 20、rerank_top_k 5、max_refinements 3、score_threshold 0.3 起。  
记忆检索：top_k 10、min_score 0.3、按 entity_type/marketplace/category 过滤（tenant_id 强制注入）。

### 18.4 客服 Agent RAG 设计

客服 Agent 采用"业务工具 + RAG 知识库 + 客户记忆 + 风险审核"组合。  
知识库集合：tenant_policy_docs / marketplace_policy_docs / product_docs / faq_docs / logistics_docs。  
检索 metadata：tenant_id / marketplace / locale / document_type / product_id / policy_version / effective_from / permission_scope。  
流程：Planner 判断是否需 RAG → Retriever metadata 过滤 → 向量+reranker → 去重保留来源 → 评估证据是否充分（不足转人工）→ 生成带来源引用草稿 → Critic 检查过度承诺/违规补偿/政策冲突/情绪升级。

验收：商品咨询能引用商品说明/FAQ；退换货/保修能引用政策版本；跨平台按 marketplace 过滤；跨租户严格隔离；无依据转人工；检索/rerank/来源全部记 trace。

### 18.5 API 设计

在 v0.2 基础上新增记忆、决策、上下文、Bad Case、租户端点：

- POST /workflows / GET /workflows / GET /workflows/{id} / POST /workflows/{id}/cancel / retry / GET .../trace / .../evaluations
- GET /approvals / POST /approvals/{id}/approve / reject
- GET /listings / GET /listings/{id}
- GET /support-tickets / POST /support-tickets/{id}/draft-response
- GET /marketplaces / GET /marketplaces/{id}/rules
- **GET /workflows/{id}/decisions**（决策时间线）
- **GET /workflows/{id}/memory**（本工作流写入/检索的记忆）
- **GET /workflows/{id}/context**（上下文压缩日志）
- **GET /workflows/{id}/bad-cases**（Bad Case 记录）
- **GET /tenants/{id}/memory**（记忆库浏览，受权限）
- **GET /tenants/{id}/quota**（配额与用量）
- **GET /tenants/{id}/bad-case-datasets**（数据集与回归状态）

所有端点从令牌解析 tenant_id，不接受客户端传入；IDOR 一律返 404。

### 18.6 异步执行

API 创建 workflow 并入队；Worker 执行工作流图；UI 轮询/订阅看进度；每步持久化；失败局部重试；重规划由 Planner 在图内完成；Bad Case 隔离态暂停等待处理。

### 18.7 幂等性

写操作工具必须用 idempotency_key：publish_listing / update_price / update_inventory / issue_refund / create_promotion / write_memory / record_bad_case / quarantine_artifact。

---

## 19. 安全与风控

### 19.1 权限控制

角色：Admin / Operator / Reviewer / Support / Read-only observer。  
权限：workflow:create/read/cancel、approval:review、listing:publish、price:update、refund:issue、support:respond、observability:read、memory:read/write、bad_case:review。  
所有权限校验在 `ToolExecutor` 与 API 层双重执行；`permission_scope` 限定作用范围。

### 19.2 LLM 安全边界

- LLM 不接收密钥。
- LLM 不直接写外部系统。
- 工具层校验输入并执行权限控制。
- 工具输出涉敏感信息时摘要化。
- Agent 生成内容在执行前必须校验。

### 19.3 Prompt Injection 与记忆投毒风险

潜在攻击面：供应商描述����品 Listing 文本、评论内容、客服消息、上传文档。  
缓解：外部文本当不可信数据；系统指令与检索内容分离；schema 化工具与权限控制；执行前验证输出；高风险动作审批。  
**记忆投毒（新增）**：外部数据不得直接作为可信记忆写入，须先经 Critic 评审与脱敏；可疑模式（如供应商描述里夹带"本店最优"类声明）不入 semantic 记忆，记 Bad Case。

### 19.4 多租户隔离铁律

- tenant_id 永远由系统注入（网关鉴权后覆盖 `Tenant-Context`），不接受 LLM/客户端传入。
- 工具函数签名不含 tenant_id 参数，由 ToolExecutor 注入到工具内部上下文。
- 任何工具若试图从入参 payload 读取 tenant_id，视为越权，记 Bad Case。
- 所有按 id 查询必须校验 tenant_id 匹配（不匹配返 404 防枚举）。

### 19.5 密钥与成本隔离

- 凭证存 secret manager，key 形如 `tenant/{tenant_id}/marketplace/{marketplace}`，不进 LLM 上下文或 trace 明文。
- 每租户 token/cost 配额；超限：新 workflow 拒绝，已运行 workflow 降级或暂停（可配）。
- 成本归因到 `(tenant_id, workflow_id)`；Dashboard 按租户展示。

### 19.6 执行与队列隔离

- 每租户并发上限与优先级；超额进租户队列等待。
- worker 不缓存跨 workflow/跨租户的可变对象；所有状态从 DB/Redis/图 state 读取。

---

## 20. Bad Case 处理（新增章）

### 20.1 分类法（八类）

| 类别      | 典型场景                              | 严重度 | 检测                               | 默认处理                 |
| ------- | --------------------------------- | --- | -------------------------------- | -------------------- |
| A 输入异常  | prompt injection、超长、非法 schema、PII | 高   | input guardrail + 注入分类器          | 拒绝/脱敏后重试             |
| B 输出失控  | 幻觉/编造（编造物流）、schema 不符、夸大声明、漂移     | 高   | output schema 校验 + Critic + eval | CritiqueLoop 重写/升级人工 |
| C 计算异常  | 除零、缺失输入、负利润、单位错误                  | 中   | 确定性计算抛错                          | 暂停要用户补输入/重规划         |
| D 工具失败  | adapter 错误、限流、超时、幂等冲突             | 中   | 工具返回错误码                          | 指数退避重试/重规划/跳过平台      |
| E 流程异常  | 循环死循环、审批拒绝、重规划失败、状态机卡死            | 高   | 循环计数 + 超时看门狗                     | 强制升级人工/abort         |
| F 记忆异常  | 记忆投毒、膨胀、检索不相关、记忆冲突                | 高   | Critic 评审 + 记忆访问监控               | 隔离不入 semantic/巩固/遗忘  |
| G 上下文异常 | token 超预算、压缩丢关键信息、上下文污染           | 中   | token 预算 + 压缩前后校验                | 额外压缩/裁剪/升级人工         |
| H 业务违规  | 违规 Listing、高风险供应商、客服过度承诺、退款异常     | 高   | Critic + 规则校验 + 运营异常检测           | 阻断 + 审批              |

### 20.2 纵深检测层

```
输入 guardrail ──▶ LLM/工具 ──▶ 输出 schema 校验 ──▶ Critic 评审 ──▶ eval/规则 ──▶ 审批 ��─▶ 审计
   (A)            (C/D)         (B)                (B/H)        (H)        (H)        (全)
```

- **input guardrail**：schema 校验、长度上限、PII 检测、prompt-injection 分类器（把供应商描述/评论/客服消息当不可信）。
- **output 校验**：结构化输出 schema 校验（JSON Schema / Pydantic），不符则进 CritiqueLoop 自修复。
- **Critic**：夸大声明、缺失字段、平台规则冲突、客服过度承诺、记忆不相关。
- **eval/规则校验器**：确定性规则（利润阈值、必填字段、声明黑名单）。
- **异常检测**：运营指标异常（转化骤降、退货激增）。
- **记忆访问监控**：检索相关性长期偏低、命中频次异常、跨租户命中。

### 20.3 处理策略

| 策略            | 适用      | 行为                      |
| ------------- | ------- | ----------------------- |
| 隔离 quarantine | A/B/F/H | 产出物进隔离区，不发布、不写记忆        |
| 降级 fallback   | C/D/G   | 切确定性兜底/便宜模型/人工          |
| 重试            | D（临时）   | 指数退避 + 幂等 key           |
| 重规划           | D/E     | Planner 改路径（换供应商/跳平台）   |
| 拒绝            | A/E/H   | 终止并记录原因                 |
| 升级人工          | B/E/H   | 转审批/人工处理                |
| 回滚            | E/H     | workflow state 回退到上一稳定点 |

per-class 默认决策见 §20.1 最后一列；具体由 Critic/Planner 在护栏内决策，高风险一律人工。

### 20.4 Bad Case 状态机

```
            检测命中
               │
               ▼
        ┌─────────────┐
        │  detected   │
        └─────────────┘
               │ 分类 + 严重度评估
               ▼
        ┌─────────────┐
        │ quarantined │  (隔离，不发布/不写记忆)
        └─────────────┘
               │ 分诊
   ┌───────────┼───────────┬───────────┐
   ▼           ▼           ▼           ▼
 retry     reroute     escalate     abort
 (D临时)   (重规划)     (人工)      (终止)
   │           │           │           │
   └───────────┴───────────┴───────────┘
               │
               ▼
        ┌─────────────┐
        │  resolved   │  (处理完成，记 outcome)
        └─────────────┘
               │ 评估是否可复用
               ▼
        ┌─────────────┐
        │ sink to    │  (进 BadCaseDataset 黄金回归集)
        │  dataset   │
        └─────────────┘
```

- 每个 bad case 必有 `outcome`（resolved/escalated/aborted）与 `handling` 记录。
- 进入 dataset 的 bad case 在 CI eval 门禁里作为回归用例，防回归。

### 20.5 沉淀与回归闭环

- 检测到的 bad case 一律写 `BadCase` 记录。
- 高价值 bad case（如新发现的注入模式、新幻觉类型）经评审后进 `BadCaseDataset`，成为黄金回归场景。
- CI eval 门禁跑 `BadCaseDataset`；新代码导致旧 bad case 复现即阻断合并。
- 与记忆自改进闭环联动：反复出现的 bad case 模式沉淀为 `LearningRule`（如"该类目 Listing 易出现 X 声明"）。
- **Few-Shot 示例回流**：高价值 bad case 与修正后的正确样例，经评审后补充进对应任务的 Few-Shot 示例库（`prompts/` 下按任务分文件），下一轮同类任务直接携带，形成 prompt 级迭代闭环；入库须脱敏且去掉租户敏感信息。
- **微调数据集沉淀**：反复出现、且非单纯规则可覆盖的模式（如特定类目声明偏差、特定客服话术失误），沉淀进微调数据集（区别于 `BadCaseDataset` 的 eval 回归用途），供后续可选 SFT 提升基线准确率；微调数据集默认租户私有，脱敏后可贡献公共集。

### 20.6 红队 seed 场景

预置 bad case seed，强制系统暴露弱点并验证护栏：

- 评论里夹带"ignore previous instructions"类注入。
- 客服工单诱导 Agent 编造物流状态。
- Listing 草稿含"保证治愈""100% 不坏"夸大声明。
- 供应商描述里夹带"本店全网最优"等不可信声明（测记忆投毒）。
- 利润输入缺失关键字段（测计算异常 + 重规划）。
- adapter 持续返回 503（测重规划 + 跳过平台）。
- 同一 CritiqueLoop 触发第 4 轮（测升级人工）。
- 跨租户引用别的租户的 supplier_id（测防越权 + Bad Case）。

---

## 21. 工程化补充（新增章）

### 21.1 CI/CD 与 eval 门禁

- CI：lint + unit + contract + integration；e2e 在合并前跑。
- eval 门禁：`evals/run_evals.py` 在 CI 跑黄金场景 + BadCaseDataset，分数低于阈值阻断合并。
- Docker build 自动化；Docker Compose 保证作品集可复现。
- 隔离扫描：CI 跑"无 tenant_id 查询扫描"，命中即失败。

### 21.2 Demo 健壮性

- 为演示关键路径预生成/缓存确定性 LLM 输出兜底，避免现场 LLM 方差或限流翻车。
- 演示用 seeded mock data；演示前可一键 reset。
- 演示脚本固定场景（可折叠床底收纳箱），每步预期输出预先校验。

### 21.3 Mock 数据生成器

Mock 数据质量决定 Demo 观感。要求：版本化、可 reset、覆盖 2-3 个类目；竞品/评论/关键词/供应商/订单/工单均有生成器；数据间外键一致；含"故意有风险"的样本（如含夸大声明的评论、高风险供应商）以触发护栏与记忆；含红队 seed。

### 21.4 成本预算

- 单次完整工作流预估 token 与成本（含上下文压缩节省）；**生图按"每套图片"单列成本**，不计入 token 预算。
- 记忆巩固批处理在低峰跑。
- 成本超阈值告警；按租户归因与限额。

---

## 22. MVP 开发里程碑

里程碑 1：基础设施

- DB schema（含记忆/决策/上下文/隔离/Bad Case 新表）、mock 数据 seed、租户与用户、Marketplace adapter 接口 + 三个 mock、带风险元数据的工具注册中心、tenant_id 注入中间件。

里程碑 2：三角编排与自主决策

- product_launch graph（Planner/Executor/Critic）、go/no-go 闸门、研究深化、CritiqueLoop、工作流持久化、共享 scratchpad。

里程碑 3：记忆与上下文

- 记忆模块（write/retrieve/consolidate）、上下文组装/summarization/压缩、记忆访问日志、token 预算、pgvector 多租户过滤。

里程碑 4：审批与执行

- ApprovalRequest、审批中心、模拟发布、幂等性、审计日志、重规划路由。

里程碑 5：运营、客服与 RAG

- 模拟运营数据、客服工单、Support Agent + RAG + 客户记忆、退款审批。

里程碑 6：可观测性、评估与 Bad Case

- 决策时间线、记忆面板、上下文压缩日志、成本汇总、评估回流、Bad Case 检测/隔离/数据集、CI 回归门禁、回放。

里程碑 7：工程化与作品集打磨

- CI + eval 门禁 + 隔离扫描、Demo 缓存、Docker Compose、README、截图、面试讲解要点。

---

## 23. MVP 验收标准

- 用户可从 Dashboard 创建商品启动 workflow。
- Planner 在启动时加载并命中相关记忆（trace 可见，记忆属本租户）。
- 系统生成带证据的选品分析，证据不足时自主触发深化研究。
- 系统通过确定性工具计算利润。
- 系统评估供应商并命中供应商风险记忆。
- 系统为至少三个 mock 平台生成 Listing。
- Listing 被 Critic 评审，不通过时进入 CritiqueLoop，重写后分数可证明上升。
- 顶层 go/no-go 决策存在且可追溯。
- 审批通过后 mock 发布并生成 listing_id。
- 发布后监控模拟表现。
- 客服 Agent 能为工单生成有依据回复，并引用客户记忆。
- 退款/补偿需审批。
- Workflow detail 展示 trace、决策时间线、记忆、上下文压缩、工具调用、审批与评估。
- 失败 adapter 调用可重试，持续失败可重规划。
- 评估结果回流写入记忆，下一工作流可命中。
- **多租户隔离可验证**：跨租户读取/检索被阻断并告警，至少两个种子租户。
- **Bad Case 可沉淀可回归**：至少一个 Bad Case 进入数据集并在 CI 跑通。
- Demo 数据可重置并回放。

---

## 24. 推荐 Demo 场景

推荐商品：可折叠床底收纳箱。  
Demo 流程：

1. 创建任务：面向美国和英国市场，"可折叠床底收纳箱"，选 MockAmazon/MockShopify/MockTikTokShop。
2. Planner 加载记忆（命中"收纳类在 TikTok 转化偏低"的历史记忆）。
3. Research Agent 发现需求信号与竞品痛点，证据评分偏低 → Planner 自主触发第二轮研究。
4. Profit 工具计算到岸成本与利润率。
5. Supplier Agent 推荐主/备供应商，命中历史高风险供应商并降权。
6. Planner go/no-go：proceed。
7. Listing Agent 生成三个平台草稿。
8. Critic 标记一个无证据声明 → CritiqueLoop 重写 → 通过。
9. 用户审批发布。
10. Mock adapter 发布。
11. Ops 发现某平台转化偏低，建议优化（高风险动作审批）。
12. Support Agent 为物流延迟工单生成回复，引用客户历史工单记忆。
13. Retrospective 总结，并把本次评估与决策回流写入记忆。

附加 Bad Case demo：红队 seed 评论夹带注入 → input guardrail 拦截 → 记 Bad Case → 隔离 → 沉定进数据集 → CI 回归跑通。

---

## 25. 工程风险

范围膨胀：风险——Agent/记忆/上下文/协作/隔离/坏例全做不完。缓解——严格按"最小惊艳集"，Localization 并入 Listing、Ops 简化、Supervisor 改 Planner。  
自主决策失控：风险——循环死循环或乱 abort。缓解——循环硬上限、决策护栏、CritiqueLoop 最多 3 轮、研究深化最多 2 轮。  
记忆膨胀/投毒：风险——记忆无限增长或被外部数据污染。缓解——巩固+遗忘、Critic 评审后才能写入 semantic 记忆、Bad Case 隔离。  
上下文压缩丢关键信息：风险——摘要丢证据。缓解——保留 evidence ID、压缩前后 token 记录、可回放全量。  
**多租户越权**：风险——跨租户数据/记忆/凭证串用。缓解——tenant_id 注入铁律、十层隔离矩阵、IDOR 校验、隔离扫描 CI 门禁。  
**Bad Case 漏检/不沉淀**：风险——同类错误反复出现。缓解——纵深检测层、强制 BadCase 记录、CI 回归门禁、红队 seed。  
Demo 翻车：风险——现场 LLM 方差/限流。缓解——关键路径预生成输出兜底。  
过度依赖 LLM：风险——数学/规则/记忆判断不可靠。缓解——计算与规则走确定性服务、记忆检索走确定性向量。  
看起来像聊天机器人/固定脚本：风险——面试官认为只是 prompt wrapper 或死流程。缓解——突出三角回路、决策时间线、记忆面板、自批判 diff、Bad Case 闭环。  
评估质量主观：风险——LLM-as-judge 波动。缓解——确定性优先、固定 rubric + few-shot、黄金场景。  
真实 API 接入拖慢：风险——账号/权限/审核。缓解——先 mock，保留真实接入边界。

---

## 26. 待确认问题

- 记忆巩固批处理是定时跑还是工作流结束触发？建议低峰定时 + 工作流结束触发增量。
- 遗忘策略是纯 TTL 还是相关性衰减？建议两者结合，高风险记忆衰减更慢。
- CritiqueLoop 与人工审批的边界：重写 3 轮未过升级人工，是否还需管理者二次审批？建议默认升级即人工。
- 记忆向量库用 pgvector 还是 Qdrant？建议 pgvector 减少基础设施。
- 部署优先 Docker Compose（作品集可复现）还是云服务？建议 Docker Compose。
- 多租户隔离 MVP 用 shared_db 够吗？建议 shared_db + 强制 tenant_id 过滤，高敏感租户后续切 schema。
- Bad Case 数据集是否对外可见？建议默认租户私有，跨租户脱敏后可贡献到公共回归集。

---

## 27. 后续路线图

Phase 2：真实数据导入（CSV、供应商报价表、平台导出、政策/商品文档向量化）。  
Phase 3：真实平台连接器（优先 Shopify，真实发布仍强制审批）。  
Phase 4：高级运营（多国家定价、汇率、税费关税、物流 SLA、退货率预测）。  
Phase 5：增长与广告（关键词扩展、广告草稿、预算 guardrails、短视频/图片 brief）。  
Phase 6：团队与企业能力（多人审批、角色化策略、组织级审计导出、定时监控、webhook 与告警、记忆治理、多租户计费、高敏感租户独立 schema）。

---

## 28. 面试讲解要点

- 为什么 Agent 应通过工具执行动作，而不是直接访问外部 API。
- Marketplace adapter 如何隔离平台差异。
- 为什么利润计算与规则检查做成确定性服务。
- 人工审批如何降低真实业务风险。
- **为什么本版从"固定状态机"升级为"Planner/Executor/Critic 三角 + 决策闸门 + 自批判 loop"，自主性体现在哪。**
- **记忆如何跨工作流学习（供应商风险/类目表现），如何防投毒与遗忘。**
- **上下文压缩如何让 Agent 在 12 步链路不失忆、不爆 token。**
- **多 Agent 协作如何通过生成-评审-重写回路体现真实协作而非串行交接。**
- **多租户十层隔离矩阵与"tenant_id 一律系统注入"的铁律如何防越权，向量库如何多租户。**
- **Bad Case 处理闭环：检测→隔离→沉定→CI 回归，如何让系统越用越稳而非反复踩同一坑。**
- 可观测性如何让 Agent 行为可调试（决策时间线、记忆面板、压缩日志、Bad Case 面板）。
- 评估体系如何防回归，且评估结果如何回流成记忆形成自改进。
- 幂等性与重试对外部写操作为何重要。
- mock adapter 如何让 MVP 快速开发，同时保留生产扩展边界。
- 如何从 MVP 演进到真实生产系统，而不是推倒重来。

---

## 29. 参考资料

- LangGraph：<https://docs.langchain.com/oss/python/langgraph/overview>
- LangGraph Application structure：<https://docs.langchain.com/oss/python/langgraph/application-structure>
- LangGraph Thinking in LangGraph：<https://docs.langchain.com/oss/python/langgraph/thinking-in-langgraph>
- LangGraph Subgraphs：<https://docs.langchain.com/oss/python/langgraph/use-subgraphs>
- LangSmith：<https://docs.smith.langchain.com/>
- OpenAI Agents SDK：<https://openai.github.io/openai-agents-python/>
- Amazon Selling Partner API：<https://developer-docs.amazon.com/sp-api/>
- Shopify Admin GraphQL API：<https://shopify.dev/docs/api/admin-graphql/latest>
- TikTok Shop API concepts：<https://partner.tiktokshop.com/docv2/page/tts-api-concepts-overview>
