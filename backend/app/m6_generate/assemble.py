"""M6 上下文组装：把 M5 精排结果压缩进 token 预算窗口，带 marker 映射。

与 LLM 无关，纯文本 + 预算截断，便于单测：给定固定 results fixture，
输出 context 总估算 token 不超预算，marker 与 chunk 一一对应。
"""
from __future__ import annotations

from typing import Any

# prompt 模板（system + query 指令）预留的 token，避免材料挤占生成空间
PROMPT_OVERHEAD = 256
# 每个块行（"[n] " + content）的固定开销
PER_LINE_OVERHEAD = 8


def _est_tokens(text: str) -> int:
    """粗糙 token 估算：中文为主按 2 字符/token，保守偏大防超窗。"""
    return (len(text) + 1) // 2


def assemble(
    query: str, results: list[dict[str, Any]], max_data_tokens: int = 4000
) -> tuple[str, dict[int, dict[str, Any]]]:
    """results（按 M5 精排位次，每项含 chunk_id/content/full_doc_id/score）
    → (context, markers)。context 每行以 [n] 开头；markers[n] = 块引用。
    """
    context_lines: list[str] = []
    markers: dict[int, dict[str, Any]] = {}
    used = PROMPT_OVERHEAD + _est_tokens(query)
    for i, r in enumerate(results, 1):
        content = (r.get("content") or "").strip()
        cost = _est_tokens(content) + PER_LINE_OVERHEAD
        if used + cost > max_data_tokens:
            break
        markers[i] = {
            "chunk_id": r["chunk_id"],
            "full_doc_id": r["full_doc_id"],
            "content": content,
            "score": r["score"],
        }
        context_lines.append(f"[{i}] {content}")
        used += cost
    return "\n\n".join(context_lines), markers