#!/usr/bin/env python
"""离线校准：在固定检索上下文（top8_contexts_cservice35.json）上对比 matcher。

对每 fact 在固定 top8 contexts 上跑三态 matcher，输出：
1. 每 fact 三态命中矩阵 + NEW 命中块 rank/content 摘录
2. NEW vs STRICT / NEW vs ORIG diff（评测口径，非全库）
3. 汇总 found 计数

用法：PYTHONPATH=. python scripts/calib_goldrank.py
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PROJ = Path(__file__).resolve().parents[1]
CTX = PROJ / "scripts" / "top8_contexts_cservice35.json"

from scripts.probe_goldrank_regression import orig_match, strict_match  # noqa: E402
from app.m9_eval.metrics.gold_rank import _lexical_match  # noqa: E402

MATCHERS = {"ORIG": orig_match, "STRICT": strict_match, "NEW": _lexical_match}


def gold_rank_for(facts, contexts, mf):
    """返回 {fact: {"rank": 1-based 命中 rank or None, "cid": chunk_id}}。"""
    out = {}
    for fact in facts:
        found = None
        for c in contexts:
            if mf(fact, c["content"]):
                found = {"rank": c["rank"], "cid": c["chunk_id"], "content": c["content"]}
                break
        out[fact] = found
    return out


def main() -> None:
    payload = json.load(open(CTX, encoding="utf-8"))

    report = {}
    total_facts = 0
    for q in payload:
        if not q["key_facts"]:
            continue
        per_state = {}
        for state, mf in MATCHERS.items():
            per_state[state] = gold_rank_for(q["key_facts"], q["contexts"], mf)
        report[q["id"]] = {"category": q["category"], "per_state": per_state}
        total_facts += len(q["key_facts"])

    # 汇总 found
    from collections import Counter
    found = Counter()
    for qid, r in report.items():
        for state in MATCHERS:
            f = sum(1 for v in r["per_state"][state].values() if v)
            found[state] += f
    print(f"facts 总数: {total_facts}")
    for state in ("ORIG", "STRICT", "NEW"):
        print(f"  {state:6s} found: {found[state]}/{total_facts} = {found[state]/total_facts:.1%}")

    # diff per fact
    def diff(a, b):
        rows = []
        for qid, r in report.items():
            for fact in r["per_state"][a]:
                va = r["per_state"][a][fact]
                vb = r["per_state"][b][fact]
                if bool(va) != bool(vb):
                    rows.append((qid, fact, va, vb))
        return rows

    print("\n=== NEW vs STRICT ===")
    ns = diff("STRICT", "NEW")
    gained = [(t[0], t[1], t[3]) for t in ns if t[3]]
    lost = [(t[0], t[1], t[2]) for t in ns if t[2]]
    print(f"差异 {len(ns)}：NEW 找回 {len(gained)} / NEW 新增漏 {len(lost)}")
    for qid, fact, vb in gained:
        print(f"  + {qid} {fact[:44]}")
        if vb: print(f"      rank{vb['rank']} cid={vb['cid'][:16]}… {vb['content'][:60]!r}")
    for qid, fact, va in lost:
        print(f"  - {qid} {fact[:44]}")

    print("\n=== NEW vs ORIG ===")
    no = diff("ORIG", "NEW")
    gained2 = [(t[0], t[1], t[3]) for t in no if t[3]]
    lost2 = [(t[0], t[1], t[2]) for t in no if t[2]]
    print(f"差异 {len(no)}：NEW 找回 {len(gained2)} / NEW 新增漏 {len(lost2)}")

    # audit 关键 fact：命中 chunk 内容
    print("\n=== 命中块审计（NEW 全命中 fact 的 chunk content）===")
    for qid, r in report.items():
        for fact, v in r["per_state"]["NEW"].items():
            if v:
                print(f"[{qid}] {fact[:48]}  →  rank{v['rank']} {v['content'][:90]!r}")


if __name__ == "__main__":
    main()