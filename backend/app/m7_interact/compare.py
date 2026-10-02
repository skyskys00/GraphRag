"""M7 参数对比：跨文档检索同一 query，并排返回各型号的原文片段。

设计取舍（见 docs/modules/DEVICE_SCENARIO.md §5.1）：**不做 LLM 归并**。
常见做法是「抽参数 → LLM 归并成表」，但那样会引入错位与编造风险。
本模块改为「检索式对比」——对每个型号单独检索同一 query、各取 top_k，
每段都带页码溯源；某型号没有该参数时如实留空，不推断。
"""
from __future__ import annotations

from typing import Any

from app.m5_retrieve.retriever import retrieve

from . import documents
from .bootstrap import AppDeps

MAX_DOCS = 6      # 单次对比的型号上限（前端对比卡再宽也放不下更多）
TOP_K = 3         # 每个型号返回的片段数（M5 精排池上限为 RERANK_TOP=5）
MIN_SCORE = 0.2   # 绝对下限：低于此视为与 query 无关
SCORE_RATIO = 0.35  # 相对阈值：低于本次最高分此比例的片段丢弃


async def compare_params(
    deps: AppDeps, query: str, doc_ids: list[str], top_k: int = TOP_K
) -> dict[str, Any]:
    """对每个 doc_id 单独检索 query，返回并排片段（含页码/图片溯源）。

    doc_ids 按传入顺序去重，超上限截断；不可检索的文档（不存在/已删）在行内如实标 error。
    """
    if not query.strip():
        raise ValueError("query 不能为空")
    ids = [d for d in dict.fromkeys(doc_ids) if d][:MAX_DOCS]
    if not ids:
        raise ValueError("doc_ids 不能为空")

    allowed = deps.allowed_docs
    excluded = deps.excluded_docs or set()
    rows: list[dict[str, Any]] = []

    for doc_id in ids:
        if (allowed is not None and doc_id not in allowed) or doc_id in excluded:
            rows.append({"doc_id": doc_id, "doc_name": doc_id, "error": "文档不存在或已删除", "snippets": []})
            continue

        retr = await retrieve(
            deps.rag, query, deps.sparse, entities=deps.entities, allowed_docs=[doc_id]
        )
        snippets: list[dict[str, Any]] = []
        for r in (retr.get("results") or [])[:top_k]:
            meta = deps.sidecar.resolve(r["chunk_id"]) if deps.sidecar else None
            img = meta.image_path if meta else None
            snippets.append(
                {
                    "content": r["content"],
                    "score": round(float(r["score"]), 4),
                    "page_range": meta.page_range if meta else None,
                    "block_type": meta.block_type if meta else None,
                    "image_url": f"/docs/{doc_id}/{img}" if img else None,
                }
            )
        doc = documents.get_doc(deps, doc_id) or {}
        rows.append(
            {
                "doc_id": doc_id,
                "doc_name": doc.get("filename") or doc_id,
                "snippets": snippets,
            }
        )

    # 相关性门槛：跨行取本次最高分，低于其 SCORE_RATIO（或绝对下限）的片段丢弃。
    # 这是「如实留空」承诺的落点——某型号没有该参数时，宁可返回空，也不要塞不相关片段
    # 让人误以为找到了（实测：问「精度等级」时纯注射泵文档会返回内径表片段）。
    scores = [s["score"] for r in rows for s in r["snippets"]]
    cutoff = max(MIN_SCORE, max(scores) * SCORE_RATIO) if scores else MIN_SCORE
    for r in rows:
        r["snippets"] = [s for s in r["snippets"] if s["score"] >= cutoff]
    return {"query": query, "rows": rows, "score_cutoff": round(cutoff, 4)}
