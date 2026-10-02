"""探针：验证 comparison 题的 per-doc 检索为何必须用「不含其他型号名」的子查询。

背景（v5.29）：题集 comparison 题的 `question` 是完整对比问题（含两个型号名），
而线上 M7 `compare.py` 用的是「参数名 + 指定文档」。若直接把完整问题喂给 per-doc
检索，其他型号名会把本文档的向量召回打到 0（LightRAG 向量路 cosine=0.2 阈值）。
本脚本对同一文档跑两次检索（完整问题 vs 题集 `per_doc_queries` 子查询）对比条数。

跑法：cd backend && python3 scripts/probe_perdoc_subquery.py
需要 PG + Xinference 在跑（`./dev.sh status`）。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.m9_eval.runner import _build_deps, _resolve_collection  # noqa: E402

COLLECTION = "col_b7b876b1"
# DV-CP-001：题集里的完整对比问题
FULL_Q = (
    "融柏 LSP-1C 系列（型号 LSP01-1BC）与中芯启恒 Cchippump-2 两台注射泵，"
    "在运行/工作模式与行程上限上有何不同？"
)
# DV-CP-001 的 per_doc_queries[融柏恒流泵_LSP-1C系列注射泵_说明书.pdf]
SUB_Q = "融柏 LSP-1C 系列（型号 LSP01-1BC）注射泵有哪几种运行模式？自定义行程的范围是多少？"
LSP_DOC = "8a22bfff101b31f1"


async def main() -> None:
    from app.m5_retrieve.retriever import retrieve

    working_dir, workspace = _resolve_collection(COLLECTION)
    rag, sparse, _query_func, entities = await _build_deps(working_dir, workspace)

    for tag, q in (("完整对比问题", FULL_Q), ("per_doc 子查询", SUB_Q)):
        r = await retrieve(rag, q, sparse, entities=entities, allowed_docs=[LSP_DOC])
        print(f"  {tag}: {len(r.get('results', []))} 条  ← {q}")


if __name__ == "__main__":
    asyncio.run(main())
