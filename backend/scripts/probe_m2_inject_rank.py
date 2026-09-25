"""M2 注入全量模拟的排序级判定：注入前后对比关键题目标块的三阶段排名。

回答三个决策问题（judge 波动不可作为依据，用排序层证据判定）：
  1. q004 城市分级表为何全量注入仍不进 top8（fused 层 / rerank 层哪卡住）
  2. q008/q013 赔偿表/配纸量表是否真被拉进池并保住 rerank
  3. q002/q009/q012/q019 的 nDCG 变化是否来自真实排序变动（哪些块进出 top8）

注入口径与 probe_m2_inject_all.py 完全一致（INJECT_MODE=sib，meta content 原样=保守）。

用法：cd backend && /opt/anaconda3/envs/graphrag/bin/python scripts/probe_m2_inject_rank.py
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))

from app.m9_eval.runner import _build_deps, load_testset
from app.m5_retrieve.sparse_index import _sparse_encode
from app.m5_retrieve import retriever as ret_mod

CHUNKS_DIR = PROJ / "data" / "chunks" / "eval_admin"
WORKSPACE = "eval_admin_ws"

# 关键题：{qid: [(chunk_id, 说明), ...]}
CASES = {
    "adm_q004": [
        ("chunk-47930c608e5f8ce0fb7ad8e005adb8bd", "A2 3.1 城市分级表（上海=一类）"),
        ("chunk-4d323af7021bf938844ac5098be0a9c9", "A2 3.2 住宿费表（普通员工一类=350）"),
    ],
    "adm_q008": [
        ("chunk-522ca76443e15487b3eeba2c4f400540", "A3 6.2 赔偿标准表（疏忽大意30%）"),
        ("chunk-6a37820369f8b15dce09224d82ecfde6", "A3 6.2 赔偿比例明细表（故意100%）"),
    ],
    "adm_q013": [
        ("chunk-bb0c843f8a60019920b9618da1b92b07", "A1 配纸量表（20-50人=4箱）"),
        ("chunk-163119c008a647d0d22ad71cffeb85f3", "A1 每箱=5包=2500张（换算注）"),
    ],
    "adm_q002": [],  # 只对比 top8 进出
    "adm_q009": [],
    "adm_q012": [],
    "adm_q019": [],
    "adm_q010": [],
    "adm_q001": [],  # 真降题：办公用品分类表
}

_STRIP = re.compile(r"\*\*(?P<t>[^*]+)\*\*")


def _plain(head: str) -> str:
    return _STRIP.sub(lambda m: m.group("t"), head).strip()


def build_facts(sparse: dict) -> dict:
    """cid -> {parent, leaf, siblings}，按 content 对齐 chunks jsonl（与 probe_m2_inject_all 同口径）。"""
    by_content: dict[str, tuple[str, str]] = {}
    for jf in CHUNKS_DIR.glob("*.jsonl"):
        for line in jf.open(encoding="utf-8"):
            u = json.loads(line)
            tp = u.get("title_path")
            parts = [_plain(x) for x in tp.split(" / ")] if tp else []
            by_content[u["content"]] = parts
    sibling_map: dict[str, set[str]] = {}
    for parts in by_content.values():
        if len(parts) < 2:
            continue
        sibling_map.setdefault(parts[-2], set()).add(parts[-1])
    units: dict[str, dict] = {}
    for cid, m in sparse["chunks"].items():
        parts = by_content.get(m["content"])
        if not parts:
            continue
        parent = parts[-2] if len(parts) >= 2 else ""
        units[cid] = {
            "parent": parent,
            "leaf": parts[-1] if parts else "",
            "siblings": sorted(sibling_map.get(parent, set())),
        }
    return units


def inject(sparse: dict, units: dict, doc_titles: dict, with_meta: bool) -> int:
    """sib 口径注入。with_meta=True 同时替换 meta content（rerank text 注入，等价正式落地）。
    与 probe_m2_inject_all 完全一致。返回注入块数。"""
    n = 0
    for cid, m in sparse["chunks"].items():
        if m.get("block_type") != "table":
            continue
        u = units.get(cid)
        if not u:
            continue
        parent, leaf = u["parent"], u["leaf"]
        sibs = u.get("siblings", [])
        prefix = f"{parent} 本表章节含 {' '.join(sibs)}"
        new_c = f"{prefix}\n{m['content']}"
        sparse["index"][cid] = _sparse_encode([new_c])[0]
        if with_meta:
            m["content"] = new_c
        n += 1
    return n


def fmt_route(routes: dict, cid: str) -> str:
    r = []
    r.append("G" if cid in routes.get("graph", []) else "-")
    r.append("V" if cid in routes.get("vector", []) else "-")
    r.append("K" if cid in routes.get("keyword", []) else "-")
    return "".join(r)


def probe_block(retr: dict, cid: str, label: str) -> dict:
    fusion = retr["fusion"]
    routes = retr["routes"]
    rrf = fusion["rrf_scores"]
    fused40 = fusion["fused_top40"]
    rerank = fusion["rerank_scores"]

    fused_rank = next((i for i, it in enumerate(fused40) if it["chunk_id"] == cid), None)
    final_rank = next((r["rank"] for r in retr["results"] if r["chunk_id"] == cid), None)
    return {
        "label": label,
        "routes": fmt_route(routes, cid),
        "fused#": fused_rank,  # 0-based；None=未进 fused top40
        "rerank": round(rerank.get(cid, 0.0), 4) if cid in rerank else None,
        "final(top8)": final_rank,
        "rrf": round(rrf.get(cid, 0.0), 6) if cid in rrf else None,
    }


def top8_list(retr: dict) -> list[str]:
    return [(r["rank"], r["chunk_id"][:20]) for r in retr["results"]]


async def main() -> None:
    rag, sparse, query_func = await _build_deps(WORKSPACE)
    print(f"[deps] sparse chunks {len(sparse['chunks'])}", flush=True)

    units = build_facts(sparse)
    doc_titles: dict[str, str] = {}
    for cid, u in units.items():
        m = sparse["chunks"][cid]
        docid = m["full_doc_id"]
        if docid not in doc_titles:
            doc_titles[docid] = u["leaf"]

    ts = load_testset(PROJ / "tests" / "testsets" / "testset_admin_30.json")
    qmap = {q["id"]: q for q in ts["questions"]}

    ret_mod.RERANK_TOP = 8

    out = []
    for qid in CASES:
        qtext = qmap[qid]["question"]
        print(f"\n========== {qid} | {qmap[qid]['category']} ==========", flush=True)

        # baseline（未注入）
        base_retr = await ret_mod.retrieve(rag, qtext, sparse, entities=None)
        print("  --- baseline top8 ---")
        for rk, cid in top8_list(base_retr):
            print(f"    #{rk} {cid}")

        # 注入（内存替换 sparse index；with_meta 时也换 meta content，跑完都回滚）
        _meta_backup = {}
        injected_idx = {}
        n_inj = 0
        for cid, m in sparse["chunks"].items():
            if m.get("block_type") != "table":
                continue
            u = units.get(cid)
            if not u:
                continue
            prefix = f"{u['parent']} 本表章节含 {' '.join(u.get('siblings', []))}"
            new_c = f"{prefix}\n{m['content']}"
            injected_idx[cid] = (sparse["index"][cid], _sparse_encode([new_c])[0])
            n_inj += 1
        for cid, (old, new) in injected_idx.items():
            sparse["index"][cid] = new
        print(f"  [inject] {n_inj} 块（sib 保守）", flush=True)

        inj_retr = await ret_mod.retrieve(rag, qtext, sparse, entities=None)
        print("  --- sib(保守rerank) top8 ---")
        for rk, cid in top8_list(inj_retr):
            print(f"    #{rk} {cid}")

        if CASES[qid]:
            print("  --- 目标块三阶段（base | sib保守） ---")
            for cid, label in CASES[qid]:
                b = probe_block(base_retr, cid, label)
                i = probe_block(inj_retr, cid, label)
                print(f"    base GVFK={b['routes']} fused#{b['fused#']} rerank={b['rerank']} final={b['final(top8)']}")
                print(f"    sib  GVFK={i['routes']} fused#{i['fused#']} rerank={i['rerank']} final={i['final(top8)']} | {label}")

        # 升级为 sib+meta（rerank text 注入）
        for cid, (old, new) in injected_idx.items():
            sparse["index"][cid] = old
        _meta_backup = {}
        for cid, m in sparse["chunks"].items():
            if m.get("block_type") != "table":
                continue
            u = units.get(cid)
            if not u:
                continue
            prefix = f"{u['parent']} 本表章节含 {' '.join(u.get('siblings', []))}"
            new_c = f"{prefix}\n{m['content']}"
            _meta_backup[cid] = m["content"]
            m["content"] = new_c
            if cid in injected_idx:
                sparse["index"][cid] = injected_idx[cid][1]
            else:
                sparse["index"][cid] = _sparse_encode([new_c])[0]
        print(f"  [inject] sib+meta（rerank text 也注入，等价正式落地）", flush=True)

        meta_retr = await ret_mod.retrieve(rag, qtext, sparse, entities=None)
        print("  --- sib+meta top8 ---")
        for rk, cid in top8_list(meta_retr):
            print(f"    #{rk} {cid}")
        if CASES[qid]:
            print("  --- 目标块三阶段（sib+meta） ---")
            for cid, label in CASES[qid]:
                i = probe_block(meta_retr, cid, label)
                print(f"    meta GVFK={i['routes']} fused#{i['fused#']} rerank={i['rerank']} final={i['final(top8)']} | {label}")

        # 回滚注入（index + meta）
        for cid, (old, new) in injected_idx.items():
            sparse["index"][cid] = old
        for cid, old_c in _meta_backup.items():
            sparse["chunks"][cid]["content"] = old_c

        out.append({
            "qid": qid,
            "base_top8": top8_list(base_retr),
            "sib_top8": top8_list(inj_retr),
            "meta_top8": top8_list(meta_retr),
            "targets": [dict(probe_block(meta_retr, c, l), **{"cid": c}) for c, l in CASES[qid]] if CASES[qid] else [],
        })

    (PROJ / "tests" / "reports" / "probe_m2_inject_rank.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2))
    print("\n[report] tests/reports/probe_m2_inject_rank.json")
    await rag.finalize_storages()


if __name__ == "__main__":
    asyncio.run(main())