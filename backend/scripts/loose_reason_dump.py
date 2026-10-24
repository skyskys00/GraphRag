"""8 回落题宽松池归因：逐个拿 judge 的 unsupported reason（缓存在则零 LLM 成本）。"""
import asyncio, json
from pathlib import Path
import sys
sys.path.insert(0, '.')

QIDS = ["DV-FS-001","DV-FS-002","DV-IO-001","DV-IO-004","DV-FS-010","DV-TN-008","DV-FS-013","DV-FS-018"]

async def main():
    from app.m7_interact.bootstrap import get_deps
    from app.m5_retrieve.retriever import retrieve
    from app.m9_eval.metrics.faithfulness import compute_faithfulness
    deps = await get_deps("col_b7b876b1")
    report = json.loads(Path("tests/reports/agent/run_agent_device_50_20261023_fixed.json").read_text())
    for q in report["questions"]:
        if q["id"] not in QIDS:
            continue
        ctx_map = {}
        for c in q["tool_chain"]:
            a = json.loads(c.get("args") or "{}")
            if c.get("tool") != "tool_retrieve" or not a.get("query"):
                continue
            tk = max(1, int(a.get("top_k") or 5))
            res = await retrieve(deps.rag, a["query"], deps.sparse, entities=deps.entities,
                                exclude_docs=sorted(deps.excluded_docs),
                                allowed_docs=sorted(deps.allowed_docs) if deps.allowed_docs else None)
            for r in res.get("results", [])[:tk]:
                cid = str(r["chunk_id"])
                if cid in ctx_map:
                    continue
                meta = deps.sidecar.resolve(cid) if deps.sidecar else None
                ctx_map[cid] = meta.content if meta else r.get("content", "")
        ctx_list = [{"chunk_id": k, "content": v} for k, v in ctx_map.items()]
        strict = q["metrics"]["faithfulness"]
        print(f"\n===== {q['id']} | strict={strict} | loose_pool={len(ctx_list)}chunks")
        print(f"QUESTION: {q['question'][:80]}")
        fai = await compute_faithfulness(deps.query_func, q["question"], q["answer"], ctx_list)
        print(f"LOOSE: score={fai['score']:.3f} total={fai.get('total_statements')} su={fai.get('supported_statements')}")
        print(f"  reason: {fai.get('reason','')}")
        for u in fai.get("unsupported", []):
            print(f"  [不依] {u.get('statement','')[:140]}")
            print(f"         {u.get('reason','')[:260]}")
    await deps.dispose()

asyncio.run(main())