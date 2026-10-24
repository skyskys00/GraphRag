"""v5.48 分箱冒烟：对 8 落题重生成，验证 <advice> 协议与朴素+数值核验评测。

流程（对齐 evaluate_agent 口径）：run_agent 重新生成 → 主体 answer / advice / citations
→ contexts 由 citations 反查 sidecar → compute_faithfulness（新朴素单层）打分。

用法：cd backend && python scripts/probe_agent_box.py
"""
from __future__ import annotations

import asyncio, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

QIDS = ["DV-FS-001", "DV-FS-002", "DV-IO-001", "DV-IO-004", "DV-FS-010", "DV-TN-008", "DV-FS-013", "DV-FS-018"]


async def main() -> None:
    report = json.loads(Path("tests/reports/agent/run_agent_device_50_20261023_fixed.json").read_text())
    from app.m7_interact.bootstrap import get_deps
    from app.m10_agent.agent import run_agent
    from app.m9_eval.metrics.faithfulness import compute_faithfulness
    deps = await get_deps("col_b7b876b1")
    print(f"boot deps col_b7b876b1\n", flush=True)

    rows = []
    for q in report["questions"]:
        if q["id"] not in QIDS:
            continue
        res = await run_agent(deps, q["question"], max_rounds=5)
        answer, advice = res["answer"], res.get("advice", "")
        # contexts 按 evaluate_agent 口径：citations → sidecar
        contexts = []
        seen = set()
        for c in res.get("citations", []):
            if not isinstance(c, dict):
                continue
            tid = c.get("text_unit_id") or c.get("chunk_id")
            doc_id = c.get("full_doc_id") or c.get("doc_id") or "?"
            key = (doc_id, str(tid))
            if not tid or key in seen:
                continue
            seen.add(key)
            meta = deps.sidecar.by_text_unit(tid) if getattr(deps, "sidecar", None) else None
            contexts.append({
                "chunk_id": str(tid), "content": meta.content if meta else c.get("content") or c.get("snippet") or "",
                "full_doc_id": doc_id,
            })
        fai = await compute_faithfulness(deps.query_func, q["question"], answer, contexts)
        rows.append({
            "id": q["id"], "old": q.get("metrics", {}).get("faithfulness"),
            "new": fai["score"], "answer": answer, "advice": advice,
            "citations": len(contexts), "rescued": fai.get("numeric_rescued", []),
            "penalized": [u.get("statement", "") for u in fai.get("unsupported", [])],
            "reason": fai["reason"],
        })
        print(f"\n===== {q['id']} | 旧={q.get('metrics', {}).get('faithfulness')} → 新={fai['score']:.3f} | ctx={len(contexts)} =====", flush=True)
        print(f"主体: {answer[:110]}", flush=True)
        print(f"建议: {advice[:110] if advice else '(空)'}", flush=True)
        for u in fai.get("unsupported", []):
            print(f"  [罚] {u.get('statement','')[:100]}", flush=True)

    Path("tests/reports/agent/box_probe_v548.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2))
    print("\nsaved → tests/reports/agent/box_probe_v548.json")
    await deps.dispose()


asyncio.run(main())