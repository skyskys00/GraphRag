"""Docling adapter：docx/pptx/xlsx/html/... -> parse/<doc_id>/（blocks.jsonl + md）。

契约：docs/modules/M0_contracts/parse.md；实测 docx prov 全空（无页码/无坐标），anchor 留空待自研。
"""
from __future__ import annotations

import hashlib
import shutil
import tempfile
from pathlib import Path
from typing import Any

from ._util import make_doc_id, write_jsonl, write_meta


def _docling_block_type(label: str) -> str:
    if label in ("title", "section_header"):
        return "heading"
    if label == "table":
        return "table"
    if label in ("picture", "figure"):
        return "drawing"
    if label in ("formula", "equation"):
        return "equation"
    if label in ("text", "list_item", "paragraph", "code"):
        return "paragraph"
    return "mixed"


def _table_html(item: Any, doc: Any) -> str:
    """表格块 content：优先 docling-crore TableItem.export_to_html，兜底拼文本行。"""
    try:
        fn = getattr(item, "export_to_html", None)
        if callable(fn):
            html = fn(doc)
            if html:
                return html
    except Exception:  # noqa: BLE001 - 不同 docling 版本 API 差异
        pass
    dt = getattr(item, "data_table", None)
    if dt is not None and getattr(dt, "rows", None):
        return "\n".join(
            " | ".join((c.text or "") for c in getattr(r, "cells", []))
            for r in dt.rows
        )
    return ""


def build_blocks_from_docling(doc: Any, doc_id: str) -> list[dict]:
    """DoclingDocument 项 -> blocks.jsonl。docx：无 page/anchor（prov 为空）。"""
    out: list[dict[str, Any]] = []
    for i, (item, _level) in enumerate(doc.iterate_items()):
        label = item.label.value if item.label else "other"
        if label == "table":
            text = _table_html(item, doc)
        else:
            text = (getattr(item, "text", None) or "").strip()
        # 标题层级：实测 docx 有 title / section_header 结构化 label
        hl = 1 if label == "title" else (2 if label == "section_header" else None)
        out.append({
            "type": "content",
            "blockid": hashlib.md5(f"{doc_id}:{i}:{text[:64]}".encode("utf-8")).hexdigest()[:32],
            "format": "plain_text",
            "content": text,
            "heading": text if hl else None,
            "parent_headings": [],
            "level": hl,
            "session_type": "body",
            "table_slice": "none",
            "positions": [],
            # —— 契约扩展字段（docx 暂无 page/anchor）——
            "page_label": None,
            "block_type": _docling_block_type(label),
            "anchor": None,
        })
    return out


def parse_with_docling(src: Path, out_root: Path) -> Path:
    from docling.document_converter import DocumentConverter

    doc_id = make_doc_id(src)
    doc_dir = out_root / doc_id
    doc_dir.mkdir(parents=True, exist_ok=True)

    res = DocumentConverter().convert(str(src))
    doc = res.document

    blocks = build_blocks_from_docling(doc, doc_id)
    write_jsonl(doc_dir / "blocks.jsonl", blocks)
    (doc_dir / f"{src.stem}.md").write_text(doc.export_to_markdown(), encoding="utf-8")

    # 保留原始结构 JSON 供细粒度溯源
    tmp_json = Path(tempfile.mkdtemp(prefix="m1_docling_")) / "doc.json"
    doc.save_as_json(str(tmp_json))
    shutil.copyfile(tmp_json, doc_dir / "doc.json")

    write_meta(doc_dir, engine="docling", src_name=src.name, parse_schema="parse-v1.1",
               note="docx anchor 待自研 paraId 补丁")
    return doc_dir