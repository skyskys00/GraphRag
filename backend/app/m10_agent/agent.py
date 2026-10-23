"""M10 agent 内核：显式 StateGraph（route / execute_tools / generate + 条件边）。

P1 骨架（2026-10-23，P0 探底结论见 M10_agent §10-8）：
- LLM = v4-flash 原生 function calling（thinking=disabled 最稳，探底实证）；
- 工具注册表 = app/m10_agent/registry.py（唯一接缝，fn_factory 按 deps 绑定）；
- P1 三工具全量 = tool_list_docs / tool_retrieve（M5）/ tool_rag_answer（M6）；
- 事件 = tool_decision → tool_result → …(逐轮)… → delta → citations → done；
- 节点函数必须返回 dict（LangGraph 1.x 纪律），路由选择用独立条件函数；
- conversation 落库由 m7_interact/api.py 承接（append_round，沿 respond.py 口径）。

P1 剩余：token 级流式 delta、前端接 agent、M9 评测回归。
"""
from __future__ import annotations

import json
from typing import Any, Literal
from typing_extensions import TypedDict

from langgraph.graph import END, START, StateGraph
from openai import AsyncOpenAI

from app.m3_index.providers import _llm_config
from app.m5_retrieve.retriever import retrieve
from app.m6_generate.orchestrator import answer as rag_answer
from app.m7_interact import documents

from . import registry

SYSTEM_PROMPT = (
    "你是中文知识库助手。基于工具返回的结果回答问题，答案需带来源（doc_id）。"
    "工具结果不足时如实说明，不要编造。不要复述工具原始输出的非答案内容。"
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
            "citations": [{"doc_id": r["full_doc_id"], "chunk_id": r["chunk_id"], "rank": r["rank"]} for r in res],
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
    events = list(state["events"]) + [
        {"type": "tool_decision", "tool": c.function.name, "args": c.function.arguments or ""} for c in tc
    ]
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
        events.append({"type": "tool_result", "tool": name, "args": args, "summary": _result_summary(result)})
        if isinstance(result, dict) and result.get("citations"):
            citations.extend(result["citations"])
    return {"messages": msgs, "events": events, "citations": citations}


def _result_summary(result: Any) -> str:
    if isinstance(result, dict):
        if result.get("__error__"):
            return f"错误: {result['__error__']}"
        return "结果已返回"
    if isinstance(result, list):
        return f"{len(result)} 项结果"
    return str(result)[:80]


async def generate(state: AgentState) -> dict[str, Any]:
    """生成节点：基于含工具往返的完整消息产出最终答案，收尾事件。"""
    cfg = _llm_config()
    resp = await _chat(cfg, state["messages"], tools=False)
    text = resp.choices[0].message.content or ""
    events = list(state["events"]) + [
        {"type": "delta", "text": text},
        {"type": "citations", "citations": state["citations"]},
        {"type": "done", "text": text, "citations": state["citations"]},
    ]
    return {"answer_text": text, "events": events}


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
) -> dict[str, Any]:
    """跑一次 agent 决策循环。POST /agent（JSON）与 GET /agent/stream（事件）共用入口。"""
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
    })
    return {
        "answer": res.get("answer_text", ""),
        "events": res.get("events", []),
        "citations": res.get("citations", []),
    }