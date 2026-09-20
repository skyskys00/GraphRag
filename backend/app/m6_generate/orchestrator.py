"""M6 生成层编排：query → M5 检索 → 组装 → 生成 → 引用解析 → Answer。

契约：query + M5 精排 context → 答案（text）+ 结构化引用（citations）。
意图路由 v1 = single-window 常量（route() 留函数位，map-reduce 二期）。
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from app.m3_index.providers import build_llm_func

from .assemble import assemble
from .cite import Citation, parse_citations
from .generate import generate, generate_stream
from .sidecar import Sidecar

DEFAULT_MAX_DATA_TOKENS = 4000
# 意图路由（二期 map-reduce）：跨文档综述判断位
SUMMARY_TRIGGERS = ("对比", "汇总", "综述", "整体", "总体", "一览")


def route(query: str, retr: dict[str, Any]) -> str:
    """意图路由 v1：常量 single-window。full_doc_id 分布 + 综述触发词 → map-reduce 提示位。"""
    sources = {r["full_doc_id"] for r in retr.get("results", [])}
    if len(sources) >= 2 and any(t in query for t in SUMMARY_TRIGGERS):
        return "map-reduce(pending)"  # 二期实现，v1 仍走 single-window 组装
    return "single-window"


async def answer(
    rag,
    query: str,
    sparse_doc: dict[str, Any],
    entities: list[str] | None = None,
    sidecar: Sidecar | None = None,
    query_func=None,
    max_data_tokens: int = DEFAULT_MAX_DATA_TOKENS,
    response_type: str | None = None,
    exclude_docs: list[str] | None = None,
    allowed_docs: list[str] | None = None,
) -> dict[str, Any]:
    """M6 总入口。返回 Answer dict：query/text/citations/retrieval/meta。"""
    from app.m5_retrieve.retriever import retrieve

    retr = await retrieve(
        rag, query, sparse_doc, entities=entities,
        exclude_docs=exclude_docs, allowed_docs=allowed_docs,
    )

    mode = route(query, retr)
    context, markers = assemble(query, retr["results"], max_data_tokens)

    if query_func is None:
        _, query_func = build_llm_func()
    text = await generate(query_func, query, context, response_type)

    citations: list[Citation]
    unmatched: int
    citations, unmatched = parse_citations(text, markers, sidecar)

    src_docs = sorted({c.full_doc_id for c in citations})
    meta = {
        "mode": mode,
        "used_chunks": len(markers),
        "context_tokens": sum((len(v["content"]) + 1) // 2 for v in markers.values()),
        "unmatched_markers": unmatched,
        "source_docs": src_docs,
    }
    return {
        "query": query,
        "text": text,
        "citations": [c.to_dict() for c in citations],
        "retrieval": retr,
        "meta": meta,
    }


async def answer_stream(
    rag,
    query: str,
    sparse_doc: dict[str, Any],
    entities: list[str] | None = None,
    sidecar: Sidecar | None = None,
    query_stream_func=None,
    response_type: str | None = None,
    exclude_docs: list[str] | None = None,
    allowed_docs: list[str] | None = None,
) -> Any:
    """answer() 的流式变体：生成走 query_stream_func（真 token 级），yield (event, payload)。

    事件线：retrieved -> delta（每次增量）-> citations -> done（含完整 text）。
    组装/引用复用 answer() 逻辑，仅生成段换流式，answer() 行为不变。
    """
    from app.m5_retrieve.retriever import retrieve

    retr = await retrieve(
        rag, query, sparse_doc, entities=entities,
        exclude_docs=exclude_docs, allowed_docs=allowed_docs,
    )
    mode = route(query, retr)
    context, markers = assemble(query, retr["results"], DEFAULT_MAX_DATA_TOKENS)

    yield "retrieved", {
        "mode": mode,
        "used_chunks": len(markers),
        "context_tokens": sum((len(v["content"]) + 1) // 2 for v in markers.values()),
        "source_docs": sorted({m["full_doc_id"] for m in markers.values()}),
    }

    parts: list[str] = []
    async for delta in generate_stream(query_stream_func, query, context, response_type):
        parts.append(delta)
        yield "delta", {"text": delta}
    text = "".join(parts)

    citations, unmatched = parse_citations(text, markers, sidecar)
    citations_dicts = [c.to_dict() for c in citations]
    yield "citations", {
        "citations": citations_dicts,
        "meta": {
            "mode": mode,
            "used_chunks": len(markers),
            "unmatched_markers": unmatched,
            "source_docs": sorted({c.full_doc_id for c in citations}),
        },
    }
    yield "done", {"text": text, "query": query}


async def _main() -> None:
    """冒烟：单条 query 走一遍（供 py_compile 后手动验证）。"""
    import app.m3_index.runner as m3runner
    from app.m5_retrieve.query_preprocess import load_entities_async
    from app.m5_retrieve.sparse_index import SPARSE_FILE, load as load_sparse

    from .sidecar import load as load_sidecar

    proj = Path(__file__).resolve().parents[2]
    m3runner._load_dotenv(proj / ".env")
    ws = os.environ.get("POSTGRES_WORKSPACE", "default_ws")
    rag = await m3runner.build_rag(proj / "data" / ws)  # type: ignore[attr-defined]
    sparse = load_sparse(proj / "data" / ws / SPARSE_FILE)
    entities = await load_entities_async(ws)
    sidecar = load_sidecar(proj / "data" / "chunks")
    res = await answer(rag, "客户投诉的处理流程是怎样的？", sparse, entities=entities, sidecar=sidecar)
    print(res["text"])
    print()
    for c in res["citations"]:
        print(f"  [{c['marker']}] {c['text_unit_id']} | {c['file_path']} | page={c['page_range']}")
    await rag.finalize_storages()


if __name__ == "__main__":
    asyncio.run(_main())