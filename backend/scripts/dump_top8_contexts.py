#!/usr/bin/env python
"""一次性：dump 评测检索上下文（top8 contexts 含 content，供离线迭代 gold_rank 匹配器）。

缓存 35 题 retrieve 结果到 scripts/top8_contexts_cservice35.json，
之后迭代 probe/gold_rank 阈值无需重跑 M5 / judge。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PROJ = Path(__file__).resolve().parents[1]
TESTSET = PROJ / "tests" / "testsets" / "testset_cservice_35.json"
OUT = PROJ / "scripts" / "top8_contexts_cservice35.json"
WORKSPACE = "eval_cservice_ws"


async def main() -> None:
    from app.m9_eval.runner import _build_deps
    from app.m5_retrieve import retriever as ret_mod
    from app.m5_retrieve.retriever import retrieve

    testset = json.load(open(TESTSET, encoding="utf-8"))
    hag, sparse, _ = await _build_deps(WORKSPACE)

    orig_top = ret_mod.RERANK_TOP
    ret_mod.RERANK_TOP = 8
    try:
        payload = []
        total = len(testset["questions"])
        for i, q in enumerate(testset["questions"]):
            print(f"[{i+1}/{total}] {q['id']} ...", flush=True)
            try:
                retr = await retrieve(hag, q["question"], sparse)
                contexts = [
                    {
                        "rank": c.get("rank", 0),
                        "content": c.get("content", ""),
                        "chunk_id": c.get("chunk_id", ""),
                    }
                    for c in retr.get("results", [])[:8]
                ]
            except Exception as e:
                print(f"  ⚠️  {e}", flush=True)
                contexts = []
            payload.append({
                "id": q["id"],
                "category": q["category"],
                "difficulty": q.get("difficulty", ""),
                "question": q["question"],
                "key_facts": q.get("key_facts", []),
                "contexts": contexts,
            })
    finally:
        ret_mod.RERANK_TOP = orig_top

    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    n_ctx = sum(len(p["contexts"]) for p in payload)
    print(f"已写 {OUT}  题数={len(payload)}  总 context 条数={n_ctx}")


if __name__ == "__main__":
    asyncio.run(main())