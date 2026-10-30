"""定位 citation 裁判方差来源：用真实判定 prompt，t=0 重复跑 N 次，
输出单条引用的 score 序列（不是聚合值），看是「全线漂移」还是「阈值附近抖动」。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openai import AsyncOpenAI

from app.m9_eval.metrics.citation_accuracy import (
    PROMPT_TEMPLATE,
    SYSTEM_PROMPT,
    extract_cited_statements,
)
from app.m9_eval.judge import _parse_judge_output

MODEL = os.environ.get("QUERY_MODEL") or "deepseek-v4-flash"
REPORT = Path("tests/reports/agent/run_agent_admin_30_v65_plan.json")
PICKS = ["adm_q010", "adm_q013"]
REPEAT = 5
TEMPS = [1.0, 0.0]


async def main() -> None:
    client = AsyncOpenAI(
        api_key=os.environ["DEEPSEEK_API_KEY"],
        base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
    )
    data = {q["id"]: q for q in json.load(open(REPORT))["questions"]}

    for qid in PICKS:
        q = data[qid]
        marker_ctx = {c["marker"]: c.get("snippet", "") for c in q.get("citations", []) if c.get("marker")}
        cited = extract_cited_statements(q.get("answer") or "")
        print(f"\n=== {qid} 共 {len(cited)} 条引用")
        for temp in TEMPS:
            print(f"  --- temperature={temp}")
            agg_all = []
            for idx, item in enumerate(cited, 1):
                num, stmt = item["cite_num"], item["statement"]
                ctx = marker_ctx.get(num, "")
                if not ctx:
                    print(f"  [{num}] 无上下文")
                    continue
                prompt = PROMPT_TEMPLATE.format(
                    question=q["question"], cite_num=num,
                    cite_context=ctx, statement_with_citation=stmt + f"[{num}]",
                )
                scores, raws = [], []
                for _ in range(REPEAT):
                    r = await client.chat.completions.create(
                        model=MODEL,
                        messages=[
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": prompt},
                        ],
                        temperature=temp,
                        max_tokens=500,
                        extra_body={"thinking": {"type": "disabled"}},
                    )
                    raw = r.choices[0].message.content or ""
                    raws.append(raw.strip()[:40])
                    scores.append(_parse_judge_output(raw)["score"])
                agg_all.append(scores)
                votes = sum(1 for s in scores if s >= 0.5)
                mark = "稳定" if len(set(scores)) == 1 else "漂移"
                print(f"  [{num}] {stmt[:26]}… → {['%.2f' % s for s in scores]}  {mark} 通过 {votes}/{REPEAT}")
                if len(set(raws)) > 1:
                    for u in list(dict.fromkeys(raws))[:2]:
                        print(f"        raw: {u}")
            if agg_all:
                per_run = [sum(col) / len(col) for col in zip(*agg_all)]
                print(f"  整题分数按轮: {['%.2f' % x for x in per_run]}  极差 {max(per_run) - min(per_run):.2f}")


if __name__ == "__main__":
    asyncio.run(main())
