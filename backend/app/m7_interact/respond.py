"""M7 应答：调 M6 orchestrator → JSON 或 SSE 事件流。"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator

from app.m6_generate.orchestrator import answer, answer_stream

from .bootstrap import AppDeps
from .events import sse
from .history import build_query_with_history


def _jsonable_result(result: dict[str, Any]) -> dict[str, Any]:
    """retrieval 里 M5 原始结果可能含非 JSON 类型（如 numpy 标量），序列化失败就摘要化。"""
    retr = result.get("retrieval")
    try:
        json.dumps(retr, ensure_ascii=False)
        return result
    except (TypeError, ValueError):
        return {
            **result,
            "retrieval": {
                "__truncated__": True,
                "query": retr.get("query"),
                "n_results": len(retr.get("results", [])),
            },
        }


async def make_answer(
    deps: AppDeps,
    query: str,
    history: list[dict] | None = None,
    response_type: str | None = None,
) -> dict[str, Any]:
    q = build_query_with_history(query, history)
    excluded = sorted(deps.excluded_docs)
    result = await answer(
        deps.rag, q, deps.sparse, entities=deps.entities,
        sidecar=deps.sidecar, query_func=deps.query_func,
        response_type=response_type, exclude_docs=excluded,
    )
    return _jsonable_result(result)


async def stream_answer(
    deps: AppDeps,
    query: str,
    history: list[dict] | None = None,
    response_type: str | None = None,
) -> AsyncIterator[str]:
    q = build_query_with_history(query, history)
    async for event, payload in answer_stream(
        deps.rag, q, deps.sparse, entities=deps.entities,
        sidecar=deps.sidecar, query_stream_func=deps.query_stream_func,
        response_type=response_type, exclude_docs=sorted(deps.excluded_docs),
    ):
        yield sse(event, payload)