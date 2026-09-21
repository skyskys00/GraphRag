"""M7 应答：调 M6 orchestrator → JSON 或 SSE 事件流。

v7：多会话持久化——带 conversation_id 时历史从后端会话读、答完落库；
缺省（None）退回「前端透传 history」的旧行为（WebUI/旧客户端不受影响）。
"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator

from app.m6_generate.orchestrator import answer, answer_stream

from . import conversations
from .bootstrap import AppDeps
from .collections import add_recent_query
from .events import sse
from .history import build_query_with_history


def _history_from_conversation(deps: AppDeps, conv_id: str) -> list[dict] | None:
    conv = conversations.get_conversation(deps, conv_id)
    if conv is None:
        return None
    return [{"role": m.get("role"), "content": m.get("content") or ""}
            for m in conv.get("messages", [])
            if m.get("role") in ("user", "assistant") and m.get("content")]


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
    conversation_id: str | None = None,
) -> dict[str, Any]:
    if conversation_id:
        history = _history_from_conversation(deps, conversation_id)
    q_gen = build_query_with_history(query, history)
    excluded = sorted(deps.excluded_docs)
    allowed = sorted(deps.allowed_docs) if deps.allowed_docs else None
    result = await answer(
        deps.rag, q_gen, deps.sparse, entities=deps.entities,
        sidecar=deps.sidecar, query_func=deps.query_func,
        response_type=response_type, exclude_docs=excluded,
        allowed_docs=allowed,
        retrieval_query=query,
    )
    add_recent_query(deps.proj, deps.collection_id, query)
    if conversation_id:
        result_j = _jsonable_result(result)
        await conversations.append_round(
            deps, conversation_id,
            user=query,
            assistant={"content": result_j.get("text", ""),
                       "citations": result_j.get("citations"), "meta": result_j.get("meta")},
        )
        return result_j
    return _jsonable_result(result)


async def stream_answer(
    deps: AppDeps,
    query: str,
    history: list[dict] | None = None,
    response_type: str | None = None,
    conversation_id: str | None = None,
) -> AsyncIterator[str]:
    if conversation_id:
        history = _history_from_conversation(deps, conversation_id)
    q_gen = build_query_with_history(query, history)
    allowed = sorted(deps.allowed_docs) if deps.allowed_docs else None
    citations: list[dict] = []
    meta: dict[str, Any] | None = None
    final_text = ""
    async for event, payload in answer_stream(
        deps.rag, q_gen, deps.sparse, entities=deps.entities,
        sidecar=deps.sidecar, query_stream_func=deps.query_stream_func,
        response_type=response_type, exclude_docs=sorted(deps.excluded_docs),
        allowed_docs=allowed,
        retrieval_query=query,
    ):
        if event == "citations":
            citations = payload.get("citations") or []
            meta = payload.get("meta")
        elif event == "done":
            final_text = payload.get("text") or ""
        yield sse(event, payload)
    add_recent_query(deps.proj, deps.collection_id, query)
    if conversation_id and final_text:
        await conversations.append_round(
            deps, conversation_id,
            user=query,
            assistant={"content": final_text, "citations": citations, "meta": meta},
        )