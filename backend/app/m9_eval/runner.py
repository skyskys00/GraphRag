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

# LLM listwise 终审（--reranker llm）：候选池大小（fusion.fused_top40 前 N）
LLM_POOL_SIZE = 20


def _load_dotenv() -> None:
    """加载 .env（复用 m3_index 同名函数）。"""
    from app.m3_index.runner import _load_dotenv
    _load_dotenv(PROJ / ".env")


async def _build_deps(working_dir: Path, workspace: str):
    """构建评测所需依赖：rag + sparse_index + query_func(裁判用) + entities。

    Phase 1 只需要检索能力 + 裁判 LLM，不需要 sidecar / 流式生成。
    但 retriever 需要 rag（aquery_data）和 sparse_doc（关键词路 + 元数据）。
    entities 用于查询预处理的同义词/专名扩展（与 M7 线上链路口径一致）。
    """
    from app.m3_index.providers import build_llm_func
    from app.m3_index.runner import build_rag
    from app.m5_retrieve.query_preprocess import load_entities_async
    from app.m5_retrieve.sparse_index import SPARSE_FILE, load as load_sparse

    rag = await build_rag(working_dir, workspace=workspace)

    sparse_path = working_dir / SPARSE_FILE
    if not sparse_path.exists():
        raise FileNotFoundError(f"稀疏索引不存在: {sparse_path}，请先建库")
    sparse = load_sparse(sparse_path)

    try:
        entities = await load_entities_async(workspace)
    except Exception:  # noqa: BLE001 —— 实体名只是 query 预处理的可选项
        entities = None

    _, query_func = build_llm_func()

    return rag, sparse, query_func, entities


# 旧式扁平库：collection id → LightRAG workspace 名（数据目录 = data/<ws>）
_LEGACY_WS = {
    "default": "default_ws",
    "eval_cservice": "eval_cservice_ws",
    "eval_admin": "eval_admin_ws",
}


def _resolve_collection(collection: str) -> tuple[Path, str]:
    """collection → (working_dir 绝对路径, LightRAG workspace 名)。

    旧三库保持扁平布局 data/<ws>；新式 col_* 库走 M7 的 collection_paths
    （data/collections/<id>），workspace 名即 collection id。
    """
    if collection in _LEGACY_WS:
        ws = _LEGACY_WS[collection]
        return PROJ / "data" / ws, ws

    from app.m7_interact.collections import collection_paths

    paths = collection_paths(PROJ, collection)
    return PROJ / paths["working_dir"], collection


def _load_doc_name_map(working_dir: Path) -> dict[str, str]:
    """filename → full_doc_id 映射（comparison 题 per-doc 检索用）。"""
    p = working_dir / "documents.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 —— 映射缺失时退回单查询即可
        return {}
    out: dict[str, str] = {}
    for v in (data.values() if isinstance(data, dict) else data):
        fn, did = v.get("filename"), v.get("doc_id")
        if fn and did and not v.get("deleted"):
            out[fn] = did
    return out


def _comparison_targets(
    q: dict[str, Any], name_map: dict[str, str], allowed_docs: list[str] | None
) -> list[tuple[str, str]]:
    """comparison 题 → [(doc_id, 完整对比问题)]；<2 个 doc 则返回空（退回单查询）。

    对齐线上 M7 compare.py 的跑法：同一 query 分别对每个 doc 独立检索（per-doc 空间），
    交错合并后统一窗口。

    v5.30 起退役题集 per_doc_queries（每文档定制子查询是评测独有输入，线上 compare.py
    不消费 ⇒ 泄漏红线，见 CLAUDE.md「评测信息泄漏红线」）。每 doc 直接用完整 question
    检索 —— 与生产形态一致，数字反映真实对比检索质量。
    """
    if q.get("category") != "comparison":
        return []
    targets: list[tuple[str, str]] = []
    for fn in (q.get("source_docs") or []):
        did = name_map.get(fn)
        if did:
            targets.append((did, q["question"]))
    if allowed_docs:
        allow = set(allowed_docs)
        targets = [(d, s) for d, s in targets if d in allow]
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for d, s in targets:
        if d not in seen:
            seen.add(d)
            out.append((d, s))
    return out if len(out) >= 2 else []


def _interleave(lists: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """按位交错合并多路结果（doc1[0],doc2[0],doc1[1],…），使 top-k 窗口对各文档公平。"""
    merged: list[dict[str, Any]] = []
    for i in range(max((len(l) for l in lists), default=0)):
        for l in lists:
            if i < len(l):
                merged.append(l[i])
    return merged


async def evaluate_retrieval(
    testset: dict[str, Any],
    rag,
    sparse: dict,
    query_func,
    entities: list[str] | None = None,
    allowed_docs: list[str] | None = None,
    exclude_docs: list[str] | None = None,
    doc_name_map: dict[str, str] | None = None,
    *,
    gold_rank_mode: str = "lexical",
    eval_top_n: int = 5,
    reranker: str = "standard",
    ablation_routes: list[str] | None = None,
) -> list[dict[str, Any]]:
    """逐题跑检索 + 评测 context recall / precision + gold_rank。

    Args:
        gold_rank_mode: 'lexical'（默认，零成本）或 'llm'（精确）
        eval_top_n: 检索取多少块用于评测（默认 5，对齐生产 RERANK_TOP=5；探底放宽可传更大值）
        reranker: 'standard'（cross-encoder+融合，默认）或 'llm'（LLM listwise 终审，正式可选）
        ablation_routes: 召回路数对照（ablation）。None=三路全走；子集如 ['vector'] 只走指定路。
    """
    from app.m5_retrieve import retriever as ret_mod
    from app.m5_retrieve.retriever import retrieve
    from .metrics.context_precision import compute_context_precision
    from .metrics.context_recall import compute_context_recall
    from .metrics.gold_rank import compute_gold_rank
    from .metrics.ndcg import compute_ndcg, ndcg_at_k

    # 评测窗口对齐生产（RERANK_TOP=5）；探底放宽时仍可传大值
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
                targets = _comparison_targets(q, doc_name_map or {}, allowed_docs)
                if targets:
                    lists = []
                    for did, sub_q in targets:
                        r = await retrieve(
                            rag, sub_q, sparse,
                            entities=entities,
                            allowed_docs=[did],
                            exclude_docs=exclude_docs,
                            ablation_routes=ablation_routes,
                        )
                        lists.append(r.get("results", []))
                    contexts = _interleave(lists)
                else:
                    retr = await retrieve(
                        rag, question_text, sparse,
                        entities=entities,
                        allowed_docs=allowed_docs,
                        exclude_docs=exclude_docs,
                        ablation_routes=ablation_routes,
                    )
                    if reranker == "llm":
                        # LLM listwise 终审重排（正式可选 reranker，绕过 cross-encoder 表格失明）
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

            # 评测窗口 = 检索窗口（RERANK_TOP=eval_top_n，默认 5 对齐生产）
            contexts = contexts[:eval_top_n]

            # 2. 计算指标（单窗口，一次 LLM 调用）
            metrics: dict[str, Any] = {}

            # gold_rank（词汇模式零 LLM 成本，跨窗口诊断）
            if q["category"] != "unanswerable" and q.get("key_facts"):
                try:
                    gr = await compute_gold_rank(
                        q["key_facts"], contexts,
                        mode=gold_rank_mode, query_func=query_func,
                    )
                    metrics["gold_rank"] = gr
                except Exception as e:
                    print(f"  ⚠️  gold_rank 计算失败: {e}", flush=True)
                    metrics["gold_rank"] = None

            # context_recall：单窗口一次 LLM 调用
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
                        "window": f"top{eval_top_n}",
                    }
                except Exception as e:
                    print(f"  ⚠️  context_recall 计算失败: {e}", flush=True)
                    metrics["context_recall"] = None
            else:
                metrics["context_recall"] = None

            # context_precision + nDCG（单窗口，len(contexts) 次 LLM 调用）
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
                    "window": f"top{eval_top_n}",
                }
                # nDCG：从 per_chunk 的 score（相关度 0~1）推导，零额外 LLM 成本
                metrics["ndcg"] = ndcg_at_k(cp["per_chunk"], eval_top_n)
            except Exception as e:
                print(f"  ⚠️  context_precision 计算失败: {e}", flush=True)
                metrics["context_precision"] = None
                metrics["context_precision_weighted"] = None
                metrics["ndcg"] = None

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
    doc_name_map: dict[str, str] | None = None,
    *,
    gold_rank_mode: str = "lexical",
    eval_top_n: int = 5,
) -> list[dict[str, Any]]:
    """逐题跑 M6 answer + 检索指标 + 生成质量指标。

    Phase 2 骨架：先打通 M6 answer 调用链路，生成指标留空（Step 2 实现）。
    检索指标复用 retrieval 模式的全套（单窗口 + gold_rank + nDCG）。
    """
    from app.m5_retrieve import retriever as ret_mod
    from app.m6_generate.orchestrator import answer as m6_answer

    # 评测窗口对齐生产（RERANK_TOP=eval_top_n，默认 5）；探底放宽时仍可传大值
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

            # 1. 调用 M6 answer（内部会自己做检索）；comparison 题 per-doc 各跑一次再合并
            try:
                targets = _comparison_targets(q, doc_name_map or {}, allowed_docs)
                if targets:
                    ans_list = []
                    ctx_lists = []
                    for did, sub_q in targets:
                        a = await m6_answer(
                            rag, sub_q, sparse,
                            entities=entities,
                            query_func=query_func,
                            allowed_docs=[did],
                            exclude_docs=exclude_docs,
                        )
                        ans_list.append(a)
                        ctx_lists.append(a.get("retrieval", {}).get("results", []))
                    answer_text = "\n\n".join(a.get("text", "") for a in ans_list)
                    citations = [c for a in ans_list for c in (a.get("citations") or [])]
                    contexts = _interleave(ctx_lists)
                    gen_meta = ans_list[0].get("meta", {})
                    retr = ans_list[0].get("retrieval", {})
                else:
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

            # 评测窗口 = 检索窗口（RERANK_TOP=eval_top_n，默认 5 对齐生产）
            contexts = contexts[:eval_top_n]

            # 2. 检索指标（同 retrieval 模式，复用同一套）
            metrics: dict[str, Any] = {}

            # gold_rank
            if q["category"] != "unanswerable" and q.get("key_facts"):
                try:
                    from .metrics.gold_rank import compute_gold_rank
                    gr = await compute_gold_rank(
                        q["key_facts"], contexts,
                        mode=gold_rank_mode, query_func=query_func,
                    )
                    metrics["gold_rank"] = gr
                except Exception as e:
                    print(f"  ⚠️  gold_rank 计算失败: {e}", flush=True)
                    metrics["gold_rank"] = None

            # context_recall（单窗口一次 LLM 调用）
            if q["category"] != "unanswerable" and q.get("key_facts"):
                try:
                    from .metrics.context_recall import compute_context_recall
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
                        "window": f"top{eval_top_n}",
                    }
                except Exception as e:
                    print(f"  ⚠️  context_recall 计算失败: {e}", flush=True)
                    metrics["context_recall"] = None
            else:
                metrics["context_recall"] = None

            # context_precision + nDCG（单窗口，len(contexts) 次 LLM 调用）
            try:
                from .metrics.context_precision import compute_context_precision
                from .metrics.ndcg import ndcg_at_k
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
                    "window": f"top{eval_top_n}",
                }
                metrics["ndcg"] = ndcg_at_k(cp["per_chunk"], eval_top_n)
            except Exception as e:
                print(f"  ⚠️  context_precision 计算失败: {e}", flush=True)
                metrics["context_precision"] = None
                metrics["context_precision_weighted"] = None
                metrics["ndcg"] = None

            # 3. 生成质量指标
            try:
                from .metrics.faithfulness import compute_faithfulness
                fai = await compute_faithfulness(
                    query_func, question_text, answer_text, contexts,
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
                        contexts, citations,
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

    # 确定 collection / workspace / 数据目录
    if args.workspace:
        workspace = args.workspace
        collection = args.collection or workspace.rstrip("_ws")
        working_dir = PROJ / "data" / workspace
    else:
        collection = args.collection or testset.get("collection", "default")
        working_dir, workspace = _resolve_collection(collection)

    print(f"评测模式: {args.mode}")
    print(f"Collection: {collection} → workspace: {workspace}")
    print(f"数据目录: {working_dir}")
    print()

    # 构建依赖
    print("初始化 RAG / 稀疏索引 / 实体 / 裁判 LLM...", flush=True)
    rag, sparse, query_func, entities = await _build_deps(working_dir, workspace)
    print(f"  稀疏索引 chunks: {len(sparse.get('chunks', {}))}"
          f"｜实体 {len(entities) if entities else 0}", flush=True)
    doc_name_map = _load_doc_name_map(working_dir)
    print(f"  文档名映射: {len(doc_name_map)} 份（comparison 题 per-doc 检索用）", flush=True)
    print("  初始化完成\n", flush=True)

    # 执行评测
    t_start = time.time()
    if args.mode in ("answer", "e2e"):
        results = await evaluate_answer(testset, rag, sparse, query_func,
                                        entities=entities, doc_name_map=doc_name_map)
    else:
        ablation_routes = args.ablation_routes.split(",") if args.ablation_routes else None
        results = await evaluate_retrieval(testset, rag, sparse, query_func,
                                           entities=entities,
                                           doc_name_map=doc_name_map,
                                           reranker=args.reranker,
                                           ablation_routes=ablation_routes)
    total_time = round(time.time() - t_start, 1)

    # 汇总报告
    config = {
        "mode": args.mode,
        "collection": collection,
        "workspace": workspace,
        "reranker": args.reranker,
        "ablation_routes": args.ablation_routes,
        "comparison_mode": "per_doc",
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
                        help="检索排序终审：standard=cross-encoder+融合（默认）；llm=LLM listwise 终审（正式可选，仅评测）")
    parser.add_argument("--ablation-routes", default="graph,vector,keyword",
                        help="召回路数对照（ablation，仅 retrieval 模式生效）：逗号分隔限定 RRF 融合的路由；"
                             "如 'vector'（纯向量）、'vector,graph'（图+向量），默认三条全走（现行为）")

    args = parser.parse_args()

    # --limit 支持：截取测试集前 N 题（在 load 之后通过 testset 切片实现）
    # 直接改 testset questions 太 hack，先在 evaluate_retrieval 之前切片
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
