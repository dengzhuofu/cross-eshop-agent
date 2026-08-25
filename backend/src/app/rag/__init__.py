"""Agentic RAG 基础设施（M9）：分词 / 结构感知切块 / 混合检索 / 查询改写与分级。

设计文档见 docs/RAG_DESIGN.md。本包全部离线可运行：BM25 与确定性改写/分级
不依赖网络；嵌入引擎跟随 .env（无 key 自动降级 app.llm.embeddings 的 hash 引擎）。
"""
