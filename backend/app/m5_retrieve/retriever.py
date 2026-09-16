"""M5 正式检索：三路召回 + RRF(k=60) + bge-reranker 精排 + 引用溯源。

三路：
- graph：aquery_data(mode='mix') 返回的 chunk 排序（图检索证据位次）
- vector：aquery_data(mode='naive') 返回的 chunk 排序（纯 bge-m3 dense 位次）
- keyword：bge-m3 sparse 与预构建索引内积排序（sparse_index.py）

融合后经 RRF(k=60) 取 top，bge-reranker-v2-m3 精排，full_doc_id 溯源直接查库
（lightrag_doc_chunks 自带 full_doc_id，见 M4/M5 已知坑）。
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from lightrag import LightRAG, QueryParam

from .query_preprocess import PreprocessedQuery, preprocess as preprocess_query
from .rerank import rerank
from .sparse_index import score as sparse_score

RRF_K = 60
FUSED_TOP = 40
RERANK_TOP = 8


def _rrf(ranked_lists: list[list[str]]) -> list[tuple[str, float]]:
    agg: dict[str, float] = defaultdict(float)
    for lst in ranked_lists:
        for pos, cid in enumerate(lst):
            agg[cid] += 1.0 / (RRF_K + pos + 1)
    return sorted(agg.items(), key=lambda kv: kv[1], reverse=True)


async def retrieve(
    rag: LightRAG,
    query: str,
    sparse_doc: dict[str, Any],
    entities: list[str] | None = None,
    exclude_docs: list[str] | None = None,
    allowed_docs: list[str] | None = None,
) -> dict[str, Any]:
    """正式检索：返回 {query, preprocess, routes, fusion, results, sources}。

    entities: 实体名列表（PG 图节点名），用于预处理的同义词扩展/专名匹配。
              None 时跳过预处理增强，只用原 query。
    exclude_docs: 软删文档的 full_doc_id 集合，召回结果按此过滤（保证删除后不再推荐）。
    allowed_docs: 白名单文档集合。如有，则只返回属于这些文档的 chunk；
                  与 exclude_docs 同时存在时，取「allowed 且不 excluded」的交集。
    """
    prep: PreprocessedQuery | None = None
    q_vec = query
    q_kw = query
    ll_kw: list[str] = []
    if entities:
        prep = preprocess_query(query, entities)
        q_vec = prep.expanded  # 向量路用扩展 query
        q_kw = prep.expanded  # keyword 路也用扩展 query
        ll_kw = prep.ll_keywords

    graph_data = (
        await rag.aquery_data(
            query,
            QueryParam(
                mode="mix",
                top_k=15,
                chunk_top_k=20,
                only_need_context=True,
                enable_rerank=False,
                ll_keywords=ll_kw,
            ),
        )
    ).get("data", {})
    vector_data = (
        await rag.aquery_data(
            q_vec,
            QueryParam(mode="naive", top_k=20, chunk_top_k=20, only_need_context=True, enable_rerank=False),
        )
    ).get("data", {})

    g_chunks = [c.get("chunk_id") for c in graph_data.get("chunks", []) if c.get("chunk_id")]
    v_chunks = [c.get("chunk_id") for c in vector_data.get("chunks", []) if c.get("chunk_id")]
    kw_chunks = [cid for cid, _ in sparse_score(q_kw, sparse_doc, top_k=FUSED_TOP)]

    fused = _rrf([g_chunks, v_chunks, kw_chunks])[:FUSED_TOP]
    fused_ids = [cid for cid, _ in fused]

    meta = sparse_doc["chunks"]
    candidates = [(cid, meta[cid]["content"]) for cid in fused_ids if cid in meta]
    reranked = rerank(query, candidates, top_n=RERANK_TOP)

    results = []
    excluded = set(exclude_docs or [])
    allowed = set(allowed_docs or [])
    rank = 0
    for item in reranked:
        cid = item["chunk_id"]
        doc_id = meta[cid]["full_doc_id"]
        if excluded and doc_id in excluded:
            continue
        if allowed and doc_id not in allowed:
            continue
        rank += 1
        results.append(
            {
                "rank": rank,
                "chunk_id": cid,
                "content": meta[cid]["content"],
                "full_doc_id": meta[cid]["full_doc_id"],
                "score": item["score"],
            }
        )
    return {
        "query": query,
        "preprocess": {
            "expanded": prep.expanded if prep else query,
            "ll_keywords": prep.ll_keywords if prep else [],
            "matched_entities": prep.matched_entities if prep else [],
            "weighted_terms": prep.weighted_terms if prep else {},
        } if prep else None,
        "routes": {"graph": g_chunks, "vector": v_chunks, "keyword": kw_chunks},
        "fusion": {"scores": dict(fused)},
        "results": results,
    }