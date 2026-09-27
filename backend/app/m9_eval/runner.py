"""M9 评测 runner：CLI 入口，支持 retrieval / answer 两种模式。

用法：
    cd backend
    python -m app.m9_eval.runner \
        --testset tests/testsets/testset_cservice_50.json \
        --mode retrieval \
        --collection eval_cservice \
        --report tests/reports/run_baseline.json

模式：
    retrieval — 仅检索指标（Phase 1，context_recall/precision/nDCG/gold_rank）
    e2e / answer — 端到端（Phase 2，检索 + 生成 + 生成质量指标）
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

# 项目根
PROJ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJ))

from .report import build_report, save_report, to_markdown
from .testset import load_testset, stats as testset_stats

# LLM listwise 终审（探底 A/B）：候选池大小（fusion.fused_top40 前 N）
LLM_POOL_SIZE = 20


def _load_dotenv() -> None:
    """加载 .env（复用 m3_index 同名函数）。"""
    from app.m3_index.runner import _load_dotenv
    _load_dotenv(PROJ / ".env")


async def _build_deps(workspace: str):
    """构建评测所需依赖：rag + sparse_index + query_func(裁判用)。

    Phase 1 只需要检索能力 + 裁判 LLM，不需要 sidecar / entities / 流式生成。
    但 retriever 需要 rag（aquery_data）和 sparse_doc（关键词路 + 元数据）。
    """
    from app.m3_index.providers import build_llm_func
    from app.m3_index.runner import build_rag
    from app.m5_retrieve.sparse_index import SPARSE_FILE, load as load_sparse

    working_dir = PROJ / "data" / workspace
    rag = await build_rag(working_dir, workspace=workspace)

    sparse_path = working_dir / SPARSE_FILE
    if not sparse_path.exists():
        raise FileNotFoundError(f"稀疏索引不存在: {sparse_path}，请先建库")
    sparse = load_sparse(sparse_path)

    _, query_func = build_llm_func()

    return rag, sparse, query_func


async def evaluate_retrieval(
    testset: dict[str, Any],
    rag,
    sparse: dict,
    query_func,
    entities: list[str] | None = None,
    allowed_docs: list[str] | None = None,
    exclude_docs: list[str] | None = None,
    *,
    gold_rank_mode: str = "lexical",
    eval_top_n: int = 8,
    reranker: str = "standard",
) -> list[dict[str, Any]]:
    """逐题跑检索 + 评测 context recall / precision + gold_rank。

    Args:
        gold_rank_mode: 'lexical'（默认，零成本）或 'llm'（精确）
        eval_top_n: 检索取多少块用于评测（默认 8，可同时出 top5/top8 两套指标）
        reranker: 'standard'（cross-encoder+融合）或 'llm'（LLM listwise 终审，探底 A/B）
    """
    from app.m5_retrieve import retriever as ret_mod
    from app.m5_retrieve.retriever import retrieve
    from .metrics.context_precision import compute_context_precision
    from .metrics.context_recall import compute_context_recall
    from .metrics.gold_rank import compute_gold_rank
    from .metrics.ndcg import compute_ndcg, ndcg_at_k

    # 评测用更大的候选窗口，便于同时出 top5/top8 两套指标
    orig_rerank_top = ret_mod.RERANK_TOP
    ret_mod.RERANK_TOP = eval_top_n

    questions = testset["questions"]
    total = len(questions)
    results: list[dict[str, Any]] = []

    try:
        for i, q in enumerate(questions):
            qid = q["id"]
            question_text = q["question"]
            print(f"[{i+1}/{total}] {qid} - {q['category']} - {question_text[:50]}...", flush=True)

            t0 = time.time()

            # 1. 检索（取 eval_top_n 块）
            try:
                retr = await retrieve(
                    rag, question_text, sparse,
                    entities=entities,
                    allowed_docs=allowed_docs,
                    exclude_docs=exclude_docs,
                )
                if reranker == "llm":
                    # 探底 A/B：LLM listwise 终审重排（绕过 cross-encoder 表格失明）
                    from .llm_rerank import rerank_with_llm

                    contexts = await rerank_with_llm(
                        query_func, question_text, retr, sparse,
                        top_n=eval_top_n, pool_size=LLM_POOL_SIZE,
                    )
                    retr["results"] = contexts  # 后续统一走 contexts
                else:
                    contexts = retr.get("results", [])
            except Exception as e:
                print(f"  ⚠️  检索失败: {e}", flush=True)
                contexts = []

            retrieval_time = round(time.time() - t0, 2)

            # top5 / top8 切片（不足则取全部）
            contexts_top5 = contexts[:5]
            contexts_top8 = contexts[:8]

            # 2. 计算指标（只跑 top8 一套 LLM 调用，top5 指标从结果推导，接近零额外成本）
            metrics: dict[str, Any] = {}

            # gold_rank（词汇模式零 LLM 成本，跨窗口诊断）
            if q["category"] != "unanswerable" and q.get("key_facts"):
                try:
                    gr = await compute_gold_rank(
                        q["key_facts"], contexts_top8,
                        mode=gold_rank_mode, query_func=query_func,
                    )
                    metrics["gold_rank"] = gr
                except Exception as e:
                    print(f"  ⚠️  gold_rank 计算失败: {e}", flush=True)
                    metrics["gold_rank"] = None

            # context_recall：top5 和 top8 各跑一遍（LLM 调用量约 +50%，35题≈+100次）
            # 无法从 top8 推导 top5，因为 judge 不记录 fact 命中在第几块
            if q["category"] != "unanswerable" and q.get("key_facts"):
                # top8 recall（完整窗口）
                try:
                    cr8 = await compute_context_recall(
                        query_func, question_text, q["key_facts"], contexts_top8,
                    )
                    metrics["context_recall_top8"] = cr8["score"]
                    metrics["context_recall_detail"] = {
                        "total_facts": cr8["total_facts"],
                        "hit_facts": cr8["hit_facts"],
                        "failed_facts": cr8.get("failed_facts", 0),
                        "per_fact": cr8["per_fact"],
                        "reason": cr8["reason"],
                        "window": "top8",
                    }
                except Exception as e:
                    print(f"  ⚠️  context_recall top8 计算失败: {e}", flush=True)
                    metrics["context_recall_top8"] = None

                # top5 recall（窄窗口，回答"top5 够用吗"）
                try:
                    cr5 = await compute_context_recall(
                        query_func, question_text, q["key_facts"], contexts_top5,
                    )
                    metrics["context_recall_top5"] = cr5["score"]
                    metrics["context_recall"] = cr5["score"]  # 兼容字段默认 top5
                    if metrics.get("context_recall_detail"):
                        metrics["context_recall_detail"]["per_fact_top5"] = cr5["per_fact"]
                        metrics["context_recall_detail"]["hit_facts_top5"] = cr5["hit_facts"]
                except Exception as e:
                    print(f"  ⚠️  context_recall top5 计算失败: {e}", flush=True)
                    metrics["context_recall_top5"] = None
                    metrics["context_recall"] = None
            else:
                metrics["context_recall"] = None
                metrics["context_recall_top5"] = None
                metrics["context_recall_top8"] = None

            # context_precision：只跑 top8（8 次 LLM 调用，top5 从 per_chunk 切片）
            try:
                cp8 = await compute_context_precision(
                    query_func, question_text,
                    q.get("ground_truth", ""), q.get("key_facts", []),
                    contexts_top8,
                )
                metrics["context_precision_top8"] = cp8["score"]
                metrics["context_precision_weighted_top8"] = cp8["weighted_score"]
                metrics["context_precision_detail"] = {
                    "total_chunks": cp8["total_chunks"],
                    "relevant_chunks": cp8["relevant_chunks"],
                    "failed_chunks": cp8.get("failed_chunks", 0),
                    "per_chunk": cp8["per_chunk"],
                    "reason": cp8["reason"],
                    "window": "top8",
                }

                # top5 precision 从 per_chunk 切片推导
                per_chunk_top5 = [c for c in cp8["per_chunk"] if c.get("rank") and c["rank"] <= 5]
                evaluated5 = [c for c in per_chunk_top5 if c.get("relevant") is not None]
                failed5 = len(per_chunk_top5) - len(evaluated5)
                if evaluated5:
                    rel_count5 = sum(1 for c in evaluated5 if c["relevant"])
                    cp5_score = round(rel_count5 / len(evaluated5), 4)
                    n5 = len(evaluated5)
                    weights5 = [1.0 / (i + 1) for i in range(n5)]
                    total_w5 = sum(weights5)
                    cp5_weighted = round(
                        sum(w * (1.0 if c["relevant"] else 0.0)
                            for w, c in zip(weights5, evaluated5))
                        / total_w5 if total_w5 else 0.0, 4
                    )
                else:
                    cp5_score = None
                    cp5_weighted = None
                metrics["context_precision_top5"] = cp5_score
                metrics["context_precision_weighted_top5"] = cp5_weighted
                metrics["context_precision"] = cp5_score  # 兼容字段默认 top5
                metrics["context_precision_weighted"] = cp5_weighted

                # nDCG：从 per_chunk 的 score（相关度 0~1）推导，零额外 LLM 成本
                # top8 nDCG 直接用完整 per_chunk 算
                ndcg8 = ndcg_at_k(cp8["per_chunk"], 8)
                metrics["ndcg_top8"] = ndcg8
                # top5 nDCG 用 rank ≤5 的切片（按原始 rank）
                ndcg5 = ndcg_at_k(cp8["per_chunk"], 5)
                metrics["ndcg_top5"] = ndcg5
                metrics["ndcg"] = ndcg5  # 兼容字段默认 top5
            except Exception as e:
                print(f"  ⚠️  context_precision 计算失败: {e}", flush=True)
                metrics["context_precision"] = None

            total_time = round(time.time() - t0, 2)

            result = {
                "id": qid,
                "category": q["category"],
                "difficulty": q["difficulty"],
                "question": question_text,
                "retrieval": {
                    "num_results": len(contexts),
                    "top_docs": [c.get("full_doc_id", "?") for c in contexts[:5]],
                    "time_s": retrieval_time,
                },
                "metrics": metrics,
                "time_s": total_time,
            }

            cr_val = metrics.get("context_recall")
            cp_val = metrics.get("context_precision")
            gr = metrics.get("gold_rank")
            gr_info = f"  gr_avg={gr['avg_rank']}" if gr and gr.get('avg_rank') else ""
            print(f"  recall={cr_val:.2f}" if cr_val is not None else "  recall=N/A",
                  f"precision={cp_val:.2f}" if cp_val is not None else "precision=N/A",
                  f"({total_time}s){gr_info}", flush=True)

            results.append(result)
    finally:
        # 恢复原值
        ret_mod.RERANK_TOP = orig_rerank_top

    return results


async def evaluate_answer(
    testset: dict[str, Any],
    rag,
    sparse: dict,
    query_func,
    entities: list[str] | None = None,
    allowed_docs: list[str] | None = None,
    exclude_docs: list[str] | None = None,
    *,
    gold_rank_mode: str = "lexical",
    eval_top_n: int = 8,
) -> list[dict[str, Any]]:
    """逐题跑 M6 answer + 检索指标 + 生成质量指标。

    Phase 2 骨架：先打通 M6 answer 调用链路，生成指标留空（Step 2 实现）。
    检索指标复用 retrieval 模式的全套（双窗口 + gold_rank + nDCG）。
    """
    from app.m5_retrieve import retriever as ret_mod
    from app.m6_generate.orchestrator import answer as m6_answer

    # 评测用更大的候选窗口，便于同时出 top5/top8 两套指标
    orig_rerank_top = ret_mod.RERANK_TOP
    ret_mod.RERANK_TOP = eval_top_n

    questions = testset["questions"]
    total = len(questions)
    results: list[dict[str, Any]] = []

    try:
        for i, q in enumerate(questions):
            qid = q["id"]
            question_text = q["question"]
            print(f"[{i+1}/{total}] {qid} - {q['category']} - {question_text[:50]}...", flush=True)

            t0 = time.time()

            # 1. 调用 M6 answer（内部会自己做检索）
            try:
                ans = await m6_answer(
                    rag, question_text, sparse,
                    entities=entities,
                    query_func=query_func,
                    allowed_docs=allowed_docs,
                    exclude_docs=exclude_docs,
                )
                answer_text = ans.get("text", "")
                citations = ans.get("citations", [])
                retr = ans.get("retrieval", {})
                contexts = retr.get("results", [])
                gen_meta = ans.get("meta", {})
            except Exception as e:
                print(f"  ⚠️  answer 失败: {e}", flush=True)
                answer_text = ""
                citations = []
                contexts = []
                retr = {}
                gen_meta = {}

            answer_time = round(time.time() - t0, 2)

            # top5 / top8 切片（不足则取全部）
            contexts_top5 = contexts[:5]
            contexts_top8 = contexts[:8]

            # 2. 检索指标（同 retrieval 模式，复用同一套）
            metrics: dict[str, Any] = {}

            # gold_rank
            if q["category"] != "unanswerable" and q.get("key_facts"):
                try:
                    from .metrics.gold_rank import compute_gold_rank
                    gr = await compute_gold_rank(
                        q["key_facts"], contexts_top8,
                        mode=gold_rank_mode, query_func=query_func,
                    )
                    metrics["gold_rank"] = gr
                except Exception as e:
                    print(f"  ⚠️  gold_rank 计算失败: {e}", flush=True)
                    metrics["gold_rank"] = None

            # context_recall（top5 + top8）
            if q["category"] != "unanswerable" and q.get("key_facts"):
                try:
                    from .metrics.context_recall import compute_context_recall
                    cr8 = await compute_context_recall(
                        query_func, question_text, q["key_facts"], contexts_top8,
                    )
                    metrics["context_recall_top8"] = cr8["score"]
                    metrics["context_recall_detail"] = {
                        "total_facts": cr8["total_facts"],
                        "hit_facts": cr8["hit_facts"],
                        "failed_facts": cr8.get("failed_facts", 0),
                        "per_fact": cr8["per_fact"],
                        "reason": cr8["reason"],
                        "window": "top8",
                    }
                    cr5 = await compute_context_recall(
                        query_func, question_text, q["key_facts"], contexts_top5,
                    )
                    metrics["context_recall_top5"] = cr5["score"]
                    metrics["context_recall"] = cr5["score"]
                    if metrics.get("context_recall_detail"):
                        metrics["context_recall_detail"]["per_fact_top5"] = cr5["per_fact"]
                        metrics["context_recall_detail"]["hit_facts_top5"] = cr5["hit_facts"]
                except Exception as e:
                    print(f"  ⚠️  context_recall 计算失败: {e}", flush=True)
                    metrics["context_recall"] = None
                    metrics["context_recall_top5"] = None
                    metrics["context_recall_top8"] = None
            else:
                metrics["context_recall"] = None
                metrics["context_recall_top5"] = None
                metrics["context_recall_top8"] = None

            # context_precision + nDCG（top8 跑，top5 推导）
            try:
                from .metrics.context_precision import compute_context_precision
                from .metrics.ndcg import ndcg_at_k
                cp8 = await compute_context_precision(
                    query_func, question_text,
                    q.get("ground_truth", ""), q.get("key_facts", []),
                    contexts_top8,
                )
                metrics["context_precision_top8"] = cp8["score"]
                metrics["context_precision_weighted_top8"] = cp8["weighted_score"]
                metrics["context_precision_detail"] = {
                    "total_chunks": cp8["total_chunks"],
                    "relevant_chunks": cp8["relevant_chunks"],
                    "failed_chunks": cp8.get("failed_chunks", 0),
                    "per_chunk": cp8["per_chunk"],
                    "reason": cp8["reason"],
                    "window": "top8",
                }
                per_chunk_top5 = [c for c in cp8["per_chunk"] if c.get("rank") and c["rank"] <= 5]
                evaluated5 = [c for c in per_chunk_top5 if c.get("relevant") is not None]
                if evaluated5:
                    rel_count5 = sum(1 for c in evaluated5 if c["relevant"])
                    cp5_score = round(rel_count5 / len(evaluated5), 4)
                    n5 = len(evaluated5)
                    weights5 = [1.0 / (i + 1) for i in range(n5)]
                    total_w5 = sum(weights5)
                    cp5_weighted = round(
                        sum(w * (1.0 if c["relevant"] else 0.0)
                            for w, c in zip(weights5, evaluated5))
                        / total_w5 if total_w5 else 0.0, 4
                    )
                else:
                    cp5_score = None
                    cp5_weighted = None
                metrics["context_precision_top5"] = cp5_score
                metrics["context_precision_weighted_top5"] = cp5_weighted
                metrics["context_precision"] = cp5_score
                metrics["context_precision_weighted"] = cp5_weighted

                ndcg8 = ndcg_at_k(cp8["per_chunk"], 8)
                ndcg5 = ndcg_at_k(cp8["per_chunk"], 5)
                metrics["ndcg_top8"] = ndcg8
                metrics["ndcg_top5"] = ndcg5
                metrics["ndcg"] = ndcg5
            except Exception as e:
                print(f"  ⚠️  context_precision 计算失败: {e}", flush=True)
                metrics["context_precision"] = None

            # 3. 生成质量指标
            try:
                from .metrics.faithfulness import compute_faithfulness
                fai = await compute_faithfulness(
                    query_func, question_text, answer_text, contexts_top5,
                )
                metrics["faithfulness"] = fai["score"]
                metrics["faithfulness_detail"] = {
                    "total_statements": fai.get("total_statements", 0),
                    "supported_statements": fai.get("supported_statements", 0),
                    "unsupported": fai.get("unsupported", []),
                    "reason": fai["reason"],
                }
            except Exception as e:
                print(f"  ⚠️  faithfulness 计算失败: {e}", flush=True)
                metrics["faithfulness"] = None

            try:
                from .metrics.answer_relevance import compute_answer_relevance
                ar = await compute_answer_relevance(query_func, question_text, answer_text)
                metrics["answer_relevance"] = ar["score"]
                metrics["answer_relevance_detail"] = {"reason": ar["reason"]}
            except Exception as e:
                print(f"  ⚠️  answer_relevance 计算失败: {e}", flush=True)
                metrics["answer_relevance"] = None

            try:
                from .metrics.correctness import compute_correctness
                crr = await compute_correctness(
                    query_func, question_text,
                    q.get("ground_truth", ""), answer_text,
                )
                metrics["correctness"] = crr["score"]
                metrics["correctness_detail"] = {
                    "total_facts": crr.get("total_facts", 0),
                    "correct_facts": crr.get("correct_facts", 0),
                    "incorrect": crr.get("incorrect", []),
                    "reason": crr["reason"],
                }
            except Exception as e:
                print(f"  ⚠️  correctness 计算失败: {e}", flush=True)
                metrics["correctness"] = None

            try:
                from .metrics.citation_accuracy import compute_citation_accuracy
                ca = await compute_citation_accuracy(
                    query_func, question_text, answer_text,
                        contexts_top8, citations,
                )
                metrics["citation_accuracy"] = ca["score"]
                metrics["citation_accuracy_detail"] = {
                    "total_cites": ca.get("total_cites", 0),
                    "supported_cites": ca.get("supported_cites", 0),
                    "details": ca.get("details", []),
                    "reason": ca["reason"],
                }
            except Exception as e:
                print(f"  ⚠️  citation_accuracy 计算失败: {e}", flush=True)
                metrics["citation_accuracy"] = None

            total_time = round(time.time() - t0, 2)

            result = {
                "id": qid,
                "category": q["category"],
                "difficulty": q["difficulty"],
                "question": question_text,
                "ground_truth": q.get("ground_truth", ""),
                "key_facts": q.get("key_facts", []),
                "answer": answer_text,
                "citations": citations,
                "retrieval": {
                    "num_results": len(contexts),
                    "top_docs": [c.get("full_doc_id", "?") for c in contexts[:5]],
                },
                "gen_meta": gen_meta,
                "metrics": metrics,
                "time_s": total_time,
                "answer_time_s": answer_time,
            }

            cr_val = metrics.get("context_recall")
            cp_val = metrics.get("context_precision")
            gr = metrics.get("gold_rank")
            gr_info = f"  gr_avg={gr['avg_rank']}" if gr and gr.get('avg_rank') else ""
            print(f"  recall={cr_val:.2f}" if cr_val is not None else "  recall=N/A",
                  f"precision={cp_val:.2f}" if cp_val is not None else "precision=N/A",
                  f"({total_time}s){gr_info}", flush=True)

            results.append(result)
    finally:
        ret_mod.RERANK_TOP = orig_rerank_top

    return results


async def main_async(args: argparse.Namespace) -> None:
    _load_dotenv()

    # 加载测试集
    testset = load_testset(args.testset)
    if args.limit and args.limit > 0:
        testset["questions"] = testset["questions"][:args.limit]
        print(f"[limit] 只跑前 {args.limit} 题\n")
    ts_stats = testset_stats(testset["questions"])
    print(f"测试集: {testset['testset_id']} ({testset.get('domain', '')})")
    print(f"  共 {ts_stats['total']} 题：{ts_stats['by_category']}")
    print(f"  难度分布：{ts_stats['by_difficulty']}")
    print()

    # 确定 workspace
    if args.workspace:
        workspace = args.workspace
        collection = args.collection or workspace.rstrip("_ws")
    else:
        collection = args.collection or testset.get("collection", "default")
        if collection == "default":
            workspace = "default_ws"
        elif collection == "eval_cservice":
            workspace = "eval_cservice_ws"
        elif collection == "eval_admin":
            workspace = "eval_admin_ws"
        else:
            workspace = collection  # 假设 collection id 即 workspace 名

    print(f"评测模式: {args.mode}")
    print(f"Collection: {collection} → workspace: {workspace}")
    print()

    # 构建依赖
    print("初始化 RAG / 稀疏索引 / 裁判 LLM...", flush=True)
    rag, sparse, query_func = await _build_deps(workspace)
    print(f"  稀疏索引 chunks: {len(sparse.get('chunks', {}))}", flush=True)
    print("  初始化完成\n", flush=True)

    # 执行评测
    t_start = time.time()
    if args.mode in ("answer", "e2e"):
        results = await evaluate_answer(testset, rag, sparse, query_func)
    else:
        results = await evaluate_retrieval(testset, rag, sparse, query_func,
                                           reranker=args.reranker)
    total_time = round(time.time() - t_start, 1)

    # 汇总报告
    config = {
        "mode": args.mode,
        "collection": collection,
        "workspace": workspace,
        "reranker": args.reranker,
    }
    report = build_report(testset, results, config=config)
    report["summary"]["total_time_s"] = total_time

    # 保存
    report_path = args.report or f"tests/reports/run_{int(time.time())}.json"
    save_report(report, report_path)

    # 打印摘要
    print("\n" + "=" * 60)
    print(to_markdown(report))
    print(f"总耗时：{total_time} 秒")
    print(f"报告已保存：{report_path}")
    print("=" * 60)

    await rag.finalize_storages()


def main() -> None:
    parser = argparse.ArgumentParser(description="M9 RAG 评测 runner")
    parser.add_argument("--testset", required=True, help="测试集 JSON 路径")
    parser.add_argument("--mode", default="retrieval",
                        choices=["retrieval", "e2e"],
                        help="评测模式：retrieval=仅检索指标（Phase 1）；e2e=端到端（Phase 2）")
    parser.add_argument("--collection", default=None,
                        help="评测的 collection（缺省从测试集元数据读）")
    parser.add_argument("--workspace", default=None,
                        help="直接指定 workspace 名（绕过 collection→workspace 映射）")
    parser.add_argument("--report", default=None, help="报告输出路径")
    parser.add_argument("--limit", type=int, default=None,
                        help="只跑前 N 题（冒烟用）")
    parser.add_argument("--reranker", default="standard",
                        choices=["standard", "llm"],
                        help="检索排序终审：standard=cross-encoder+融合（默认）；llm=LLM listwise 终审（探底 A/B）")

    args = parser.parse_args()

    # --limit 支持：截取测试集前 N 题（在 load 之后通过 testset 切片实现）
    # 直接改 testset questions 太 hack，先在 evaluate_retrieval 之前切片
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
