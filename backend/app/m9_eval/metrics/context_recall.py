"""Context Recall：ground_truth 的关键事实点在检索上下文中的覆盖率。

对每个 key_fact，让 LLM 裁判判断它是否能在 retrieved contexts 中找到依据。
最终 recall = 命中事实数 / 总事实数。

同时也用 LLM 做整体判断作为交叉验证，但以事实点细粒度计数为准。
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable

from ..judge import judge

SYSTEM_PROMPT = """你是一个严谨的 RAG 评测裁判。你的任务是判断给定的事实陈述能否在检索到的上下文（retrieved contexts）中找到依据。
只根据上下文内容判断，不要使用外部知识。如果事实在上下文中有明确的等价表述，就算命中。"""

FACT_PROMPT_TEMPLATE = """请判断以下事实陈述是否能在「检索上下文」中找到明确依据。

【事实陈述】
{fact}

【检索上下文】
{context_str}

请严格只基于检索上下文判断。输出 JSON 格式：
{{
  "hit": true 或 false,
  "score": 0.0 到 1.0 的置信度,
  "reason": "一句话说明判断依据"
}}
"""


async def compute_context_recall(
    query_func: Callable,
    question: str,
    key_facts: list[str],
    retrieved_contexts: list[dict],
) -> dict[str, Any]:
    """计算 context recall。

    retrieved_contexts: list of {chunk_id, content, full_doc_id, score, rank}
    返回: {score, total_facts, hit_facts, per_fact: [{fact, hit, score, reason}], reason}
    """
    if not key_facts:
        return {"score": 0.0, "total_facts": 0, "hit_facts": 0, "per_fact": [], "reason": "无 key_facts"}

    context_str = "\n---\n".join(
        f"[{c.get('rank', i+1)}] (doc: {c.get('full_doc_id', '?')}) {c.get('content', '')}"
        for i, c in enumerate(retrieved_contexts)
    )

    # 每个事实点独立判断，并发调用
    tasks = []
    for fact in key_facts:
        prompt = FACT_PROMPT_TEMPLATE.format(fact=fact, context_str=context_str)
        cache_parts = ["context_recall", fact, context_str[:500]]
        tasks.append(_judge_fact(query_func, fact, prompt, cache_parts))

    results = await asyncio.gather(*tasks)

    # 裁判失败的事实点排除出均分，只统计成功判定的
    evaluated = [r for r in results if r.get("hit") is not None]
    failed = len(results) - len(evaluated)
    if not evaluated:
        return {
            "score": None, "total_facts": len(key_facts), "hit_facts": 0,
            "failed_facts": failed, "per_fact": results,
            "reason": "裁判全部失败，无法判分",
        }

    hit_count = sum(1 for r in evaluated if r["hit"])
    score = hit_count / len(evaluated)

    suffix = f"，{failed} 个事实裁判失败" if failed else ""
    return {
        "score": round(score, 4),
        "total_facts": len(key_facts),
        "hit_facts": hit_count,
        "failed_facts": failed,
        "per_fact": results,
        "reason": f"{hit_count}/{len(evaluated)} 个事实点被检索结果覆盖{suffix}",
    }


async def _judge_fact(query_func: Callable, fact: str, prompt: str, cache_parts: list[str]) -> dict:
    result = await judge(
        query_func,
        metric="context_recall",
        prompt=prompt,
        cache_key_parts=cache_parts,
        system_prompt=SYSTEM_PROMPT,
    )
    if result.get("error"):
        # 裁判调用失败：标记 error 而非按 0 分计，避免污染 recall 均分
        return {
            "fact": fact,
            "hit": None,
            "score": None,
            "reason": result["reason"],
            "error": result["error"],
        }
    # 用 score >= 0.5 作为 hit 阈值
    return {
        "fact": fact,
        "hit": result["score"] >= 0.5,
        "score": result["score"],
        "reason": result["reason"],
    }
