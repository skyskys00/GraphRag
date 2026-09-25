"""Correctness（正确性）：答案与标准答案的事实一致性。

让 LLM 裁判对比答案和标准答案，判断答案中的关键事实点是否正确。
不同于 faithfulness（只看是否有上下文依据），correctness 看答案本身是否正确。
"""
from __future__ import annotations

from typing import Any, Callable

from ..judge import judge

SYSTEM_PROMPT = """你是一个严谨的 RAG 评测裁判。你的任务是对比答案与标准答案，判断答案在事实层面的正确性。
正确性 = 答案中正确的关键事实点 ÷ 答案中的关键事实点总数。
注意：
- 只判断事实是否正确，不判断措辞是否完全一致（语义一致即可）。
- 答案中提到但标准答案里没有的内容，如果本身不与标准答案矛盾，不扣分。
- 如果答案明确说「材料不足」「无法回答」，而标准答案有明确答案，视为不正确。
- 如果答案部分正确，按正确事实点的比例给分。
只根据标准答案判断，不要使用外部知识。"""

PROMPT_TEMPLATE = """请评估以下答案的正确性：对比答案与标准答案，判断答案中的关键事实点是否正确。

【问题】
{question}

【标准答案】
{ground_truth}

【答案】
{answer}

请先找出答案中的关键事实点，再逐一与标准答案对比。输出 JSON 格式：
{{
  "score": 0.0 到 1.0 的正确性分数（正确事实点数 / 总事实点数）,
  "total_facts": 答案中的关键事实点总数,
  "correct_facts": 正确的事实点数,
  "incorrect": [
    {{"fact": "不正确的事实点", "reason": "与标准答案哪里不一致"}}
  ],
  "reason": "一句话总结判断结果"
}}
"""


async def compute_correctness(
    query_func: Callable,
    question: str,
    ground_truth: str,
    answer: str,
) -> dict[str, Any]:
    """计算 correctness。

    返回: {score, total_facts, correct_facts, incorrect, reason}
    """
    if not answer or not answer.strip():
        return {
            "score": 0.0,
            "total_facts": 0,
            "correct_facts": 0,
            "incorrect": [],
            "reason": "空答案，不正确",
        }

    if not ground_truth or not ground_truth.strip():
        return {
            "score": 1.0,
            "total_facts": 0,
            "correct_facts": 0,
            "incorrect": [],
            "reason": "无标准答案，默认正确",
        }

    prompt = PROMPT_TEMPLATE.format(
        question=question,
        ground_truth=ground_truth,
        answer=answer,
    )

    result = await judge(
        query_func,
        "correctness",
        prompt,
        system_prompt=SYSTEM_PROMPT,
        cache_key_parts=[question, ground_truth, answer],
    )

    return {
        "score": result["score"],
        "total_facts": result.get("total_facts", 0),
        "correct_facts": result.get("correct_facts", 0),
        "incorrect": result.get("incorrect", []),
        "reason": result["reason"],
    }
