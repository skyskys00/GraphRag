"""统一 blocks.jsonl 构建 + 契约扩展字段（parse.md §3）。"""
from __future__ import annotations

import hashlib
from typing import Any


def _blockid(doc_id: str, i: int, text: str) -> str:
    return hashlib.md5(f"{doc_id}:{i}:{text[:64]}".encode("utf-8")).hexdigest()[:32]


def _mineru_block_type(typ: str, text_level: int | None) -> str:
    if typ == "table":
        return "table"
    if typ in ("inline_equation", "interline_equation", "equation"):
        return "equation"
    if typ == "image":
        return "drawing"
    if text_level and text_level >= 1:
        return "heading"
    return "paragraph"


def build_blocks_from_mineru(data: list[dict], doc_id: str) -> list[dict]:
    """MinerU content_list 条目 -> blocks.jsonl 行（每行含扩展字段）。
    text_level 数值语义：0=正文，>=1=标题（v3.4.5 实测，adapter 阶段再校准）。
    """
    out: list[dict[str, Any]] = []
    for i, it in enumerate(data):
        text = (it.get("text") or "").strip()
        tl = it.get("text_level")
        page = it.get("page_idx")
        bbox = it.get("bbox")
        typ = it.get("type") or "text"
        is_heading = bool(tl and tl >= 1)

        # MinerU 表格的 text 为 None，实际内容在 table_body（HTML）和 table_footnote 里
        if typ == "table":
            html_parts: list[str] = []
            caption = it.get("table_caption") or []
            if caption:
                html_parts.append(
                    f'<p class="table-caption">{"".join(caption)}</p>'
                )
            table_body = it.get("table_body") or ""
            if table_body:
                html_parts.append(table_body)
            footnote = it.get("table_footnote") or []
            for fn in footnote:
                html_parts.append(f'<p class="table-footnote">{fn}</p>')
            content = "\n".join(html_parts)
            fmt = "html"
        else:
            content = text
            fmt = "plain_text"

        positions = []
        if page is not None:
            p: dict[str, Any] = {"type": "bbox", "anchor": page}
            if bbox:
                p["bbox"] = bbox
            positions.append(p)

        block = {
            "type": "content",
            "blockid": _blockid(doc_id, i, text),
            "format": fmt,
            "content": content,
            "heading": text if is_heading else None,
            "parent_headings": [],
            "level": tl,
            "session_type": "body",
            "table_slice": "none",
            "positions": positions,
            # —— 契约扩展字段（parse.md §3）——
            "page_label": page,
            "block_type": _mineru_block_type(typ, tl),
            "anchor": f"{page}:{':'.join(map(str, bbox))}" if (page is not None and bbox) else None,
        }
        out.append(block)
    return out