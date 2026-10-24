"""诊断 8 个回落题：宽松池重建 vs 答案引用比对（区分伪影/重建残缺/真实越界）。

输入：agent 评测报告 + 回落题 qid 列表
产出（逐题）：
- tool_chain 全貌（工具 + args）
- 重建上下文 len / 引用的 citations len
- 答案引用 chunk 在重建上下文中的命中率（应=1，若<1 则重建残缺或评测时检索失效）
- 未引用但出现在重建里的 chunk 数（潜在"标注缺口"）
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(PROJ))

from app.m5_retrieve.retriever import retrieve


def _chain(q: dict) -> list[tuple[str, dict]]:
    out = []
    for c in q.get("tool_chain", []):
        a = c.get("args") or "{}"
        try:
            a = json.loads(a) if isinstance(a, str) else a
        except Exception:
            a = {}
        out.append((c.get("tool", ""), a))
    return out


async def rebuild(deps, q: dict) -> dict[str, str]:
    """重建 agent 检索上下文，返回 {chunk_id: content}。"""
    ctx: dict[str, str] = {}
    order: list[str] = []
    for tool, args in _chain(q):
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
                cid = str(r.get("chunk_id", ""))
                if cid and cid not in ctx:
                    ctx[cid] = r.get("content", "")[:2000]
                    order.append(cid)
        except Exception as e:
            print(f"    ⚠️ rebuild 失败 {query}: {e}", flush=True)
    return ctx


async def main(qids: list[str]) -> None:
    report = json.loads(Path("tests/reports/agent/run_agent_device_50_20261023_fixed.json").read_text())
    collection = report.get("config", {}).get("collection") or "col_b7b876b1"
    from app.m7_interact.bootstrap import get_deps
    deps = await get_deps(collection)
    print(f"boot deps {collection}\n")

    for q in report["questions"]:
        if q["id"] not in qids:
            continue
        fid = q["metrics"].get("faithfulness", None)
        # strict 从 Q 里取（评测已评），loose 用 probe 记录的比对口径
        cited = [c.get("chunk_id") for c in q.get("citations", []) if c.get("chunk_id")]
        ctx = await rebuild(deps, q)
        cited_in_ctx = [c for c in cited if c in ctx]
        n_rebuild = len(ctx)
        n_cited = len(cited)
        # 标注缺口：重建里有、但答案完全没引用（列表不看相关性，先量个底）
        print(f"### {q['id']}  |  faith(评测)={fid}")
        print(f"  tool_chain({len(q.get('tool_chain', []))}): ")
        for t, a in _chain(q):
            print(f"    - {t} {json.dumps(a, ensure_ascii=False)[:120]}")
        print(f"  重建 ctx={n_rebuild} chunks | 答案 cites={n_cited} | cited中在重建内={len(cited_in_ctx)}")
        print(f"  cited 未命中重建: {[c for c in cited if c not in ctx]}")
        # 宽松池判定（复用 probe 同键缓存，不烧 LLM）：取裁判 unsupported 明细
        answer = q.get("answer", "")
        ctx_list = [{"content": v} for v in ctx.values()]
        from app.m9_eval.metrics.faithfulness import compute_faithfulness
        fai = await compute_faithfulness(deps.query_func, q.get("question", ""), answer, ctx_list)
        uns = fai.get("unsupported", [])
        print(f"  宽松池判定: score={fai['score']:.3f} total={fai.get('total_statements')} su={fai.get('supported_statements')}")
        for u in uns:
            print(f"    不依: {u.get('statement', '')[:150]}")
            print(f"          原因: {u.get('reason', '')[:220]}")
        print(f"  question: {q.get('question', '')[:80]}")
        print()

    await deps.dispose()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--qids", nargs="+", required=True)
    a = ap.parse_args()
    asyncio.run(main(a.qids))