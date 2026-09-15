"""M6 引用解析：正文 [n] 标记 → 结构化 Citation（text_unit 级溯源）。"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .sidecar import ChunkMeta, Sidecar

CITE_RE = re.compile(r"\[(\d+)\]")


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
        cs = sidecar.resolve(ref["full_doc_id"], ref["content"]) if sidecar else None
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
                snippet=ref["content"][:120],
                score=ref["score"],
            )
        )
    # 修复：assemble 按精排位次编号，但正文 [n] 出现顺序不等于位次 →
    # 引用列表按置信度 score 降序，前端面板顶部即「置信度第一」。
    citations.sort(key=lambda c: c.score, reverse=True)
    return citations, unmatched