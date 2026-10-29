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
import logging
import re
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Literal
from typing_extensions import TypedDict

from langgraph.graph import END, START, StateGraph
from openai import AsyncOpenAI

from app.m3_index.providers import _llm_config
from app.m5_retrieve.retriever import retrieve
from app.m6_generate.cite import _html_to_text
from app.m6_generate.orchestrator import answer as rag_answer
from app.m7_interact import documents

from . import registry, telemetry

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "你是中文知识库助手。基于工具返回的结果回答问题；答案需带引用来源，"
    "回答时用 [n] 标注（n 为最末一条消息中给出的资料编号）。工具结果不足时如实说明，不要编造。"
    "除纯问候或与本知识库无关的闲聊外，回答任何事实性问题前必须先调用检索工具获取新的资料依据，"
    "不得仅凭已有对话历史或上一轮检索结果直接作答。"
    "一个事实语句若依据跨多个资料块，必须并列标注全部相关编号（如 [1][2]），禁止只标其中一块；"
    "编号必须是最末一条消息资料清单里实际出现的编号，清单里没有的不要标。"
    "不要复述工具原始输出的非答案内容。"
    "【答案分箱】主体部分只撰写资料中有明确依据的事实内容（每句都能 [n] 对应到资料）；"
    "任何操作建议、安全提醒、经验性结论、超出资料范围的补充说明，必须放在答案末尾的"
    " <advice> 与 </advice> 之间（单独成一段）；没有建议就不要输出 <advice> 段，主体不残留任何 <advice> 标签。"
)
MAX_ROUNDS = 5  # 最大决策轮 N（M10 §2.2 硬约束）
MAX_HISTORY_MSGS = 8        # 历史注入上限：最近 4 轮（问+答）
HISTORY_CHAR_BUDGET = 6000  # 历史字符预算（中文约 1 字 ≈ 1 token，留余量）


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
    spans: list[dict[str, Any]]        # 节点级运行数据（v6.2 可观测；内核只采集，API 层落库）


# v6.4：deps / on_event 不进 state —— checkpointer 会把 state msgpack 序列化落 PG，
# 而 AppDeps（LightRAG 实例/函数/Path/set）不可序列化，恢复后也是死对象。
# 改走 contextvar：同 asyncio 任务内可见、不进快照（等同 LangGraph 的 runtime context）。
_CTX: ContextVar[dict[str, Any]] = ContextVar("agent_ctx", default={})


def _ctx(key: str, default: Any = None) -> Any:
    return _CTX.get().get(key, default)


async def _emit(state: AgentState, type_: str, **payload: Any) -> None:
    """若配置了事件回调（SSE 流式），立即透出该事件；否则仅累积（POST JSON 模式）。"""
    fn = _ctx("on_event")
    if fn:
        await fn({"type": type_, **payload})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _span(node: str, t0: float, started_at: str, **fields: Any) -> dict[str, Any]:
    """节点级 span：耗时 + 可选 token 用量，供 API 层落库（v6.2）。"""
    return {
        "span_id": uuid.uuid4().hex[:12],
        "node": node,
        "started_at": started_at,
        "duration_ms": int((time.perf_counter() - t0) * 1000),
        **fields,
    }


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
        "列出知识库中的文档清单（doc_id/文件名/状态），用于盘点知识库有哪些文档、存在性核对。"
        "应在检索/作答之前作为第一步调用；已进入检索或作答后，一般无需再调用（避免冗余盘点）",
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
    t0, started_at = time.perf_counter(), _now()
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
    u = resp.usage
    spans = [*state.get("spans", []), _span(
        "route", t0, started_at,
        tokens_in=getattr(u, "prompt_tokens", None),
        tokens_out=getattr(u, "completion_tokens", None),
    )]
    return {
        "messages": msgs,
        "events": events,
        "spans": spans,
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
    spans = list(state.get("spans", []))
    for call in calls:
        name = call["function"]["name"]
        args = json.loads(call["function"]["arguments"] or "{}")
        t0, started_at = time.perf_counter(), _now()
        try:
            result = await registry.dispatch(name, args, _ctx("deps"))
        except Exception as e:  # noqa: BLE001 —— 工具失败不杀 agent loop
            result = {"__error__": f"{type(e).__name__}: {e}"}
        spans.append(_span(
            "execute_tools", t0, started_at, tool=name,
            detail={"args": json.dumps(args, ensure_ascii=False)[:300]},
        ))
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
    return {"messages": msgs, "events": events, "citations": citations, "spans": spans}


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


def _strip_unbacked_markers(text: str, index: dict[int, dict]) -> str:
    """剥掉编号清单之外的 [n]（未检索直答时的编造编号等），防假引用出现在正文。"""
    return re.sub(r"[ \t]*\[(\d+)\]", lambda m: m.group(0) if int(m.group(1)) in index else "", text)


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


async def _stream_chat(cfg: dict, messages: list[dict], usage: dict[str, int] | None = None) -> Any:
    """流式对话调用（generate 用）：逐 chunk yield content 增量文本。

    传入 usage 字典时顺带回填 token 用量（v6.2 可观测）。include_usage 是 OpenAI 兼容扩展，
    个别后端会拒绝 —— 此时回退一次不带该参数，用量记空，不影响生成。
    """
    client = AsyncOpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"])
    try:
        kw: dict[str, Any] = {"model": cfg["query_model"], "messages": messages, **cfg["extra_kwargs"]}
        try:
            stream = await client.chat.completions.create(
                **kw, stream=True, stream_options={"include_usage": True})
        except Exception:  # noqa: BLE001 —— 后端不支持 include_usage 时降级
            stream = await client.chat.completions.create(**kw, stream=True)
        async for chunk in stream:
            u = getattr(chunk, "usage", None)
            if u is not None and usage is not None:
                usage["tokens_in"] = getattr(u, "prompt_tokens", None) or 0
                usage["tokens_out"] = getattr(u, "completion_tokens", None) or 0
            if not chunk.choices:
                continue
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
    t0, started_at = time.perf_counter(), _now()
    index, index_text = _build_citation_index(state["citations"])
    messages = list(state["messages"])
    if index_text:
        messages.append({"role": "system", "content": f"可引用资料（编号即 [n]）：\n{index_text}"})
    else:
        messages.append({
            "role": "system",
            "content": "本轮可引用资料清单为空：不要输出任何 [n] 角标；如实基于现有信息回答，或说明无法回答。",
        })

    text = ""
    events = list(state["events"])
    usage: dict[str, int] = {}
    async for delta in _stream_chat(cfg, messages, usage):
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

    main = _strip_unbacked_markers(main, index)
    advice = _strip_unbacked_markers(advice, index)

    final = _resolve_citations(main, index) or state["citations"]
    events += [
        {"type": "citations", "citations": final},
        {"type": "done", "text": main, "advice": advice, "citations": final},
    ]
    await _emit(state, "citations", citations=final)
    await _emit(state, "done", text=main, advice=advice, citations=final)
    spans = [*state.get("spans", []), _span(
        "generate", t0, started_at,
        tokens_in=usage.get("tokens_in"), tokens_out=usage.get("tokens_out"),
    )]
    return {"answer_text": main, "advice": advice, "events": events, "citations": final, "spans": spans}


def build_graph(checkpointer: Any = None) -> StateGraph:
    g = StateGraph(AgentState)
    g.add_node("route", route)
    g.add_node("execute_tools", execute_tools)
    g.add_node("generate", generate)
    g.add_edge(START, "route")
    g.add_conditional_edges("route", decide, {"execute_tools": "execute_tools", "generate": "generate"})
    g.add_edge("execute_tools", "route")
    g.add_edge("generate", END)
    return g.compile(checkpointer=checkpointer)


_HIST_MARK_RE = re.compile(r"\s*\[\d+\]")


def _strip_history_markers(text: str) -> str:
    """剥除历史答案里的旧 [n] 角标。

    编号每轮按当轮 citations 重排，旧编号复用会指向错块（§11.2「旧角标语义漂移」）。
    """
    return _HIST_MARK_RE.sub("", text)


def _prepare_history(history: list[dict]) -> tuple[list[dict], int]:
    """裁剪历史：最近 MAX_HISTORY_MSGS 条 + 字符预算，剥除旧角标；返回 (历史, 省略条数)。"""
    msgs = [
        {"role": h["role"], "content": str(h.get("content") or "")}
        for h in history or []
        if h.get("role") in ("user", "assistant") and h.get("content")
    ]
    dropped = 0
    if len(msgs) > MAX_HISTORY_MSGS:
        dropped += len(msgs) - MAX_HISTORY_MSGS
        msgs = msgs[-MAX_HISTORY_MSGS:]
    total = sum(len(m["content"]) for m in msgs)
    while msgs and total > HISTORY_CHAR_BUDGET:
        total -= len(msgs.pop(0)["content"])
        dropped += 1
    if msgs and msgs[0]["role"] != "user":  # 按轮裁剪：不从答案中间切断
        dropped += 1
        msgs.pop(0)
    for m in msgs:
        if m["role"] == "assistant":
            m["content"] = _strip_history_markers(m["content"])
    return msgs, dropped


def _messages_to_history(messages: list[dict]) -> list[dict]:
    """checkpoint 消息流 → 可复用历史：只留 user 与「已作答」的 assistant 文本。

    跳过 tool 消息、空内容、以及带 tool_calls 的 assistant 消息——后者是 route 节点的
    决策前导语（「我先检索…」），不是给用户看的答案，进历史会污染上下文（且导致连续 assistant）。
    裁剪交给下游 `_prepare_history`（预算 6000 字符），此处不做二次裁剪。
    """
    return [
        {"role": m["role"], "content": m["content"]}
        for m in messages or []
        if m.get("role") in ("user", "assistant")
        and (m.get("content") or "").strip()
        and not m.get("tool_calls")
    ]


def _prior_context_text(citations: list[dict]) -> str:
    """把上一轮引用压成「已知资料」提示（工作记忆）：让 route 知道已有哪些依据，追问走增量检索。"""
    lines = []
    for c in citations[:5]:
        doc = c.get("doc_id") or c.get("full_doc_id") or ""
        snip = (c.get("snippet") or "")[:80]
        lines.append(f"- {doc}：{snip}")
    return (
        "上一轮已检索到的资料（可结合本轮检索使用；引用编号在生成时统一分配，勿自行编造）：\n"
        + "\n".join(lines)
    )


def _initial_messages(
    query: str,
    history: list[dict] | None,
    response_type: str | None,
    prior_citations: list[dict] | None = None,
) -> list[dict]:
    msgs: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    hist, dropped = _prepare_history(history or [])
    if dropped:
        msgs.append({
            "role": "system",
            "content": f"更早的 {dropped} 条历史对话已省略；如需依赖更早内容，请重新检索确认。",
        })
    msgs.extend(hist)
    if prior_citations:
        msgs.append({"role": "system", "content": _prior_context_text(prior_citations)})
    if response_type:
        query = f"{query}\n（回答要求：{response_type}）"
    msgs.append({"role": "user", "content": query})
    return msgs


async def run_agent(
    deps: Any,
    query: str,
    *,
    conversation_id: str | None = None,
    history: list[dict] | None = None,
    prior_citations: list[dict] | None = None,
    response_type: str | None = None,
    max_rounds: int = MAX_ROUNDS,
    on_event: Any = None,
) -> dict[str, Any]:
    """跑一次 agent 决策循环。POST /agent（JSON）与 GET /agent/stream（事件）共用入口。

    conversation_id: 有值时挂 checkpointer（thread_id = conversation_id），
      多轮历史以 checkpoint 为唯一来源；无快照（新会话/老会话）时用 history 作种子。
      评测与脚本不传该参数 → 走调用方给的 history，不写 checkpoint。
    history: 无 checkpoint 时的历史来源（评测路径 / PG 不可用降级）。
    prior_citations: 上一轮的引用（工作记忆）——并入本轮引用池，追问可直接复用已检索依据。
    on_event: 可选异步回调 on_event(evt)，SSE 端点传入以实时透出事件（节点内 await 透出）。
    """
    ckpt = telemetry.get_checkpointer()
    config = {"configurable": {"thread_id": conversation_id}} if (ckpt and conversation_id) else None
    app = build_graph(ckpt if config else None)

    base_history, base_prior = history or [], prior_citations
    if config:
        try:
            snap = await app.aget_state(config)
            vals = snap.values or {}
            seed = _messages_to_history(vals.get("messages") or [])
            if seed:  # 有快照 → checkpoint 为准；首轮用调用方 history 作种子
                base_history = seed
                base_prior = vals.get("citations") or base_prior
        except Exception:  # noqa: BLE001 —— checkpoint 读失败降级为 JSON 历史
            log.warning("agent: 读取 checkpoint 失败，回退调用方 history", exc_info=True)

    token = _CTX.set({"deps": deps, "on_event": on_event})
    try:
        res = await app.ainvoke({
            "query": query,
            "collection_id": getattr(deps, "collection_id", "default"),
            "response_type": response_type,
            "history": base_history,
            "remaining_rounds": max_rounds,
            "messages": _initial_messages(query, base_history, response_type, base_prior),
            "events": [],
            "citations": list(base_prior or []),
            "spans": [],
        }, config)
    finally:
        _CTX.reset(token)
    return {
        "answer": res.get("answer_text", ""),
        "advice": res.get("advice", ""),
        "events": res.get("events", []),
        "citations": res.get("citations", []),
        "spans": res.get("spans", []),
        "model": _llm_config()["query_model"],
    }