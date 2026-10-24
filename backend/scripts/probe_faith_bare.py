"""探底：裸版 faithful（无 fact/meta/enhance 分类、无指纹、无核验）对 8 落题重判。

用途：与双轨口径对照——若裸版对同一批答案的分数与双轨接近，说明双轨的价值主要在
「解释性」（meta 单报）而非分数本身；若裸版偏低较多，则说明双轨确有「宽恕推断结论」
的实际托底作用。

口径（裸版 = RAGAS 原始 faithful）：
  score = (total_statements - len(unsupported)) / total_statements
  不分类、不区分 meta/enhance、不叠代码侧兜底，只按「无依据即罚」。

探底纪律：不改 app/ 下任何正式文件；用独立 metric 名 "faithfulness_bare" 走 judge，
不污染生产 "faithfulness" 缓存；脚本留档 scripts/ 可复跑。

用法：cd backend && python scripts/probe_faith_bare.py
"""
from __future__ import annotations

import asyncio, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.m5_retrieve.retriever import retrieve
from app.m9_eval.judge import judge

QIDS = ["DV-FS-001", "DV-FS-002", "DV-IO-001", "DV-IO-004", "DV-FS-010", "DV-TN-008", "DV-FS-013", "DV-FS-018"]

SYSTEM_BARE = """你是一个严谨的 RAG 评测裁判。你的任务是判断给定的答案是否完全基于提供的上下文材料。
对答案中的每一个陈述，判断它是否能在检索上下文中找到明确依据；找不到依据的即是幻觉（即使该陈述本身是正确知识）。
只根据上下文判断，不要使用外部知识。"""

TEMPLATE_BARE = """请评估以下答案的忠实度：答案中的每一个陈述是否都能在检索上下文中找到依据。

【问题】
{question}

【检索上下文】
{context}

【答案】
{answer}

请先找出答案中的所有事实陈述（排除连接词、语气词、客套语等），然后逐一判断每个陈述是否能在上下文中找到明确依据。
输出 JSON 格式：
{{
  "score": 0.0 到 1.0 的忠实度分数（有依据陈述数 / 陈述总数）,
  "total_statements": 答案的全部陈述数,
  "unsupported": [
    {{"statement": "上下文找不到依据的陈述", "reason": "为什么找不到依据"}}
  ],
  "reason": "一句话总结判断结果"
}}
如果某个无依据的陈述实际有上下文证据，务必标为有依据，不要漏标。
"""


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


async def main() -> None:
    report = json.loads(Path("tests/reports/agent/run_agent_device_50_20261023_fixed.json").read_text())
    ws = "col_b7b876b1"
    from app.m7_interact.bootstrap import get_deps
    deps = await get_deps(ws)
    print(f"boot deps {ws}\n", flush=True)

    for q in report["questions"]:
        if q["id"] not in QIDS:
            continue
        ctx: dict[str, str] = {}
        for tool, a in _chain_args(q):
            if tool != "tool_retrieve" or not a.get("query"):
                continue
            tk = max(1, int(a.get("top_k") or 5))
            try:
                res = await retrieve(deps.rag, a["query"], deps.sparse, entities=deps.entities,
                                     exclude_docs=sorted(deps.excluded_docs),
                                     allowed_docs=sorted(deps.allowed_docs) if deps.allowed_docs else None)
            except Exception as e:
                print(f"[{q['id']}] rebuild 失败 {a['query'][:40]}: {e}", flush=True)
                continue
            for r in res.get("results", [])[:tk]:
                cid = str(r.get("chunk_id", ""))
                if cid and cid not in ctx:
                    meta = deps.sidecar.resolve(cid) if deps.sidecar else None
                    ctx[cid] = meta.content if meta else r.get("content", "")
        ctx_list = [{"chunk_id": k, "content": v} for k, v in ctx.items()]
        context_text = "\n\n".join(f"[{i+1}] {c['content']}" for i, c in enumerate(ctx_list))
        prompt = TEMPLATE_BARE.format(question=q.get("question", ""), context=context_text, answer=q.get("answer", ""))
        result = await judge(deps.query_func, "faithfulness_bare", prompt,
                             system_prompt=SYSTEM_BARE,
                             cache_key_parts=[q.get("question", ""), q.get("answer", ""), context_text])
        uns = result.get("unsupported") or []
        total = max(0, result.get("total_statements", 0) or 0)
        if total:
            bare = max(0.0, (total - len(uns)) / total)
        else:
            bare = float(result.get("score", 0.0))
        old = q.get("metrics", {}).get("faithfulness")
        flag = "↑" if bare - (old or 0) > 0.02 else ("↓" if bare - (old or 0) < -0.02 else "=")
        print(f"{q['id']}: 旧(评测)={old} → 裸版={bare:.3f} {flag} | stmts={total} 无依据={len(uns)}", flush=True)
        for u in uns:
            print(f"    [无依据] {u.get('statement', '')[:90]}", flush=True)
    await deps.dispose()


asyncio.run(main())