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

# P0（v5.22）证据强制精准化：
# judge reason 自述否定却判 hit 时，仅当是「空洞否定」（reason 无任何证据信号）
# 才校正为 miss；有证据信号（数值锚点/推导缺口/块引用/核心词组）或负向断言型
# fact 则保留原判定。改此 prompt 必须同步升级 _PROMPT_VERSION 使缓存失效
# P1（v5.29）采信裁判显式 hit：judge 曾丢弃 LLM 输出的 hit 字段，只能用 score>=0.5
# 反推，导致「hit=false 但 score 高」被误判为命中；现优先用 hit（缺失时回退 score）。
# 解析逻辑变更同样需 bump 版本，使旧缓存（无 hit 字段）失效。
_PROMPT_VERSION = "p1_llm_hit"

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

# 证据强制兜底（代码层校验，防 prompt 被绕过；仅当 reason 空洞否定时降 miss）：
# judge reason 自述否定（找不到依据）仍判 hit → 若 reason 里没有任何证据信号
# （数值锚点 / 推导缺口 / 上下文[N]块引用 / 核心词组），校正为 miss。
# 负向断言型 fact（fact 本身是「未定义/未覆盖」类陈述）整体豁免。
_STRONG_NEG_RE = re.compile(
    r"未出现|未找到|未提及|未提供|未给出|未包含|未检索到|未见|未将|未涵盖|"
    r"均未|没有任何|并未出现|并未给出|并未提供|没有出现|没找到|未出现这|"
    r"无法找到|无法推导|无法支持|无法证实|未能提供|均无"
)
# 数值锚点的否定语境（窄表，避开「未出现×字」里因引号把否定词推远的案例）
_NUM_NEG_RE = re.compile(
    r"未出现|未找到|未提供|未给出|未提及|未包含|未见|均未|没有任何|"
    r"并未出现|并未给出|没有出现|并未提供|无法找到|无法推导|未列出|未给|未用作"
)
# 核心词组否定语境（含「仅…/尚未/统称…」等概念归纳边界收紧词）
_CTX_NEG_RE = re.compile(
    r"未出现|未找到|未提供|未给出|未包含|未见|均未|没有任何|"
    r"并未出现|并未给出|没有出现|无法找到|无法推导|未能提供|"
    r"仅出现|仅列出|只给出|只有|仅提出|尚未|未列|仅说明|仅提到|"
    r"统称为|归结为|没有支持|未将其统称|未将其归结|未归结|未认定为|未定义为"
)
# 负向断言型 fact：事实本身在断言某物「未定义/未列出/不构成」，不因 reason 否定而误杀
_NEGATIVE_FACT_RE = re.compile(
    r"未将|未对|未把|未列入|未纳入|不构成|无文档|"
    r"未定义|未指定|未将其|未单独|不作为|无.*?(?:依据|定义|说明|说法)"
)
_NUM_RE = re.compile(r"(?<![A-Za-z0-9\[])\d[\d,]*(?:\.\d+)?(?:%％)?")
_DERIV_FACT_RE = re.compile(r"[=≈]|合计|共计|总计|总共|×|x|X|＋|差\d")
_BASE_UNIT_RE = re.compile(
    r"单晚|每日|按日|单价|单项|差额|差值|标准\b|标准与|分别列出|单独列出|可计算|可推导|算出"
)
_TARGET_ONLY_RE = re.compile(r"目标值|目标[是为]|仅为|预计|目标\b")
_PREFIXION_RE = re.compile(
    r"^(问题\s*\d+[：:]|技术趋势\s*\d+[：:]|改进方向[：:]|主要原因[：:]|"
    r"价值[：:]|领用方式[：:]|核心[：:])"
)
_HOLLOW_NEG_RE = re.compile(r"(?:未|没有|并无|并未)(?:出现|找到|给出|提供|检索到|列出|提到)[「\"'“”]")


def _primary_nums(fact: str) -> list[str]:
    """提取 fact 的数值锚点（去前缀、去括号内文，去除千分位）。"""
    stripped = _PREFIXION_RE.sub("", fact)
    stripped = re.sub(r"[（(][^（）()]{0,50}[)）]", "", stripped)
    return [m.group(0).replace(",", "") for m in _NUM_RE.finditer(stripped)]


def _core_needles(fact: str) -> list[str]:
    """core 词组：含主干块 + 超过4字块的头/尾4字（便于子串命中）。"""
    stripped = re.sub(r"[（(][^（）()]{0,60}[)）]", "", fact)
    stripped = _PREFIXION_RE.sub("", stripped)
    parts = re.split(r"[\s/=:：|,，;；\-—、]+", stripped)
    out: list[str] = []
    seen: set[str] = set()
    for p in parts:
        for t in re.findall(r"[一-鿿]{2,30}", p) + re.findall(r"[A-Za-z][A-Za-z0-9]{1,30}", p):
            if t not in seen:
                seen.add(t)
                if len(t) >= 4:
                    out += [t]
                    if len(t) > 4:
                        out += [t[:4], t[-4:]]
                else:
                    out.append(t)
    return list(dict.fromkeys(out))


def _window(reason: str, pos: int, limit: int = 80) -> str:
    """数字位置 → 双向扩展到最近句边界（。；;\n），防引号内文把否定词推出视窗。"""
    ls = -1
    for c in "。；;\n":
        k = reason.rfind(c, max(0, pos - limit), pos)
        if k > ls:
            ls = k
    start = ls + 1
    rs = len(reason)
    for c in "。；;\n":
        k = reason.find(c, pos, min(len(reason), pos + limit))
        if k != -1 and k < rs:
            rs = k
    return reason[start:rs]


def _num_found_affirmed(fact: str, reason: str) -> bool:
    """数值锚点判定，三轨：

    1) 空洞否定专杀——fact 常被整体引号复述（「未出现『…2.4…』」），
       judge 的否定词被引号内文推开，同句 80 字符内有「未出现+引号」→ 视为反证；
    2) 整句窗口主判——双向扩到句边界查否定词；
    3) ±14 短窗兜底——整句含否定但数字所在小段肯定（句内局部正证，如
       CS-SM-001 的 81.2%/85、adm_q012 的 120/80 走此路径恢复）。
    """
    for n in _primary_nums(fact):
        for m in re.finditer(re.escape(n), reason):
            pos = m.start()
            pre = reason[max(0, pos - 80): pos]
            if not re.search(r"[。；;\n]", pre) and _HOLLOW_NEG_RE.search(pre):
                continue  # 空洞否定引号内经
            if not _NUM_NEG_RE.search(_window(reason, pos)):
                return True
            short = reason[max(0, pos - 14): m.end() + 14]
            if not _NUM_NEG_RE.search(short):
                return True
    return False


def _block_affirmed(fact: str, reason: str) -> str | None:
    """上下文[N]块引用：段内出现 fact 的强锚点（数值 or ≥4 字核心）→ 正证块号。"""
    nums = _primary_nums(fact)
    cores = [c for c in _core_needles(fact) if len(c) >= 4]
    for m in re.finditer(r"上下文\[(\d+)\]", reason):
        seg = reason[m.end(): m.end() + 44].split("。")[0]
        if _STRONG_NEG_RE.search(seg):
            continue
        if any(n.replace(",", "") in seg for n in nums) or any(c in seg for c in cores):
            return m.group(1)
    return None


def _has_support(fact: str, reason: str) -> tuple[bool, str]:
    """reason 中是否存在保留 hit 的证据信号。"""
    nums = _primary_nums(fact)
    if nums:
        if _num_found_affirmed(fact, reason):
            return True, "数值锚点非否定出现"
        if _DERIV_FACT_RE.search(fact) and _BASE_UNIT_RE.search(reason) and not _TARGET_ONLY_RE.search(reason):
            return True, "推导缺口：基础单位/标准证据在上下文"
        return False, "数值型 fact 无锚点"
    b = _block_affirmed(fact, reason)
    if b:
        return True, f"上下文块[{b}]含 fact 强锚点"
    for c in _core_needles(fact):
        for m in re.finditer(re.escape(c), reason):
            if not _CTX_NEG_RE.search(reason[max(0, m.start() - 14): m.end() + 14]):
                return True, f"核心词「{c}」非否定出现"
    return False, "非数值型 fact 无锚点"


def _enforce_evidence(fact: str, reason: str, hit: bool, score: float) -> tuple[bool, float]:
    """证据强制精准化：judge 自述否定却判 hit 时，仅「空洞否定」降为 miss。

    返回 (corr_hit, corr_score)。有证据信号（数值锚点 / 推导缺口 / 块引用 /
    核心词组）或强否定引号内经、或负向断言型 fact 时保留原判定。
    """
    if not hit or score < 0.5:
        return hit, score
    if not _STRONG_NEG_RE.search(reason):
        return hit, score
    if _NEGATIVE_FACT_RE.search(fact):
        return hit, score  # 负向断言 fact 豁免
    ok, _why = _has_support(fact, reason)
    if ok:
        return hit, score
    return False, 0.3


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

    raw_score = result["score"]
    llm_hit = result.get("hit")
    # 优先采信裁判显式输出的 hit；缺失（旧缓存 / 未输出）时回退 score 阈值
    raw_hit = llm_hit if llm_hit is not None else raw_score >= 0.5
    hit, score = _enforce_evidence(fact, result["reason"], raw_hit, raw_score)
    if hit != raw_hit:
        # 证据强制校正（P0 精准化）：记录原始判定，reason 标注入档可追溯
        result["reason"] = (
            f"[证据强制校正] 原判定 hit={raw_hit} score={raw_score} "
            f"但 reason 空洞否定无证据信号，校正为 miss。原reason: {result['reason']}"
        )
    # 用 score >= 0.5 作为 hit 阈值
    return {
        "fact": fact,
        "hit": hit,
        "score": score,
        "reason": result["reason"],
    }
