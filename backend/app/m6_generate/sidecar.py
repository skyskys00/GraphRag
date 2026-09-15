"""M6 溯源 sidecar：chunk 定位元数据映射。

M5 返回的 chunk_id 是 LightRAG 哈希键（PG doc_chunks.id，如 chunk-7371...），
与 M2 产物的 text_unit_id（如 035cfc...-chunk-000）不一致。溯源信息（
file_path/title_path/page_range/anchor）在 M2 产物 data/chunks/*.jsonl 里，
这里用 (full_doc_id, content) 精确对齐——同一批索引的 content 文本一致，
30 条量极小。resolve 失败时降级为「文件 + 文本片段」（无 page_range/anchor）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ChunkMeta:
    text_unit_id: str
    content: str
    file_path: str
    full_doc_id: str
    title_path: str | None = None
    page_range: list[int] | None = None
    anchor: str | None = None
    block_type: str | None = None


class Sidecar:
    """chunk 溯源查询器：按 (full_doc_id, content) 精确匹配文本段。"""

    def __init__(self, items: list[ChunkMeta]) -> None:
        self.items = items
        self._by_doc_content: dict[tuple[str, str], ChunkMeta] = {}
        self._by_text_unit: dict[str, ChunkMeta] = {}
        for m in items:
            self._by_doc_content[(m.full_doc_id, m.content)] = m
            self._by_text_unit[m.text_unit_id] = m

    def resolve(self, full_doc_id: str, content: str) -> ChunkMeta | None:
        return self._by_doc_content.get((full_doc_id, content))

    def by_text_unit(self, text_unit_id: str) -> ChunkMeta | None:
        return self._by_text_unit.get(text_unit_id)


def load(data_chunks_dir: str | Path) -> Sidecar:
    """读 data/chunks/*.jsonl 构建 sidecar（全量，启动时加载）。"""
    d = Path(data_chunks_dir)
    items: list[ChunkMeta] = []
    for p in sorted(d.glob("*.jsonl")):
        for line in p.open(encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            tid = rec.get("text_unit_id")
            if not tid:
                continue
            items.append(
                ChunkMeta(
                    text_unit_id=tid,
                    content=rec.get("content") or "",
                    file_path=rec.get("file_path") or "",
                    full_doc_id=rec.get("full_doc_id") or "",
                    title_path=rec.get("title_path"),
                    page_range=rec.get("page_range"),
                    anchor=rec.get("anchor"),
                    block_type=rec.get("block_type"),
                )
            )
    return Sidecar(items)