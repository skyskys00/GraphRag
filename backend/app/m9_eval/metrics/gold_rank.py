"""Gold Rank：每个 gold fact 最早出现在检索结果的第几名。

双模式：
- lexical（默认，零成本）：基于数字 token + 关键词子串匹配，对数值型事实较准
- llm（精确，需额外 LLM 调用）：逐块问 LLM 裁判，覆盖推导型事实

输出每题：min/median/avg/max rank、top_k_recall（K=1/3/5/8）、per_fact 明细。
"""
from __future__ import annotations

import asyncio
import difflib
import re
from typing import Any, Callable

from ..judge import judge

# 中文停用词（不再用于 keyword 命中，保留供潜在 keyword 形态回退）

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


def _num_in_content(num: str, content: str) -> bool:
    """数字 token 边界匹配：避免「30」误中「130」「30%」等粘连形式。

    两侧都去逗号后匹配，兼容千分位（fact「14,826」/「1,963」对 chunk
    原文「14,826」「1,963」等价命中）。
    """
    clean = num.replace(",", "")
    return re.search(rf"(?<!\d){re.escape(clean)}(?!\d)", content.replace(",", "")) is not None


# 文本佐证用分隔符归一化：去标点/空格/符号，保汉字/字母/数字/百分号，
# 让「知识图谱 + 大模型融合」与「| 疏忽大意 | … | 30% |」可跨单元格连续匹配
_SEP = re.compile(r"[^一-龥A-Za-z0-9%]")


def _norm(text: str) -> str:
    return _SEP.sub("", text).lower()


# ── 数字锚分类（2026-09-27 定稿）────────────────────────────
# 强锚 = 单位绑定数字 / 小数 / 比较符后数字（80%、52秒、=30、2.4）；
# 弱锚 = 版本号 / 年份 / 差值 / 序数（V2.3、2026、+13.2%、趋势2）→ 不构成强证据。
# 结论位 = 等式/≈/→ 最右右值、≤≥<> 后数字；无比较式时退化为全部强锚
# （并列主值，如「西南 52 秒 vs 华东 38 秒」任一命中即可）。
_UNIT_AFFIX = ("分", "小时", "分钟", "次", "秒", "件", "元", "天", "点", "周", "人", "亿")
_ORD_PREFIX = ("第", "问题", "趋势", "阶段", "季度", "款", "轮", "条", "项", "列", "节")
_DIFF_SUFFIX = ("pp", "个百分点", "百分点", "倍")


def _is_weak_anchor(num: str, fact: str) -> bool:
    if re.search(rf"[Vv]\s*{re.escape(num)}", fact) or re.search(rf"{re.escape(num)}\s*版", fact) \
            or (num.count(".") == 1 and re.search(r"[Vv版]", fact)):
        return True
    if re.match(r"^(?:19|20)\d{2}$", num):
        return True
    if re.search(rf"[+\-]\s*{re.escape(num)}", fact) \
            or any(re.search(rf"{re.escape(num)}\s*{s}", fact) for s in _DIFF_SUFFIX):
        return True
    if any(re.search(rf"{p}\s*{re.escape(num)}", fact) for p in _ORD_PREFIX) \
            or re.search(rf"[Qq]{re.escape(num)}", fact):
        return True
    return False


def _number_anchors(fact: str) -> tuple[set[str], set[str]]:
    fact_nums = set(_extract_number_tokens(fact))
    strong = set()
    for n in fact_nums:
        if _is_weak_anchor(n, fact):
            continue
        if "." in n or n.endswith("%") \
                or any(re.search(rf"{re.escape(n)}\s*{u}", fact) for u in _UNIT_AFFIX) \
                or re.search(rf"[=≈≤≥<>→]\s*{re.escape(n)}", fact):
            strong.add(n)
    # 结论位：等式/≈/→ 右值是主结论（必中）；无等式时比较符后数字（目标值）
    # 作为结论位；再无则退化为全部 strong（并列主值，任一命中即可）。
    rv = [m.group(1).replace(",", "") for m in re.finditer(r"[=≈→]\s*([\d,]+(?:\.\d+)?%?)", fact)]
    cmp = {m.group(1).replace(",", "") for m in re.finditer(r"[≤≥<>]\s*([\d,]+(?:\.\d+)?%?)", fact)}
    if rv:
        concl = {rv[-1]}
    elif cmp:
        concl = cmp
    else:
        concl = strong
    return strong, concl


def _lexical_match(fact: str, chunk_content: str) -> bool:
    """词汇模式：判断 fact 是否出现在 chunk 中。

    形态（探针 + 全库回归校准，2026-09-27）：
    1. 数字 token 用边界正则匹配（`(?<!\d)…(?!\d)`，避免「30」误中「130」），
       千分位去逗号后等价匹配；
    2. fact/chunk 都做分隔符归一化，数字粘连词（「疏忽大意30%」）与分离表格块
       （「| 疏忽大意 | … | 30% |」）可连续匹配；
    3. 文本佐证用「累计匹配长度」而非最长块——表格碎片/连接词把公共内容
       打断成多段，lcs 会低估（「GraphRAG核心」lcs=8 但两个块合计 17，长度 19
       覆盖 89%）；累计长度对碎片化鲁棒；
    4. 数字事实：数字是强信号，文本佐证需 ≥ 较大子串；多数字事实还要求
       多数数字边界命中（纯「30分钟」式弱支持无法靠单数字通过）;
    5. 纯文本事实要求实质覆盖（累计 ≥ max(4, len/2)），否则 4 字短语
       （如「一级投诉」）会误中只顺带提及该词的长块。
    6. 锚定分类（2026-09-27 v5.23 定稿）：区分「结论位/强锚」数字与「弱锚」
       （版本/年份/差值/序数）。对含强锚数字的事实，若强锚任一致命缺失
       或结论位数字未命中则拒绝——结论位 = 等式/≈/→ 最右右值（主结论，
       优先），无等式时才以 ≤≥<> 后数字为目标值，均无则退化为全部强锚
       （并列主值，任一命中即可）。堵住「合计扣分=20+10=30分」这类推导型
       事实只命中前提数字（20/10）而结论数值（30）未出现的伪命中；同时
       「西南52秒 vs 华东38秒」这种并列主值只要任一值被检索到即可命中。
    """
    fact_nums = set(_extract_number_tokens(fact))
    has_num = bool(fact_nums)
    if has_num:
        strong, concl = _number_anchors(fact)
        if strong:
            strong_hits = sum(1 for n in strong if _num_in_content(n, chunk_content))
            if strong_hits < 1:
                return False
            if len(strong) >= 2 and strong_hits / len(strong) < 0.5:
                return False
            if concl and not any(_num_in_content(n, chunk_content) for n in concl):
                return False
        else:
            num_hits = sum(1 for n in fact_nums if _num_in_content(n, chunk_content))
            if num_hits < 1:
                return False
            # 多数字事实：要求多数数字命中（至少一半），单个数字的弱支持不够
            if len(fact_nums) >= 2 and num_hits / len(fact_nums) < 0.5:
                return False
    f_norm = _norm(fact)
    if not f_norm:
        return True
    chunk_norm = _norm(chunk_content)
    sm = difflib.SequenceMatcher(None, f_norm, chunk_norm, autojunk=False)
    cum = sum(m.size for m in sm.get_matching_blocks() if m.size >= 2)
    if has_num:
        return cum >= max(2, min(4, len(f_norm))) or sm.ratio() >= 0.5
    return cum >= max(4, len(f_norm) // 2) or sm.ratio() >= 0.5


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
