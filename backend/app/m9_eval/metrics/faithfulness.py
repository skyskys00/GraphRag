"""Faithfulness（忠实度）：答案主体是否只基于检索上下文，没有编造。

v5.48 答案分箱后回落为朴素单层口径（用户拍板）：generate 把建议/推断/补充物理拆进
<advice> 区，judge 只审主答案 —— 每个陈述无依据即罚，不再做 fact/meta/enhance 分类、
不再叠 meta 指纹。保留唯一代码侧兜底：

- 数值词面核验（`_numerically_supported`）：被判无依据的陈述若其数值 token（小数 / 整数+单位）
  能在上下文原文逐字命中 → 判 LLM 看漏了依据，救回为有依据（单向放松，只救不罚；
  型号孤立数字如 LSP-1C 与引用角标 [n] 不在 token 内，不会被误救）。
"""
from __future__ import annotations

import re
from typing import Any, Callable

from ..judge import judge

SYSTEM_PROMPT = """你是一个严谨的 RAG 评测裁判。你的任务是判断给定的答案是否完全基于提供的上下文材料。
对答案中的每一个陈述，判断它是否能在检索上下文中找到明确依据；找不到依据的即是幻觉（即使该陈述本身是正确知识）。
只根据上下文判断，不要使用外部知识。"""
PROMPT_TEMPLATE = """请评估以下答案的忠实度：答案中的每一个陈述是否都能在检索上下文中找到依据。

【问题】
{question}

【检索上下文】
{context}

【答案】
{answer}

特别判据：以「根据…说明书/资料/示意图标注」等开头的来源引导语，只是声明后续内容的出处，不是独立事实陈述——不要单独把它计入陈述总数，也不要因它单独标记无依据；但该引导语之后的具体内容，仍必须按它在上下文中有无依据正常判断。
请先找出答案中的所有事实陈述（排除连接词、语气词等），然后逐一判断每个陈述是否能在上下文中找到明确依据。
输出 JSON 格式：
{{
  "score": 0.0 到 1.0 的忠实度分数（有依据陈述数 / 陈述总数）,
  "total_statements": 答案的全部陈述数,
  "unsupported": [
    {{"statement": "上下文找不到依据的陈述", "reason": "为什么在上下文中找不到依据"}}
  ],
  "reason": "一句话总结判断结果"
}}
如果某个无依据的陈述实际有上下文证据，务必标为有依据，不要漏标。
"""

# 数值词面核验用 token：只认「小数」或「整数+测量单位」，不认型号孤立数字（1C）与引用角标 [n]
_DECIMALS = re.compile(r"\d+\.\d+")
_INT_UNIT = re.compile(
    r"\d+(?:\.\d+)?\s*(?:u[lL]|μ[lL]|[mM][lL]|℃|°|%|毫克|克|小时|分钟|秒|[kKmMGT]?[hH]z|[kKmMGT]?V[A]?|Ω|dB)"
)


def _numerically_supported(statement: str, context_text: str) -> bool:
    """LLM 判无依据的句子，若数值在上下文原文逐字命中 → 判 LLM 看漏了依据。

    规则：含小数（0.35 类）时要求全部小数字符串都命中上下文；
    仅整数带单位（5uL 类）时要求每个「数字+单位」复合串命中。单向放松，只救不罚。
    """
    decimals = _DECIMALS.findall(statement)
    if decimals:
        return all(d in context_text for d in decimals)
    int_units = _INT_UNIT.findall(statement)
    return bool(int_units) and all(u in context_text for u in int_units)


async def compute_faithfulness(
    query_func: Callable,
    question: str,
    answer: str,
    contexts: list[dict],
) -> dict[str, Any]:
    """计算 faithfulness（朴素单层口径）。

    返回: {score, total_statements, supported_statements, unsupported, numeric_rescued, reason}
    score = 有依据陈述 / 全部陈述；无依据即罚，数值核验救回的计入有依据。
    """
    if not answer or not answer.strip():
        return {
            "score": 1.0,
            "total_statements": 0,
            "supported_statements": 0,
            "unsupported": [],
            "numeric_rescued": [],
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

    uns = [
        {
            "statement": u.get("statement", ""),
            "reason": u.get("reason", ""),
        }
        for u in result.get("unsupported") or []
        if u.get("statement")
    ]
    # 数值词面核验：LLM 判无依据、但全部数值 token 在上下文逐字命中 → 判看漏，救回为有依据
    rescued: list[str] = []
    penalized: list[dict] = []
    for u in uns:
        if _numerically_supported(u["statement"], context_text):
            rescued.append(u["statement"])
        else:
            penalized.append(u)

    total_all = max(max(0, result.get("total_statements", 0) or 0), len(uns))
    fact_supported = max(0, total_all - len(penalized))
    core_score = (fact_supported / total_all) if total_all else 1.0

    return {
        "score": core_score,
        "total_statements": total_all,
        "supported_statements": fact_supported,
        "penalized_count": len(penalized),
        "unsupported": penalized,
        "numeric_rescued": rescued,
        "reason": result["reason"],
    }