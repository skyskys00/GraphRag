"""判别性验证：agent 忠实度劣化是「引用标注粒度」还是「生成遗漏/幻觉」？

对同一份答案，比较两种 judge 上下文窗口：
  A. 严格口径（现评测）＝ 仅答案显式引用（[n] marker）的 text_unit 完整内容
  B. 宽松口径（判别用）＝ agent 全程工具检索返回的完整内容（tool_chain 的每个
     tool_retrieve query 确定性地重跑 retrieve，top_k 用轮次实际值，合集去重）

若 B 显著回升接近直连（≈0.96）→ 信息都在 agent 上下文里，纯是「一个 [n] 没覆盖
全部依据块」的标注粒度问题（答对、资料在、引用标不全）；
若 B 仍低 → 生成阶段止步于引用内容之外（可能工具结果截断/答案编造），是真缺陷。

用法:
  python scripts/probe_agent_loose.py --report tests/reports/agent/run_agent_device_50_20261023_fixed.json
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

from app.m5_retrieve.retriever import retrieve
from app.m9_eval.metrics.faithfulness import compute_faithfulness


def _chain_args(q: dict[str, Any]) -> list[tuple[str, dict]]:
    """提取 (tool, args_dict) 序列（arg 是 JSON 字符串）。"""
    out = []
    for c in q.get("tool_chain", []):
        a = c.get("args") or "{}"
        try:
            a = json.loads(a) if isinstance(a, str) else a
        except Exception:
            a = {}
        out.append((c.get("tool", ""), a))
    return out


async def rebuild_agent_context(deps: Any, q: dict[str, Any]) -> list[dict[str, Any]]:
    """按 tool_chain 重建 agent 全程看到的检索内容（确定性：同 query+同库→同结果）。"""
    ctx: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for tool, args in _chain_args(q):
        if tool != "tool_retrieve":
            continue
        query = args.get("query")
        if not query:
            continue
        top_k = max(1, int(args.get("top_k") or 5))
        try:
            res = await retrieve(
                deps.rag, query, deps.sparse,
                entities=deps.entities,
                exclude_docs=sorted(deps.excluded_docs),
                allowed_docs=sorted(deps.allowed_docs) if deps.allowed_docs else None,
            )
            for r in res.get("results", [])[:top_k]:
                did, cid = r.get("full_doc_id"), r.get("chunk_id")
                key = (str(did), str(cid))
                if key in seen:
                    continue
                seen.add(key)
                meta = deps.sidecar.resolve(cid) if deps.sidecar else None
                ctx.append({
                    "chunk_id": str(cid),
                    # 优先完整 text_unit（与评测口径一致）；否则用检索返回 content
                    "content": meta.content if meta else r.get("content", ""),
                    "full_doc_id": str(did),
                    "rank": len(ctx) + 1,
                    "score": r.get("score", 0.0),
                })
        except Exception as e:
            print(f"    ⚠️ rebuild 失败 {query}: {e}", flush=True)
    return ctx


async def main(args: argparse.Namespace) -> None:
    report = json.loads(args.report.read_text(encoding="utf-8"))
    collection = report.get("config", {}).get("collection") or "col_b7b876b1"

    from app.m7_interact.bootstrap import get_deps
    print(f"boot deps for {collection} ...", flush=True)
    deps = await get_deps(collection)
    print("ready\n", flush=True)

    # 只跑劣化题（faithfulness <= threshold），控制 LLM 成本
    qs = [q for q in report["questions"] if q["metrics"].get("faithfulness", 1.0) <= args.threshold]
    print(f"选取 {len(qs)} 题（faith ≤ {args.threshold}）重判宽松口径\n", flush=True)

    rows = []
    for i, q in enumerate(qs):
        qid = q["id"]
        strict = q["metrics"]["faithfulness"]
        answer = q.get("answer", "")
        ctx = await rebuild_agent_context(deps, q)
        fai = await compute_faithfulness(deps.query_func, q.get("question", ""), answer, ctx)
        loose = fai["score"]
        delta = loose - strict
        rows.append((qid, strict, loose, len(ctx), fai.get("total_statements", 0)))
        flag = "回升" if delta > 0.02 else ("回落" if delta < -0.02 else "持平")
        print(f"[{i+1}/{len(qs)}] {qid}: strict={strict:.3f} → loose={loose:.3f} (Δ{delta:+.3f}) "
              f"[{flag}] rebuild_ctx={len(ctx)}", flush=True)

    await deps.dispose()

    if rows:
        up = [r for r in rows if r[2] - r[1] > 0.02]
        flat = [r for r in rows if abs(r[2] - r[1]) <= 0.02]
        down = [r for r in rows if r[2] - r[1] < -0.02]
        avg_d = sum(r[2] - r[1] for r in rows) / len(rows)
        print("\n==== 汇总 ====")
        print(f"回升 {len(up)} / 持平 {len(flat)} / 回落 {len(down)}，平均 Δ = {avg_d:+.3f}")
        print("结论：")
        print(f"  平均宽松−严格 = {avg_d:+.3f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="宽松口径判别：引用粒度 vs 生成遗漏")
    parser.add_argument("--report", required=True, type=Path, help="agent 评测报告")
    parser.add_argument("--threshold", default=0.75, type=float, help="只重判 faith ≤ 此值 的题")
    args = parser.parse_args()
    asyncio.run(main(args))