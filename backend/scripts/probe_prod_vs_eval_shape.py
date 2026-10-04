"""生产口径 vs 评测口径：comparison 题「同一 query 打所有文档」与「每文档定制子查询」的召回差。

背景（用户 2026-10-03 方法论质疑）：评测题集的 per_doc_queries 是**每文档各自定制**的
子查询，而生产 `app/m7_interact/compare.py` 是**同一个 query 打所有 doc**
（`POST /compare {query, doc_ids}`）。若评测用前者、生产只有后者，则评测数值虚高。

本脚本做受控对照：把评测的 per_doc_queries 取**并集**、同一套 query 打两个文档
（= 用户对每台设备各发起一次 compare 调用、参数名相同的真实用法），
与评测的「每文档定制」跑法对比，看 top-k 命中差。

跑法：cd backend && python3 scripts/probe_prod_vs_eval_shape.py（需 PG + Xinference）
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.m5_retrieve.retriever import retrieve  # noqa: E402
from app.m9_eval.runner import (  # noqa: E402
    _build_deps,
    _interleave,
    _load_doc_name_map,
    _merge_subqueries,
    _resolve_collection,
)

COLLECTION = "col_b7b876b1"
TESTSET = Path(__file__).resolve().parents[1] / "tests/testsets/testset_device_30.json"

# 生产 compare_params 的 TOP_K=3；评测窗口 comparison=12
PROD_TOP_K = 3
EVAL_WIN = 12

# key_fact 证据关键词（人工标注，用于判定命中）
EVIDENCE = {
    "DV-CP-001": [["灌注", "抽取", "连续"], ["120"], ["140", "双通道"]],
    "DV-CP-002": [["255", "240"], ["279"]],
    "DV-CP-003": [["99%", "35%"], ["100%"]],
    "DV-CP-004": [["24小时", "蓝牙", "CF"], ["Android", "会诊"]],
}


async def _run_doc(rag, sparse, entities, doc_id: str, subs: list[str], top_k: int) -> list[dict]:
    """对单文档跑一组子查询并合并（复刻 runner 的 _merge_subqueries）。"""
    lists = []
    for s in subs:
        r = await retrieve(rag, s, sparse, entities=entities, allowed_docs=[doc_id])
        lists.append(r.get("results") or [])
    return _merge_subqueries(lists)[:top_k]


def _hit_keywords(chunks: list[dict], kws: list[str]) -> bool:
    blob = " ".join(c.get("content", "") for c in chunks)
    return any(k in blob for k in kws)


async def main() -> None:
    data = json.loads(TESTSET.read_text(encoding="utf-8"))
    qs = data["questions"] if isinstance(data, dict) and "questions" in data else data
    cmp_qs = [q for q in qs if q.get("category") == "comparison"]

    wd, ws = _resolve_collection(COLLECTION)
    rag, sparse, _qf, entities = await _build_deps(wd, ws)
    fn2id = _load_doc_name_map(wd)

    print(f"{'题号':<10} {'口径':<8} {'窗口':<6} 命中事实 / 总数")
    print("-" * 60)
    summary = {"eval": [0, 0], "prod": [0, 0]}
    for q in cmp_qs:
        qid = q["id"]
        ev = EVIDENCE.get(qid)
        if not ev:
            continue
        pdq = q.get("per_doc_queries") or {}
        docs = [fn2id.get(fn) for fn in (q.get("source_docs") or [])]
        docs = [d for d in docs if d]
        if len(docs) < 2:
            print(f"{qid}: 文档映射不全，跳过 ({q.get('source_docs')})")
            continue

        # 并集（生产口径：同一套 query 打两个文档）
        union: list[str] = []
        for fn in (q.get("source_docs") or []):
            v = pdq.get(fn)
            subs = [v] if isinstance(v, str) else list(v or [])
            for s in subs:
                if s not in union:
                    union.append(s)

        for label, win, per_doc in (("eval", EVAL_WIN, True), ("prod", PROD_TOP_K, False)):
            # 每文档取结果
            per_doc_lists = []
            for fn, did in zip(q.get("source_docs") or [], docs):
                v = pdq.get(fn)
                subs = [v] if isinstance(v, str) else list(v or [])
                if not per_doc:
                    subs = union  # 生产：所有文档用同一套
                per_doc_lists.append(await _run_doc(rag, sparse, entities, did, subs, win))
            merged = _interleave(per_doc_lists)[:win]

            # 事实命中：每个 fact 的证据关键词落在该 fact 所属 doc 的结果里
            hits = 0
            for fi, kws in enumerate(ev):
                # fact 顺序与 source_docs 顺序一致
                src = per_doc_lists[fi] if fi < len(per_doc_lists) else merged
                if _hit_keywords(src, kws):
                    hits += 1
            summary[label][0] += hits
            summary[label][1] += len(ev)
            print(f"{qid:<10} {label:<8} top{win:<4} {hits} / {len(ev)}")

    print("-" * 60)
    for label in ("eval", "prod"):
        h, t = summary[label]
        print(f"{label:<8} 汇总: {h}/{t} = {h/t:.4f}" if t else f"{label}: 无数据")
    print(f"\n注：eval=每文档定制子查询（题集 per_doc_queries）；prod=同一套 query 打所有文档（线上 compare.py 形态）")


if __name__ == "__main__":
    asyncio.run(main())
