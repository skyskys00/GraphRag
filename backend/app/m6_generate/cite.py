"""M6 引用解析：正文 [n] 标记 → 结构化 Citation（text_unit 级溯源）。"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .sidecar import ChunkMeta, Sidecar

CITE_RE = re.compile(r"\[(\d+)\]")

# 引用置信度相对阈值：低于 max_score × 此比例的引用视为噪音，不展示给用户
# 例如 max=0.976, ratio=0.1 → 0.0976 以下的引用过滤掉（避免 SOP/销售报告等弱相关文档凑数）
CITE_SCORE_RATIO = 0.1
# 最少保留条数：即使低于阈值，也至少保留这几条（极端情况下不致空引用）
CITE_MIN_KEEP = 2

# 表格/富文本 chunk 内容转纯文本（引用 snippet 展示用，还原行/格分隔符为空格）
_TAG_END_RE = re.compile(r"</(?:td|th|tr|p|div|li|br)>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _html_to_text(html: str) -> str:
    t = _TAG_END_RE.sub(" ", html)
    t = _TAG_RE.sub("", t)
    return _WS_RE.sub(" ", t).strip()


@dataclass
class Citation:
    marker: int
    text_unit_id: str
    full_doc_id: str
    file_path: str
    title_path: str | None
    page_range: list[int] | None
    anchor: str | None
    snippet: str
    score: float
    image_path: str | None = None

    def to_dict(self) -> dict:
        return {
            "marker": self.marker,
            "text_unit_id": self.text_unit_id,
            "full_doc_id": self.full_doc_id,
            "file_path": self.file_path,
            "title_path": self.title_path,
            "page_range": self.page_range,
            "anchor": self.anchor,
            "snippet": self.snippet,
            "score": self.score,
            "image_path": self.image_path,
        }


def parse_citations(
    text: str, markers: dict[int, dict], sidecar: Sidecar | None = None
) -> tuple[list[Citation], int]:
    """text 中的每个 [n]（去重）→ Citation；返回 (citations, unmatched数)。

    sidecar 为 None 或未命中时降级：text_unit_id 用 chunk_id、file_path 用空串，
    page_range/anchor 不填（「文件 + 文本片段」定位）。
    sidecar 命中时补 file_path/page_range/anchor/title_path + M2 text_unit_id。
    """
    citations: list[Citation] = []
    seen: set[int] = set()
    unmatched = 0
    for m in CITE_RE.finditer(text):
        n = int(m.group(1))
        if n in seen:
            continue
        seen.add(n)
        ref = markers.get(n)
        if not ref:
            unmatched += 1
            continue
        cs = sidecar.resolve(ref["chunk_id"]) if sidecar else None
        meta: ChunkMeta | None = cs
        citations.append(
            Citation(
                marker=n,
                text_unit_id=(meta.text_unit_id if meta else ref["chunk_id"]),
                full_doc_id=ref["full_doc_id"],
                file_path=(meta.file_path if meta else ""),
                title_path=(meta.title_path if meta else None),
                page_range=(meta.page_range if meta else None),
                anchor=(meta.anchor if meta else None),
                snippet=_html_to_text(ref["content"])[:120],
                score=ref["score"],
                image_path=(meta.image_path if meta else None),
            )
        )
    # 修复：assemble 按精排位次编号，但正文 [n] 出现顺序不等于位次 →
    # 引用列表按置信度 score 降序，前端面板顶部即「置信度第一」。
    citations.sort(key=lambda c: c.score, reverse=True)
    # 相对阈值过滤：低于 max × ratio 的弱引用移除，避免不相关文档凑数；
    # 但至少保留 CITE_MIN_KEEP 条，防止极端情况下全过滤掉。
    if citations:
        threshold = citations[0].score * CITE_SCORE_RATIO
        filtered = [c for c in citations if c.score >= threshold]
        if len(filtered) < CITE_MIN_KEEP:
            filtered = citations[:max(CITE_MIN_KEEP, 1)]
        citations = filtered
    return citations, unmatched