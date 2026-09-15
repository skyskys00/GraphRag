"""M7 多轮最小注入：把 history 拼进 query（v1 独立检索，不建会话态）。"""
from __future__ import annotations


def build_query_with_history(
    query: str,
    history: list[dict] | None,
    max_entries: int = 4,
    max_chars_per: int = 300,
) -> str:
    if not history:
        return query
    lines: list[str] = []
    for h in history[-max_entries:]:
        role = "用户" if h.get("role") == "user" else "助手"
        content = (h.get("content") or "")[:max_chars_per]
        lines.append(f"{role}：{content}")
    return "\n".join(lines) + f"\n\n当前问题：{query}"