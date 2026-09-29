"""M5 正式检索：三路召回 + RRF(k=60) + bge-reranker 精排 + 引用溯源。

三路：
- graph：aquery_data(mode='mix') 返回的 chunk 排序（图检索证据位次）
- vector：aquery_data(mode='naive') 返回的 chunk 排序（纯 bge-m3 dense 位次）
- keyword：bge-m3 sparse 与预构建索引内积排序（sparse_index.py）

融合后经 RRF(k=60) 取 top，bge-reranker-v2-m3 精排，full_doc_id 溯源直接查库
（lightrag_doc_chunks 自带 full_doc_id，见 M4/M5 已知坑）。
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any

from lightrag import LightRAG, QueryParam

from .feature_fusion import fuse_and_rank
from .query_preprocess import PreprocessedQuery, is_numeric_query, numeric_terms, preprocess as preprocess_query
from .rerank import rerank
from .sparse_index import score as sparse_score
from .table_summary import build_rerank_text

RRF_K = 60
FUSED_TOP = 40
RERANK_TOP = 5


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
    ablation_routes: list[str] | None = None,
) -> dict[str, Any]:
    """正式检索：返回 {query, preprocess, routes, fusion, results, sources}。

    entities: 实体名列表（PG 图节点名），用于预处理的同义词扩展/专名匹配。
              None 时跳过预处理增强，只用原 query。
    exclude_docs: 软删文档的 full_doc_id 集合，召回结果按此过滤（保证删除后不再推荐）。
    allowed_docs: 白名单文档集合。如有，则只返回属于这些文档的 chunk；
                  与 exclude_docs 同时存在时，取「allowed 且不 excluded」的交集。
    ablation_routes: 召回路数对照（ablation）。None/缺省 = graph+vector+keyword 三路全走；
                     传子集（如 ["vector"] 或 ["vector","graph"]）时只执行选中路并入 RRF 融合，
                     keyword 未选中时其 sparse 特征也不参与融合（对照组纯度）。
    """
    active = set(ablation_routes) if ablation_routes is not None else {"graph", "vector", "keyword"}
    prep: PreprocessedQuery | None = None
    q_vec = query
    q_kw = query
    ll_kw: list[str] = []
    if entities:
        prep = preprocess_query(query, entities)
        q_vec = prep.expanded  # 向量路用扩展 query
        q_kw = prep.expanded  # keyword 路也用扩展 query
        ll_kw = prep.ll_keywords

    g_chunks: list[str] = []
    if "graph" in active:
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
        g_chunks = [c.get("chunk_id") for c in graph_data.get("chunks", []) if c.get("chunk_id")]

    v_chunks: list[str] = []
    if "vector" in active:
        vector_data = (
            await rag.aquery_data(
                q_vec,
                QueryParam(mode="naive", top_k=20, chunk_top_k=20, only_need_context=True, enable_rerank=False),
            )
        ).get("data", {})
        v_chunks = [c.get("chunk_id") for c in vector_data.get("chunks", []) if c.get("chunk_id")]
    # 数字感知检索（v5.8）：数字型问题把精确数字 token 追加进 keyword 路 query，
    # 提升含同数字表格行的稀疏点积得分；数字 token 是精确强信号，1x 权重不引入噪声
    kw_chunks: list[str] = []
    kw_score_map: dict[str, float] = {}
    if "keyword" in active:
        kw_query = q_kw
        if is_numeric_query(query):
            nums = numeric_terms(query)
            if nums:
                kw_query = q_kw + " " + " ".join(nums)
        kw_chunks_with_score = sparse_score(kw_query, sparse_doc, top_k=FUSED_TOP)
        kw_chunks = [cid for cid, _ in kw_chunks_with_score]
        kw_score_map = dict(kw_chunks_with_score)

    fused = _rrf([g_chunks, v_chunks, kw_chunks])[:FUSED_TOP]
    fused_ids = [cid for cid, _ in fused]
    rrf_score_map = dict(fused)
    meta = sparse_doc["chunks"]
    candidates = [(cid, build_rerank_text(cid, meta[cid])) for cid in fused_ids if cid in meta]
    # reranker 返回全部候选的分数（不只 topN），供特征融合使用
    reranked_all = await asyncio.to_thread(rerank, query, candidates, top_n=len(candidates))
    rerank_score_map = {item["chunk_id"]: item["score"] for item in reranked_all}

    # 多特征融合排序（v5.9）：在 cross-encoder 语义分基础上，
    # 融合数字匹配度、sparse 内积、RRF 位次等特征，
    # 解决 cross-encoder 对表格/数值型数据理解弱的问题
    fused_results = fuse_and_rank(
        query=query,
        chunk_ids=fused_ids,
        chunk_meta=meta,
        rerank_scores=rerank_score_map,
        sparse_scores=kw_score_map,
        rrf_scores=rrf_score_map,
        top_n=FUSED_TOP,
    )
    reranked = fused_results[:RERANK_TOP]

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
                "features": item.get("features", {}),
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
        "fusion": {
            "rrf_scores": dict(fused),
            "rerank_scores": rerank_score_map,
            "sparse_scores": kw_score_map,
            "fused_top40": [
                {"chunk_id": r["chunk_id"], "score": r["score"], "features": r["features"]}
                for r in fused_results
            ],
        },
        "results": results,
    }