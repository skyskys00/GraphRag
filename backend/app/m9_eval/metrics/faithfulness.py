"""Faithfulness（忠实度）：答案是否只基于检索上下文，没有编造。

让 LLM 裁判逐句检查答案中的陈述，判断每个陈述是否能在
检索上下文中找到依据。faithfulness = 有依据的陈述数 / 总陈述数。
"""
from __future__ import annotations

from typing import Any, Callable

from ..judge import judge

SYSTEM_PROMPT = """你是一个严谨的 RAG 评测裁判。你的任务是判断给定的答案是否完全基于提供的上下文材料。
忠实度 = 答案中所有有依据的陈述 ÷ 答案中的总陈述数。
如果答案中的某个说法在上下文中找不到依据，即使它本身是正确的，也视为不忠实（幻觉）。
只根据上下文判断，不要使用外部知识。"""

PROMPT_TEMPLATE = """请评估以下答案的忠实度：答案中的每一个陈述是否都能在检索上下文中找到依据。

【问题】
{question}

【检索上下文】
{context}

【答案】
{answer}

请先找出答案中的所有事实陈述（排除连接词、语气词等），然后逐一判断每个陈述是否能在上下文中找到明确依据。
输出 JSON 格式：
{{
  "score": 0.0 到 1.0 的忠实度分数（有依据的陈述数 / 总陈述数）,
  "total_statements": 总陈述数,
  "supported_statements": 有依据的陈述数,
  "unsupported": [
    {{"statement": "无法找到依据的陈述内容", "reason": "为什么在上下文中找不到依据"}}
  ],
  "reason": "一句话总结判断结果"
}}
"""


async def compute_faithfulness(
    query_func: Callable,
    question: str,
    answer: str,
    contexts: list[dict],
) -> dict[str, Any]:
    """计算 faithfulness。

    返回: {score, total_statements, supported_statements, unsupported, reason}
    """
    if not answer or not answer.strip():
        return {
            "score": 1.0,
            "total_statements": 0,
            "supported_statements": 0,
            "unsupported": [],
            "reason": "空答案默认忠实（无内容可幻觉）",
        }

    context_text = "\n\n".join(
        f"[{i+1}] {c.get('content', '')}" for i, c in enumerate(contexts)
    )

    prompt = PROMPT_TEMPLATE.format(
        question=question,
        context=context_text,
        answer=answer,
    )

    result = await judge(
        query_func,
        "faithfulness",
        prompt,
        system_prompt=SYSTEM_PROMPT,
        cache_key_parts=[question, answer, context_text],
    )

    return {
        "score": result["score"],
        "total_statements": result.get("total_statements", 0),
        "supported_statements": result.get("supported_statements", 0),
        "unsupported": result.get("unsupported", []),
        "reason": result["reason"],
    }
