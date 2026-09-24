"""一次性：v5.10 workspace 跑 table_numeric 题，验证生成答案质量。

复用 M6 orchestrator.answer()，指定 eval_cservice_v510_ws。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))

from app.m3_index.runner import _load_dotenv, build_rag
from app.m3_index.providers import build_llm_func
from app.m5_retrieve.sparse_index import SPARSE_FILE, load as load_sparse
from app.m5_retrieve.query_preprocess import load_entities_async
from app.m6_generate.sidecar import load as load_sidecar
from app.m6_generate.orchestrator import answer

WS = "eval_cservice_v510_ws"

QUESTIONS = [
    ("CS-TN-001", "2026 年 Q3 客服中心总工单量是多少？其中 9 月份的工单量比 7 月份增长了多少？"),
    ("CS-TN-002", "Q3 各大区中，哪个大区的人均工单量最高？具体数值是多少？解决率最低的是哪个大区？"),
    ("CS-TN-003", "2026 年中国客服智能化市场规模预计达到多少？相比 2023 年增长了多少倍？"),
    ("CS-TN-004", "客服质检评分标准中，信息准确性和服务态度的分值分别是多少？如果客服被投诉态度问题且有一次信息错误，总共扣多少分？"),
]


async def main() -> None:
    _load_dotenv(PROJ / ".env")
    rag = await build_rag(PROJ / "data" / WS, workspace=WS)
    sparse = load_sparse(PROJ / "data" / WS / SPARSE_FILE)
    print(f"[sparse] chunks={len(sparse.get('chunks', {}))}")
    try:
        entities = await load_entities_async(WS)
        print(f"[entities] {len(entities)} 个")
    except Exception as e:  # noqa: BLE001
        entities = None
        print(f"[entities] 加载失败（跳过）: {type(e).__name__}: {e}")
    sidecar = load_sidecar(PROJ / "data" / "chunks" / "eval_cservice")
    print(f"[sidecar] {len(sidecar.items)} 条")
    _, query_func = build_llm_func()

    out = []
    for qid, q in QUESTIONS:
        print(f"\n{'=' * 70}\n[{qid}] {q}\n{'=' * 70}")
        res = await answer(rag, q, sparse, entities=entities, sidecar=sidecar, query_func=query_func)
        print(f"\n--- 检索 chunks ---")
        for i, r in enumerate(res["retrieval"]["results"]):
            bt = r.get("block_type", "?")
            sc = r.get("score", 0.0)
            print(f"  rank{i+1}: {bt} | score={sc:.3f} | {r['content'][:60]!r}")
        print(f"\n--- 答案 ---")
        print(res["text"])
        print(f"\n--- 引用 ---")
        for c in res["citations"]:
            print(f"  [{c['marker']}] {c['text_unit_id']} | {c['file_path']} | {c['snippet'][:50]!r}")
        print(f"\n--- meta ---")
        print(json.dumps(res["meta"], ensure_ascii=False))
        out.append({"id": qid, "question": q, "text": res["text"],
                    "citations": res["citations"], "meta": res["meta"]})

    with open(PROJ / "tests" / "reports" / "qa_v510_table_numeric.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n保存到 tests/reports/qa_v510_table_numeric.json")
    await rag.finalize_storages()


if __name__ == "__main__":
    asyncio.run(main())