"""M10 agent 内核：显式 StateGraph（route / execute_tools / generate + 条件边）。

P0 骨架（2026-10-23，探底结论见 M10_agent §10-8）：
- LLM = v4-flash 原生 function calling（thinking=disabled 最稳，探底实证）；
- 工具注册表 = app/m10_agent/registry.py（唯一接缝，fn_factory 按 deps 绑定）；
- 事件 = tool_decision → tool_result → …(逐轮)… → delta → citations → done；
- 节点函数必须返回 dict（LangGraph 1.x 纪律），路由选择用独立条件函数。

尚未接入（P1 起）：M7 conversations 落库、token 级流式 delta、三工具全量。
"""
from __future__ import annotations

import json
from typing import Any, Literal
from typing_extensions import TypedDict

from langgraph.graph import END, START, StateGraph
from openai import AsyncOpenAI

from app.m3_index.providers import _llm_config
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


def _register_builtin_tools() -> None:
    registry.register_tool(
        "tool_list_docs",
        lambda deps: lambda **kw: documents.list_docs(deps),
        "列出知识库中的文档清单（doc_id/文件名/状态），用于确认有哪些文档可查、存在性核对",
        {"type": "object", "properties": {}},
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