"""探针：comparison 题真实漏召回定位——目标 chunk 在三路召回的完整排名。

背景（v5.29 遗留）：DV-CP-001 的 per-doc 子查询检索中，LSP-1C「行程」chunk
（chunk-bb7dc287…）三路候选池（graph 20 / vector 20 / keyword 40）全不含，
导致 comparison 题漏召回、生成四项垫底。

本脚本把三路的 top_k 放大到全库，dump 目标 chunk 的**真实排名与分数**，
判断瓶颈是「三路都排不进」还是「某一路排不进」，以及离入选边界差多远。

⚠️ **2026-10-03 探底结论：关闭扩展不采纳、已回滚**——生产代码 `retriever.py` 维持
`q_vec/q_kw = prep.expanded`（ON 行为），故文末「融合链路逐环」段的 ON/OFF 对照**重新
有意义**（ON = 线上行为，OFF = 探底分支）。探底副产品（「M5 稀疏权重」已排除、真瓶颈
是 M2 切分粒度）见 CHANGELOG v5.29「探底：关闭 M5 query 扩展」。

跑法：cd backend && python3 scripts/probe_cmp_miss.py
需要 PG + Xinference 在跑（`./dev.sh status`）。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lightrag import QueryParam  # noqa: E402

from app.m5_retrieve.query_preprocess import preprocess as preprocess_query  # noqa: E402
from app.m5_retrieve.sparse_index import score as sparse_score  # noqa: E402
from app.m9_eval.runner import _build_deps, _resolve_collection  # noqa: E402

COLLECTION = "col_b7b876b1"
SUB_Q = "融柏 LSP-1C 系列（型号 LSP01-1BC）注射泵有哪几种运行模式？自定义行程的范围是多少？"
LSP_DOC = "8a22bfff101b31f1"
TARGET = "chunk-bb7dc2875933cadbb12c70ed3dc51252"

# 线上真实入选边界（retriever.py）
GRAPH_K = 20
VECTOR_K = 20
KEYWORD_K = 40


def _rank_of(chunks: list[str], target: str) -> str:
    if target not in chunks:
        return f"未入选（共 {len(chunks)} 条）"
    return f"第 {chunks.index(target) + 1} 名 / 共 {len(chunks)} 条"


async def main() -> None:
    working_dir, workspace = _resolve_collection(COLLECTION)
    rag, sparse, _qf, entities = await _build_deps(working_dir, workspace)
    total = len(sparse["index"])
    print(f"库 {COLLECTION} / workspace={workspace} / 共 {total} chunk")
    print(f"目标: {TARGET}\n")

    prep = preprocess_query(SUB_Q, entities)
    print("── query 预处理 ──")
    print("原始 :", SUB_Q)
    print("扩展 :", prep.expanded)
    print("ll_keywords:", prep.ll_keywords)
    print("weighted_terms:", prep.weighted_terms)
    print()

    # keyword 路：全库排序
    kw_all = sparse_score(prep.expanded, sparse, top_k=total)
    kw_ids = [cid for cid, _ in kw_all]
    kw_sc = {cid: s for cid, s in kw_all}
    print("── keyword 路（sparse 内积，全库排序）──")
    print("目标:", _rank_of(kw_ids, TARGET), f"score={kw_sc.get(TARGET, 0.0):.4f}")
    print(f"线上入选边界 top{KEYWORD_K} 最低分: {kw_all[KEYWORD_K - 1][1]:.4f}")
    print("top10:", [(cid[:16], round(s, 4)) for cid, s in kw_all[:10]])
    print()

    # 对照：不同 query 形态下目标块的 keyword 排名（区分「切分粒度」vs「扩展词污染」）
    print("── 对照：query 形态 → 目标块 keyword 排名 ──")
    variants = {
        "A 扩展后（线上实际用）": prep.expanded,
        "B 原始 query（未扩展）": SUB_Q,
        "C 加权词拼接": " ".join(prep.weighted_terms.keys()),
        "D 仅核心区别词": "行程 内径 自定义 注射泵",
    }
    for tag, q in variants.items():
        ids = [cid for cid, _ in sparse_score(q, sparse, top_k=total)]
        print(f"  {tag:24} 目标块 {_rank_of(ids, TARGET)}")
    print()

    # vector 路：naive 全库排序（ON=线上 expanded query / OFF=原始 query）
    async def _vector_ids(q: str) -> list[str]:
        d = (
            await rag.aquery_data(
                q,
                QueryParam(mode="naive", top_k=total, chunk_top_k=total, only_need_context=True, enable_rerank=False),
            )
        ).get("data", {})
        return [c.get("chunk_id") for c in d.get("chunks", []) if c.get("chunk_id")]

    print("── vector 路（naive，全库排序）──")
    for tag, q in (("ON  扩展 query（线上）", prep.expanded), ("OFF 原始 query", SUB_Q)):
        v_ids = await _vector_ids(q)
        print(f"  {tag:22} 目标块 {_rank_of(v_ids, TARGET)}")
    print()

    # graph 路：mix 全库排序
    gdata = (
        await rag.aquery_data(
            SUB_Q,
            QueryParam(
                mode="mix",
                top_k=total,
                chunk_top_k=total,
                only_need_context=True,
                enable_rerank=False,
                ll_keywords=prep.ll_keywords,
            ),
        )
    ).get("data", {})
    g_ids = [c.get("chunk_id") for c in gdata.get("chunks", []) if c.get("chunk_id")]
    print("── graph 路（mix，全库排序；用 ll_keywords，不受 expanded 影响 ⇒ ON=OFF）──")
    print("目标:", _rank_of(g_ids, TARGET))
    print(f"线上入选边界 top{GRAPH_K}: {g_ids[GRAPH_K - 1][:16] if len(g_ids) >= GRAPH_K else 'n/a'}")
    print()

    # LSP 文档内所有含「行程」的 chunk，看有无替代块
    print("── LSP 文档内 content 含「行程」的 chunk ──")
    for cid, m in sparse["chunks"].items():
        if m.get("full_doc_id") == LSP_DOC and "行程" in m.get("content", ""):
            print(f"  {cid[:24]} kw_rank={_rank_of(kw_ids, cid):>20} bt={m.get('block_type')}")
            print(f"     {m['content'][:120]!r}")

    # ── 融合链路逐环排名：ON vs OFF（完整 retrieve，per-doc 限 LSP）──
    # retrieve() 返回值自带中间产物：routes（三路候选）/ fusion.rrf_scores（RRF 全部）/
    # fusion.fused_top40（精排后 40 条完整排序）。逐环看目标块在哪一步掉队。
    from app.m5_retrieve import retriever as ret_mod  # noqa: E402
    from app.m5_retrieve.retriever import retrieve  # noqa: E402

    def _ids(res: list) -> list[str]:
        return [(x.get("chunk_id") or x.get("id")) for x in res if (x.get("chunk_id") or x.get("id"))]

    print("── 融合链路逐环排名（retrieve，per-doc 限 LSP）──")
    _orig = ret_mod.preprocess_query

    def _no_expand(q, e):
        p = _orig(q, e)
        p.expanded = p.original
        return p

    pools: dict[str, set[str]] = {}
    for tag, patch in (("ON ", _orig), ("OFF", _no_expand)):
        ret_mod.preprocess_query = patch
        r = await retrieve(rag, SUB_Q, sparse, entities=entities, allowed_docs=[LSP_DOC])
        routes = r["routes"]
        rrf_ids = [cid for cid, _ in sorted(r["fusion"]["rrf_scores"].items(), key=lambda kv: kv[1], reverse=True)]
        f40 = r["fusion"]["fused_top40"]
        f40_ids = [x["chunk_id"] for x in f40]
        res_ids = _ids(r["results"])
        pools[tag] = set(f40_ids)
        print(
            f"  [{tag}] 候选池: keyword {_rank_of(routes['keyword'], TARGET)} | "
            f"vector {_rank_of(routes['vector'], TARGET)} | graph {_rank_of(routes['graph'], TARGET)}"
        )
        print(
            f"         RRF池({len(rrf_ids)}) {_rank_of(rrf_ids, TARGET)} | "
            f"精排池({len(f40_ids)}) {_rank_of(f40_ids, TARGET)} | 最终({len(res_ids)}) {_rank_of(res_ids, TARGET)}"
        )
        print("         精排池 top8:")
        for x in f40[:8]:
            txt = sparse["chunks"].get(x["chunk_id"], {}).get("content", "")[:36].replace("\n", " ")
            print(f"           {x['chunk_id'][:20]} {x['score']:.4f} {txt!r}")
    ret_mod.preprocess_query = _orig

    on40, off40 = pools["ON "], pools["OFF"]
    print(f"  (精排池 ON/OFF 重叠 {len(on40 & off40)}/{len(on40)}；OFF 独有 {len(off40 - on40)} 条)")


if __name__ == "__main__":
    asyncio.run(main())
