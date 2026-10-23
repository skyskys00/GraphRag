"""M10 工具注册表：agent 与 RAG 底座的唯一接缝（见 M10_agent §2/§4）。

每个工具 = (fn_factory, description, args_schema)，一次注册同时驱动
① LangGraph route 节点的 OpenAI tools 定义 ② dispatch 执行 ③ 前端可用工具展示。

collection 隔离：fn 用 factory(deps) 按请求上下文生成，避免全局注册表跨库串扰。
sync / async 工具通吃。
"""
from __future__ import annotations

import inspect
from typing import Any, Callable

FnFactory = Callable[["AppDeps"], Callable[..., Any]]  # type: ignore[name-defined]

TOOLS: dict[str, dict[str, Any]] = {}


def register_tool(
    name: str,
    fn_factory: FnFactory,
    description: str,
    args_schema: dict[str, Any],
) -> None:
    """注册工具。fn_factory(deps) -> fn(*args) 在每次请求时绑定当前 collection 的 deps。"""
    TOOLS[name] = {"fn_factory": fn_factory, "description": description, "args_schema": args_schema}


def tool_definitions() -> list[dict[str, Any]]:
    """OpenAI tools 格式（route 节点 tools= 参数用）。"""
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": cfg["description"],
                "parameters": cfg["args_schema"],
            },
        }
        for name, cfg in sorted(TOOLS.items())
    ]


async def dispatch(name: str, args: dict[str, Any], deps: Any) -> Any:
    """执行工具：按当前 deps 生成绑定函数并调用，返回 JSON 化结果。sync/async factory 与 fn 通吃。"""
    if name not in TOOLS:
        raise KeyError(f"未知工具: {name}")
    factory = TOOLS[name]["fn_factory"]
    fn = factory(deps)
    if inspect.isawaitable(fn):
        fn = await fn
    out = fn(**args)
    if inspect.isawaitable(out):
        out = await out
    return out