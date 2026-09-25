"""Answer Relevance（答案相关性）：答案是否回答了问题，有没有跑题。

让 LLM 裁判判断答案与问题的语义相关度。
不看答案正确与否，只看是否在回答同一个话题。
"""
from __future__ import annotations

from typing import Any, Callable

from ..judge import judge

SYSTEM_PROMPT = """你是一个严谨的 RAG 评测裁判。你的任务是判断答案是否与问题相关。
相关性 = 答案在多大程度上回应了问题所问的内容。
注意：
- 只看答案是否在回答问题的话题，不判断答案是否正确。
- 如果答案答非所问、跑题、或者说「我不知道」但问题其实问的是具体事情，相关性就低。
- 如果答案明确说明了材料不足无法回答，但点出了与问题相关的范围，也算有一定相关性。
只根据问题和答案的语义判断，不要使用外部知识。"""

PROMPT_TEMPLATE = """请评估以下答案与问题的相关性。

【问题】
{question}

【答案】
{answer}

请判断答案在多大程度上回应了问题所问的内容。输出 JSON 格式：
{{
  "score": 0.0 到 1.0 的相关性分数,
  "reason": "一句话说明判断依据"
}}
"""


async def compute_answer_relevance(
    query_func: Callable,
    question: str,
    answer: str,
) -> dict[str, Any]:
    """计算 answer relevance。

    返回: {score, reason}
    """
    if not answer or not answer.strip():
        return {"score": 0.0, "reason": "空答案，无相关性"}

    prompt = PROMPT_TEMPLATE.format(question=question, answer=answer)

    result = await judge(
        query_func,
        "answer_relevance",
        prompt,
        system_prompt=SYSTEM_PROMPT,
        cache_key_parts=[question, answer],
    )

    return {
        "score": result["score"],
        "reason": result["reason"],
    }
