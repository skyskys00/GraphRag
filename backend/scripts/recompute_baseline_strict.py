"""M6 直连基线 strict 重判：按「citations 反查完整内容」窄池重算 faithfulness + citation_accuracy。

背景：初版 M6 e2e 基线的 faithfulness / citation_accuracy 用「检索 top5 宽池」判——
裁判手上是全部 5 个块，答案任何陈述只要在任意一块找到即算支持/引用成立。
而 M10 agent 评测修正后是「citations 显式绑定」窄池：只喂 answer 实际显式引用
（[n] marker）的 text_unit 完整内容，裁判只能在被引用的材料里找依据。

两者口径不对等 ⇒ 直接相减判「不劣化」会系统性压低 agent。本脚本把基线也按
窄池重判（M6 citations 的 chunk hash id 可经 sidecar.resolve() 反查完整内容），
得到与 agent 严格同口径的基线数字，供 build_agent_ab 使用。

用法:
  python scripts/recompute_baseline_strict.py \
      --report tests/final_results/run_e2e_device_50_20261005.json \
      --out tests/final_results/run_e2e_device_50_20261005_strict.json
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
from app.m9_eval.metrics.faithfulness import compute_faithfulness


def _resolve_content(deps: Any, c: dict[str, Any]) -> str:
    """citations 条目 → 完整内容。基线/M6 用 chunk hash id（resolve），agent 用 text_unit id。"""
    meta = None
    tid = c.get("text_unit_id")
    if tid:
        meta = getattr(deps.sidecar, "by_text_unit", lambda _x: None)(tid)
        if meta is None:
            meta = getattr(deps.sidecar, "resolve", lambda _x: None)(tid)
    if meta is not None and getattr(meta, "content", ""):
        return meta.content
    return c.get("snippet") or c.get("content") or ""


async def _strict_question(deps: Any, q: dict[str, Any]) -> None:
    answer = q.get("answer", "")
    citations = q.get("citations", [])

    contexts: list[dict[str, Any]] = []
    marker_ctx: dict[int, str] = {}
    seen: set[tuple[str, str]] = set()
    for c in citations:
        if not isinstance(c, dict):
            continue
        tid = c.get("text_unit_id")
        doc_id = c.get("full_doc_id") or c.get("doc_id") or "?"
        key = (doc_id, str(tid))
        if not tid or key in seen:
            continue
        seen.add(key)
        content = _resolve_content(deps, c)
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

    # faithfulness：窄池（显式引用内容）
    fai = await compute_faithfulness(deps.query_func, q.get("question", ""), answer, contexts)
    q["metrics"]["faithfulness"] = fai["score"]
    q["metrics"]["faithfulness_detail"] = {
        "total_statements": fai.get("total_statements", 0),
        "supported_statements": fai.get("supported_statements", 0),
        "unsupported": fai.get("unsupported", []),
        "reason": fai["reason"],
    }

    # citation_accuracy：marker→完整内容 精确对齐
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
        of, oc = q["metrics"].get("faithfulness"), q["metrics"].get("citation_accuracy")
        await _strict_question(deps, q)
        nf, nc = q["metrics"]["faithfulness"], q["metrics"]["citation_accuracy"]
        print(f"[{i+1}/{total}] {qid}: faith {of} → {nf} | cite {oc} → {nc}", flush=True)

    await deps.dispose()

    new_report = build_report(
        {"testset_id": report.get("testset_id", ""),
         "domain": report.get("domain", ""),
         "version": report.get("version", "")},
        report["questions"],
        config=report.get("config", {}),
    )
    new_report["summary"]["total_time_s"] = report.get("summary", {}).get("total_time_s")
    saved = save_report(new_report, args.out)
    o = new_report["summary"]["overall"]
    print(f"\n已保存 strict 基线: {saved}")
    print(f"faithfulness {o['faithfulness']:.4f} | citation_accuracy {o['citation_accuracy']:.4f}（窄池同口径）")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="M6 基线 strict 口径重判")
    parser.add_argument("--report", required=True, type=Path, help="基线 e2e 报告")
    parser.add_argument("--out", default=None, type=Path, help="输出路径")
    args = parser.parse_args()
    if args.out is None:
        args.out = args.report.with_name(args.report.stem + "_strict.json")
    asyncio.run(main(args))