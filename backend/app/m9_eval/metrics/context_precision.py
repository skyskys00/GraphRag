"""Context Precision：检索结果中相关 chunk 的比例。

让 LLM 裁判对每个检索到的 chunk 判断：它是否与问题+标准答案相关。
precision = 相关 chunk 数 / 总检索 chunk 数。

同时也计算 weighted precision（按排名加权，排在前面的权重更高）。
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable

from ..judge import judge

SYSTEM_PROMPT = """你是一个严谨的 RAG 评测裁判。你的任务是判断某一段检索到的文本（context）是否与给定的问题和标准答案相关。
相关 = 这段文本包含回答问题所需的信息、或提供了有价值的背景/佐证。
不相关 = 这段文本与问题完全无关、或只是恰巧提到了某个词但实质内容对回答没有帮助。
只根据文本内容判断，不要使用外部知识。"""

CHUNK_PROMPT_TEMPLATE = """请判断以下检索文本是否与问题及标准答案相关。

【问题】
{question}

【标准答案摘要】
{ground_truth_summary}

【检索文本（第 {rank} 条）】
{content}

请判断这段文本对回答问题是否有帮助。输出 JSON 格式：
{{
  "relevant": true 或 false,
  "score": 0.0 到 1.0 的相关度,
  "reason": "一句话说明判断依据"
}}
"""


async def compute_context_precision(
    query_func: Callable,
    question: str,
    ground_truth: str,
    key_facts: list[str],
    retrieved_contexts: list[dict],
) -> dict[str, Any]:
    """计算 context precision。

    返回: {score, total_chunks, relevant_chunks, weighted_score, per_chunk: [...], reason}
    """
    if not retrieved_contexts:
        return {
            "score": 0.0, "total_chunks": 0, "relevant_chunks": 0,
            "weighted_score": 0.0, "per_chunk": [],
            "reason": "无检索结果",
        }

    gt_summary = ground_truth[:500] if ground_truth else "、".join(key_facts[:5])

    tasks = []
    for ctx in retrieved_contexts:
        rank = ctx.get("rank", 0)
        content = ctx.get("content", "")[:1500]
        prompt = CHUNK_PROMPT_TEMPLATE.format(
            question=question, ground_truth_summary=gt_summary,
            rank=rank, content=content,
        )
        cache_parts = ["context_precision", question, content[:300]]
        tasks.append(_judge_chunk(query_func, rank, content, prompt, cache_parts))

    results = await asyncio.gather(*tasks)

    # 裁判失败的 chunk 排除出均分，只统计成功判定的
    evaluated = [r for r in results if r.get("relevant") is not None]
    failed = len(results) - len(evaluated)
    if not evaluated:
        return {
            "score": None, "weighted_score": None,
            "total_chunks": len(results), "relevant_chunks": 0,
            "failed_chunks": failed, "per_chunk": results,
            "reason": "裁判全部失败，无法判分",
        }

    relevant_count = sum(1 for r in evaluated if r["relevant"])
    score = relevant_count / len(evaluated)

    # 按排名加权：第 1 条权重最高，递减（仅对成功判定的 chunk）
    n = len(evaluated)
    weights = [1.0 / (i + 1) for i in range(n)]
    total_w = sum(weights)
    weighted = sum(w * (1.0 if r["relevant"] else 0.0) for w, r in zip(weights, evaluated)) / total_w if total_w else 0.0

    suffix = f"，{failed} 条裁判失败" if failed else ""
    return {
        "score": round(score, 4),
        "weighted_score": round(weighted, 4),
        "total_chunks": len(results),
        "relevant_chunks": relevant_count,
        "failed_chunks": failed,
        "per_chunk": results,
        "reason": f"{relevant_count}/{len(evaluated)} 个 chunk 与问题相关{suffix}",
    }


async def _judge_chunk(query_func: Callable, rank: int, content: str, prompt: str, cache_parts: list[str]) -> dict:
    result = await judge(
        query_func,
        metric="context_precision",
        prompt=prompt,
        cache_key_parts=cache_parts,
        system_prompt=SYSTEM_PROMPT,
    )
    if result.get("error"):
        # 裁判调用失败：标记 error 而非按 0 分计，避免污染 precision 均分
        return {
            "rank": rank,
            "relevant": None,
            "score": None,
            "reason": result["reason"],
            "error": result["error"],
        }
    return {
        "rank": rank,
        "relevant": result["score"] >= 0.5,
        "score": result["score"],
        "reason": result["reason"],
    }
