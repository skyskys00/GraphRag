"""M5 检索层 v0.1：LightRAG 检索冒烟 CLI（纯召回验证，不调 LLM 生成）。

定位：正式 M5 检索（三路 RRF + rerank + 关键词路外层自研）之前的**基线冒烟工具**——
在指定索引库上验证 local/global/mix/naive 四模式召回质量，输出实体/关系/块统计。

运行：
    python -m app.m5_retrieve.runner [-w data/default_ws] [-q 问题] [-m 模式]
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from lightrag import LightRAG, QueryParam

from app.m3_index.runner import _load_dotenv, build_rag
from .query_preprocess import load_entities_async
from .retriever import retrieve as formal_retrieve
from .sparse_index import SPARSE_FILE, build as build_sparse, load as load_sparse

DEFAULT_QUESTIONS = [
    ("智能客服", "智能客服系统有哪些核心功能？涉及哪些主要角色？"),
    ("季度复盘", "本季度销售业绩表现如何？哪些区域或产品线增长明显？"),
    ("投诉处理", "客户投诉的处理流程是怎样的？分哪几个步骤？"),
    ("实体关联", "智能客服系统迭代会议决定了哪些事项？谁负责？"),
]
MODES = ["local", "global", "mix", "naive"]


def _doc_of_chunk(chunk: dict) -> str:
    """从 chunk 定位来源文档。

    注意：aquery_data 返回的 chunk 仅含 content/file_path/chunk_id，
    file_path 实测恒为 unknown_source（索引时未带入）。真正来源在
    text_chunks KV / vdb payload 的 full_doc_id 字段——M5 引用溯源需
    经 chunk_id 回连（见 consent/docs 相关坑）。此处暂用 chunk_id 前缀。
    """
    return chunk.get("chunk_id", "?")[:8]


async def run_one(rag: LightRAG, label: str, q: str, mode: str) -> None:
    param = QueryParam(mode=mode, top_k=10, chunk_top_k=5, only_need_context=True)
    result = await rag.aquery_data(q, param)
    data = result.get("data", {})
    entities = data.get("entities", [])
    rels = data.get("relationships", [])
    chunks = data.get("chunks", [])
    ent_names = sorted({e.get("entity_name", "") for e in entities})[:12]
    rel_pairs = [f"{r.get('src_id','')}--{r.get('tgt_id','')}" for r in rels[:6]]
    print(f"  [{mode}] 实体({len(entities)}) 关系({len(rels)}) 块({len(chunks)})")
    if ent_names:
        print(f"    entities: {ent_names}")
    if rel_pairs:
        print(f"    rel-pair: {rel_pairs}")
    if chunks:
        srcs = {}
        for c in chunks:
            d = _doc_of_chunk(c)
            srcs[d] = srcs.get(d, 0) + 1
        print(f"    chunk来源分布: {dict(sorted(srcs.items()))}")


async def run_questions(rag: LightRAG, questions: list[tuple[str, str]], modes: list[str]) -> None:
    for label, q in questions:
        print(f"\n{'='*70}\n[{label}] {q}\n{'='*70}")
        for mode in modes:
            try:
                await run_one(rag, label, q, mode)
            except Exception as e:  # noqa: BLE001
                print(f"  [{mode}] ERROR: {type(e).__name__}: {e}")


async def run_formal(rag: LightRAG, questions: list[tuple[str, str]], sparse_doc: dict, entities: list[str] | None) -> None:
    """正式链路（三路 RRF + rerank + 溯源 + query 预处理）。"""
    for label, q in questions:
        print(f"\n{'='*70}\n[{label}] {q}\n{'='*70}")
        try:
            res = await formal_retrieve(rag, q, sparse_doc, entities=entities)
        except Exception as e:  # noqa: BLE001
            print(f"  ERROR: {type(e).__name__}: {e}")
            continue
        rt = res["routes"]
        print(f"  routes: graph={len(rt['graph'])} vector={len(rt['vector'])} keyword={len(rt['keyword'])}")
        prep = res.get("preprocess")
        if prep and prep.get("ll_keywords"):
            print(f"  preprocess: ll_kw={prep['ll_keywords']} matched={len(prep['matched_entities'])} entities")
        for r in res["results"]:
            src = r["full_doc_id"][:16]
            print(f"    #{r['rank']} [{src}] {r['score']:.4f}  {r['content'][:56]!r}")


async def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="m5-retrieve", description="M5 检索冒烟（LightRAG 召回验证）")
    ap.add_argument("-w", "--working-dir", default="data/default_ws", help="索引工作目录")
    ap.add_argument("-q", "--question", default=None, help="单个查询（默认跑内置问题集）")
    ap.add_argument("-m", "--mode", default=None, help="检索模式 local/global/mix/naive（默认全跑）")
    ap.add_argument("--formal", action="store_true", help="正式链路：三路 RRF + rerank + 溯源（PG 库）")
    args = ap.parse_args(argv)

    proj = Path(__file__).resolve().parents[2]
    _load_dotenv(proj / ".env")
    rag = await build_rag(proj / args.working_dir)

    if args.question:
        questions = [("query", args.question)]
    else:
        questions = DEFAULT_QUESTIONS

    if args.formal:
        import os

        ws = os.environ.get("POSTGRES_WORKSPACE", "default_ws")
        sparse_path = proj / args.working_dir / SPARSE_FILE
        if not sparse_path.exists():
            print(f"[formal] 稀疏索引不存在，构建 -> {sparse_path}")
            await asyncio.to_thread(build_sparse, ws, sparse_path)
        try:
            entities = await load_entities_async(ws)
            print(f"[formal] 加载实体名 {len(entities)} 个（query 预处理用）")
        except Exception as e:  # noqa: BLE001
            print(f"[formal] 实体名加载失败（跳过预处理）：{type(e).__name__}: {e}")
            entities = None
        await run_formal(rag, questions, load_sparse(sparse_path), entities)
    else:
        modes = [args.mode] if args.mode else MODES
        await run_questions(rag, questions, modes)

    await rag.finalize_storages()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))