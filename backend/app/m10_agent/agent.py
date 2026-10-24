"""M10 agent 内核：显式 StateGraph（route / execute_tools / generate + 条件边）。

P1 骨架（2026-10-23，P0 探底结论见 M10_agent §10-8）：
- LLM = v4-flash 原生 function calling（thinking=disabled 最稳，探底实证）；
- 工具注册表 = app/m10_agent/registry.py（唯一接缝，fn_factory 按 deps 绑定）；
- P1 三工具全量 = tool_list_docs / tool_retrieve（M5）/ tool_rag_answer（M6）；
- 事件 = tool_decision → tool_result → …(逐轮)… → delta → citations → done；
- 节点函数必须返回 dict（LangGraph 1.x 纪律），路由选择用独立条件函数；
- conversation 落库由 m7_interact/api.py 承接（append_round，沿 respond.py 口径）；
- 事件实时透出 = 节点内 await _emit → on_event 回调 → SSE queue（token 级 delta，v5.45）；
- 生成引用 = [n] 角标协议（对齐 M6 直连语义）：编号清单注入 → LLM 出 [n] → 还原带 marker citations。

P1 剩余：M9 评测回归（agent 测试集 v0 + RAG 对比），[n] 与 token 流式已结。
"""
from __future__ import annotations

import json
import re
from typing import Any, Literal
from typing_extensions import TypedDict

from langgraph.graph import END, START, StateGraph
from openai import AsyncOpenAI

from app.m3_index.providers import _llm_config
from app.m5_retrieve.retriever import retrieve
from app.m6_generate.cite import _html_to_text
from app.m6_generate.orchestrator import answer as rag_answer
from app.m7_interact import documents

from . import registry

SYSTEM_PROMPT = (
    "你是中文知识库助手。基于工具返回的结果回答问题；答案需带引用来源，"
    "回答时用 [n] 标注（n 为最末一条消息中给出的资料编号）。工具结果不足时如实说明，不要编造。"
    "不要复述工具原始输出的非答案内容。"
    "【答案分箱】主体部分只撰写资料中有明确依据的事实内容（每句都能 [n] 对应到资料）；"
    "任何操作建议、安全提醒、经验性结论、超出资料范围的补充说明，必须放在答案末尾的"
    " <advice> 与 </advice> 之间（单独成一段）；没有建议就不要输出 <advice> 段，主体不残留任何 <advice> 标签。"
)
MAX_ROUNDS = 5  # 最大决策轮 N（M10 §2.2 硬约束）


class AgentState(TypedDict, total=False):
    query: str
    collection_id: str
    response_type: str | None
    history: list[dict[str, Any]]
    messages: list[dict[str, Any]]      # 决策环消息流（system/…/user/tool 往返）
    remaining_rounds: int               # 剩余决策轮，每轮递减
    events: list[dict[str, Any]]        # 决策链事件（供 SSE 透出）
    citations: list[dict[str, Any]]
    answer_text: str
    _deps: Any                          # AppDeps（进程内传递，跨节点工具绑定）
    _on_event: Any                      # 异步事件回调 on_event(evt)——SSE 实时透出（进程内传递）


async def _emit(state: AgentState, type_: str, **payload: Any) -> None:
    """若配置了事件回调（SSE 流式），立即透出该事件；否则仅累积（POST JSON 模式）。"""
    fn = state.get("_on_event")
    if fn:
        await fn({"type": type_, **payload})


async def _retrieve_tool(deps: Any):
    """tool_retrieve：M5 三路召回+RRF+rerank（含软删过滤/白名单），返回带来源上下文。"""

    async def fn(query: str, top_k: int = 5, **_kw: Any) -> dict[str, Any]:
        result = await retrieve(
            deps.rag, query, deps.sparse,
            entities=deps.entities,
            exclude_docs=sorted(deps.excluded_docs),
            allowed_docs=sorted(deps.allowed_docs) if deps.allowed_docs else None,
        )
        res = result.get("results", [])[: max(1, int(top_k))]
        citations: list[dict[str, Any]] = []
        for r in res:
            # 复用 M6 sidecar 溯源：chunk_id → M2 text_unit（对齐直连引用字段）
            meta = deps.sidecar.resolve(r["chunk_id"]) if deps.sidecar else None
            c: dict[str, Any] = {
                "doc_id": r["full_doc_id"],
                "full_doc_id": r["full_doc_id"],
                "chunk_id": r["chunk_id"],
                "rank": r["rank"],
                "score": r.get("score", 0.0),
                "snippet": _html_to_text(r["content"])[:120],
                "text_unit_id": (meta.text_unit_id if meta else r["chunk_id"]),
            }
            if meta:
                c["file_path"] = meta.file_path
                if meta.title_path:
                    c["title_path"] = meta.title_path
                if meta.page_range:
                    c["page_range"] = meta.page_range
            citations.append(c)
        return {
            "query": result.get("query"),
            "n_results": len(res),
            "results": [
                {
                    "rank": r["rank"],
                    "doc_id": r["full_doc_id"],
                    "content": r["content"][:1500],
                    "score": round(r.get("score", 0.0), 4),
                }
                for r in res
            ],
            "citations": citations,
        }

    return fn


async def _rag_answer_tool(deps: Any):
    """tool_rag_answer：M6 综合应答一条龙（组装+生成+引用），简单/综述问题直答落点。"""

    async def fn(query: str, response_type: str | None = None, **_kw: Any) -> dict[str, Any]:
        result = await rag_answer(
            deps.rag, query, deps.sparse, entities=deps.entities,
            sidecar=deps.sidecar, query_func=deps.query_func,
            response_type=response_type or None,
            exclude_docs=sorted(deps.excluded_docs),
            allowed_docs=sorted(deps.allowed_docs) if deps.allowed_docs else None,
            retrieval_query=query,
        )
        return {
            "text": result.get("text", ""),
            "citations": result.get("citations", []),
            "status": "answered",
        }

    return fn


def _register_builtin_tools() -> None:
    registry.register_tool(
        "tool_list_docs",
        lambda deps: lambda **kw: documents.list_docs(deps),
        "列出知识库中的文档清单（doc_id/文件名/状态），用于确认有哪些文档可查、存在性核对",
        {"type": "object", "properties": {}},
    )
    registry.register_tool(
        "tool_retrieve",
        _retrieve_tool,
        "在知识库中检索 query，返回带来源（doc_id）的上下文片段——查找具体信息的首选工具",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索问题"},
                "top_k": {"type": "integer", "description": "返回片段条数（默认 5）"},
            },
            "required": ["query"],
        },
    )
    registry.register_tool(
        "tool_rag_answer",
        _rag_answer_tool,
        "直接生成一份带引用的综合应答——适用于需要立即可答/综述/对比，无需再分步检索的场景",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "要回答的问题"},
                "response_type": {"type": "string", "description": "回答要求，如：分段落、先总后分、对比、汇总"},
            },
            "required": ["query"],
        },
    )


_register_builtin_tools()


async def _chat(cfg: dict, messages: list[dict], tools: bool) -> Any:
    client = AsyncOpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"])
    try:
        kw: dict[str, Any] = {"model": cfg["query_model"], "messages": messages, **cfg["extra_kwargs"]}
        if tools:
            kw["tools"] = registry.tool_definitions()
        return await client.chat.completions.create(**kw)
    finally:
        await client.close()


async def route(state: AgentState) -> dict[str, Any]:
    """决策节点：LLM bind_tools 判「调工具 / 直答」，把 assistant 消息与 tool_decision 事件写入状态。"""
    cfg = _llm_config()
    resp = await _chat(cfg, state["messages"], tools=True)
    msg = resp.choices[0].message
    tc = msg.tool_calls or []
    msgs = list(state["messages"])
    if tc:
        msgs.append({
            "role": "assistant", "content": msg.content or "",
            "tool_calls": [
                {"id": c.id, "type": "function",
                 "function": {"name": c.function.name, "arguments": c.function.arguments}}
                for c in tc
            ],
        })
    else:
        msgs.append({"role": "assistant", "content": msg.content or ""})
    events = list(state["events"])
    for c in tc:
        evt = {"type": "tool_decision", "tool": c.function.name, "args": c.function.arguments or ""}
        events.append(evt)
        await _emit(state, "tool_decision", tool=evt["tool"], args=evt["args"])
    return {
        "messages": msgs,
        "events": events,
        "remaining_rounds": state["remaining_rounds"] - 1,
    }


def decide(state: AgentState) -> Literal["execute_tools", "generate"]:
    """条件函数（LangGraph 1.x：路由选择独立于节点）：还有工具请求且余轮>0 → 执行，否则直答。"""
    if state["remaining_rounds"] > 0 and state["messages"][-1].get("tool_calls"):
        return "execute_tools"
    return "generate"


async def execute_tools(state: AgentState) -> dict[str, Any]:
    """工具节点：本批 tool_calls 全部 dispatch，ToolMessage 回填 + tool_result 事件 + citations 收集。"""
    calls = (state["messages"][-1].get("tool_calls") or [])
    msgs, events, citations = list(state["messages"]), list(state["events"]), list(state["citations"])
    for call in calls:
        name = call["function"]["name"]
        args = json.loads(call["function"]["arguments"] or "{}")
        try:
            result = await registry.dispatch(name, args, state["_deps"])
        except Exception as e:  # noqa: BLE001 —— 工具失败不杀 agent loop
            result = {"__error__": f"{type(e).__name__}: {e}"}
        msgs.append({
            "role": "tool", "tool_call_id": call["id"],
            "content": json.dumps(result, ensure_ascii=False, default=str)[:4000],
        })
        summary = _result_summary(result)
        snippets = _result_snippets(result)
        events.append({"type": "tool_result", "tool": name, "args": args, "summary": summary, "snippets": snippets})
        await _emit(state, "tool_result", tool=name, args=args, summary=summary, snippets=snippets)
        if isinstance(result, dict) and result.get("citations"):
            citations.extend(result["citations"])
    return {"messages": msgs, "events": events, "citations": citations}


def _result_snippets(result: Any, limit: int = 3) -> list[str]:
    """从工具返回提取命中预览（决策链展开用，前 limit 条、每条 ~100 字）。

    来源优先级：citations[].snippet（回收缩器的检索片段）→ results[].content。
    """
    out: list[str] = []
    if not isinstance(result, dict):
        return out
    for c in result.get("citations") or []:
        if isinstance(c, dict):
            s = (c.get("snippet") or "") or (c.get("content") or "")[:100]
            if s:
                out.append(s[:100])
    if not out:
        for r in (result.get("results") or []):
            if isinstance(r, dict) and r.get("content"):
                out.append(r["content"][:100])
    return out[:limit]


def _result_summary(result: Any) -> str:
    if isinstance(result, dict):
        if result.get("__error__"):
            return f"错误: {result['__error__']}"
        return "结果已返回"
    if isinstance(result, list):
        return f"{len(result)} 项结果"
    return str(result)[:80]


_NUM_RE = re.compile(r"\[(\d+)\]")


def _build_citation_index(citations: list[dict]) -> tuple[dict[int, dict], str | None]:
    """把累积 citations 按 (doc_id, chunk_id) 去重编号，返回 (编号→引用, 注入清单文本)。

    编号 n 即生成阶段的 [n] 引用号（对齐 M6 直连语义：正文 [n] ↔ citations marker）。
    """
    seen: set[tuple[str, str]] = set()
    index: dict[int, dict] = {}
    lines: list[str] = []
    for c in citations:
        doc_id = c.get("doc_id") or c.get("full_doc_id") or ""
        chunk_id = c.get("chunk_id") or ""
        key = (doc_id, chunk_id)
        if not doc_id or key in seen:
            continue
        seen.add(key)
        n = len(index) + 1
        index[n] = c
        snippet = (c.get("snippet") or "")[:120] or (c.get("content") or "")[:120]
        lines.append(f"[{n}] {doc_id}：{snippet}")
    return index, "\n".join(lines) if lines else None


def _resolve_citations(text: str, index: dict[int, dict]) -> list[dict]:
    """从答案文本提取 [n]，映射回带 marker 的引用；未在清单中的编号（编造/漂移）直接丢弃。"""
    used: list[dict] = []
    seen: set[int] = set()
    for m in _NUM_RE.finditer(text):
        n = int(m.group(1))
        if n in seen or n not in index:
            continue
        seen.add(n)
        c = dict(index[n])
        c["marker"] = n
        used.append(c)
    return used


async def _stream_chat(cfg: dict, messages: list[dict]) -> Any:
    """流式对话调用（generate 用）：逐 chunk yield content 增量文本。"""
    client = AsyncOpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"])
    try:
        kw: dict[str, Any] = {"model": cfg["query_model"], "messages": messages, **cfg["extra_kwargs"]}
        stream = await client.chat.completions.create(**kw, stream=True)
        async for chunk in stream:
            delta = chunk.choices[0].delta.content or ""
            if delta:
                yield delta
    finally:
        await client.close()


async def generate(state: AgentState) -> dict[str, Any]:
    """生成节点：流式产出最终答案 + [n] 引用还原。

    事件：SSE 侧逐 chunk 透出 delta（打字机），聚合端（POST）events 保留整段 delta；
    末尾 citations/done 使用还原后的带 marker 引用。
    """
    cfg = _llm_config()
    index, index_text = _build_citation_index(state["citations"])
    messages = list(state["messages"])
    if index_text:
        messages.append({"role": "system", "content": f"可引用资料（编号即 [n]）：\n{index_text}"})

    text = ""
    events = list(state["events"])
    async for delta in _stream_chat(cfg, messages):
        if not delta:
            continue
        text += delta
        await _emit(state, "delta", text=delta)
    events.append({"type": "delta", "text": text})

    # 答案分箱（v5.48）：<advice>…</advice> 区为建议/补充，剥离开；主体不留标签。
    # [n] 引用只对主体还原；无 <advice> 段时整段当主体（容错降级为旧形态）。
    advice = ""
    m = re.search(r"<advice>(.*?)</advice>", text, flags=re.S)
    if m:
        advice = m.group(1).strip()
        main = re.sub(r"<advice>.*?</advice>", "", text, flags=re.S).strip()
    else:
        main = text.strip()

    final = _resolve_citations(main, index) or state["citations"]
    events += [
        {"type": "citations", "citations": final},
        {"type": "done", "text": main, "advice": advice, "citations": final},
    ]
    await _emit(state, "citations", citations=final)
    await _emit(state, "done", text=main, advice=advice, citations=final)
    return {"answer_text": main, "advice": advice, "events": events, "citations": final}


def build_graph() -> StateGraph:
    g = StateGraph(AgentState)
    g.add_node("route", route)
    g.add_node("execute_tools", execute_tools)
    g.add_node("generate", generate)
    g.add_edge(START, "route")
    g.add_conditional_edges("route", decide, {"execute_tools": "execute_tools", "generate": "generate"})
    g.add_edge("execute_tools", "route")
    g.add_edge("generate", END)
    return g.compile()


def _initial_messages(query: str, history: list[dict] | None, response_type: str | None) -> list[dict]:
    msgs: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for h in history or []:
        if h.get("role") in ("user", "assistant") and h.get("content"):
            msgs.append({"role": h["role"], "content": str(h["content"])})
    if response_type:
        query = f"{query}\n（回答要求：{response_type}）"
    msgs.append({"role": "user", "content": query})
    return msgs


async def run_agent(
    deps: Any,
    query: str,
    *,
    history: list[dict] | None = None,
    response_type: str | None = None,
    max_rounds: int = MAX_ROUNDS,
    on_event: Any = None,
) -> dict[str, Any]:
    """跑一次 agent 决策循环。POST /agent（JSON）与 GET /agent/stream（事件）共用入口。

    on_event: 可选异步回调 on_event(evt)，SSE 端点传入以实时透出事件（节点内 await 透出）。
    """
    app = build_graph()
    res = await app.ainvoke({
        "query": query,
        "collection_id": getattr(deps, "collection_id", "default"),
        "response_type": response_type,
        "history": history or [],
        "remaining_rounds": max_rounds,
        "messages": _initial_messages(query, history, response_type),
        "events": [],
        "citations": [],
        "_deps": deps,
        "_on_event": on_event,
    })
    return {
        "answer": res.get("answer_text", ""),
        "advice": res.get("advice", ""),
        "events": res.get("events", []),
        "citations": res.get("citations", []),
    }