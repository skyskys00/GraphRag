"""核心切分：blocks.jsonl（P 策略思想）-> TextUnit 分块。

规则（参照 interfaces.md / qa-notes §E）：
- 标题驱动分组：heading(level>=1) 开新块，正文归入当前标题链；标题链维护 title_path
- 表格整块：block_type=table 单独成块（仍带当前标题上下文）
- 聚合扩展字段：page_range（PDF）/ anchor / block_type（主导类型）
"""
from __future__ import annotations

import html as _html
from html.parser import HTMLParser
from typing import Any

HEADING_TYPES = ("heading", "title")
# 大表格行切分阈值：数据行超过该值时按每组 max_rows 行切多组（层级 2 表格行级切分）
# v5.8 由 5 → 3：单条数字行更易成为独立检索单元，配合 M5 数字感知检索提升表格题 precision
TABLE_SPLIT_ROWS = 3


class _TableHTMLParser(HTMLParser):
    """提取 HTML 表格的 caption / 表头行 / 数据行 / footnote。

    兼容 MinerU（<p class=table-caption>/<p class=table-footnote>）与
    Docling export_to_html 的 <table> 结构。cell 文本忽略内嵌标签，rowspan/colspan 语义不还原。
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.captions: list[str] = []
        self.footnotes: list[str] = []
        self.rows: list[list[str]] = []
        self._in_table = False
        self._in_cell = False
        self._cur_row: list[str] | None = None
        self._buf: list[str] = []
        self._mode: str | None = None  # caption / footnote

    def handle_starttag(self, tag: str, attrs) -> None:
        cls = dict(attrs).get("class", "")
        if tag == "table":
            self._in_table = True
        elif tag == "tr" and self._in_table:
            self._cur_row = []
            self.rows.append(self._cur_row)
        elif tag in ("td", "th") and self._in_table:
            self._in_cell = True
            self._buf = []
        elif tag == "p":
            if "table-caption" in cls:
                self._mode = "caption"
            elif "table-footnote" in cls:
                self._mode = "footnote"

    def handle_endtag(self, tag: str) -> None:
        if tag == "table":
            self._in_table = False
        elif tag in ("td", "th") and self._in_cell:
            self._in_cell = False
            if self._cur_row is not None:
                self._cur_row.append("".join(self._buf).strip())
            self._buf = []
        elif tag == "tr":
            self._cur_row = None
        elif tag == "p" and self._mode:
            text = "".join(self._buf).strip()
            if text:
                (self.captions if self._mode == "caption" else self.footnotes).append(text)
            self._mode = None
            self._buf = []

    def handle_data(self, data: str) -> None:
        if self._in_cell or self._mode:
            self._buf.append(data)


def _table_components(
    html: str,
) -> tuple[list[str], list[list[str]], list[list[str]], list[str]]:
    """HTML 表格 -> (captions, header_row, data_rows, footnotes)。

    约定首行恒为表头（MinerU/Docling 表格首行均为表头），其余为数据行。
    """
    p = _TableHTMLParser()
    p.feed(html)
    header, data = [], []
    if p.rows:
        header = p.rows[0]
        data = p.rows[1:]
    return p.captions, header, data, p.footnotes


def _cells_to_md(cells: list[str]) -> str:
    return "| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |"


def _table_markdown(header: list[str], rows: list[list[str]]) -> str:
    """单元格 -> Markdown 表格（clean 文本，进 embedding / 生成上下文）。"""
    lines = [_cells_to_md(header)] if header else []
    if header:
        lines.append("|" + "|".join("---" for _ in header) + "|")
    lines.extend(_cells_to_md(r) for r in rows)
    return "\n".join(lines)


def _cells_to_html_tr(cells: list[str], head: bool = False) -> str:
    tag = "th" if head else "td"
    inner = "".join(f"<{tag}>{_html.escape(c)}</{tag}>" for c in cells)
    return f"<tr>{inner}</tr>"


def _group_table_html(header: list[str], rows: list[list[str]]) -> str:
    parts = ["<table>"]
    if header:
        parts.append("<thead>" + _cells_to_html_tr(header, head=True) + "</thead>")
    if rows:
        parts.append("<tbody>" + "".join(_cells_to_html_tr(r) for r in rows) + "</tbody>")
    parts.append("</table>")
    return "".join(parts)


def split_table_block(block: dict, max_rows: int = TABLE_SPLIT_ROWS) -> list[dict]:
    """table block -> 若干子块：content=Markdown，html=HTML（预览展示）。

    - 数据行 <= max_rows：整表一组；否则按 max_rows 行分组，每组带表头（层级 2 行级切分）。
    - caption 附首组、footnote 附末组。
    - 层级 0 双表示：content 转 markdown 供 embedding/生成，html 保留表格语义供预览。
    """
    block_html = block.get("content") or ""
    if not block_html.strip():
        return []

    caps, header, data, notes = _table_components(block_html)
    if not header and not data:
        return [{"content": block_html, "html": block_html}]

    groups = [(header, data)]
    if len(data) > max_rows:
        groups = [(header, data[i:i + max_rows]) for i in range(0, len(data), max_rows)]

    out: list[dict] = []
    n = len(groups)
    for gi, (h, g_rows) in enumerate(groups):
        md_lines: list[str] = []
        if gi == 0 and caps:
            md_lines.append(f"**{'、'.join(caps)}**")
        md_lines.append(_table_markdown(h, g_rows))
        if gi == n - 1 and notes:
            md_lines.extend(f"> 注：{fn}" for fn in notes)

        html_parts: list[str] = []
        if gi == 0:
            html_parts.extend(f'<p class="table-caption">{_html.escape(c)}</p>' for c in caps)
        html_parts.append(_group_table_html(h, g_rows))
        if gi == n - 1:
            html_parts.extend(f'<p class="table-footnote">{_html.escape(fn)}</p>' for fn in notes)

        b2 = dict(block)
        b2["content"] = "\n\n".join(md_lines)
        b2["html"] = "\n".join(html_parts)
        out.append(b2)
    return out


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

    unit = {
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
    # 表格子块携带 HTML（预览用），content 已是 Markdown（索引/生成用）
    for blk in blocks:
        if blk.get("html"):
            unit["html"] = blk["html"]
            break
    return unit


def _build_split_unit(
    b2: dict, i: int, doc_id: str, file_path: str,
    path: list[tuple[int, str]],
) -> dict:
    """由 split_table_block 产出的表格子块直接构建 TextUnit（content=Markdown, html=HTML）。"""
    title_path = " / ".join(h for _lvl, h in path) if path else None
    last_lvl, last_head = (path[-1] if path else (None, None))
    parent_headings = [h for _lvl, h in path[:-1]]
    heading = None
    if last_head is not None:
        heading = {"level": last_lvl, "heading": last_head, "parent_headings": parent_headings}

    content = b2.get("content") or ""
    unit = {
        "text_unit_id": f"{doc_id}-chunk-{i:03d}",
        "content": content,
        "tokens": approx_tokens(content),
        "full_doc_id": doc_id,
        "chunk_order_index": i,
        "file_path": file_path,
        "heading": heading,
        "title_path": title_path,
        "page_range": _page_range_of([b2]) if b2.get("page_label") is not None else None,
        "anchor": b2.get("anchor"),
        "block_type": "table",
        "embedding": None,
        "entity_refs": [],
        "llm_cache_list": [],
    }
    if b2.get("html"):
        unit["html"] = b2["html"]
    return unit


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
            # 层级 0/2：表格 HTML -> 若干子块（content=Markdown, html=HTML），可能行级切分
            subs = split_table_block(b)
            if not subs:
                continue
            head_only = cur is not None and cur["blocks"] and all(
                x.get("block_type") in HEADING_TYPES for x in cur["blocks"]
            )
            if head_only:
                # 标题刚开即紧接表格：标题+首个表格子块并入同一 TextUnit（保上下文），其余切分独立
                cur["blocks"].append(subs[0])
                cur["is_table"] = True
                chunks.append(cur)
                cur = None
                for b2 in subs[1:]:
                    chunks.append(_build_split_unit(b2, len(chunks), doc_id, file_path, path))
            else:
                _flush(cur)
                cur = None
                for b2 in subs:
                    chunks.append(_build_split_unit(b2, len(chunks), doc_id, file_path, path))
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

    # 表格子块已由 _build_split_unit 构建为终态 TextUnit（无 blocks 键），原样保留；
    # 其余为「上下文形态」（blocks/path），统一转终态。
    out = [
        c if "blocks" not in c else _build_textunit(c, i, doc_id, file_path)
        for i, c in enumerate(chunks)
    ]
    return [u for u in out if u["content"].strip()]