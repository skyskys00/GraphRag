"""P0 探底（2026-10-23）：langgraph async StateGraph 最小空跑。

验证：①langgraph 在当前环境可 import；②显式 StateGraph（route / execute_tools / generate
+ 条件边）async 空跑可通，作为 M10_agent §2.2 编排骨格的可行占位；③条件边两个出口
（余轮>0 继续 / 耗尽直答）均可达。

placeholders：route 用 remaining_rounds 驱动（真实实现 = LLM bind_tools 决策，见 P0 探底项 1）。
不改动任何正式代码。
"""
import asyncio
from typing import Literal
from typing_extensions import TypedDict

from langgraph.graph import END, START, StateGraph


class AgentState(TypedDict):
    query: str
    remaining_rounds: int
    messages: list[str]


def route(state: AgentState):
    """决策节点占位：消耗一轮（模拟 LLM bind_tools 决策）。返回状态更新 dict。"""
    return {"remaining_rounds": state["remaining_rounds"] - 1}


def decide(state: AgentState) -> Literal["execute_tools", "generate"]:
    """条件函数：余轮剩则继续检索，耗尽走直答（模拟直答短路）。"""
    return "execute_tools" if state["remaining_rounds"] > 0 else "generate"


def execute_tools(state: AgentState):
    """工具节点占位：模拟 tool_retrieve -> ToolMessage 回填。"""
    return {
        "messages": state["messages"]
        + [
            "[tool_decision] tool_retrieve(query=%r) 执行" % state["query"],
            "[tool_result] 命中 3 条 context（含来源 doc_id）",
        ]
    }


def generate(state: AgentState):
    """生成节点占位：基于工具结果产出答案 + done。"""
    return {"messages": state["messages"] + ["[delta] 最终答案（已 grounded 在 tool_result）。", "[done]"]}


async def main():
    g = StateGraph(AgentState)
    g.add_node("route", route)
    g.add_node("execute_tools", execute_tools)
    g.add_node("generate", generate)
    g.add_edge(START, "route")
    g.add_conditional_edges("route", decide, {"execute_tools": "execute_tools", "generate": "generate"})
    g.add_edge("execute_tools", "route")
    g.add_edge("generate", END)

    app = g.compile()
    print("graph compiled:", type(app).__name__)
    res = await app.ainvoke(
        {"query": "医疗器械消毒章节在哪个文档？", "remaining_rounds": 3, "messages": []},
    )
    print("--- 执行轨迹 ---")
    for m in res["messages"]:
        print(m)
    print("--- 结果 ---")
    print("remaining_rounds:", res["remaining_rounds"])


if __name__ == "__main__":
    asyncio.run(main())