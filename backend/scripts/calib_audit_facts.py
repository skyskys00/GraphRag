#!/usr/bin/env python
"""关键 fact 完整命中块审计：NEW vs STRICT 找回的存疑 fact，打印命中块全文。

用于人工判定「真恢复（正确块）」vs「伪命中（无关块）」，指导 _lexical_match 定稿。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CTX = Path(__file__).resolve().parents[1] / "scripts" / "top8_contexts_cservice35.json"
PROJ = Path(__file__).resolve().parents[1]

from scripts.probe_goldrank_regression import orig_match, strict_match   # noqa: E402
from app.m9_eval.metrics.gold_rank import _lexical_match, _num_in_content, _extract_number_tokens, _norm  # noqa: E402

MATCHERS = {"ORIG": orig_match, "STRICT": strict_match, "NEW": _lexical_match}

FACTS_WATCH = [
    "锁定时长 = 30 分钟",
    "连续输错次数 = 5 次",
    "PRD 中目标值为 70%",
    "SOP：三级投诉的标准回答工具",
    "话术规范：自动回复话术训练校准的依据",
    "投诉处理最终负责人（一级投诉抄送她）",
    "SOP V2.0/V2.3 修订人",
    "首问解决率 = FCR = 首次联系解决率（同一概念）",
    "衡量客户服务体验的核心指标",
    "v1.0 无指标目标记录 → 核心指标目标无法 v1.0 vs v2.1 比较",
    "问题 1：解决率距 85% 目标差 3.8pp",
    "技术趋势 2：多模态（语音成熟、图片早期）",
    "响应速度：智能秒级 vs 人工需排队",
    "含义三：AHT 平均处理时长 = 工单打开到关闭平均耗时",
    "Q3 整体 AHT = 2.4 小时（目标三级投诉 ≤ 2 小时）",
    "行业水平：40% → 70%+（LLM 驱动）",
]


def main() -> None:
    payload = json.load(open(CTX, encoding="utf-8"))
    for q in payload:
        if q["category"] == "unanswerable":
            continue
        for fact in q["key_facts"]:
            if fact not in FACTS_WATCH:
                continue
            print("=" * 100)
            fn = _extract_number_tokens(fact)
            f_norm = _norm(fact)
            print(f"FACT: {fact}  [nums={fn} len_norm={len(f_norm)}]")
            print(f"  f_norm: {f_norm!r}")
            # 三态在该题 contexts 上的命中 rank
            for state, mf in MATCHERS.items():
                hit = next((c for c in q["contexts"] if mf(fact, c["content"])), None)
                print(f"  [{state}] {'rank' + str(hit['rank']) if hit else '未命中'}")
            # 打印 NEW 命中块全文 + 数字边界细节
            for idx, c in enumerate(q["contexts"]):
                if _lexical_match(fact, c["content"]):
                    cid = c["chunk_id"]
                    print(f"\n  --- NEW 命中 rank{c['rank']} cid={cid} ---")
                    print(f"      content 全文：")
                    print(c["content"])


if __name__ == "__main__":
    main()