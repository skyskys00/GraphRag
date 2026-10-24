"""Faithfulness（忠实度）：答案是否只基于检索上下文，没有编造。

P1 双轨计分（v5.47，口径用户拍板：只罚 fact + meta 单报 + 数值断言强制归 fact）：
每个「上下文找不到原文依据」的陈述分三类打标签——
  - fact   ：核心事实/数值断言（参数、数值结论、型号、禁止事项），必须溯源 → 计入忠实惩罚
  - meta   ：元信息/概括（来源出处、型号推断、概括性结论、否定性说明"资料中仅给出……"、
             由原文可推出的排除/推断结论——事实锚点在上下文有原文、仅结论表述未逐字）
             → 不参与计分，单独上报（meta 单报，供人工核查防止把编造挂 meta 名下规避）
  - enhance：域知识补强（安全建议、常识性提醒），agent 本体增值 → 不计惩罚，单独计「知识增强分」
恩诚度 = （fact 中有依据数）/（fact 总数）；meta/enhance 不进分母。
数值断言强制归 fact：凡含具体数值/单位/参数/规格的陈述，裁判必须归 fact，不得以 meta 规避。
推断结论的锚点若在上下文不存在（归源错误），仍归 fact 罚，不得以"推断"规避。
"""
from __future__ import annotations

import re
from typing import Any, Callable

from ..judge import judge

SYSTEM_PROMPT = """你是一个严谨的 RAG 评测裁判。你的任务是判断给定的答案是否完全基于提供的上下文材料。
忠实度只按答案中的"事实陈述（fact）"计：fact 若在上下文中找不到依据，即使它本身是正确的，也视为不忠实（幻觉）。
对每个陈述分三类：
- fact（核心事实/数值断言）：含具体数值、单位、参数、规格、型号、剂量、禁止事项、明确比较结论。强制归 fact——不得因"上下文没有原文"而降级为 meta。无依据即判不忠实。
- meta（元信息/概括）：来源出处、文档名、版次、无具体数值的概括性结论、"资料中仅给出……"这类否定性说明、单纯的泛化表述；以及由上下文原文可合理推出的推断性/排除性结论（所有事实锚点在上下文有原文、仅结论表述未逐字出现，如由"科研用途"推出"非临床"）。meta 不参与计分，只需如实登记。
- enhance（域知识补强）：安全操作建议（如"应在专业人员监护下操作"）、常识性提醒。这类允许超出字面上下文，但必须是常识级合理建议，不得编造具体事实。合理的 enhance 不计不忠实。
判定提醒（供 meta 归类时用）：
- 概括/推断的锚点看「具体事实是否在上下文有原文」，不看标签词本身是否逐字出现。例：上下文列有"微流体控制、化学反应注射、动物药物注射"，答案概括为"科研用途、非临床"→ 锚点有原文 → 归 meta；不得因上下文没有"科研"二字而拒归 meta。
- 关于引用来源、资料涵盖范围、自身上一轮表述的自我说明（含纠错，如"此前将该句归到[2]，实为[2]未包含该句"）→ 一律 meta，单报备查，不核查其真伪是否影响分类。
边界：推断结论若引用的出处/型号/对象在上下文中不存在（归源错误，如把句子挂在上下文查无的型号名下），不得以"推断/泛化"为由降为 meta，仍归 fact 如实计罚。
只根据上下文判断，不要使用外部知识。"""
PROMPT_TEMPLATE = """请评估以下答案的忠实度：答案中的每一个陈述是否都能在检索上下文中找到依据。

【问题】
{question}

【检索上下文】
{context}

【答案】
{answer}

请先找出答案中的所有事实陈述（排除连接词、语气词等），然后逐一判断每个陈述是否能在上下文中找到明确依据。
对每个陈述标注 kind：
- 含具体数值/单位/参数/规格/型号/剂量/禁止事项/明确比较结论 → kind 强制为 fact（即使上下文没有原文依据）。
- 来源出处、文档名、版次、无具体数值的概括结论、"资料中仅给出……"类否定说明 → kind = meta。
- 由上下文原文可合理推出的推断性/排除性结论（所有事实锚点在上下文有原文、仅该结论表述未逐字出现）→ kind = meta；概括/推断的锚点看"具体事实是否有原文"不看标签词（例：上下文有"微流体控制、化学注射、动物注射"，答案写"科研用途而非临床" → meta）；关于引用来源/资料涵盖范围/自身上一轮表述的自我说明（含纠错）一律 meta。但若该结论引用的出处/型号/对象在上下文中不存在（归源错误），仍归 fact。
- 常识级安全操作建议、提醒（超出字面上下文但合理）→ kind = enhance。
输出 JSON 格式：
{{
  "score": 0.0 到 1.0 的忠实度分数（按 fact 计算：有依据的 fact / fact 总数）,
  "total_statements": 答案的全部陈述数（含 fact/meta/enhance）,
  "fact_total": 其中 fact 类陈述总数（含有无依据）,
  "supported_statements": 有依据的 fact 数,
  "unsupported": [
    {{"statement": "上下文找不到依据的陈述内容", "kind": "fact|meta|enhance", "reason": "为什么在上下文中找不到依据"}}
  ],
  "reason": "一句话总结判断结果"
}}
如果某个无依据的陈述实际有上下文证据，务必标为有依据，不要漏标。
"""

# ---- 代码侧 meta 指纹兜底（2026-10-24）：压 LLM 对 kind 判别的方差 ----
# 方向安全（单向放松）：只把「高置信 meta 句式」从 fact 降为 meta，绝不反向加罚。
# 命中 meta 指纹且无强罚指纹（测量值/小数/禁止条款）→ 降 meta，仍进 meta_report 单报供人工核查。
# 无条件 meta：自我纠错/引用归属说明（用户口径：此句不论带不带数值/禁止条款，一律 meta 单报，不核查真伪）
_META_UNCOND = re.compile(r"上一轮|本条资料|此前的回答|我引用")
_META_FINGERPRINTS = re.compile(
    r"根据.{0,24}(说明书|示意图|资料|手册|文档|分类目录)"
    r"|《[^》]{1,24}》"
    r"|(未写明|未说明|未包含|未提及|未直接|并未|仅给出|仅提供|仅说明|只说明|无从|无法核实|无法判断)"
)
_STRONG_PATTERNS = re.compile(
    r"\d+(?:\.\d+)?\s*(?:u[lL]|μ[lL]|[mM][lL]|℃|°|%|毫克|克|小时|分钟|秒|[kKmMGT]?[hH]z|[kKmMGT]?V[A]?|Ω|dB)"
    r"|\d[.,]\d"
    r"|(?:不能用于|不得|禁止|严禁|不允许|不可用于|禁止用于|无法用于)"
)

# 数值词面核验用 token：只认「小数」或「整数+测量单位」，不认型号孤立数字（1C）与引用角标 [n]
_DECIMALS = re.compile(r"\d+\.\d+")
_INT_UNIT = re.compile(
    r"\d+(?:\.\d+)?\s*(?:u[lL]|μ[lL]|[mM][lL]|℃|°|%|毫克|克|小时|分钟|秒|[kKmMGT]?[hH]z|[kKmMGT]?V[A]?|Ω|dB)"
)


def _numerically_supported(statement: str, context_text: str) -> bool:
    """LLM 判 fact 无依据的句子，若数值在上下文原文逐字命中 → 判 LLM 看漏依据。

    规则：含小数（0.35 类）时要求全部小数字符串都命中上下文；
    仅整数带单位（5uL 类）时要求每个「数字+单位」复合串命中。==> 单向放松，只救不罚。
    """
    decimals = _DECIMALS.findall(statement)
    if decimals:
        return all(d in context_text for d in decimals)
    int_units = _INT_UNIT.findall(statement)
    return bool(int_units) and all(u in context_text for u in int_units)


def _harden_kind(statement: str, kind: str) -> str:
    """LLM 判 fact、但陈述命中高置信 meta 句式 → 降为 meta。

    降级目的：出处引用句（"根据…说明书示意图"）、否定性说明（"只说明…未写明…"）、
    自我纠错句（"上一轮我归到[2]…"）这类 LLM 常误判为 fact 的边缘陈述，统一按
    "meta 单报"走，不再计入忠实惩罚；数值断言/禁止条款/归源错误仍留 fact 侧。
    纠错/引用归属句无条件降（不核查引号内嵌数值）；出处/否定性句需同时无强罚指纹。
    """
    if kind != "fact":
        return kind
    if _META_UNCOND.search(statement):
        return "meta"
    if _META_FINGERPRINTS.search(statement) and not _STRONG_PATTERNS.search(statement):
        return "meta"
    return kind


async def compute_faithfulness(
    query_func: Callable,
    question: str,
    answer: str,
    contexts: list[dict],
) -> dict[str, Any]:
    """计算 faithfulness。

    返回: {score, total_statements, supported_statements, unsupported, meta_report,
           enhanced, enhance_count, enhance_score, kind_counts, reason}
    score 只按 fact 计；meta 进入 meta_report（单报）；enhance 单列知识增强分。
    """
    if not answer or not answer.strip():
        return {
            "score": 1.0,
            "total_statements": 0,
            "supported_statements": 0,
            "unsupported": [],
            "meta_report": {"count": 0, "statements": []},
            "enhanced": [],
            "enhance_count": 0,
            "enhance_score": 0.0,
            "kind_counts": {"fact": 0, "meta": 0, "enhance": 0},
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

    uns = []
    n_downgraded = 0  # 代码侧把 LLM 判 fact 的陈述降为 meta 的条数（要从 fact 总数扣减）
    for u in result.get("unsupported") or []:
        stmt = u.get("statement", "")
        kind = u.get("kind") if u.get("kind") in ("fact", "meta", "enhance") else "fact"
        hard = _harden_kind(stmt, kind)
        if hard != kind and kind == "fact":
            n_downgraded += 1
            kind = hard
        uns.append({"statement": stmt, "kind": kind, "reason": u.get("reason", "")})
    # 只罚 fact；meta 单报；enhance 计知识增强分
    penalized_raw = [u for u in uns if u["kind"] == "fact"]
    meta_report = [u for u in uns if u["kind"] == "meta"]
    enhanced = [u for u in uns if u["kind"] == "enhance"]

    # 数值词面核验（2026-10-24 用户拍板）：LLM 判 fact 无依据、但陈述中的全部数值
    # token（小数 / 整数+单位）都能在上下文原文逐字命中 → 判 LLM 看漏了依据，救回为有依据。
    # 单向放松：只救不罚；型号孤立数字（LSP-1C）与引用角标 [n] 不在 token 内，不会被误救。
    rescued = []
    kept_penalized = []
    for u in penalized_raw:
        if _numerically_supported(u["statement"], context_text):
            rescued.append(u["statement"])
        else:
            kept_penalized.append(u)
    penalized = kept_penalized

    total_all = max(0, result.get("total_statements", 0))
    fact_total_raw = max(0, result.get("fact_total", 0) or 0)
    if fact_total_raw:
        # 分母扣掉被指纹降为 meta 的条数（它们不再属于 fact 集合）
        fact_total = max(0, fact_total_raw - n_downgraded)
    else:
        # 兜底：裁判未给 fact_total，用全部陈述扣除 meta/enhance 近似
        fact_total = max(0, total_all - len(meta_report) - len(enhanced))
    fact_supported_coarse = max(0, result.get("supported_statements", 0))
    # 用事实口径重新算支撑数（避免裁判漏标 unsupported 与 supported 的对齐问题）
    fact_unsupported = len(penalized)
    fact_supported = max(0, fact_total - fact_unsupported)
    core_score = (fact_supported / fact_total) if fact_total else 1.0
    enhance_score = (len(enhanced) / fact_total) if fact_total else 0.0

    return {
        "score": core_score,
        "total_statements": fact_total,
        "supported_statements": fact_supported,
        "penalized_count": fact_unsupported,
        "unsupported": penalized,       # 仅含计入惩罚的 fact
        "meta_report": {                # meta 单报：不参与计分，供人工核查
            "count": len(meta_report),
            "statements": [
                {"statement": u["statement"], "reason": u["reason"]} for u in meta_report
            ],
        },
        "enhanced": enhanced,
        "enhance_count": len(enhanced),
        "enhance_score": enhance_score,
        "numeric_rescued": rescued,     # 数值词面核验救回的陈述（原被判无依据，实际数值在上下文）
        "kind_counts": {
            "fact": sum(1 for u in uns if u["kind"] == "fact"),
            "meta": sum(1 for u in uns if u["kind"] == "meta"),
            "enhance": len(enhanced),
        },
        "reason": result["reason"],
    }