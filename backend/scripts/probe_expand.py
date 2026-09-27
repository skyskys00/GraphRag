#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探底 Step A（v5.21 后）：children + neighbor 展开能救回多少池外答案块。

机制验证，零模块代码改动，不加 LLM 调用：
- children：检索命中「纯标题块」→ 展开其章节子树子孙（M2 阶段将固化为 children 字段）
- neighbor：检索命中任意块 → 同文档 chunk_order_index ±K 展开（K=3）

事实判定：testset key_facts 词法匹配（复用 gold_rank._lexical_match，
数字 token + 关键词子串，与 gold_rank 词汇模式同口径）。

用法（backend 下）：
  python scripts/probe_expand.py                 # eval_admin 全量 30 题
  python scripts/probe_expand.py --limit 8       # 前 8 题快速自测（含 q008）
  python scripts/probe_expand.py --k 3           # neighbor 窗口
"""
from __future__ import annotations

import argparse
import asyncio
import difflib
import glob
import json
import re
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))

from lightrag.utils import sanitize_text_for_encoding
from lightrag.utils_pipeline import make_custom_chunk_id

HEADING_TYPES = ("heading", "title")


def _load_units(chunks_dir: Path) -> dict[str, dict]:
    """读全库 chunks jsonl -> {chunk_md5: unit dict}（含 doc/order/title_path/heading/block_type）。"""
    md5_to_unit: dict[str, dict] = {}
    for fp in glob.glob(str(chunks_dir / "*.jsonl")):
        for line in open(fp, encoding="utf-8"):
            u = json.loads(line)
            cid = make_custom_chunk_id(u["full_doc_id"], sanitize_text_for_encoding(u["content"]))
            md5_to_unit[cid] = u
    return md5_to_unit


def _docs_ordered(md5_to_unit: dict[str, dict]) -> dict[str, list[dict]]:
    """按 full_doc_id 聚合 + 按 chunk_order_index 排序。"""
    from collections import defaultdict
    by_doc: dict[str, list[dict]] = defaultdict(list)
    for u in md5_to_unit.values():
        by_doc[u["full_doc_id"]].append(u)
    for k in by_doc:
        by_doc[k].sort(key=lambda u: u["chunk_order_index"])
    return by_doc


def _is_heading_only(u: dict) -> bool:
    """纯标题块：bt=heading 且 content 即标题本身（无正文混入）。"""
    if u.get("block_type") not in HEADING_TYPES:
        return False
    h = (u.get("heading") or {}).get("heading")
    if not h:
        return False
    return (u.get("content") or "").strip() == h.strip()


def _chapter_children(by_doc: dict[str, list[dict]], u: dict) -> list[dict]:
    """标题块 u -> 本章剩余内容块（文档序连续 + 标题层级下界）。

    注意不能靠 title_path 前缀：M2 把孤立标题块建成独立 TextUnit 后，
    子标题的 title_path 直接挂在更上层（跳过被孤立的标题），
    因此用「章节标题 level 下界」判定——遇到同/更高级别标题即本章结束。
    """
    lvl = (u.get("heading") or {}).get("level")
    if lvl is None:
        return []
    kids = []
    for nb in by_doc.get(u["full_doc_id"], []):
        if nb["chunk_order_index"] <= u["chunk_order_index"]:
            continue
        nbt = nb.get("block_type")
        if nbt in HEADING_TYPES:
            nlvl = (nb.get("heading") or {}).get("level")
            if nlvl is not None and nlvl <= lvl:
                break  # 同级/更高层标题：兄弟或更高，本章到此为止
        kids.append(nb)
    return kids


def _neighbors(by_doc: dict[str, list[dict]], u: dict, k: int) -> list[dict]:
    """同文档 order ±k 的相邻块（不含自身）。"""
    seq = by_doc.get(u["full_doc_id"], [])
    idx = next((i for i, x in enumerate(seq) if x["chunk_order_index"] == u["chunk_order_index"]), None)
    if idx is None:
        return []
    return [x for x in seq[max(0, idx - k): idx + k + 1] if x["chunk_order_index"] != u["chunk_order_index"]]


_PURE_NUM = re.compile(r"(?<!\d)\d[\d,]*(?:\.\d+)?(?!\d)")


def _match_fact(fact: str, content: str) -> bool:
    """宽松事实判定（从宽防漏，探针用）：数字边界精确匹配 + 公共子串。

    1. 数字 token 必须至少 1 个在全 content 中以独立数字出现（边界匹配，
       避免「50」误中「5000」）；
    2. 去数字后的事实文本与 content 最长公共子串 ≥4 字 或 相似度 ≥0.5。

    比 gold_rank._lexical_match 宽：gold_rank 把「疏忽大意30%」当整串 keyword
    对「| 疏忽大意 | … | 30% |」失配，是系统性假阴性；SequenceMatcher 直接
    找「IT部和直属上级」这类重叠块，能匹配事实与文档的表述差异。
    """
    cl = content.lower()
    nums = {m.group().replace(",", "") for m in _PURE_NUM.finditer(fact)}
    if nums:
        num_ok = sum(1 for n in nums
                     if re.search(rf"(?<!\d){re.escape(n)}(?!\d)", cl)) >= 1
        if not num_ok:
            return False
    f_clean = _PURE_NUM.sub("", fact.lower()).strip()
    if not f_clean:
        return True
    sm = difflib.SequenceMatcher(None, f_clean, cl)
    longest = max((m.size for m in sm.get_matching_blocks()), default=0)
    return longest >= 4 or sm.ratio() >= 0.5


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunks", default=str(PROJ / "data" / "chunks" / "eval_admin"))
    ap.add_argument("--sparse", default=str(PROJ / "data" / "eval_admin_ws" / "m5_sparse.json"))
    ap.add_argument("--testset", default=str(PROJ / "tests" / "testsets" / "testset_admin_30.json"))
    ap.add_argument("--workspace", default="eval_admin_ws")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--k", type=int, default=3)
    args = ap.parse_args()

    from app.m9_eval.runner import _build_deps
    from app.m5_retrieve.retriever import retrieve

    rag, sparse, _ = await _build_deps(args.workspace)
    chunks = sparse["chunks"]
    md5_to_unit = _load_units(Path(args.chunks))
    by_doc = _docs_ordered(md5_to_unit)
    testset = json.load(open(args.testset, encoding="utf-8"))
    questions = [q for q in testset["questions"] if q["category"] != "unanswerable"]
    if args.limit:
        questions = questions[: args.limit]

    rows = []
    for i, q in enumerate(questions, 1):
        qid = q["id"]
        facts = q.get("key_facts", [])
        print(f"[{i}/{len(questions)}] {qid} ({q['category']})", flush=True)
        retr = await retrieve(rag, q["question"], sparse)
        fused_ids = [f["chunk_id"] for f in retr["fusion"]["fused_top40"]]

        def fact_carriers(cids) -> set[str]:
            got = set()
            for cid in cids:
                for fi, f in enumerate(facts):
                    if _match_fact(f, chunks[cid]["content"]):
                        got.add(fi)
            return got

        base_facts = fact_carriers(fused_ids)

        if qid == "adm_q008":
            doc = "7878fe49cd0ee82b"
            order = {md5_to_unit[c]["text_unit_id"]: i + 1 for i, c in enumerate(fused_ids)
                     if c in md5_to_unit}
            for n in (27, 28, 29, 30, 31, 32, 33):
                tid = f"{doc}-chunk-{n:03d}"
                print(f"  [q008] {tid} fused排名={order.get(tid, '池外')}")

        # 展开命中块：children（纯标题）+ neighbor（±k），去重，去掉已在池的
        new_cids: set[str] = set()
        for cid in fused_ids:
            u = md5_to_unit.get(cid)
            if u is None:
                continue
            if _is_heading_only(u):
                for kid in _chapter_children(by_doc, u):
                    new_cids.add(make_custom_chunk_id(kid["full_doc_id"],
                                                       sanitize_text_for_encoding(kid["content"])))
            for nb in _neighbors(by_doc, u, args.k):
                new_cids.add(make_custom_chunk_id(nb["full_doc_id"],
                                                   sanitize_text_for_encoding(nb["content"])))
        new_cids -= set(fused_ids)
        new_cids = {c for c in new_cids if c in chunks}

        new_facts = fact_carriers(new_cids) - base_facts
        rescued_facts = sorted(new_facts)
        total_facts = len(facts)
        new_carrier_ids = [c for c in new_cids if fact_carriers({c})]  # 单个判定太慢，下面批量
        rows.append({
            "qid": qid,
            "category": q["category"],
            "fused40_blocks": len(fused_ids),
            "heading_in_pool": sum(1 for c in fused_ids
                                   if _is_heading_only(md5_to_unit.get(c, {}))),
            "base_facts": len(base_facts),
            "new_blocks": len(new_cids),
            "rescued_facts": len(rescued_facts),
            "rescued_which": rescued_facts,
            "total_facts": total_facts,
        })

    print("\n=== 汇总 ===")
    print("fused40 覆盖 facts | 池内标题块(children触发点) | 展开新增块 | 新救回 facts")
    for r in rows:
        flag = "  <<< q008" if r["qid"] == "adm_q008" else ""
        print(f"{r['qid']:<11} base {r['base_facts']}/{r['total_facts']} "
              f"标题{r['heading_in_pool']} new {r['new_blocks']:>3}块 "
              f"救回 {r['rescued_which']}{flag}")

    # 汇总判定
    q008 = next((r for r in rows if r["qid"] == "adm_q008"), None)
    if q008:
        print(f"\nq008 摘要：fused40 覆盖 {q008['base_facts']}/{q008['total_facts']} 个 fact；"
              f"展开新增 {q008['new_blocks']} 块，救回 facts {q008['rescued_which']}")
    sibling_hits = [r for r in rows if r["rescued_facts"] and r["qid"] != "adm_q008"]
    print(f"neighbor/children 救回 ≥1 fact 的其它题：{[r['qid'] for r in sibling_hits]}")
    await rag.finalize_storages()


if __name__ == "__main__":
    asyncio.run(main())