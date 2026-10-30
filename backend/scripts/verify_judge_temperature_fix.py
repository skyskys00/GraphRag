"""验证 judge 温度修复是否收敛评测方差：用两份历史报告的固定答案重判 citation_accuracy，
比较修复前后「同一答案的判分复现性」与「两轮之间的差距」。

修复后（judge t=0 + cache key 带温度前缀）不会命中旧缓存，全部重判。
用法：python -m scripts.verify_judge_temperature_fix
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.m7_interact.bootstrap import get_deps
from app.m9_eval.metrics.citation_accuracy import compute_citation_accuracy

REPORTS = {
    "v6.4.1": "tests/reports/agent/run_agent_admin_30_v641_temp.json",
    "v6.5": "tests/reports/agent/run_agent_admin_30_v65_plan.json",
}


def _marker_ctx(q: dict, sidecar) -> dict[int, str]:
    out: dict[int, str] = {}
    for c in q.get("citations") or []:
        tid = c.get("text_unit_id") or c.get("chunk_id")
        meta = sidecar.by_text_unit(tid) if (tid and sidecar) else None
        content = meta.content if meta else (c.get("content") or c.get("snippet") or "")
        m = c.get("marker")
        if isinstance(m, int) and m > 0 and content:
            out[m] = content
    return out


async def score_once(q: dict, deps) -> float:
    ca = await compute_citation_accuracy(
        deps.query_func, q["question"], q.get("answer") or "", [],
        q.get("citations") or [], ctx_by_marker=_marker_ctx(q, deps.sidecar),
    )
    return ca["score"]


async def main() -> None:
    deps = await get_deps("eval_admin")
    data = {
        tag: {q["id"]: q for q in json.load(open(p))["questions"]}
        for tag, p in REPORTS.items()
    }
    print(f"{'题号':10s} {'v6.4.1':>8s} {'v6.5':>8s} {'Δ':>8s}")
    sums = {tag: [] for tag in REPORTS}
    deltas = []
    for qid in data["v6.4.1"]:
        row = {}
        for tag in REPORTS:
            row[tag] = await score_once(data[tag][qid], deps)
            sums[tag].append(row[tag])
        d = row["v6.5"] - row["v6.4.1"]
        deltas.append(abs(d))
        print(f"{qid:10s} {row['v6.4.1']:>8.3f} {row['v6.5']:>8.3f} {d:>+8.3f}")
    for tag in REPORTS:
        print(f"{tag} 均值: {sum(sums[tag]) / len(sums[tag]):.4f}")
    print(f"逐题 |Δ| 均值: {sum(deltas) / len(deltas):.4f}  最大: {max(deltas):.3f}")


if __name__ == "__main__":
    asyncio.run(main())
