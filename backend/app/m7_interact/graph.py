"""M8 v2.2 图谱导出：PG 图存储全量 + 软删文档独有实体过滤。

数据源：
- rag.chunk_entity_relation_graph.get_all_nodes()/get_all_edges() —— LightRAG 图存储实例
  （PG lightrag_graph_nodes/edges 全量。rag.graph_storage 是类名字符串，勿用）
  （节点 {id=实体名, entity_type, source_id, description}；边 {source, target,
  description(关系描述), weight, source_id}）。
- lightrag_doc_chunks(id → full_doc_id)：把节点/边的 source_id（chunk 列表）归因到文档。

软删过滤语义：实体所有贡献文档都 ∈ excluded_docs → 剔除；含任一有效文档 → 保留；
无法归因（source_id 空/映射不到）→ 保守保留（当前库中软删文档的 chunk 均在册，不会走到）。
"""
from __future__ import annotations

import os
from typing import Any

import asyncpg

SEP = "<SEP>"


async def _chunk_to_doc(workspace: str) -> dict[str, str]:
    """chunk_id → full_doc_id。连接范式与 app.m5_retrieve.sparse_index._pg_chunks 一致。"""
    conn = await asyncpg.connect(
        host="127.0.0.1", port=5432, user="postgres", password="postgres", database="postgres"
    )
    try:
        rows = await conn.fetch(
            "SELECT id, full_doc_id FROM lightrag_doc_chunks WHERE workspace = $1", workspace
        )
        return {r["id"]: r["full_doc_id"] for r in rows}
    finally:
        await conn.close()


def _as_list(source_id: Any) -> list[str]:
    if not source_id:
        return []
    if isinstance(source_id, (list, tuple)):
        return [str(s) for s in source_id]
    return [s for s in str(source_id).split(SEP) if s]


def _docs_of(source_ids: list[str], chunk_to_doc: dict[str, str]) -> set[str]:
    return {chunk_to_doc[c] for c in source_ids if c in chunk_to_doc}


def _keep(doc_set: set[str], excluded: set[str], allowed: set[str] | None) -> bool:
    """归属文档过滤：
    - allowed 有值：至少一个归属文档在白名单内 → 保留
    - excluded 有值：全部归属文档都在排除集合 → 剔除
    - 无法归因（doc_set 空）→ 保守保留
    """
    if not doc_set:
        return True
    if allowed is not None and not doc_set.intersection(allowed):
        return False
    if excluded and doc_set.issubset(excluded):
        return False
    return True


async def collect_graph(
    rag: Any,
    excluded_docs: set[str],
    doc_id: str | None = None,
    allowed_docs: set[str] | None = None,
) -> dict[str, Any]:
    """图数据导出。doc_id 指定时仅保留该文档贡献的实体（按文档维度过滤，见 M8 v2.3）。
    allowed_docs 白名单：如有，则只保留归属至少一个白名单文档的实体/边。
    """
    ws = os.environ.get("POSTGRES_WORKSPACE", "lightrag_m4")
    chunk_to_doc = await _chunk_to_doc(ws)
    excluded = set(excluded_docs or ())

    raw_nodes = await rag.chunk_entity_relation_graph.get_all_nodes()
    raw_edges = await rag.chunk_entity_relation_graph.get_all_edges()

    keep_ids: set[str] = set()
    nodes: list[dict[str, Any]] = []
    for n in raw_nodes:
        source_ids = _as_list(n.get("source_id"))
        doc_set = _docs_of(source_ids, chunk_to_doc)
        if not _keep(doc_set, excluded, allowed_docs):
            continue
        if doc_id is not None and doc_id not in doc_set:
            continue
        keep_ids.add(n["id"])
        nodes.append({
            "id": n["id"],
            "entity_type": n.get("entity_type") or "unknown",
            "description": n.get("description") or "",
            "docs": sorted(doc_set),
            # chunks 保留原始 chunk-id 列表，供前端「引用 → 图谱」反查高亮
            "chunks": sorted(source_ids),
        })

    edges: list[dict[str, Any]] = []
    for e in raw_edges:
        src, tgt = e.get("source"), e.get("target")
        if src in keep_ids and tgt in keep_ids:
            edges.append({
                "source": src,
                "target": tgt,
                "relation": e.get("description") or e.get("relation_name") or "",
                "weight": float(e.get("weight") or 0),
            })

    return {
        "nodes": nodes,
        "edges": edges,
        "meta": {"node_count": len(nodes), "edge_count": len(edges)},
    }