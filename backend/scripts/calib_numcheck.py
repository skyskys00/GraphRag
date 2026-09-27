#!/usr/bin/env python
"""数字弱锚专项验证：对 CS-FC-005/AHT、CS-SM-001、CS-TN-004、CS-SM-002 的关键 fact
打印每个候选块的数字边界命中明细 + 文本公共块，确认真伪。
"""
from __future__ import annotations

import difflib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CTX = Path(__file__).resolve().parents[1] / "scripts" / "top8_contexts_cservice35.json"
from app.m9_eval.metrics.gold_rank import (_lexical_match, _norm, _num_in_content,
                                           _extract_number_tokens, _SEP)  # noqa: E402

WATCH = {
    "CS-FC-005": ["Q3 整体 AHT = 2.4 小时（目标三级投诉 ≤ 2 小时）"],
    "CS-SM-001": ["问题 1：解决率距 85% 目标差 3.8pp"],
    "CS-TN-004": ["合计扣分 = 20 + 10 = 30 分", "剩余 = 70 分（及格以上）"],
    "CS-SM-002": ["技术趋势 2：多模态（语音成熟、图片早期）"],
}


def num_detail(fact, content) -> dict:
    nums = _extract_number_tokens(fact)
    d = {"nums": nums}
    d["hits"] = {n: _num_in_content(n, content) for n in nums}
    return d


def main() -> None:
    payload = json.load(open(CTX, encoding="utf-8"))
    for q in payload:
        if q["id"] not in WATCH:
            continue
        for fact in WATCH[q["id"]]:
            print("=" * 100)
            f_norm = _norm(fact)
            fn = _extract_number_tokens(fact)
            print(f"FACT: {fact}  nums={fn} f_norm={f_norm!r}")
            for c in q["contexts"]:
                hit = _lexical_match(fact, c["content"])
                if not hit:
                    continue
                nd = num_detail(fact, c["content"])
                chunk_norm = _norm(c["content"])
                sm = difflib.SequenceMatcher(None, f_norm, chunk_norm, autojunk=False)
                blocks = sorted([m.size for m in sm.get_matching_blocks() if m.size >= 2], reverse=True)
                cum = sum(blocks)
                ratio = sm.ratio()
                need = max(2, min(4, len(f_norm))) if fn else max(4, len(f_norm) // 2)
                print(f"\n  rank{c['rank']} cid={c['chunk_id']}")
                print(f"    数字命中: {nd['hits']}  公共块sizes={blocks} cum={cum} ratio={ratio:.2f} need={need}")
                print(f"    content: {c['content'][:220]!r}")


if __name__ == "__main__":
    main()