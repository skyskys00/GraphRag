"""Context Recall：ground_truth 的关键事实点在检索上下文中的覆盖率。

对每个 key_fact，让 LLM 裁判判断它是否能在 retrieved contexts 中找到依据。
最终 recall = 命中事实数 / 总事实数。

同时也用 LLM 做整体判断作为交叉验证，但以事实点细粒度计数为准。
"""
from __future__ import annotations

import asyncio
import re
from typing import Any, Callable

from ..judge import judge

# P0（v5.22）：证据强制。改此 prompt 必须同步升级 _PROMPT_VERSION 使缓存失效
_PROMPT_VERSION = "p0_evidence"

SYSTEM_PROMPT = """你是一个严谨的 RAG 评测裁判。你的任务是判断给定的事实陈述能否在检索到的上下文（retrieved contexts）中找到明确依据。
判断规则：
1. 只根据上下文内容判断，不要使用外部知识。
2. 只有在上下文中能找到该事实的直接表述、或明确等价表述时，才判 hit=true。
3. 判 hit=true 必须在 reason 中逐字引用上下文中的对应原文作为证据。
4. 如果上下文中没有该事实的任何依据（未出现 / 未找到 / 无法推导），即使语义上"感觉可能对"，也必须判 hit=false——不允许"没有依据却命中"。
5. reason 必须与判定自洽：判 hit=true 的 reason 只能陈述找到了什么，不得出现"未出现 / 未找到 / 无法推导"等否定性自述。"""

FACT_PROMPT_TEMPLATE = """请判断以下事实陈述是否能在「检索上下文」中找到明确依据。

【事实陈述】
{fact}

【检索上下文】
{context_str}

请严格只基于检索上下文判断。输出 JSON 格式：
{{
  "hit": true 或 false,
  "score": 0.0 到 1.0 的置信度,
  "reason": "判定依据，必须引用上下文原文；判 hit=true 时不得写‘未出现/未找到/无法推导’等否定描述"
}}
"""

# 证据强制兜底（代码层校验，防 prompt 被绕过）：
# reason 自述强否定（找不到依据）却仍判 hit → 无条件过宽，强制降为 miss。
# 有「部分/间接」等半依据补偿词时保留原判定（允许合理宽松）。
_STRONG_NEG_RE = re.compile(
    r"未出现|未找到|未提及|未提供|未给出|未包含|未检索到|"
    r"无法找到|无法推导|无法支持|无法证实|均未|无等价|无依据|未能提供"
)
_COMPENSATION_RE = re.compile(r"部分|间接|基本一致|大体|与.*对应|均有对应")


def _enforce_evidence(reason: str, hit: bool, score: float) -> tuple[bool, float]:
    """证据强制：judge 自述无依据却判 hit 时，校正为 miss。

    返回 (corr_hit, corr_score)。
    """
    if not hit or score < 0.5:
        return hit, score
    if _STRONG_NEG_RE.search(reason) and not _COMPENSATION_RE.search(reason):
        return False, 0.3
    return hit, score


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
        cache_parts = ["context_recall", _PROMPT_VERSION, fact, context_str[:500]]
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

    raw_hit = result["score"] >= 0.5
    raw_score = result["score"]
    hit, score = _enforce_evidence(result["reason"], raw_hit, raw_score)
    if hit != raw_hit:
        # 证据强制校正（P0）：记录原始判定，reason 标注入档可追溯
        result["reason"] = (
            f"[证据强制校正] 原判定 hit={raw_hit} score={raw_score} "
            f"但 reason 自述无依据，校正为 miss。原reason: {result['reason']}"
        )
    # 用 score >= 0.5 作为 hit 阈值
    return {
        "fact": fact,
        "hit": hit,
        "score": score,
        "reason": result["reason"],
    }
