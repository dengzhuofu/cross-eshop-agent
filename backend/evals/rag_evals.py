"""M9 RAG 检索质量评估门禁（agentic RAG 的评估体系，PRD §13 / RAG_DESIGN.md §8）。

用法：
  python evals/rag_evals.py          # 全量报告（指标表 + 忠实度护栏 + 失配归因）
  python evals/rag_evals.py --gate   # CI 门禁：任一指标低于阈值或护栏失守 → exit 1

评估内容（全部 hermetic：无 key 自动 hash 嵌入，临时 SQLite，不连真实 PG）：
1. 检索质量：黄金查询集（rag_golden.py，种子五类 + ops_playbook + 真机爬取语料）
   走与线上一致的混合检索（BM25 词面 + 余弦语义 → RRF），统计
   Recall@3 / Recall@5 / MRR@5 / HitRate@5，整体一张表、分 category 一张表；
2. 混合检索健全性：hybrid 路径返回项必须带 rrf 可解释分数（缺失即门禁失败）；
3. 忠实度护栏：FAITHFULNESS_SAMPLES 过 M7 bad-case detector 注册表——
   夸大声明/投毒/注入样本必须被拦截，客观事实回答必须零命中。

阈值定标方法：首次全量实测得到基线后，取「基线 − 10~15pt 安全边际」作为门禁线——
门禁防的是回归（检索质量突然塌方），不是卡上限；改语料/改分词导致基线上升时
应重新定标而不是硬扛旧线。
"""

import argparse
import asyncio
import os
import sys
import tempfile
from pathlib import Path

# 与 tests/conftest、evals/run_evals.py 同款封闭环境：脚本可独立于 pytest 运行
_tmp = Path(tempfile.mkdtemp(prefix="cesa-rag-eval-")).as_posix()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp}/rag-eval.db"
os.environ["AUTO_APPROVE"] = "true"
os.environ["SILICONFLOW_API_KEY"] = ""  # 评测永不出网：强制确定性 hash 嵌入引擎

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from knowledge_seed_data import KNOWLEDGE_SEEDS  # noqa: E402
from rag_golden import FAITHFULNESS_SAMPLES, RAG_GOLDEN_QUERIES  # noqa: E402
from rag_web_corpus import WEB_CORPUS  # noqa: E402

from app.guardrails.badcases import run_all_detectors  # noqa: E402
from app.llm.embeddings import embed_texts  # noqa: E402
from app.persistence.db import adispose_database, init_db  # noqa: E402
from app.persistence.repositories.workflow_repo import WorkflowRepository  # noqa: E402

TENANT_ID = "t_rag_eval"
TOP_K = 5

# 门禁线 = 2026-08 基线实测（Recall@3 90.3 / Recall@5 96.8 / MRR 86.0 / Hit@5 100）
# 减 10~15pt 安全边际；per-category 线另留一档余量：每类仅 4~5 条查询，
# 单条翻车即 ±20~25pt——线放在一翻之下、两翻之上，既防塌方又不被单点抖动卡 CI
THRESHOLDS = {
    "overall": {"recall_at_3": 75.0, "recall_at_5": 85.0, "mrr": 70.0, "hit_rate_at_5": 90.0},
    "per_category_hit_rate_at_5_min": 70.0,  # 仅对 query 数 ≥3 的类别生效
}


def _query_metrics(hits: list[dict], expect_refs: set[str]) -> dict:
    """单条 golden 的指标。expect_refs 为 any-of 集合：top-k 命中任一即算召回。"""
    refs = [h.get("ref") for h in hits]
    ranks = [i + 1 for i, r in enumerate(refs) if r in expect_refs]
    return {
        "recall_at_3": sum(1 for r in refs[:3] if r in expect_refs) / len(expect_refs),
        "recall_at_5": sum(1 for r in refs if r in expect_refs) / len(expect_refs),
        "mrr": 1.0 / ranks[0] if ranks else 0.0,
        "hit_rate_at_5": 1.0 if ranks else 0.0,
        "first_rank": ranks[0] if ranks else None,
    }


async def build_corpus(repo: WorkflowRepository) -> tuple[int, str]:
    """种子五类 + 真机爬取快照一起入临时库，返回（文档数, 嵌入引擎）。"""
    await repo.ensure_tenant(TENANT_ID, "RAG Eval")
    docs = [
        {"category": s["category"], "title": s["title"], "content": s["content"],
         "ref": s.get("ref"), "meta": {"seed": True}}
        for s in KNOWLEDGE_SEEDS
    ] + [
        {"category": d["category"], "title": d["title"], "content": d["content"],
         "ref": d["ref"], "meta": dict(d["meta"])}
        for d in WEB_CORPUS
    ]
    vectors, _usage, engine = await embed_texts(
        [f"{d['title']} {d['content']}" for d in docs]
    )
    for d, emb in zip(docs, vectors):
        await repo.insert_knowledge(
            tenant_id=TENANT_ID, category=d["category"], title=d["title"],
            content=d["content"], embedding=emb, ref=d["ref"], meta=d["meta"],
        )
    return len(docs), engine


async def run_retrieval_eval(repo: WorkflowRepository) -> tuple[list[dict], int]:
    """跑全部 golden 查询，返回（带指标的逐条记录, hybrid 分数缺失数）。"""
    records: list[dict] = []
    missing_rrf = 0
    for g in RAG_GOLDEN_QUERIES:
        vecs, _u, _e = await embed_texts([g["query"]])
        hits = await repo.search_knowledge(
            tenant_id=TENANT_ID, category=None, query_embedding=vecs[0],
            top_k=TOP_K, query_text=g["query"],
        )
        missing_rrf += sum(1 for h in hits if "rrf" not in h)
        rec = dict(g)
        rec.update(_query_metrics(hits, set(g["expect_refs"])))
        rec["top5"] = [(h.get("ref") or h["title"][:20]) for h in hits]
        records.append(rec)
    return records, missing_rrf


def _agg(records: list[dict]) -> dict:
    n = len(records)
    keys = ("recall_at_3", "recall_at_5", "mrr", "hit_rate_at_5")
    return {k: sum(r[k] for r in records) / n * 100 for k in keys}


def print_report(records: list[dict]) -> dict[str, dict]:
    """打印整体 + 分类别两张表，返回各类别聚合（含 overall）。"""
    agg = {"overall": _agg(records)}
    cats = sorted({r["category"] for r in records})
    for c in cats:
        agg[c] = _agg([r for r in records if r["category"] == c])

    hdr = f"{'语料':<14}{'n':>4}{'Recall@3':>10}{'Recall@5':>10}{'MRR@5':>8}{'Hit@5':>8}"
    print(hdr)
    print("-" * len(hdr))
    order = ["overall"] + cats
    for name in order:
        a = agg[name]
        label = "整体" if name == "overall" else name
        n = len(records) if name == "overall" else sum(
            1 for r in records if r["category"] == name
        )
        print(f"{label:<14}{n:>4}"
              f"{a['recall_at_3']:>10.1f}{a['recall_at_5']:>10.1f}{a['mrr']:>8.1f}{a['hit_rate_at_5']:>8.1f}")
    return agg


def print_misses(records: list[dict]) -> list[dict]:
    """列出未满分查询的归因行（期望 vs 实际 top5），返回失配清单。"""
    misses = [r for r in records if r["hit_rate_at_5"] == 0.0]
    if misses:
        print(f"\n未命中归因（{len(misses)} 条）：")
        for r in misses:
            print(f"  ✗ [{r['note']}] {r['query']}")
            print(f"      期望 {sorted(r['expect_refs'])} | 实际 top5 {r['top5']}")
    partial = [r for r in records if 0.0 < r["recall_at_5"] < 1.0]
    if partial:
        print(f"部分命中（{len(partial)} 条，top5 未覆盖全部合法依据）：")
        for r in partial:
            print(f"  △ [{r['note']}] 期望 {sorted(r['expect_refs'])} | 实际 {r['top5']}")
    return misses


def run_faithfulness() -> tuple[int, list[str]]:
    """忠实度护栏：detector 注册表对样本集的判定必须与预期一致。"""
    fails: list[str] = []
    for s in FAITHFULNESS_SAMPLES:
        flagged = bool(run_all_detectors(s["text"]))
        ok = flagged == s["should_flag"]
        mark = "✓" if ok else "✗"
        verdict = "拦截" if flagged else "放行"
        print(f"  {mark} [{verdict}] {s['note']}：{s['text'][:42]}…")
        if not ok:
            fails.append(s["note"])
    return len(fails), fails


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gate", action="store_true", help="CI 门禁模式：低于阈值退出码 1")
    args = parser.parse_args()

    await init_db()
    repo = WorkflowRepository()
    n_docs, engine = await build_corpus(repo)
    print(f"RAG 检索质量评估 | 语料 {n_docs} 条（种子 {len(KNOWLEDGE_SEEDS)} + "
          f"爬取快照 {len(WEB_CORPUS)}）| 黄金查询 {len(RAG_GOLDEN_QUERIES)} 条 | "
          f"嵌入引擎={engine} | 混合检索 BM25+余弦→RRF")
    print()

    records, missing_rrf = await run_retrieval_eval(repo)
    agg = print_report(records)
    print_misses(records)

    failures: list[str] = []

    th = THRESHOLDS["overall"]
    ov = agg["overall"]
    for key, line in (
        ("recall_at_3", "整体 Recall@3"),
        ("recall_at_5", "整体 Recall@5"),
        ("mrr", "整体 MRR@5"),
        ("hit_rate_at_5", "整体 HitRate@5"),
    ):
        ok = ov[key] >= th[key]
        print(f"\n{'✓' if ok else '✗'} {line}: {ov[key]:.1f} （门禁 ≥{th[key]}）")
        if not ok:
            failures.append(line)

    min_hr = THRESHOLDS["per_category_hit_rate_at_5_min"]
    for cat, a in sorted(agg.items()):
        if cat == "overall" or sum(1 for r in records if r["category"] == cat) < 3:
            continue  # 样本太少的类别只展示不设线，避免单条查询抖动卡 CI
        ok = a["hit_rate_at_5"] >= min_hr
        verdict = "✓" if ok else "✗"
        print(f"{verdict} [{cat}] HitRate@5: {a['hit_rate_at_5']:.1f} （门禁 ≥{min_hr}）")
        if not ok:
            failures.append(f"category:{cat}")

    if missing_rrf:
        failures.append("hybrid_sanity")
        print(f"\n✗ 混合检索健全性: {missing_rrf} 个返回项缺 rrf 分数（hybrid 路径失效）")
    else:
        print("\n✓ 混合检索健全性: 全部返回项携带 bm25/rrf 可解释分数")

    print("\n忠实度护栏（M7 detector 注册表）:")
    ff_fails, ff_notes = run_faithfulness()
    if ff_fails:
        failures.append("faithfulness")
        print(f"  ✗ 忠实度失守: {ff_notes}")

    await adispose_database()

    print()
    if args.gate and failures:
        print("RAG 门禁：FAIL（阻断合并）→", ", ".join(failures))
        return 1
    print("RAG 门禁：" + ("PASS" if not failures else "FAIL（--gate 才阻断）"))
    print("（报告模式不设退出码；CI 用 --gate）" if not args.gate else "")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
