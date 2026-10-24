"""Tool Accuracy：LLM 裁判评判 agent 单轮工具调用链是否合理。

M10 §7 新增指标：对每个问题，把 agent 本轮的工具调用序列（工具名 + 参数）
交给裁判，判工具选择是否恰当、参数是否合适、步数是否冗余/不足。
返回 {score: 0-1, reason}，整体判（每题一次 LLM 调用）。
"""
from __future__ import annotations

from typing import Any, Callable

from ..judge import judge

_PROMPT_VERSION = "v1_toolchain"

SYSTEM_PROMPT = """你是一个中文 AI Agent 工具调用评测裁判。你的任务是判断一次问答中 agent 的工具调用序列是否合理。

判断规则：
1. 依据【问题】评判【工具链】：工具类型是否匹配问题意图、参数是否恰当、步数是否冗余或不足。
2. 合理情形示例：事实查询先检索再回答；需要在多文档核对时做文档清单确认；简单问题直接作答不调用工具也视为合理。
3. 不合理情形示例：调用了不相关工具；参数明显错误（如把必须的 query 参数省略）；同一步操作无意义地重复多次检索；明显需要检索却未检索直接作答且资料不足；或已获得答案仍多余调用工具。
4. 不确定的资料合一重复调用数不扣分；只要整体能高效得到答案即可判高分。
5. 只根据给定信息判断，不要引入外部知识。"""

PROMPT_TEMPLATE = """请判断以下问答中 agent 工具调用序列的合理性。

【问题】
{question}

【工具调用序列】
{chain_str}

输出 JSON 格式：
{{
  "score": 0.0 到 1.0（合理程度，1.0=完全合理）,
  "reason": "逐条说明每步是否恰当，并给出总体结论"
}}
"""


def _chain_str(tool_chain: list[dict[str, Any]]) -> str:
    if not tool_chain:
        return "（无工具调用，agent 直接作答）"
    lines = []
    for i, step in enumerate(tool_chain, 1):
        args = step.get("args") or ""
        if isinstance(args, dict):
            args = ", ".join(f"{k}={v}" for k, v in args.items())
        lines.append(f"{i}. {step.get('tool')}({args})")
    return "\n".join(lines)


async def compute_tool_accuracy(
    query_func: Callable,
    question: str,
    tool_chain: list[dict[str, Any]],
) -> dict[str, Any]:
    """评判工具链合理性。返回 {score, reason}（judge 失败 score=0 并附 reason）。"""
    if not tool_chain:
        # 无工具直接作答：不扣分，由裁判确认是否合理直答
        prompt = PROMPT_TEMPLATE.format(question=question, chain_str="（无工具调用，agent 直接作答）")
    else:
        prompt = PROMPT_TEMPLATE.format(question=question, chain_str=_chain_str(tool_chain))

    result = await judge(
        query_func,
        metric="tool_accuracy",
        prompt=prompt,
        cache_key_parts=["tool_accuracy", _PROMPT_VERSION, question, _chain_str(tool_chain)],
        system_prompt=SYSTEM_PROMPT,
    )
    return {
        "score": result.get("score", 0.0),
        "reason": result.get("reason", ""),
        "tool_count": len(tool_chain),
        "chain": tool_chain,
    }