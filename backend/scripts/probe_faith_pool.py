"""P2-cite 探底：固定 v5.48 的 8 落题答案，对比「被引窄池 vs 重建宽池」faithfulness。

目的：隔离「评测口径」单变量（答案不变），验证 A2 的宽池修正到底能救回多少——
宽池救"跨块综合但池里有的"句，救不回"型号/元陈述只在块外元信息"的句。

用法：cd backend && python scripts/probe_faith_pool.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

QIDS = ["DV-FS-001", "DV-FS-002", "DV-IO-001", "DV-IO-004", "DV-FS-010", "DV-TN-008", "DV-FS-013", "DV-FS-018"]


async def main() -> None:
    report = json.loads(Path("tests/reports/agent/run_agent_device_50_v548.json").read_text())
    from app.m7_interact.bootstrap import get_deps
    from app.m9_eval.runner import _rebuild_retrieval_pool
    from app.m9_eval.metrics.faithfulness import compute_faithfulness
    deps = await get_deps("col_b7b876b1")

    rows = []
    for q in report["questions"]:
        if q["id"] not in QIDS:
            continue
        answer = q.get("answer", "")
        tool_chain = q.get("tool_chain", [])
        # 被引窄池：从 report 的 citations 反查（与 evaluate_agent 原口径一致）
        narrow = []
        seen = set()
        for c in q.get("citations", []):
            if not isinstance(c, dict):
                continue
            tid = c.get("text_unit_id") or c.get("chunk_id")
            doc_id = c.get("full_doc_id") or c.get("doc_id") or "?"
            if not tid or (doc_id, str(tid)) in seen:
                continue
            seen.add((doc_id, str(tid)))
            meta = deps.sidecar.by_text_unit(tid)
            narrow.append({"chunk_id": str(tid), "content": meta.content if meta else c.get("content") or "",
                           "full_doc_id": doc_id})
        wide = await _rebuild_retrieval_pool(tool_chain, deps, fallback=narrow)
        wide_plain = [{"title": "", **b} for b in wide]  # 隔离 title 贡献的对照端
        f_narrow = await compute_faithfulness(deps.query_func, q["question"], answer, narrow)
        f_wide_plain = await compute_faithfulness(deps.query_func, q["question"], answer, wide_plain)
        f_wide = await compute_faithfulness(deps.query_func, q["question"], answer, wide)
        old = (q.get("metrics") or {}).get("faithfulness")
        rows.append({
            "id": q["id"], "old_v548": old,
            "narrow": f_narrow["score"], "wide_plain": f_wide_plain["score"], "wide_title": f_wide["score"],
            "delta": round(f_wide_plain["score"] - f_narrow["score"], 3),
            "title_gain": round(f_wide["score"] - f_wide_plain["score"], 3),
            "n_blocks": len(narrow), "n_wpool": len(wide),
            "wide_unsup": [u.get("statement", "")[:60] for u in f_wide.get("unsupported", [])],
        })
        print(f"\n===== {q['id']} | v548窄={old:.3f} 窄判={f_narrow['score']:.3f} "
              f"宽判={f_wide_plain['score']:.3f} → 宽+标题={f_wide['score']:.3f}"
              f" | 块={len(narrow)}/{len(wide)} =====", flush=True)
        for u in f_wide.get("unsupported", []):
            print(f"  [宽+标题罚] {u.get('statement','')[:80]}", flush=True)
        for u in f_wide.get("unsupported", []):
            print(f"  [宽池罚] {u.get('statement','')[:80]}", flush=True)

    Path("tests/reports/agent/pool_probe_p2cite.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2))
    avg_old = sum(r["old_v548"] for r in rows) / len(rows)
    avg_narrow = sum(r["narrow"] for r in rows) / len(rows)
    avg_plain = sum(r["wide_plain"] for r in rows) / len(rows)
    avg_title = sum(r["wide_title"] for r in rows) / len(rows)
    print(f"\n平均: v548={avg_old:.3f} 窄判={avg_narrow:.3f} 宽判={avg_plain:.3f} 宽+标题={avg_title:.3f}"
          f" (宽-窄=+{avg_plain - avg_narrow:.3f}, title=+{avg_title - avg_plain:.3f})")
    await deps.dispose()


asyncio.run(main())