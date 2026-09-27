"""LLM listwise 终审重排（M9 正式可选 reranker：用理解数值/结构语义的层做终审）。

标准检索：cross-encoder + 多特征融合排序（fuse_and_rank + RERANK_TOP 截断）。
本模块：把融合候选池（fusion.fused_top40）前 pool_size 个交给 LLM listwise 重排，
取前 top_n 作为最终评测上下文 —— 绕过 cross-encoder 对表格/专名内容的失明
（表块能进池但被低分压出 top5）。

v5.21 A/B 验证：nDCG@5 0.8567→0.9479、Re@5 0.9524→0.9857、gold_rank avg 2.04→1.76。
设计边界（v5.21 决策）：
- 仅 M9 评测可选（--reranker llm），不进入生产检索路径（M5）；
- 不接 answer 链路（流式不兼容 + 评测同源偏置风险 + 生产 token 成本）。
"""
from __future__ import annotations

import re

RERANK_SYSTEM_PROMPT = (
    "你是文档检索重排器。给定一条用户查询和一组带编号的候选文本片段，"
    "判断每条候选与查询的相关性，输出最相关的 {top_k} 个候选的编号。"
    "只输出 JSON 数组，形如 [3, 17, 2, 9, 5]，编号按相关性从高到低排列，"
    "不要输出任何其他文字。"
)


def _truncate(content: str, n: int = 300) -> str:
    return content.replace("\n", " ⏎ ")[:n]


def _parse_ranking(text: str) -> list[int]:
    """从 LLM 输出解析编号数组。容忍 markdown fence / 尾部文字。"""
    m = re.search(r"\[([0-9,\s]+)\]", text)
    if not m:
        return []
    return [int(x) for x in re.findall(r"\d+", m.group(1))]


async def llm_listwise(query_func, question: str, pool: list[dict], top_k: int) -> list[int]:
    """把 query + 池内块交给 LLM，返回其给出的重排顺序（排名→池内下标）。"""
    system_prompt = RERANK_SYSTEM_PROMPT.format(top_k=top_k)
    lines = [f"查询：{question}\n", "候选片段："]
    for i, blk in enumerate(pool):
        lines.append(f"{i + 1}. {_truncate(blk['content'])}")
    prompt = "\n".join(lines)
    raw = (await query_func(prompt, system_prompt=system_prompt)).strip()
    return _parse_ranking(raw)


async def rerank_with_llm(
    query_func,
    question: str,
    retr: dict,
    sparse: dict,
    *,
    top_n: int,
    pool_size: int = 20,
) -> list[dict]:
    """对 retrieve() 结果做 LLM 终审重排，返回评测用 contexts。

    候选池 = fusion.fused_top40 前 pool_size（content 从 sparse chunks 补齐）；
    返回结构与 retrieve() 的 results 对齐（rank/chunk_id/content/full_doc_id/score），
    后续指标计算路径（context_recall/precision/gold_rank/nDCG）完全复用。
    """
    fusion = retr.get("fusion", {})
    fused_pool = fusion.get("fused_top40", [])[:pool_size]
    meta = sparse.get("chunks", {})
    pool = []
    for p in fused_pool:
        cid = p["chunk_id"]
        m = meta.get(cid, {})
        pool.append(
            {
                "chunk_id": cid,
                "content": m.get("content", ""),
                "full_doc_id": m.get("full_doc_id", ""),
            }
        )
    if not pool:
        return []

    order = await llm_listwise(query_func, question, pool, top_k=top_n)
    ranked_idx = [i - 1 for i in order if 1 <= i <= len(pool)]

    # 去重、保序、对齐 rank
    seen = set()
    picked: list[int] = []
    for idx in ranked_idx:
        if idx in seen or idx < 0:
            continue
        seen.add(idx)
        picked.append(idx)
        if len(picked) >= top_n:
            break

    return [
        {
            "rank": i + 1,
            "chunk_id": pool[idx]["chunk_id"],
            "content": pool[idx]["content"],
            "full_doc_id": pool[idx]["full_doc_id"],
            "score": round((top_n - i) / top_n, 4),
        }
        for i, idx in enumerate(picked)
    ]