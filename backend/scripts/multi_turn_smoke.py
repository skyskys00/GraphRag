"""多轮结构冒烟（v6.3.1）：每轮必检索、引用非空、正文角标 ⊆ 当轮引用清单。

用法（backend 目录）：
  python scripts/multi_turn_smoke.py                 # 全量
  python scripts/multi_turn_smoke.py --limit 2       # 先跑 2 组
输出：tests/reports/agent/multi_turn_<tag>.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))  # 直接以 python scripts/... 运行时补上 backend 路径
_NUM = re.compile(r"\[(\d+)\]")


def _turn_check(result: dict) -> dict:
    spans = result.get("spans") or []
    tool_calls = sum(1 for s in spans if s.get("node") == "execute_tools")
    citations = result.get("citations") or []
    markers = {int(m) for m in _NUM.findall(result.get("answer", ""))}
    resolved = {c.get("marker") for c in citations}
    return {
        "tool_calls": tool_calls,
        "citations": len(citations),
        "markers": sorted(markers),
        "markers_subset": markers <= resolved,
        "tokens_in": sum(s.get("tokens_in") or 0 for s in spans),
        "ok": tool_calls >= 1 and bool(markers) and markers <= resolved,
    }


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--testset", default="tests/testsets/testset_admin_multi_10.json")
    ap.add_argument("--collection", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--tag", default="v631")
    args = ap.parse_args()

    from app.m3_index.runner import _load_dotenv
    _load_dotenv(PROJ / ".env")

    from app.m7_interact.bootstrap import get_deps
    from app.m10_agent.agent import run_agent

    ts = json.loads((PROJ / args.testset).read_text(encoding="utf-8"))
    items = ts["items"][: args.limit or None]
    collection = args.collection or ts.get("collection", "default")
    print(f"多轮结构冒烟：{ts['testset_id']}｜{len(items)} 组｜collection={collection}", flush=True)
    deps = await get_deps(collection)

    reports = []
    for item in items:
        history: list[dict] = []
        prior: list[dict] = []
        turns = []
        for i, q in enumerate((item["turn1"], item["turn2"]), 1):
            t0 = time.time()
            r = await run_agent(deps, q, history=history, prior_citations=prior)
            chk = _turn_check(r)
            chk.update({"turn": i, "query": q, "duration_s": round(time.time() - t0, 1),
                        "answer": r.get("answer", "")[:160]})
            turns.append(chk)
            history += [{"role": "user", "content": q},
                        {"role": "assistant", "content": r.get("answer", "")}]
            prior = r.get("citations") or []
            print(f"  {item['id']} turn{i}: {chk['duration_s']}s tool={chk['tool_calls']} "
                  f"cit={chk['citations']} markers={chk['markers']} ok={chk['ok']}", flush=True)
        reports.append({"id": item["id"], "turns": turns, "ok": all(t["ok"] for t in turns)})

    passed = sum(1 for r in reports if r["ok"])
    summary = {
        "testset": ts["testset_id"],
        "collection": collection,
        "n_items": len(reports),
        "passed": passed,
        "pass_rate": round(passed / len(reports), 4) if reports else 0.0,
        "avg_duration_s": round(
            sum(t["duration_s"] for r in reports for t in r["turns"]) / max(1, 2 * len(reports)), 1),
        "reports": reports,
    }
    out = PROJ / "tests" / "reports" / "agent" / f"multi_turn_{args.tag}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n通过 {passed}/{len(reports)}｜均耗时 {summary['avg_duration_s']}s/轮｜"
          f"报告 {out.relative_to(PROJ)}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
