# Agentic RAG 设计（M9）

> 适用范围：客服 agent 的五类知识检索 + 主链路 planner/listing 的 ops_playbook 运营知识检索。
> 设计铁律与全项目一致：**LLM 只提议，代码做硬保证**；**客观事实可从 RAG 检索，实时数据必须调工具**；**离线（无 API key）全链路可运行**。

## 1. 为什么升级

M6/M8 版本的检索是「单轮直查」：固定查询串 → 纯向量余弦 → top_k。三个已坐实的问题：

1. **查询质量依赖调用方**：客服工单是口语（"东西坏了想退怎么办"），直接拿去和文档算相似度，词面/语义双弱；
2. **纯向量单路脆弱**：兜底 hash 引擎早期只认 `[a-z0-9]+`，纯中文查询向量全零，离线检索退化为任意序（已修复分词，但词级语义缺口仍在，如「退换货」vs「退货」）；
3. **无质量闭环**：检索结果不分级、不重试，命中不相关也照单全收进 prompt。

升级后的形态是标准的 **Agentic RAG**：路由 → 改写 → 混合检索 → 分级 → 重试，每一步都有确定性兜底，LLM 只做增强。

## 2. 总体架构

```mermaid
flowchart TB
    subgraph ING["离线语料管道 scripts/crawl_helpcenter.py"]
        A1[真实帮助中心页面<br/>Shopify / eBay / Amazon] --> A2[html_to_sections<br/>标题栈结构解析]
        A2 --> A3[chunk_sections<br/>800字/120重叠/短节合并]
        A3 --> A4[embed_texts 批量嵌入]
        A4 --> A5[(knowledge_base<br/>meta.source=webcrawl)]
    end

    subgraph SEED["种子知识 scripts/seed_knowledge.py"]
        B1[knowledge_seed_data.py<br/>27 篇六类知识] --> A4
    end

    subgraph LOOP["Agentic 检索循环（客服 node_support）"]
        C1[路由分类<br/>_classify_route 确定性] -->|policy| C2[查询改写<br/>LLM 提议 / 确定性兜底]
        C2 --> C3[search_knowledge<br/>mode=hybrid grade=true]
        C3 --> C4[相关性分级<br/>LLM ∩ 确定性]
        C4 -->|相关命中=0 且轮数&lt;2| C2
        C4 -->|有相关命中| C5[rag_block 只装相关命中]
        C1 -->|realtime| C6[get_order_status 工具]
        C5 --> C7[回复草稿 + 融合铁律硬保证]
        C6 --> C7
    end

    subgraph RET["检索引擎 app/rag/retrieval.py"]
        D1[BM25<br/>k1=1.5 b=0.75] --> D3[RRF 融合<br/>k=60]
        D2[向量余弦] --> D3
        D3 --> C3
    end

    subgraph EVAL["评估体系 evals/rag_evals.py"]
        E1[黄金检索集 ≥18 条] --> E2[Recall@3/5 · MRR · hit_rate]
        E3[忠实度样本] --> E4[违禁声明检测器复用<br/>+ 引用合法性]
        E2 --> E5{--gate 门禁}
        E4 --> E5
    end

    A5 --> D2
    A5 --> D1
```

## 3. 语料获取：真实文档爬取

- **来源**：公开帮助中心页面（Shopify Help Center、eBay Seller Help、Amazon 公开帮助/定价页），共 10 页，全部真实内容，非手写模拟。清单与分类映射固化在 `scripts/crawl_helpcenter.py` 顶部 `PAGES` 常量。
- **礼貌抓取**：真实 UA、页间 1s 延迟、单页失败跳过不炸整批。
- **解析**：stdlib `html.parser`（无第三方依赖），剔除 script/style/nav/header/footer 子树，h1–h4 维护标题栈形成 `heading_path`。
- **入库契约**：
  - `ref = WEB-<SLUG>-<序号>`（如 `WEB-SHOPIFY_REFUNDS-01`）；
  - `meta = {source: "webcrawl", source_url, heading_path, chunk_index, lang}`；
  - 归属租户 `t_demo_acme`（与种子知识同租户，多租户铁律不变）；
  - **幂等重灌**：先 `repo.delete_knowledge_by_source(source="webcrawl")` 再插，重复执行不翻倍。

## 4. 切块策略（结构感知）

参数：`max_chars=800`、`overlap_chars=120`、过短节（<40 字符）并入下一节。

- **按结构切，不按固定窗口切**：标题栈把文档切成语义节，节内按段落贪心装块；超长单段按句号/换行硬切；
- **相邻块携带 120 字尾部重叠**：跨块的语义（如「条件……详见下文」）不因切块断裂；
- **每块独立嵌入、独立参与 BM25**：长文档单向量会稀释主题，这是「整篇单向量」旧方案的核心缺陷之一。

种子知识（27 篇短文档）保持整篇一块——它们本来就是按知识条目写的，长度在块尺寸内。

## 5. 嵌入与索引：双引擎契约

| | 在线引擎 | 兜底引擎 |
|---|---|---|
| 载体 | SiliconFlow `bge-m3`（OpenAI 兼容 /embeddings） | 确定性 hash 词袋（md5 分桶 + L2） |
| 分词 | 引擎内部 | `app/rag/tokenize.py`：拉丁词 + jieba CJK 词 + CJK 单字 |
| 适用 | 演示/开发（有 key） | 测试 / CI / 无网环境 |

**写入/查询同引擎契约**：种子与爬取语料的嵌入和运行时查询走同一 `embed_texts`，两侧相似度才可比。切换引擎（如换嵌入模型）必须全量重灌。

> 修复记录：hash 引擎曾只认 `[a-z0-9]+`，纯中文全零向量（离线检索退化为任意序）。现与 BM25 共用规范分词器，中文主语料离线可检索；单字层保证「退换货/退货」这类词面差异仍有召回。

## 6. 检索：混合双路 + RRF 融合

`app/rag/retrieval.py`：

- **BM25**（k1=1.5, b=0.75）：词面精确匹配主力，中文经 `tokenize` 分词后与本语料统计 df/avgdl；
- **向量余弦**：语义泛化路（在线引擎下是主力，hash 引擎下是辅助）；
- **RRF 融合**（k=60）：`score(d) = Σ 1/(60 + rank)`，两路排名融合，单路退化不拖垮整体——余弦全零时 BM25 独立支撑排序，这正是 hash 引擎离线场景的保底。

工具层 `search_knowledge` v2：`mode: "vector"|"hybrid" = "hybrid"`（旧调用零感知），命中带 `bm25` / `rrf` / `similarity` 三个可解释分数。

## 7. Agentic 检索循环（客服 node_support）

每一步「LLM 增强 + 确定性兜底」，无 key 时全链路仍可运行：

| 步骤 | LLM 增强 | 确定性兜底 |
|---|---|---|
| 路由分类 | — | `_classify_route`：关键词判 realtime / policy（退款工单两者都要） |
| 查询改写 | 把口语问题改写成检索查询（JSON 单字段） | `deterministic_rewrite`：去标点/停用词的减法改写 |
| 检索 | — | `search_knowledge(mode=hybrid, grade=true, top_k=5)` |
| 相关性分级 | 对命中判 relevant（与确定性取交集） | `deterministic_grade`：词面覆盖率 × 余弦合成，阈值可解释 |
| 重试 | — | 相关命中=0 → 换写法重查，最多 2 轮，全程留 `retrieval_trace` |

`rag_block` 只装「相关命中」，引用白名单同步收窄——检索质量直接决定进 prompt 的内容质量。

### 融合铁律（硬保证，代码强制，不靠 prompt）

1. **实时数据工具优先**：订单状态/物流 ETA 只认 `get_order_status` 工具结果；LLM 草稿中任何与工具 ETA 不一致的时效表述 → 整稿弃用，回退确定性模板；
2. **RAG 只出客观事实**：政策条款、平台规则、商品说明可引用；RAG 内容不得替代研究证据、不得据此输出绝对化承诺（listing 侧同款约束）；
3. **引用白名单**：草稿引用必须 ⊆ 本次实际检索命中的 ref，杜绝幻觉引用；
4. **租户隔离**：检索永远带 `tenant_id` 过滤（工具层注入，LLM 不可见），跨租户不可见。

## 8. 评估体系（`evals/rag_evals.py`，CI 门禁）

**检索质量**（黄金集 ≥18 条中文口语查询 → 期望 ref）：

- Recall@3 / Recall@5 / MRR / hit_rate@5，总体 + 按知识类别分表；
- 至少 1/3 条目查询词面与目标文档标题不重叠——专门考核混合检索双路价值；
- 门禁阈值按实测留 10–15 个百分点余量，低于阈值 `--gate` 非零退出。

**忠实度**（确定性，不调 LLM）：

- 含绝对化/违禁表述的答案样本必须被 M7 检测器命中，谨慎版答案必须不命中（检测器回归）；
- 引用合法性：`cited_refs ⊆ retrieved_refs` 判定函数。

**CI 接线**：backend job 在红队门禁后追加 `RAG gate` 步骤，检索质量或忠实度任一不过 → CI 红。

## 9. 与主链路 ops_playbook 的关系

planner / listing 节点经同一个 `search_knowledge` 工具检索 ops_playbook，混合检索升级对它们**自动生效**（工具层统一升级，调用方无需改动）；其「RAG 是参考、不替代研究证据、不解除绝对化禁令」的约束与 §7 融合铁律同源。

## 10. 已知边界与演进接缝

- **向量存储**：embedding 存 JSON 列、余弦进程内计算（演示规模 ≤ 千级块足够）；`pgvector` 在当前演示 PG 构建不可用，`repo.search_knowledge` 是唯一检索入口，换 pgvector 只动这一层；
- **重排**：RRF 是无监督融合，接 cross-encoder 重排只需在 `hybrid_rank` 后插一级；
- **语义缓存 × RAG**：M8 实测发现离线 hash 引擎与在线 bge-m3 检索结果不同 → prompt 不同 → 缓存 key 不同，RAG 依赖节点的缓存必然 miss（详见 PROGRESS.md M8 节）。全链路离线重放需预热与重放两侧同引擎；
- **评估**：黄金集是人工标注的小集合（作品集规模），接 RAGAS 类框架需要在线 LLM 判官，属后续演进。
