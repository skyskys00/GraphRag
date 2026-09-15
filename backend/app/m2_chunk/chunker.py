"""核心切分：blocks.jsonl（P 策略思想）-> TextUnit 分块。

规则（参照 interfaces.md / qa-notes §E）：
- 标题驱动分组：heading(level>=1) 开新块，正文归入当前标题链；标题链维护 title_path
- 表格整块：block_type=table 单独成块（仍带当前标题上下文）
- 聚合扩展字段：page_range（PDF）/ anchor / block_type（主导类型）
"""
from __future__ import annotations

from typing import Any

HEADING_TYPES = ("heading", "title")


def approx_tokens(content: str) -> int:
    """近似 token 数（中文 1 字≈1 token 的粗估；真实计数在 LightRAG 侧）。"""
    return max(1, len(content))


def _joined_content(blocks: list[dict]) -> str:
    parts = [b.get("content") or "" for b in blocks]
    return "\n\n".join(p for p in parts if p.strip())


def _page_range_of(blocks: list[dict]) -> list[int] | None:
    pages = [b["page_label"] for b in blocks if b.get("page_label") is not None]
    if not pages:
        return None
    return [min(pages), max(pages)]


def _first_anchor(blocks: list[dict]) -> str | None:
    for b in blocks:
        if b.get("anchor"):
            return b["anchor"]
    return None


def _dominant_type(blocks: list[dict], is_table: bool) -> str:
    if is_table:
        return "table"
    cand = [b.get("block_type") for b in blocks if b.get("block_type")]
    if not cand:
        return "paragraph"
    # 多数派，平票取先出现者
    from collections import Counter
    return Counter(cand).most_common(1)[0][0]


def _build_textunit(
    ctx: dict[str, Any],
    i: int,
    doc_id: str,
    file_path: str,
) -> dict:
    blocks: list[dict] = ctx["blocks"]
    path: list[tuple[int, str]] = ctx["path"]
    is_table = bool(ctx.get("is_table"))

    content = _joined_content(blocks)
    title_path = " / ".join(h for _lvl, h in path) if path else None
    last_lvl, last_head = (path[-1] if path else (None, None))
    parent_headings = [h for _lvl, h in path[:-1]]

    heading = None
    if last_head is not None:
        heading = {"level": last_lvl, "heading": last_head, "parent_headings": parent_headings}

    return {
        "text_unit_id": f"{doc_id}-chunk-{i:03d}",
        "content": content,
        "tokens": approx_tokens(content),
        "full_doc_id": doc_id,
        "chunk_order_index": i,
        "file_path": file_path,
        "heading": heading,
        "title_path": title_path,
        "page_range": _page_range_of(blocks),
        "anchor": _first_anchor(blocks),
        "block_type": _dominant_type(blocks, is_table),
        "embedding": None,
        "entity_refs": [],
        "llm_cache_list": [],
    }


def chunk_blocks(blocks: list[dict], doc_id: str, file_path: str) -> list[dict]:
    """blocks.jsonl 行 -> TextUnit 列表。"""
    chunks: list[dict] = []
    path: list[tuple[int, str]] = []

    def _flush(cur: dict | None) -> None:
        if cur is not None and (cur["blocks"] or cur.get("is_table")):
            chunks.append(cur)

    cur: dict | None = None

    for b in blocks:
        bt = b.get("block_type")
        level = b.get("level")
        head = b.get("heading") or None

        if bt == "table" and b.get("content"):
            head_only = cur is not None and cur["blocks"] and all(
                x.get("block_type") in HEADING_TYPES for x in cur["blocks"]
            )
            if head_only:
                # 标题刚开即紧接表格：标题+表格并入同一 TextUnit（保上下文）
                cur["blocks"].append(b)
            else:
                _flush(cur)
                cur = {"blocks": [b], "path": list(path), "is_table": True}
            continue

        if bt in HEADING_TYPES and level is not None and head:
            _flush(cur); cur = None
            # 维护标题路径：弹出 >= 本级的祖先
            while path and path[-1][0] >= level:
                path.pop()
            path.append((level, head))
            cur = {"blocks": [b], "path": list(path), "is_table": False}
        else:
            if cur is None:
                cur = {"blocks": [], "path": list(path), "is_table": False}
            cur["blocks"].append(b)

    _flush(cur)

    out = [_build_textunit(c, i, doc_id, file_path) for i, c in enumerate(chunks)]
    return [u for u in out if u["content"].strip()]