"""M10 agent 评测修正：只重判 citation_accuracy（marker→上下文精确对齐）。

背景：初版 evaluate_agent 把 agent 的 citations 按 list order 传给
compute_citation_accuracy，误用了 M6 直连的「marker n = contexts[n-1]」约定，
导致 agent 的 [n] 生成阶段编号 与 上下文错位 → citation_accuracy 虚低（0.612 vs
真实 ~0.8+）。修复后 evaluate_agent 传显式 ctx_by_marker，本脚本从已存报告
重建 contexts/marker_ctx（sidecar 反查完整内容），只重判 citation_accuracy，
不重跑 agent，再合成修正报告。

用法:
  python scripts/recompute_agent_citation.py \
      --report tests/reports/agent/run_agent_device_50_20261023.json \
      --out tests/reports/agent/run_agent_device_50_20261023_fixed.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))

from app.m9_eval.report import build_report, save_report
from app.m9_eval.metrics.citation_accuracy import compute_citation_accuracy


async def _fix_question(deps: Any, q: dict[str, Any]) -> None:
    """重建 agent contexts + marker_ctx（同 evaluate_agent），重判 citation_accuracy。"""
    answer = q.get("answer", "")
    citations = q.get("citations", [])

    contexts: list[dict[str, Any]] = []
    marker_ctx: dict[int, str] = {}
    seen: set[tuple[str, str]] = set()
    for c in citations:
        if not isinstance(c, dict):
            continue
        tid = c.get("text_unit_id") or c.get("chunk_id")
        doc_id = c.get("full_doc_id") or c.get("doc_id") or "?"
        key = (doc_id, str(tid))
        if not tid or key in seen:
            continue
        seen.add(key)
        meta = deps.sidecar.by_text_unit(tid) if getattr(deps, "sidecar", None) else None
        content = (meta.content if meta else c.get("content") or c.get("snippet") or "")
        # 与 evaluate_agent 保持一致：content 逻辑同 runner 当前版本
        contexts.append({
            "chunk_id": str(tid),
            "content": content,
            "full_doc_id": doc_id,
            "rank": len(contexts) + 1,
            "score": c.get("score", 0.0),
        })
        marker = c.get("marker")
        if isinstance(marker, int) and marker > 0 and content:
            marker_ctx[marker] = content

    ca = await compute_citation_accuracy(
        deps.query_func, q.get("question", ""), answer, contexts, citations,
        ctx_by_marker=marker_ctx,
    )
    q["metrics"]["citation_accuracy"] = ca["score"]
    q["metrics"]["citation_accuracy_detail"] = {
        "total_cites": ca.get("total_cites", 0),
        "supported_cites": ca.get("supported_cites", 0),
        "details": ca.get("details", []),
        "reason": ca["reason"],
    }


async def main(args: argparse.Namespace) -> None:
    report = json.loads(args.report.read_text(encoding="utf-8"))
    collection = report.get("config", {}).get("collection") or "col_b7b876b1"

    from app.m7_interact.bootstrap import get_deps
    print(f"boot deps for collection={collection} ...", flush=True)
    deps = await get_deps(collection)
    print("ready\n", flush=True)

    total = len(report["questions"])
    for i, q in enumerate(report["questions"]):
        qid = q["id"]
        old = q["metrics"].get("citation_accuracy")
        await _fix_question(deps, q)
        new = q["metrics"]["citation_accuracy"]
        flag = "✓" if new == 1.0 else ("+" if isinstance(old, (int, float)) and new > old else " ")
        print(f"[{i+1}/{total}] {qid}: {old} → {new} {flag}", flush=True)

    await deps.dispose()

    # 合成修正报告（testset 元数据从原报告头部恢复）
    new_report = build_report(
        {"testset_id": report.get("testset_id", ""),
         "domain": report.get("domain", ""),
         "version": report.get("version", "")},
        report["questions"],
        config=report.get("config", {}),
    )
    new_report["summary"]["total_time_s"] = report.get("summary", {}).get("total_time_s")
    saved = save_report(new_report, args.out)
    print(f"\n已保存修正报告: {saved}")
    print(f"citation_accuracy 修正后 overall = {new_report['summary']['overall']['citation_accuracy']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="重判 agent 评测里 citation_accuracy")
    parser.add_argument("--report", required=True, type=Path, help="已存 agent 评测报告")
    parser.add_argument("--out", default=None, type=Path, help="修正报告输出路径")
    args = parser.parse_args()
    if args.out is None:
        args.out = args.report.with_name(args.report.stem + "_fixed.json")
    asyncio.run(main(args))