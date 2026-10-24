"""v5.46.1 8 回落题用新双轨口径重判定（faithfulness 新 prompt + kind 分类 + 图块感知）"""
from __future__ import annotations
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
    rows = []
    for q in report["questions"]:
        if q["id"] not in QIDS: continue
        ctx = {}
        for c in q["tool_chain"]:
            a = json.loads(c.get("args") or "{}")
            if c.get("tool") != "tool_retrieve" or not a.get("query"): continue
            tk = max(1, int(a.get("top_k") or 5))
            try:
                res = await retrieve(deps.rag, a["query"], deps.sparse, entities=deps.entities,
                                    exclude_docs=sorted(deps.excluded_docs),
                                    allowed_docs=sorted(deps.allowed_docs) if deps.allowed_docs else None)
            except Exception as e:
                print(f"[{q['id']}] rebuild 失败 {a['query']}: {e}", flush=True); continue
            for r in res.get("results", [])[:tk]:
                cid = str(r["chunk_id"])
                if cid in ctx: continue
                meta = deps.sidecar.resolve(cid) if deps.sidecar else None
                ctx[cid] = meta.content if meta else r.get("content", "")
        ctx_list = [{"chunk_id": k, "content": v} for k, v in ctx.items()]
        old = q["metrics"]["faithfulness"]
        fai = await compute_faithfulness(deps.query_func, q["question"], q["answer"], ctx_list)
        rows.append((q["id"], old, fai["score"], len(ctx_list),
                     fai["kind_counts"], fai.get("enhance_count", 0)))
        nf = fai["score"]
        flag = "↑" if nf > old + 0.02 else ("↓" if nf < old - 0.02 else "=")
        print(f"{q['id']}: 旧(评测)={old:.3f} → 新宽松={nf:.3f} {flag} | pool={len(ctx_list)} | "
              f"kind={fai['kind_counts']} enhance={fai.get('enhance_count',0)}", flush=True)
        for u in fai.get("unsupported") or []:
            print(f"    [罚-{u.get('kind')}] {u.get('statement','')[:80]}", flush=True)
        for e in fai.get("enhanced") or []:
            print(f"    [+enhance] {e.get('statement','')[:80]}", flush=True)
    print("\n=== 汇总 ===")
    ups=[r for r in rows if r[2]-r[1]>0.02]; downs=[r for r in rows if r[2]-r[1]<-0.02]; flats=[r for r in rows if abs(r[2]-r[1])<=0.02]
    print(f"回升 {len(ups)} / 持平 {len(flats)} / 回落 {len(downs)}")
    await deps.dispose()

asyncio.run(main())
