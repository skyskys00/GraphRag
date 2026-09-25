"""统一 blocks.jsonl 构建 + 契约扩展字段（parse.md §3）。"""
from __future__ import annotations

import hashlib
import re
from typing import Any

# 章级 strong marker：匹配「第一章/第3章/第一篇/第六部分」式标题。
# MinerU pipeline（PDF 链路）用模型推断 text_level，实测常把「第X章」误判为与「X.Y」
# 节同级（都=2），导致 chunker 弹栈策略丢掉章、title_path 退化为「文档名/节」、
# sibling 展开为整文档目录；docx（office 后端读 Word 大纲）章值本来就是 1，恒等不变。
_CHAPTER_RE = re.compile(r"^第(?:[一二三四五六七八九十百千万]+|[0-9０-９]+)[章节篇卷]")


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
        # 标题层级再校准：章级 marker 强制为 1（PDF 模型误判章=节的修复点，
        # 见 _CHAPTER_RE 注释；对 docx 无影响）。
        level = 1 if (is_heading and _CHAPTER_RE.match(text)) else tl

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
            "level": level,
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