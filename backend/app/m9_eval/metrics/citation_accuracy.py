"""Citation Accuracy（引用准确率）：答案中的引用是否真的能支撑对应陈述。

对于答案中每一个带 [n] 引用标记的陈述，判断其引用的 chunk 内容
是否真的能支撑该陈述。citation_accuracy = 有支撑的引用数 / 总引用数。

注意：
- 只检查答案里有显式 [n] 标记的地方（不含「文末参考文献」式罗列）。
- 引用编号如果对应不上上下文（如越界），记为不正确。
- 如果答案里完全没有引用标记，返回 1.0（无引用可判错）。
"""
from __future__ import annotations

import re
from typing import Any, Callable

from ..judge import judge

SYSTEM_PROMPT = """你是一个严谨的 RAG 评测裁判。你的任务是判断答案中的某一个引用是否真的能支撑它所标记的陈述。
引用成立 = 被引用的上下文材料中明确包含了该陈述的事实依据。
引用不成立 = 上下文里没有对应说法、或上下文说的与陈述不一致、或引用只是提到了同一个词但没有支撑陈述本身。
只根据上下文判断，不要使用外部知识。"""

PROMPT_TEMPLATE = """请判断答案中以下引用是否成立：引用标记的陈述是否能在被引用的上下文中找到依据。

【问题】
{question}

【被引用的上下文（编号 {cite_num}）】
{cite_context}

【带该引用标记的陈述（摘录自答案）】
{statement_with_citation}

请判断：该陈述是否能在被引用的上下文中找到依据。输出 JSON 格式：
{{
  "supported": true 或 false,
  "score": 0.0 到 1.0 的支撑程度,
  "reason": "一句话说明判断依据"
}}
"""

_CITE_NUM_RE = re.compile(r"\[(\d+)\]")
_SENT_BOUNDS = set("。！？.!?\n")


def _sentence_bounds(text: str, cite_pos: int) -> tuple[int, int]:
    """求引用标记 pos 所在陈述句的 [start, end) 区间。

    左边界：从 pos 往前的最近句子边界之后；若 pos 紧跟前一字符是句末标点
    （如「完成。[1]」），把该标点一并包含，从句边界再往前一句开始。
    右边界：从 pos 向后的下一个句子边界 / 换行 / 结尾。
    """
    i = cite_pos - 1
    if i >= 0 and text[i] in _SENT_BOUNDS and text[i] != "\n":
        # 引用标记紧贴句末标点 → 包含标点，回退到再前一个边界之后
        i -= 1
        while i >= 0 and text[i] not in _SENT_BOUNDS:
            i -= 1
        start = i + 1
    else:
        while i >= 0 and text[i] not in _SENT_BOUNDS:
            i -= 1
        start = i + 1
    j = cite_pos
    while j < len(text) and text[j] not in _SENT_BOUNDS:
        j += 1
    return start, j


def extract_cited_statements(answer: str) -> list[dict[str, Any]]:
    """从答案中提取每个 [n] 引用及其所属的陈述句。

    一个引用标记的 statement = 它所在的完整句子（含句末标点），
    连续引用如 [1][2]、句尾引用如「完成。[1]」都归属同一句，不截断、不缺失。
    返回: [{cite_num, statement, char_start}]
    """
    results: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for m in _CITE_NUM_RE.finditer(answer):
        num = int(m.group(1))
        if num <= 0:
            continue
        start, end = _sentence_bounds(answer, m.start())
        stmt = answer[start:end].strip()
        if not stmt:
            continue
        # 同一编号 + 相同陈述句去重
        key = (num, stmt[-30:])
        if key in seen:
            continue
        seen.add(key)
        results.append({"cite_num": num, "statement": stmt, "char_start": m.start()})
    return results


async def compute_citation_accuracy(
    query_func: Callable,
    question: str,
    answer: str,
    contexts: list[dict],
    citations: list[dict] | None = None,
) -> dict[str, Any]:
    """计算 citation accuracy。

    Args:
        contexts: 检索结果列表（含完整 content），按 M6 assemble 约定 marker n = results[n-1]。
        citations: M6 返回的 citations 列表（含 marker / snippet 等），
                   用于补足 contexts 窗口外的引用上下文。
    返回: {score, total_cites, supported_cites, details, reason}
    """
    if not answer or not answer.strip():
        return {
            "score": 1.0,
            "total_cites": 0,
            "supported_cites": 0,
            "details": [],
            "reason": "空答案，无引用可检查",
        }

    cited = extract_cited_statements(answer)
    if not cited:
        return {
            "score": 1.0,
            "total_cites": 0,
            "supported_cites": 0,
            "details": [],
            "reason": "答案中未找到显式引用标记",
        }

    # 构造 marker -> 上下文映射：优先用完整 blocks（marker n = results[n-1]，M6 assemble 约定），
    # 缺失（如引用落在评估窗口外）才回退 citations 的 snippet。
    ctx_by_marker: dict[int, str] = {}
    if contexts:
        for i, ctx in enumerate(contexts, 1):
            ctx_by_marker[i] = ctx.get("content", "") or ctx.get("snippet", "")
    if citations:
        for c in citations:
            marker = c.get("marker")
            if marker is None:
                continue
            num = int(marker)
            if num not in ctx_by_marker:
                ctx_by_marker[num] = c.get("snippet", "") or c.get("content", "")

    details: list[dict[str, Any]] = []
    supported = 0
    for item in cited:
        num = item["cite_num"]
        stmt = item["statement"]
        ctx = ctx_by_marker.get(num, "")
        if not ctx:
            details.append({
                "cite_num": num,
                "statement": stmt,
                "supported": False,
                "score": 0.0,
                "reason": f"引用编号 [{num}] 对应不上任何上下文",
            })
            continue

        prompt = PROMPT_TEMPLATE.format(
            question=question,
            cite_num=num,
            cite_context=ctx,
            statement_with_citation=stmt + f"[{num}]",
        )
        result = await judge(
            query_func,
            "citation_accuracy",
            prompt,
            system_prompt=SYSTEM_PROMPT,
            cache_key_parts=[question, str(num), ctx[:500], stmt],
        )
        supported += 1 if result["score"] >= 0.5 else 0
        details.append({
            "cite_num": num,
            "statement": stmt,
            "supported": result.get("supported", result["score"] >= 0.5),
            "score": result["score"],
            "reason": result["reason"],
        })

    total = len(cited)
    score = round(supported / total, 4) if total else 1.0

    return {
        "score": score,
        "total_cites": total,
        "supported_cites": supported,
        "details": details,
        "reason": f"{supported}/{total} 个引用有依据",
    }
