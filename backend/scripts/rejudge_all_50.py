"""50 题全量按新双轨口径重判 faithfulness（宽松池重建上下文 + fact/meta/enhance 三轨）。

口径（v5.47 用户拍板）：
  只罚 fact（含数值/单位/参数/规格/型号/剂量/禁止事项，强制归 fact）；
  meta 单报不计分（含由原文可推出的排除/推断结论，锚点须有原文；归源错误仍归 fact）；
  enhance 单独计知识增强分。

用法：
  cd backend && python scripts/rejudge_all_50.py \
      --report tests/reports/agent/run_agent_device_50_20261023_fixed.json \
      --out tests/reports/agent/rejudge_all_v547.json
"""
from __future__ import annotations

import argparse, asyncio, json, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.m5_retrieve.retriever import retrieve
from app.m9_eval.metrics.faithfulness import compute_faithfulness


def _chain_args(q: dict) -> list[tuple[str, dict]]:
    out = []
    for c in q.get("tool_chain", []):
        a = c.get("args") or "{}"
        try:
            a = json.loads(a) if isinstance(a, str) else a
        except Exception:
            a = {}
        out.append((c.get("tool", ""), a))
    return out


async def main(report_path: str, out_path: str) -> None:
    report = json.loads(Path(report_path).read_text())
    cfg = report.get("config", {})
    ws = cfg.get("workspace") or "col_b7b876b1"
    from app.m7_interact.bootstrap import get_deps
    deps = await get_deps(ws)
    print(f"boot deps {ws}\n", flush=True)

    results = []
    t0 = time.time()
    for idx, q in enumerate(report["questions"]):
        qid = q["id"]
        ctx: dict[str, str] = {}
        for tool, a in _chain_args(q):
            if tool != "tool_retrieve" or not a.get("query"):
                continue
            tk = max(1, int(a.get("top_k") or 5))
            try:
                res = await retrieve(
                    deps.rag, a["query"], deps.sparse,
                    entities=deps.entities,
                    exclude_docs=sorted(deps.excluded_docs),
                    allowed_docs=sorted(deps.allowed_docs) if deps.allowed_docs else None,
                )
            except Exception as e:
                print(f"[{qid}] rebuild 失败 {a['query'][:40]}: {e}", flush=True)
                continue
            for r in res.get("results", [])[:tk]:
                cid = str(r.get("chunk_id", ""))
                if cid and cid not in ctx:
                    meta = deps.sidecar.resolve(cid) if deps.sidecar else None
                    ctx[cid] = meta.content if meta else r.get("content", "")
        ctx_list = [{"chunk_id": k, "content": v} for k, v in ctx.items()]
        old = q.get("metrics", {}).get("faithfulness")
        fai = await compute_faithfulness(deps.query_func, q.get("question", ""), q.get("answer", ""), ctx_list)
        results.append({
            "id": qid,
            "old_faith": old,
            "new_faith": fai["score"],
            "pool": len(ctx_list),
            "total": fai.get("total_statements"),
            "supported": fai.get("supported_statements"),
            "kind_counts": fai.get("kind_counts"),
            "enhance_count": fai.get("enhance_count", 0),
            "meta_count": (fai.get("meta_report") or {}).get("count", 0),
            "penalized": [
                {"statement": u.get("statement", ""), "kind": u.get("kind", "")}
                for u in fai.get("unsupported") or []
            ],
            "reason": fai.get("reason", ""),
        })
        flag = "↑" if (fai["score"] - (old or 0)) > 0.02 else ("↓" if (fai["score"] - (old or 0)) < -0.02 else "=")
        print(f"[{idx+1}/{len(report['questions'])}] {qid}: 旧={old} → 新={fai['score']:.3f} {flag} | "
              f"pool={len(ctx_list)} kind={fai.get('kind_counts')}", flush=True)
        for u in fai.get("unsupported") or []:
            print(f"      [罚-{u.get('kind')}] {u.get('statement', '')[:80]}", flush=True)

    ups = [r for r in results if r["new_faith"] - (r["old_faith"] or 0) > 0.02]
    downs = [r for r in results if r["new_faith"] - (r["old_faith"] or 0) < -0.02]
    flats = [r for r in results if abs(r["new_faith"] - (r["old_faith"] or 0)) <= 0.02]
    up_avg = sum(r["new_faith"] - (r["old_faith"] or 0) for r in ups) / len(ups) if ups else 0.0
    down_avg = sum((r["old_faith"] or 0) - r["new_faith"] for r in downs) / len(downs) if downs else 0.0
    mean_new = sum(r["new_faith"] for r in results) / len(results)
    mean_old = sum(r["old_faith"] or 0 for r in results) / len(results)
    summmary = {
        "n_total": len(results),
        "up": len(ups), "flat": len(flats), "down": len(downs),
        "up_avg_delta": round(up_avg, 4),
        "down_avg_delta": round(down_avg, 4),
        "mean_old": round(mean_old, 4),
        "mean_new": round(mean_new, 4),
        "elapsed_sec": round(time.time() - t0, 1),
    }
    print("\n=== 汇总 ===\n" + json.dumps(summmary, ensure_ascii=False, indent=2))
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps({
        "config": {"workspace": ws, "prompt_version": "v5.47 双轨(meta含推断)"},
        "summary": summmary,
        "items": results,
    }, ensure_ascii=False, indent=2))
    print(f"\nsaved → {out_path}")
    await deps.dispose()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", default="tests/reports/agent/run_agent_device_50_20261023_fixed.json")
    ap.add_argument("--out", default="tests/reports/agent/rejudge_all_v547.json")
    a = ap.parse_args()
    asyncio.run(main(a.report, a.out))