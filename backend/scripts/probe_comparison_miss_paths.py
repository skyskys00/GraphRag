"""探针：comparison 漏召 fact 的检索路径级诊断（M5 侧定位）。

背景（v5.32 之后）：comparison 诚实口径 0.4583@5，剩余 miss fact 归因 M2 切分 / M5 检索。
本探针把每个漏召 fact 的目标块当「鱼」，在其当前生产检索形态
（localize_query + retrieve + allowed_docs=[doc]，与 M7 compare.py / M9 runner 同构）
下的命中路径逐段解剖：

  1. 三路候选（graph / vector / keyword）是否入池、各自 rank
  2. RRF 全排序位次 vs fused top40 截断 → 「差几名被截」
  3. 若进 top40，精排（rerank+特征融合）后是否进最终 top5
  4. 放宽侧：keyword@200 / vector@200 / graph 扩 top —— 量化「扩池」能救回几名

定位锚取自语料 chunk 内容（生产数据），与评测 ground_truth / key_facts 零交集，
本探针不改任何输入、不抬高指标，只读检索链。

跑法：cd backend && python3 scripts/probe_comparison_miss_paths.py
需要 PG + Xinference 在跑（./dev.sh status）。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.m9_eval.runner import _build_deps, _resolve_collection  # noqa: E402

COLLECTION = "col_b7b876b1"
TESTSET = Path(__file__).resolve().parents[1] / "tests/testsets/testset_device_30.json"

# 每个漏召 fact → (doc_id, 定位锚子串)。锚子串全部来自语料 chunk 实测内容。
# 匹配规则：content 同时含『主锚』；若主锚无命中，回退『备锚』；候选多个取 content 最长。
TARGETS: dict[str, tuple[str, tuple[str, ...], tuple[str, ...]]] = {
    "DV-CP-001.f2": ("8a22bfff101b31f1", ("对注射器的内径和行程进行设置",), ("行程必须大于0小于120",)),
    "DV-CP-001.f3": ("f33baf8f00190aa4", ("Cchippump-2", "140mm"), ("Cchippump-2",)),
    "DV-CP-002.f1": ("0d5c7f07d607e9c4", ("0-279mmHg",), ("血压量程",)),
    "DV-CP-004.f2": ("ff11a8dc007af2d9", ("在线医生", "会诊"), ("会诊",)),
}


def _reveal_id(block_type: str) -> str:
    return {
        "paragraph": "P",
        "heading": "H",
        "table": "T",
        "drawing": "IMG",
    }.get(block_type, block_type)


def _locate_target(sparse: dict, doc_id: str, anchors: tuple[str, ...], fallback: tuple[str, ...]) -> tuple[str | None, str]:
    """在 doc 内找目标块 chunk_id，返回 (chunk_id, 选择理由)。

    匹配：优先 content 同时含所有主锚；无命中则回退「含任一主锚或备锚」。
    """
    doc_cands = [
        (meta.get("content", ""), cid)
        for cid, meta in (sparse.get("chunks") or {}).items()
        if meta.get("full_doc_id") == doc_id
    ]
    cands = [(len(t), cid) for t, cid in doc_cands if all(a in t for a in anchors)]
    if not cands:
        broad = [a for a in anchors] + list(fallback)
        cands = [(len(t), cid) for t, cid in doc_cands if any(a in t for a in broad)]
    if not cands:
        return None, f"无锚命中: anchors={anchors} fb={fallback}"
    cands.sort(reverse=True)
    return cands[0][1], f"locate 候选{len(cands)}个，取最长(content={cands[0][0]}字)"


def _rrf_all(ranked_lists: list[list[str]]) -> dict[str, float]:
    from app.m5_retrieve.retriever import RRF_K

    agg: dict[str, float] = {}
    for lst in ranked_lists:
        for pos, cid in enumerate(lst):
            agg[cid] = agg.get(cid, 0.0) + 1.0 / (RRF_K + pos + 1)
    return agg


async def _kw_wide(rag, q: str, prep, entities: list[str] | None, sparse: dict, target_cid: str) -> tuple[int | None, float | None]:
    """keyword 放宽：复现 retrieve 的数字追加逻辑，sparse 打 top200 找目标块 rank/score。"""
    from app.m5_retrieve.query_preprocess import is_numeric_query, numeric_terms
    from app.m5_retrieve.sparse_index import score as sparse_score

    kwq = prep.expanded if prep else q
    if is_numeric_query(q):
        nums = numeric_terms(q)
        if nums:
            kwq = kwq + " " + " ".join(nums)
    kw200 = sparse_score(kwq, sparse, top_k=200)
    for rank, (cid, s) in enumerate(kw200):
        if cid == target_cid:
            return rank + 1, s
    return None, None


async def main() -> None:
    import json

    from lightrag import QueryParam

    from app.m5_retrieve.query_preprocess import preprocess as preprocess_query
    from app.m5_retrieve.query_localize import build_side_inputs, localize_query
    from app.m5_retrieve.retriever import _rrf, retrieve

    working_dir, workspace = _resolve_collection(COLLECTION)
    rag, sparse, _query_func, entities = await _build_deps(working_dir, workspace)

    reg = json.load(open(working_dir / "documents.json"))
    name_map: dict[str, str] = {}
    for rec in reg.values():
        fn, did = rec.get("filename"), rec.get("doc_id")
        if fn and did and not rec.get("deleted"):
            name_map[fn] = did
    id_to_filename = {did: fn for fn, did in name_map.items()}
    cands_by_doc, doc_text = build_side_inputs(sparse, id_to_filename)

    questions = {q["id"]: q for q in json.load(open(TESTSET))["questions"]}
    print("== 目标块定位 ==")
    located: dict[str, str] = {}
    for name, (doc_id, anchors, fb) in TARGETS.items():
        cid, why = _locate_target(sparse, doc_id, anchors, fb)
        located[name] = cid or ""
        if cid:
            m = (sparse.get("chunks") or {})[cid]
            print(f"  {name}: {_reveal_id(m.get('block_type','?'))} len={len(m['content'])} | {why}")
        else:
            print(f"  {name}: !! {why}")
    print()

    print("== 逐 fact 路径诊断（生产形态 = localize_query + retrieve + allowed_docs=[doc]）==")
    for name, (doc_id, anchors, _fb) in TARGETS.items():
        qid, fact = (name.split(".") + [""])[:2]
        q = questions[qid]
        cid = located.get(name) or ""
        if not cid:
            print(f"[{name}] 无目标块，跳过\n")
            continue
        sq = localize_query(q["question"], doc_id, cands_by_doc, doc_text, entities)
        r = await retrieve(rag, sq, sparse, entities=entities, allowed_docs=[doc_id])
        routes = {k: v for k, v in r["routes"].items()}
        meta = (sparse.get("chunks") or {})[cid]

        def hitin(lst: list[str]) -> int | None:
            for i, x in enumerate(lst):
                if x == cid:
                    return i + 1
            return None

        g_rank, v_rank = hitin(routes["graph"]), hitin(routes["vector"])

        print(f"[{name}] ({id_to_filename.get(doc_id,'?')}) fact={fact}")
        print(f"    q_local = {sq[:90]}")
        print(f"    target: {_reveal_id(meta.get('block_type','?'))} {id_to_filename.get(meta.get('full_doc_id',''),'?')} :: {meta['content'][:80].replace(chr(10),' ')}")
        print(f"    routes → graph={g_rank or 'MISS'} vector={v_rank or 'MISS'} keyword={hitin(routes['keyword']) or 'MISS'}")

        # RRF 全排序位次 + 融合后位次
        rrf_all = _rrf_all([routes["graph"], routes["vector"], routes["keyword"]])
        rrf_sorted = sorted(rrf_all.items(), key=lambda kv: kv[1], reverse=True)
        rrf_pos = next((i + 1 for i, (c, _) in enumerate(rrf_sorted) if c == cid), None)
        fused_pos = next(
            (i + 1 for i, item in enumerate(r["fusion"]["fused_top40"]) if item["chunk_id"] == cid), None
        )
        final_pos = next((i + 1 for i, item in enumerate(r["results"]) if item["chunk_id"] == cid), None)
        print(f"    RRF 全序 rank={rrf_pos or 'NOT-IN-RRF'}  (top40 截断 fused_pos={fused_pos or 'MISS'})  final top5={final_pos or 'MISS'}")
        if rrf_pos and rrf_pos > 40:
            print(f"      - RRF 排名{fused_pos or rrf_pos}，差 {rrf_pos - 40} 名进 top40 截断")
        if final_pos:
            score = r["results"][final_pos - 1]["score"]
            print(f"      - final rank {final_pos} score={score:.4f}")
        if fused_pos and not final_pos:
            print("      - 上下文（fused top40 里被谁压住）：")
            for j, item in enumerate(r["fusion"]["fused_top40"]):
                if j >= 12:
                    break
                mm = (sparse.get("chunks") or {}).get(item["chunk_id"], {})
                mark = "◀◀ 目标" if item["chunk_id"] == cid else "      "
                doc_n = (id_to_filename.get(mm.get("full_doc_id"), "?") or "?")[:6]
                feats = item.get("features", {})
                fs = {k: round(v, 3) for k, v in feats.items()} if isinstance(feats, dict) else feats
                print(f"        {j + 1:2d} {mark} rrf={dict(r['fusion']['rrf_scores']).get(item['chunk_id'], 0):.4f} score={item['score']:.4f} {fs} [{doc_n}] {mm.get('content', '')[:42].replace(chr(10), ' ')}")

        # 放宽侧：keyword / vector / graph 扩池
        prep = preprocess_query(sq, entities) if entities else None
        w_rank, w_score = await _kw_wide(rag, sq, prep, entities, sparse, cid)
        k_note = f"kw@200→rank={w_rank} score={w_score:.4f}" if w_rank else "kw@200→MISS"
        # vector / graph 放宽单独取
        vw = await rag.aquery_data(
            (prep.expanded if prep else sq),
            QueryParam(mode="naive", top_k=200, chunk_top_k=200, only_need_context=True, enable_rerank=False),
        )
        vrank = next(
            (i + 1 for i, c in enumerate((vw.get("data") or {}).get("chunks", [])) if c.get("chunk_id") == cid),
            None,
        )
        gw = await rag.aquery_data(
            sq,
            QueryParam(mode="mix", top_k=100, chunk_top_k=100, only_need_context=True, enable_rerank=False),
        )
        grank = next(
            (i + 1 for i, c in enumerate((gw.get("data") or {}).get("chunks", [])) if c.get("chunk_id") == cid),
            None,
        )
        print(f"    放宽侧 → {k_note} | vec@200={vrank or 'MISS'} | graph@100={grank or 'MISS'}")

        # 结论归类
        if g_rank is None and v_rank is None and hitin(routes["keyword"]) is None:
            if w_rank and w_rank <= 40:
                verdict = "keyMISS→kw@200居然在池内? 与主路冲突(routes keyword 已含), debug"
            elif w_rank is None and vrank is None and grank is None:
                verdict = "三路全 miss 即使放宽到 200/200/100 → 内容/语义匹配不足（M2 切分或 M5 稀疏/稠密质量）"
            else:
                verdict = f"主路三路全 miss，但放宽 {k_note} | vec={vrank} | graph={grank} → 扩池可救"
            print(f"    ✅ 结论: {verdict}")
        elif rrf_pos and rrf_pos <= 40 and final_pos is None:
            print(f"    ✅ 结论: 进 RRF top40（rank={rrf_pos}）但精排没进 top5 → reranker/特征融合问题")
        elif w_rank and w_rank > 40:
            print(f"    ✅ 结论: keyword 在 {w_rank} 名，RRF 前被 top40 截断差 {w_rank - 40} 名 → 扩 fused/keyword top_k 或调 RRF 权重可救")
        elif rrf_pos is None or rrf_pos > 40:
            print(f"    ✅ 结论: 未进 RRF 候选（或居{rrf_pos}）→ 单路 rank 太低，扩池效果有限（见放宽侧）")
        else:
            print(f"    ✅ 结论: 已进 top5（rank={final_pos}）— 本 fact 已被检索命中")
        print()


if __name__ == "__main__":
    asyncio.run(main())