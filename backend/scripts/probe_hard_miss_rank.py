"""探针：v5.33 未满分题的目标块检索路径级诊断（M5 侧定位）。

背景：50 题实测 Recall 0.7909；easy 0.8720 / medium 0.8542 / hard 0.4815。
逐 fact 裁判理由显示：多数失败 fact 的内容**确实存在于语料**，但未进 top5。
本探针把每个失败 fact 的目标块当「鱼」，复现 runner 生产检索形态
（非 comparison：raw question + 全库；comparison：localize_query + per-doc + 交错），
逐段解剖：

  1. 三路候选（graph / vector / keyword）是否入池、各自 rank
  2. RRF 全排序位次 vs fused top40 截断
  3. 精排后是否进最终 top5
  4. 结论归类：候选池未进 / RRF 截断 / 精排截断 / 已命中

定位锚取自语料 chunk 实测内容，与 ground_truth / key_facts 零交集，只读检索链。

跑法：cd backend && python3 scripts/probe_hard_miss_rank.py
需要 PG + Xinference 在跑（./dev.sh status）。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.m9_eval.runner import _build_deps, _resolve_collection  # noqa: E402

COLLECTION = "col_b7b876b1"
TESTSET = Path(__file__).resolve().parents[1] / "tests/testsets/testset_device_50.json"

# (qid, 目标 doc_id, 主锚, 备锚)。锚子串全部来自语料 chunk 实测内容。
TARGETS: list[tuple[str, str, tuple[str, ...], tuple[str, ...]]] = [
    # --- hard ---
    ("DV-TN-005", "a07ebcfa1b858095", ("气压静态压力输出", "0～40"), ("（0～40）kPa",)),
    ("DV-FS-008", "e10aa9a02b70ae74", ("更换荧光帽", "每一年更换一次"), ("建议维护频率",)),
    ("DV-IO-004", "ff11a8dc007af2d9", ("密码设置", "网页浏览设置"), ("帮助反馈",)),
    ("DV-CP-001", "8a22bfff101b31f1", ("行程必须大于0小于120mm",), ("行程必须大于0",)),
    ("DV-CP-001", "f33baf8f00190aa4", ("最大行程", "140mm"), ("通道数量",)),
    ("DV-CP-004", "447207be927c51ff", ("24小时动态心电监测",), ("适用人群",)),
    ("DV-CP-004", "ff11a8dc007af2d9", ("Android 与iSO",), ("测医测",)),
    # --- medium ---
    ("DV-CP-002", "a07ebcfa1b858095", ("气压动态压力模拟", "255mmHg"), ("30mmHg~255mmHg",)),
    ("DV-TN-007", "e10aa9a02b70ae74", ("pH 电极支持三点校准",), ("三点校准",)),
    ("DV-FS-006", "54f036800deb9dff", ("速率在250～500次/分",), ("250～500",)),
    # --- easy ---
    ("DV-FS-002", "447207be927c51ff", ("持续时间不小于 24 小时",), ("连续记录仪",)),
    ("DV-FS-005", "a07ebcfa1b858095", ("满电状态可以连续工作大于 8 小时",), ("大容量锂电池",)),
    ("DV-IO-006", "8a22bfff101b31f1", ("工作被中断",), ("报警文本",)),
]


def _bt(block_type: str) -> str:
    return {"paragraph": "P", "heading": "H", "table": "T", "drawing": "IMG"}.get(block_type, block_type)


def _locate(sparse: dict, doc_id: str, anchors: tuple[str, ...], fb: tuple[str, ...]) -> tuple[str | None, str]:
    doc_cands = [
        (meta.get("content", ""), cid)
        for cid, meta in (sparse.get("chunks") or {}).items()
        if meta.get("full_doc_id") == doc_id
    ]
    cands = [(len(t), cid) for t, cid in doc_cands if all(a in t for a in anchors)]
    if not cands:
        broad = list(anchors) + list(fb)
        cands = [(len(t), cid) for t, cid in doc_cands if any(a in t for a in broad)]
    if not cands:
        return None, f"无锚命中 anchors={anchors} fb={fb}"
    cands.sort(reverse=True)
    return cands[0][1], f"候选{len(cands)}取最长({cands[0][0]}字)"


def _pos(lst: list, cid: str, key=None) -> int | None:
    for i, x in enumerate(lst):
        c = x.get(key) if key else x
        if c == cid:
            return i + 1
    return None


def _rrf_all(ranked: list[list[str]]) -> dict[str, float]:
    from app.m5_retrieve.retriever import RRF_K
    agg: dict[str, float] = {}
    for lst in ranked:
        for pos, cid in enumerate(lst):
            agg[cid] = agg.get(cid, 0.0) + 1.0 / (RRF_K + pos + 1)
    return agg


async def main() -> None:
    import json

    from app.m5_retrieve.retriever import retrieve

    working_dir, workspace = _resolve_collection(COLLECTION)
    rag, sparse, _qf, entities = await _build_deps(working_dir, workspace)

    reg = json.load(open(working_dir / "documents.json"))
    name_map: dict[str, str] = {}
    for rec in reg.values():
        fn, did = rec.get("filename"), rec.get("doc_id")
        if fn and did and not rec.get("deleted"):
            name_map[fn] = did
    id_to_filename = {did: fn for fn, did in name_map.items()}

    questions = {q["id"]: q for q in json.load(open(TESTSET))["questions"]}

    # comparison 需要 localize + per-doc
    from app.m5_retrieve.query_localize import build_side_inputs, localize_query
    cands_by_doc, doc_text = build_side_inputs(sparse, id_to_filename)

    print("== 目标块定位 ==")
    loc: dict[tuple[str, str], str] = {}
    for qid, doc_id, anchors, fb in TARGETS:
        cid, why = _locate(sparse, doc_id, anchors, fb)
        loc[(qid, doc_id)] = cid or ""
        if cid:
            m = sparse["chunks"][cid]
            print(f"  {qid} @ {id_to_filename.get(doc_id,'?')[:28]:28s} [{_bt(m.get('block_type','?'))}] "
                  f"p{m.get('page_range')} len={len(m['content'])} | {why}")
        else:
            print(f"  {qid} @ {doc_id} !! {why}")
    print()

    for qid, doc_id, _anchors, _fb in TARGETS:
        cid = loc.get((qid, doc_id)) or ""
        if not cid:
            continue
        q = questions[qid]
        m = sparse["chunks"][cid]
        print(f"[{qid}] {q['difficulty']}/{q['category']} → {id_to_filename.get(doc_id,'?')}")
        print(f"    target [{_bt(m.get('block_type','?'))}] p{m.get('page_range')} :: {m['content'][:95].replace(chr(10),' ')}")

        if q["category"] == "comparison":
            sub = localize_query(q["question"], doc_id, cands_by_doc, doc_text, entities)
            print(f"    sub_q(localized) = {sub[:95]}")
            r = await retrieve(rag, sub, sparse, entities=entities, allowed_docs=[doc_id])
            scope = "per-doc(localized)"
        else:
            r = await retrieve(rag, q["question"], sparse, entities=entities)
            scope = "全库(raw q)"

        routes = r["routes"]
        g, v, k = (_pos(routes["graph"], cid), _pos(routes["vector"], cid), _pos(routes["keyword"], cid))
        rrf = _rrf_all([routes["graph"], routes["vector"], routes["keyword"]])
        rrf_sorted = sorted(rrf.items(), key=lambda kv: kv[1], reverse=True)
        rrf_pos = next((i + 1 for i, (c, _) in enumerate(rrf_sorted) if c == cid), None)
        fused = r.get("fusion", {}).get("fused_top40", [])
        fused_pos = _pos(fused, cid, "chunk_id")
        final_pos = _pos(r["results"], cid, "chunk_id")
        n_g, n_v, n_k = len(routes["graph"]), len(routes["vector"]), len(routes["keyword"])
        print(f"    [{scope}] 池: graph={n_g} vector={n_v} keyword={n_k}")
        print(f"    routes → graph={g or 'MISS'} vector={v or 'MISS'} keyword={k or 'MISS'}  | RRF全序={rrf_pos or 'NOT-IN'} fused40={fused_pos or 'MISS'} final5={final_pos or 'MISS'}")
        if final_pos:
            print("    ✅ 结论: 已进 top5")
        elif fused_pos:
            print(f"    ✅ 结论: 进 fused top40(#{fused_pos}) 但精排后掉出 top5 → 精排/特征截断")
        elif rrf_pos:
            print(f"    ✅ 结论: 进 RRF 全序(#{rrf_pos}) 但被 top40 截断 → 差 {rrf_pos - 40} 名")
        elif g or v or k:
            print("    ✅ 结论: 至少一路入池但未进 RRF top40 → 单路 rank 过低")
        else:
            print("    ✅ 结论: 三路全 miss → 候选池未进（召回侧）")
        print()


if __name__ == "__main__":
    asyncio.run(main())
