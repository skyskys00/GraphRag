"""M6 生成层 CLI：query → 答案 + 引用（单轮，非流式）。

运行：
    python -m app.m6_generate.runner -w data/lightrag_m4 [-q 问题]

依赖：PG 库在跑（M4）、Xinference 9997（M0/M5）、data/chunks 产物（M2）。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from app.m3_index.providers import build_llm_func
from app.m3_index.runner import _load_dotenv, build_rag
from app.m5_retrieve.query_preprocess import load_entities_async
from app.m5_retrieve.sparse_index import SPARSE_FILE, build as build_sparse, load as load_sparse

from .orchestrator import answer
from .sidecar import load as load_sidecar

DEFAULT_QUESTIONS = [
    ("智能客服", "智能客服系统有哪些核心功能？涉及哪些主要角色？"),
    ("季度复盘", "本季度销售业绩表现如何？哪些区域或产品线增长明显？"),
    ("投诉处理", "客户投诉的处理流程是怎样的？分哪几个步骤？"),
    ("实体关联", "智能客服系统迭代会议决定了哪些事项？谁负责？"),
    ("缩写-PRD版本", "PRD V2.1写了啥"),
]


async def run_one(rag, q: str, sparse_doc, entities, sidecar, query_func, respond: str | None) -> None:
    import time

    t0 = time.perf_counter()
    res = await answer(
        rag, q, sparse_doc, entities=entities, sidecar=sidecar, query_func=query_func,
        response_type=respond,
    )
    dt = time.perf_counter() - t0
    print(f"  [mode] {res['meta']['mode']} | chunks={res['meta']['used_chunks']} "
          f"| citations={len(res['citations'])} | {dt:.1f}s")
    print(f"  text: {res['text']}")
    for c in res["citations"]:
        print(f"    [{c['marker']}] {c['text_unit_id']} | {c['file_path']} "
              f"| page={c['page_range']} | {c['snippet'][:44]!r}")


async def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="m6-generate", description="M6 生成（答案 + 引用，单轮）")
    ap.add_argument("-w", "--working-dir", default="data/lightrag_m4", help="索引工作目录（PG ws 来自 .env）")
    ap.add_argument("--chunks", default="data/chunks", help="M2 chunk 产物目录（sidecar 来源）")
    ap.add_argument("-q", "--question", default=None, help="单个查询（默认内置问题集）")
    ap.add_argument("--response", default=None, help="response_type 中文要求（覆盖默认先总后分）")
    args = ap.parse_args(argv)

    proj = Path(__file__).resolve().parents[2]
    _load_dotenv(proj / ".env")
    rag = await build_rag(proj / args.working_dir)

    ws = os.environ.get("POSTGRES_WORKSPACE", "lightrag_m4")
    sparse_path = proj / args.working_dir / SPARSE_FILE
    if not sparse_path.exists():
        print(f"[m6] 稀疏索引不存在，构建 -> {sparse_path}")
        await asyncio.to_thread(build_sparse, ws, sparse_path)
    sparse = load_sparse(sparse_path)

    entities: list[str] | None = None
    try:
        entities = await load_entities_async(ws)
        print(f"[m6] 加载实体名 {len(entities)} 个（query 预处理用）")
    except Exception as e:  # noqa: BLE001
        print(f"[m6] 实体名加载失败（跳过预处理）：{type(e).__name__}: {e}")

    sidecar = load_sidecar(proj / args.chunks)
    print(f"[m6] sidecar {len(sidecar.items)} 条文本段")

    _, query_func = build_llm_func()

    if args.question:
        questions = [("query", args.question)]
    else:
        questions = DEFAULT_QUESTIONS

    for label, q in questions:
        print(f"\n{'=' * 70}\n[{label}] {q}\n{'=' * 70}")
        try:
            await run_one(rag, q, sparse, entities, sidecar, query_func, args.response)
        except Exception as e:  # noqa: BLE001
            print(f"  ERROR: {type(e).__name__}: {e}")

    await rag.finalize_storages()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))