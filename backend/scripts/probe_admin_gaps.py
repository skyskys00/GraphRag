"""Phase A 探针：定位 q004/q008/q013 的 gap chunk 卡在 M5 三路阶段的哪一层。

判定区间：
  进池（routes.graph/vector/keyword 是否召回该 chunk）
  → RRF 融合排名（fusion.fused_top40 里的位置）
  → rerank 语义分（fusion.rerank_scores，表块是否在 cross-encoder 中失明）
  → 最终 top8（results）

与 M9 baseline 完全一致：entities=None（无预处理）、无 allowed/exclude、RERANK_TOP=8。
复用 M9 runner 的 deps 构建（build_rag + load_sparse），不修改任何模块代码。

用法：cd backend && /opt/anaconda3/envs/graphrag/bin/python scripts/probe_admin_gaps.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))

from app.m3_index.runner import build_rag
from app.m5_retrieve import retriever as ret_mod
from app.m5_retrieve.sparse_index import SPARSE_FILE, load as load_sparse

WORKSPACE = "eval_admin_ws"
WORKING_DIR = PROJ / "data" / WORKSPACE

# 每题 Gap chunk：[chunk_id, 说明]。来自 baseline/e2e 诊断与 chunk 文本核对。
GAPS = {
    "adm_q004": [
        ("chunk-47930c608e5f8ce0fb7ad8e005adb8bd", "A2 3.1 城市分级表（上海=一类）"),
        ("chunk-4d323af7021bf938844ac5098be0a9c9", "A2 3.2 住宿费标准表（普通员工 一类=350）"),
    ],
    "adm_q008": [
        ("chunk-522ca76443e15487b3eeba2c4f400540", "A3 6.2 赔偿标准表（疏忽大意30%）"),
        ("chunk-6a37820369f8b15dce09224d82ecfde6", "A3 6.2 赔偿比例明细表（故意100%）"),
        ("chunk-87a8255cb75ce5000000000000000000", "A3 第六章 遗失损坏赔偿（章节块，id 待核）"),
    ],
    "adm_q013": [
        ("chunk-bb0c843f8a60019920b9618da1b92b07", "A1 配纸量表（20-50人=4箱）"),
        ("chunk-163119c008a647d0d22ad71cffeb85f3", "A1 每箱=5包=2500张（换算注）"),
    ],
}

# q008 章节块 id 待核实完整 id，先用内容反查（probe 里做）
import json

_SPARSE_META = None


def _resolve_q008_extras(meta: dict) -> None:
    """反查 q008 章节块「第六章 遗失、损坏与赔偿」的完整 chunk id。"""
    for cid, m in meta.items():
        if "第六章" in m["content"] and "遗失" in m["content"]:
            GAPS["adm_q008"][2] = (cid, "A3 第六章 遗失损坏赔偿（章节块）")
            return


def fmt_routes(routes: dict) -> str:
    g = routes["graph"]
    v = routes["vector"]
    k = list(routes["keyword"])
    return f"graph[{len(g)}] vector[{len(v)}] keyword[{len(k)}]"


async def probe_one(rag, sparse, qid: str, qtext: str) -> list[dict]:
    ret_mod.RERANK_TOP = 8
    retr = await ret_mod.retrieve(rag, qtext, sparse, entities=None)
    routes = retr["routes"]
    fusion = retr["fusion"]
    results = retr["results"]

    rows = []
    for cid, label in GAPS[qid]:
        rrf_map = fusion["rrf_scores"]
        rerank_map = fusion["rerank_scores"]
        fused40 = fusion["fused_top40"]

        rrf_score = rrf_map.get(cid)
        rrf_rank = None
        fused_rank = None
        fused_score = None
        for i, item in enumerate(fused40):
            if item["chunk_id"] == cid:
                fused_rank = i
                fused_score = item["score"]
                break

        final_rank = None
        for r in results:
            if r["chunk_id"] == cid:
                final_rank = r["rank"]
                break

        rows.append(
            {
                "chunk": cid[:24],
                "label": label,
                "in_graph": cid in routes["graph"],
                "in_vector": cid in routes["vector"],
                "in_keyword": cid in routes["keyword"],
                "rrf_score": round(rrf_score, 5) if rrf_score is not None else None,
                "rrf_has": rrf_score is not None,
                "rerank_score": round(rerank_map.get(cid, 0.0), 4) if cid in rerank_map else None,
                "fused_rank(0-based)": fused_rank,
                "fused_score": round(fused_score, 5) if fused_score is not None else None,
                "final_rank(top8)": final_rank,
            }
        )
    return {"qid": qid, "query": qtext, "routes_summary": fmt_routes(routes), "rows": rows}


async def main() -> None:
    rag = await build_rag(WORKING_DIR, workspace=WORKSPACE)
    sparse = load_sparse(WORKING_DIR / SPARSE_FILE)
    _resolve_q008_extras(sparse["chunks"])

    ts = json.loads((PROJ / "tests" / "testsets" / "testset_admin_30.json").read_text())
    qmap = {q["id"]: q for q in ts["questions"]}

    out = []
    for qid in GAPS:
        q = qmap[qid]
        print(f"\n========== {qid} | {q['category']} | {q['question']} ==========", flush=True)
        r = await probe_one(rag, sparse, qid, q["question"])
        print(f"  召回池: {r['routes_summary']}")
        for row in r["rows"]:
            print(
                f"  {row['label']}\n"
                f"    进池 graph={row['in_graph']} vector={row['in_vector']} keyword={row['in_keyword']}"
                f" | RRF={row['rrf_score']}{'(未进RRF)' if row['rrf_score'] is None else ''}"
                f" | rerank={row['rerank_score']}"
                f" | fused#{row['fused_rank(0-based)']}"
                f" | final={row['final_rank(top8)']}"
            )
        out.append({"qid": qid, **r})

    out_path = PROJ / "tests" / "reports" / "probe_admin_gaps.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"\n[done] 探针结果已存 {out_path.relative_to(PROJ)}")


if __name__ == "__main__":
    asyncio.run(main())