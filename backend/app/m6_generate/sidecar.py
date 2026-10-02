"""M6 溯源 sidecar：chunk 定位元数据映射。

M5 返回的 chunk_id 是 LightRAG 哈希键（PG doc_chunks.id，如 chunk-7371...），
与 M2 产物的 text_unit_id（如 035cfc...-chunk-000）不一致。溯源信息（
file_path/title_path/page_range/anchor/image_path）在 M2 产物 data/chunks/*.jsonl 里。

这里用 LightRAG 生成主键的同一个函数 make_custom_chunk_id(full_doc_id, content)
建索引，与 PG 的 chunk 主键逐一对齐。同一文档内 content 重复会算出同一个 id，
LightRAG 入库时按该 id 去重（保留首条），故此处同样 first-wins，保证两边指向同一行。
resolve 失败时降级为「文件 + 文本片段」（无 page_range/anchor）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from lightrag.utils_pipeline import make_custom_chunk_id


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
    image_path: str | None = None


class Sidecar:
    """chunk 溯源查询器：按 PG chunk_id（= LightRAG 哈希键）精确匹配。"""

    def __init__(self, items: list[ChunkMeta]) -> None:
        self.items = items
        self._by_chunk_id: dict[str, ChunkMeta] = {}
        self._by_text_unit: dict[str, ChunkMeta] = {}
        for m in items:
            # first-wins：与 LightRAG 入库去重（seen_chunk_ids）取同一取舍
            self._by_chunk_id.setdefault(
                make_custom_chunk_id(m.full_doc_id, m.content), m
            )
            self._by_text_unit[m.text_unit_id] = m

    def resolve(self, chunk_id: str) -> ChunkMeta | None:
        return self._by_chunk_id.get(chunk_id)

    def by_text_unit(self, text_unit_id: str) -> ChunkMeta | None:
        return self._by_text_unit.get(text_unit_id)


def load(data_chunks_dir: str | Path) -> Sidecar:
    """读 data/chunks/*.jsonl 构建 sidecar（全量；启动、入库、删除后各重建一次）。"""
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
                    image_path=rec.get("image_path"),
                )
            )
    return Sidecar(items)