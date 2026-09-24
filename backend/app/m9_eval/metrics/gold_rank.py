"""Gold Rank：每个 gold fact 最早出现在检索结果的第几名。

双模式：
- lexical（默认，零成本）：基于数字 token + 关键词子串匹配，对数值型事实较准
- llm（精确，需额外 LLM 调用）：逐块问 LLM 裁判，覆盖推导型事实

输出每题：min/median/avg/max rank、top_k_recall（K=1/3/5/8）、per_fact 明细。
"""
from __future__ import annotations

import asyncio
import re
from typing import Any, Callable

from ..judge import judge

# 中文停用词（极简版，只过滤最常见的）
_STOPWORDS = {
    "的", "了", "是", "在", "有", "和", "与", "及", "或", "也", "都", "就",
    "个", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十",
    "为", "中", "上", "下", "这", "那", "其", "之", "等", "将", "被",
    "对", "从", "到", "以", "由", "于", "向", "把", "给", "让",
    "可以", "能够", "可能", "应该", "需要", "进行", "表示", "根据",
    "其中", "以及", "或者", "还是", "不是", "没有", "如果", "因为",
    "达到", "约为", "约", "约", "左右", "以上", "以下", "之间",
    "多少", "什么", "哪个", "哪些", "怎么", "如何", "为什么",
    "比", "较", "更", "最", "相",
}

# 数字模式：整数/小数/百分比/带逗号千分位（连续数字串，前后无非数字字符）
# 匹配连续的数字+逗号+小数点，至少 1 位数字主体
_NUM_PATTERN = re.compile(r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?")


def _extract_number_tokens(text: str) -> list[str]:
    """从文本提取数字 token（含百分比），去重保序。"""
    nums = _NUM_PATTERN.findall(text)
    result = []
    for n in nums:
        # 归一化：去逗号，统一格式
        clean = n.replace(",", "")
        if clean not in result and len(clean) >= 1:
            result.append(clean)
    return result


def _extract_keywords(text: str) -> list[str]:
    """从 fact 文本中提取中文关键词（长度≥2、非停用词）。"""
    # 简单切分：按标点和空格切
    tokens = re.split(r"[，。、；：「」『』""''（）()【】\[\] ,.?!:;~～·\-—/\\\n]", text)
    keywords = []
    for t in tokens:
        t = t.strip()
        if len(t) < 2:
            continue
        if t in _STOPWORDS:
            continue
        # 过滤纯数字（数字 token 已单独提取）
        if re.fullmatch(r"[\d,.%]+", t):
            continue
        if t not in keywords:
            keywords.append(t)
    return keywords


def _lexical_match(fact: str, chunk_content: str) -> bool:
    """词汇模式：判断 fact 是否出现在 chunk 中。

    策略：
    - 如果 fact 有数字 token：数字命中率 ≥ 50%（至少 1 个） + 关键词命中 ≥ 1 个
      （数字是强信号，数字本身命中率已经能说明问题）
    - 如果 fact 无数字 token：关键词命中率 ≥ 60%
    """
    fact_nums = set(_extract_number_tokens(fact))
    fact_kws = _extract_keywords(fact)
    chunk_lower = chunk_content.lower()

    if fact_nums:
        # 数字命中比例
        num_hits = sum(1 for n in fact_nums if n in chunk_lower)
        num_hit_ratio = num_hits / len(fact_nums)
        if num_hits < 1 or num_hit_ratio < 0.4:
            return False
        # 关键词命中
        kw_hits = sum(1 for kw in fact_kws if kw.lower() in chunk_lower)
        # 三档判断：
        # 1. 数字几乎全中(≥75%)且数字够多(≥3个) → 强信号，直接过
        # 2. 数字中一半以上 + 至少1个关键词 → 通过
        # 3. 数字只有1个 → 必须有至少1个关键词辅助（防止单个数字误匹配）
        if num_hit_ratio >= 0.75 and len(fact_nums) >= 3:
            return True
        if fact_kws and kw_hits >= 1:
            return True
        if not fact_kws and num_hit_ratio >= 0.5:
            return True
        # 数字少(1-2个)且无关键词命中 → 疑伪，不通过
        return False
    else:
        # 纯文本事实：关键词命中率 ≥ 60%
        if not fact_kws:
            return False
        kw_hits = sum(1 for kw in fact_kws if kw.lower() in chunk_lower)
        return kw_hits / len(fact_kws) >= 0.6


def _compute_lexical_gold_rank(
    facts: list[str],
    contexts: list[dict[str, Any]],
) -> dict[str, Any]:
    """词汇模式 gold_rank 计算。"""
    per_fact = []
    ranks_found = []

    for fact in facts:
        gold_rank = None
        gold_chunk_id = None
        for ctx in contexts:
            rank = ctx.get("rank", 0)
            content = ctx.get("content", "")
            chunk_id = ctx.get("chunk_id", "")
            if _lexical_match(fact, content):
                gold_rank = rank
                gold_chunk_id = chunk_id
                break
        per_fact.append({
            "fact": fact,
            "gold_rank": gold_rank,
            "chunk_id": gold_chunk_id,
            "match_mode": "lexical",
        })
        if gold_rank is not None:
            ranks_found.append(gold_rank)

    return _build_result(facts, per_fact, ranks_found, "lexical")


def _build_result(
    facts: list[str],
    per_fact: list[dict[str, Any]],
    ranks_found: list[int],
    mode: str,
) -> dict[str, Any]:
    """从 per_fact 和 ranks_found 构建最终结果字典。"""
    total = len(facts)
    found = len(ranks_found)
    not_found = total - found

    if ranks_found:
        sorted_ranks = sorted(ranks_found)
        n = len(sorted_ranks)
        min_rank = sorted_ranks[0]
        max_rank = sorted_ranks[-1]
        avg_rank = round(sum(sorted_ranks) / n, 2)
        # 中位数
        if n % 2 == 1:
            median_rank = float(sorted_ranks[n // 2])
        else:
            median_rank = round((sorted_ranks[n // 2 - 1] + sorted_ranks[n // 2]) / 2, 2)
    else:
        min_rank = None
        max_rank = None
        avg_rank = None
        median_rank = None

    # top_k_recall（基于全部 facts，未命中的不算入 top K）
    top_ks = [1, 3, 5, 8]
    top_k_recall = {}
    for k in top_ks:
        hit = sum(1 for r in ranks_found if r <= k)
        top_k_recall[str(k)] = round(hit / total, 4) if total else 0.0

    return {
        "total_facts": total,
        "found_facts": found,
        "not_found_facts": not_found,
        "min_rank": min_rank,
        "median_rank": median_rank,
        "avg_rank": avg_rank,
        "max_rank": max_rank,
        "top_k_recall": top_k_recall,
        "per_fact": per_fact,
        "mode": mode,
    }


SYSTEM_PROMPT_LLM = """你是一个严谨的 RAG 评测裁判。你的任务是判断给定的事实陈述能否在单一段落文本（chunk）中找到明确依据。
只根据段落内容判断，不要使用外部知识。如果事实在段落中有明确的等价表述或直接提及，就算命中。"""

FACT_CHUNK_PROMPT = """请判断以下事实陈述能否在「段落文本」中找到明确依据。

【事实陈述】
{fact}

【段落文本（第 {rank} 条）】
{content}

请严格只基于段落文本判断。输出 JSON 格式：
{{
  "hit": true 或 false,
  "score": 0.0 到 1.0 的置信度,
  "reason": "一句话说明判断依据"
}}
"""


async def _llm_match_fact_chunk(
    query_func: Callable,
    fact: str,
    rank: int,
    content: str,
) -> dict[str, Any]:
    """LLM 判断单个 fact 是否在单个 chunk 中。"""
    prompt = FACT_CHUNK_PROMPT.format(fact=fact, rank=rank, content=content[:1500])
    cache_parts = ["gold_rank_llm", fact, content[:300], str(rank)]
    result = await judge(
        query_func,
        metric="gold_rank_llm",
        prompt=prompt,
        cache_key_parts=cache_parts,
        system_prompt=SYSTEM_PROMPT_LLM,
    )
    if result.get("error"):
        return {"hit": None, "score": None, "reason": result["reason"], "error": result["error"]}
    return {"hit": result["score"] >= 0.5, "score": result["score"], "reason": result["reason"]}


async def _compute_llm_gold_rank(
    facts: list[str],
    contexts: list[dict[str, Any]],
    query_func: Callable,
) -> dict[str, Any]:
    """LLM 模式 gold_rank 计算：逐 fact 逐 chunk 判断，取第一个命中的 rank。"""
    if not facts or not contexts:
        return _build_result(facts, [], [], "llm")

    per_fact = []
    ranks_found = []

    # 逐 fact 串行（每个 fact 内并发判断所有 chunk），控制总并发
    for fact in facts:
        tasks = []
        for ctx in contexts:
            rank = ctx.get("rank", 0)
            content = ctx.get("content", "")
            tasks.append(_llm_match_fact_chunk(query_func, fact, rank, content))

        results = await asyncio.gather(*tasks)

        gold_rank = None
        gold_chunk_id = None
        # 按 rank 顺序找第一个 hit
        for i, ctx in enumerate(contexts):
            r = results[i]
            if r.get("hit") is True:
                gold_rank = ctx.get("rank", i + 1)
                gold_chunk_id = ctx.get("chunk_id", "")
                break

        per_fact.append({
            "fact": fact,
            "gold_rank": gold_rank,
            "chunk_id": gold_chunk_id,
            "match_mode": "llm",
        })
        if gold_rank is not None:
            ranks_found.append(gold_rank)

    return _build_result(facts, per_fact, ranks_found, "llm")


async def compute_gold_rank(
    facts: list[str],
    contexts: list[dict[str, Any]],
    *,
    mode: str = "lexical",
    query_func: Callable | None = None,
) -> dict[str, Any]:
    """计算 gold rank。

    Args:
        facts: gold fact 列表
        contexts: 检索结果列表，每项含 rank/content/chunk_id
        mode: 'lexical'（默认）或 'llm'
        query_func: LLM 模式必需，裁判用的 query 函数

    Returns:
        {total_facts, found_facts, not_found_facts, min_rank, median_rank,
         avg_rank, max_rank, top_k_recall, per_fact, mode}
    """
    if mode == "lexical":
        return _compute_lexical_gold_rank(facts, contexts)
    elif mode == "llm":
        if query_func is None:
            raise ValueError("LLM 模式需要提供 query_func")
        return await _compute_llm_gold_rank(facts, contexts, query_func)
    else:
        raise ValueError(f"未知 mode: {mode}，可选 'lexical' / 'llm'")
