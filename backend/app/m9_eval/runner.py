"""M9 评测 runner：CLI 入口，支持 retrieval 模式（Phase 1）。

用法：
    cd backend
    python -m app.m9_eval.runner \
        --testset tests/testsets/testset_cservice_35.json \
        --mode retrieval \
        --collection eval_cservice \
        --report tests/reports/run_baseline.json
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
) -> list[dict[str, Any]]:
    """逐题跑检索 + 评测 context recall / precision。"""
    from app.m5_retrieve.retriever import retrieve
    from .metrics.context_precision import compute_context_precision
    from .metrics.context_recall import compute_context_recall

    questions = testset["questions"]
    total = len(questions)
    results: list[dict[str, Any]] = []

    for i, q in enumerate(questions):
        qid = q["id"]
        question_text = q["question"]
        print(f"[{i+1}/{total}] {qid} - {q['category']} - {question_text[:50]}...", flush=True)

        t0 = time.time()

        # 1. 检索
        try:
            retr = await retrieve(
                rag, question_text, sparse,
                entities=entities,
                allowed_docs=allowed_docs,
                exclude_docs=exclude_docs,
            )
            contexts = retr.get("results", [])
        except Exception as e:
            print(f"  ⚠️  检索失败: {e}", flush=True)
            contexts = []

        retrieval_time = round(time.time() - t0, 2)

        # 2. 计算指标
        metrics: dict[str, Any] = {}

        # context_recall：跳过 unanswerable 类（本来就不该有答案）
        if q["category"] != "unanswerable" and q.get("key_facts"):
            try:
                cr = await compute_context_recall(
                    query_func, question_text, q["key_facts"], contexts,
                )
                metrics["context_recall"] = cr["score"]
                metrics["context_recall_detail"] = {
                    "total_facts": cr["total_facts"],
                    "hit_facts": cr["hit_facts"],
                    "failed_facts": cr.get("failed_facts", 0),
                    "per_fact": cr["per_fact"],
                    "reason": cr["reason"],
                }
            except Exception as e:
                print(f"  ⚠️  context_recall 计算失败: {e}", flush=True)
                metrics["context_recall"] = None
        else:
            metrics["context_recall"] = None

        # context_precision
        try:
            cp = await compute_context_precision(
                query_func, question_text,
                q.get("ground_truth", ""), q.get("key_facts", []),
                contexts,
            )
            metrics["context_precision"] = cp["score"]
            metrics["context_precision_weighted"] = cp["weighted_score"]
            metrics["context_precision_detail"] = {
                "total_chunks": cp["total_chunks"],
                "relevant_chunks": cp["relevant_chunks"],
                "failed_chunks": cp.get("failed_chunks", 0),
                "per_chunk": cp["per_chunk"],
                "reason": cp["reason"],
            }
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
        print(f"  recall={cr_val:.2f}" if cr_val is not None else "  recall=N/A",
              f"precision={cp_val:.2f}" if cp_val is not None else "precision=N/A",
              f"({total_time}s)", flush=True)

        results.append(result)

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
    results = await evaluate_retrieval(testset, rag, sparse, query_func)
    total_time = round(time.time() - t_start, 1)

    # 汇总报告
    config = {
        "mode": args.mode,
        "collection": collection,
        "workspace": workspace,
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
    parser.add_argument("--report", default=None, help="报告输出路径")
    parser.add_argument("--limit", type=int, default=None,
                        help="只跑前 N 题（冒烟用）")

    args = parser.parse_args()

    # --limit 支持：截取测试集前 N 题（在 load 之后通过 testset 切片实现）
    # 直接改 testset questions 太 hack，先在 evaluate_retrieval 之前切片
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
