"""探针·链路验证：LLM 表格摘要注入对 top5/top8 名单的影响（CS-TN-003 单题）。

对同一问题用同一环境跑两次完整检索：
  1. baseline（当前 build_rerank_text：规则摘要）
  2. monkeypatch build_rerank_text 注入 LLM 摘要（chunk-003/004）
对比 top5 / top8 名单与 2023 块(186)的融合排名是否进入窗口。

前提：probe_llm_summary.py 已证明 rerank 分跃迁；本脚本验证上分是否传导到最终排名。
用法（backend/ 下）：/opt/anaconda3/envs/graphrag/bin/python -m scripts.probe_llm_summary_chain
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from dotenv import load_dotenv

PROJ = Path(__file__).resolve().parents[1]
load_dotenv(PROJ / ".env")

QUERY = "2026 年中国客服智能化市场规模预计达到多少？相比 2023 年增长了多少倍？"
# sparse 池中真实的 D5 表块（v5.13 构建，content 无【列：】前缀），按 content 指纹识别
FN = "2102a87db0c1cc05"
NEEDLE_2023 = "| 2023 | 186 |"
NEEDLE_2026E = "| 2026E | 425 |"
CACHE = Path("/tmp/probe_llm_summary_cache.json")

SYSTEM_PROMPT = (
    "你是表格转写助手。把 markdown 表格转写成一段自然语言，逐行描述每行数据。"
    "规则：所有数字（年份、金额、百分比）必须照抄原表；只描述表中已有的信息；"
    "禁止推导、禁止补全、禁止评价。用简洁中文输出一段话，不要加序号和前缀。"
)


def _sparse_table_blocks(sparse: dict) -> dict[str, dict]:
    """返回 sparse 池中 D5 文档的表块（content 指纹 → meta），供 rerank 文本注入。"""
    out: dict[str, dict] = {}
    for cid, m in sparse.get("chunks", {}).items():
        if m.get("full_doc_id") != FN or str(m.get("block_type", "")).lower() != "table":
            continue
        c = m.get("content", "")
        if NEEDLE_2023 in c:
            out["2023块(186)"] = {"cid": cid, "content": c}
        elif NEEDLE_2026E in c:
            out["2026E块(425)"] = {"cid": cid, "content": c}
    return out


async def _llm_summaries(blocks: dict[str, dict]) -> dict[str, str]:
    """对 sparse 真实块 content 生成 LLM 摘要，带缓存（旧缓存 key 为 text_unit_id，型号不匹配则忽略）。"""
    labels = set(blocks)
    if CACHE.exists():
        cached = json.loads(CACHE.read_text())
        if labels.issubset(cached):
            print(f"  [cache] 命中 {labels}，直接复用 LLM 摘要")
            return cached

    from app.m3_index.providers import build_llm_func

    _, query_func = build_llm_func()
    out = {}
    for label, b in blocks.items():
        out[label] = (await query_func(b["content"], system_prompt=SYSTEM_PROMPT)).strip()
        print(f"  summary[{label}] = {out[label][:70]}...")
    CACHE.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    return out


def _dump_fusion(retr: dict, blocks: dict[str, dict], label: str) -> None:
    fusion = retr.get("fusion", {})
    rs = fusion.get("rerank_scores", {})
    ss = fusion.get("sparse_scores", {})
    rr = fusion.get("rrf_scores", {})
    pool = {t["chunk_id"]: t for t in fusion.get("fused_top40", [])}
    print(f"\n[{label}] fusion 诊断：")
    for tag, b in blocks.items():
        cid = b["cid"]
        entry = pool.get(cid)
        fused = entry["score"] if entry else None
        fused_rank = (list(pool).index(cid) + 1) if cid in pool else None
        feat = entry.get("features", {}) if entry else {}
        print(f"  {tag} ({cid[-6:]}): rerank={rs.get(cid):.4f} sparse={ss.get(cid, 0):.4f} "
              f"rrf={rr.get(cid, 0):.6f} → fused={fused:.4f} @融合rank{fused_rank}  features={feat}")
    print(f"  top8 融合序 & rerank：")
    for i, c in enumerate(list(pool)[:8]):
        e = pool[c]
        print(f"    rank{i+1} {c[-6:]} fused={e['score']:.4f} rerank={rs.get(c, 0):.4f}")


def _paths(retr: dict) -> tuple[list[str], list[dict]]:
    results = retr.get("results", [])
    order = [r["chunk_id"] for r in results]
    nice = [f"{r['rank']}:{r['chunk_id'][-6:]} f={r['score']:.3f}" for r in results]
    return order, nice


async def main() -> None:
    from app.m9_eval.runner import _build_deps
    from app.m5_retrieve import retriever as ret_mod
    print("初始化 RAG / sparse...")
    rag, sparse, _ = await _build_deps("eval_cservice_ws")
    print(f"  chunks: {len(sparse.get('chunks', {}))}")
    try:
        blocks = _sparse_table_blocks(sparse)
        if len(blocks) != 2:
            print(f"!! 未按指纹识别到 2 个 D5 表块，只找到：{list(blocks)}")
            return
        print("sparse 池中识别的 D5 表块：")
        for label, b in blocks.items():
            print(f"  {label}: {b['cid']}")
            print(f"    content 前 60 字 = {b['content'][:60]!r}")

        llm_texts = await _llm_summaries(blocks)
        sum_by_id = {b["cid"]: llm_texts[label] for label, b in blocks.items()}

        orig_rr_top = ret_mod.RERANK_TOP

        # ---- 0. 池内排位诊断：RERANK_TOP=40 看融合全序中目标块位置 ----
        ret_mod.RERANK_TOP = 40
        diag = await ret_mod.retrieve(rag, QUERY, sparse)
        diag_order = [r["chunk_id"] for r in diag.get("results", [])]
        print("\n[池内诊断 RERANK_TOP=40] 目标块在融合序中的位置：")
        for label, b in blocks.items():
            pos = diag_order.index(b["cid"]) + 1 if b["cid"] in diag_order else None
            print(f"  {label}: 池内 rank = {pos}  {'(已在候选池内)' if pos else '(未进候选池)'}")

        # ---- 1. baseline（生产窗口 RERANK_TOP=5）----
        ret_mod.RERANK_TOP = 5
        base = await ret_mod.retrieve(rag, QUERY, sparse)
        base_order, base_nice = _paths(base)
        _dump_fusion(base, blocks, "baseline")

        # ---- 2. monkeypatch 注入 LLM 摘要，同窗口再跑 ----
        orig = ret_mod.build_rerank_text

        seen = {}
        def patched(cid: str, meta: dict) -> str:
            hit = cid in sum_by_id
            seen[cid] = hit
            if hit:
                return sum_by_id[cid] + "\n" + meta.get("content", "")
            return orig(cid, meta)

        ret_mod.build_rerank_text = patched
        llm_retr = await ret_mod.retrieve(rag, QUERY, sparse)
        ret_mod.build_rerank_text = orig
        ret_mod.RERANK_TOP = orig_rr_top

        hits = [cid for cid, h in seen.items() if h]
        print(f"  [debug] build_rerank_text 被调用的块数={len(seen)}，其中 LLM 注入命中={len(hits)}")
        print(f"  [debug] 命中块: {hits}")

        llm_order, llm_nice = _paths(llm_retr)
        _dump_fusion(llm_retr, blocks, "llm-注入")

        print("\n" + "=" * 70)
        print("baseline (rule summary) → ", " | ".join(base_nice))
        print("llm summary injected    → ", " | ".join(llm_nice))

        print("\n逐位对照 (baseline → llm)：")
        for i in range(5):
            b = base_order[i] if i < len(base_order) else "(空)"
            l = llm_order[i] if i < len(llm_order) else "(空)"
            tag = " <<= 缺口块" if l in sum_by_id else ""
            print(f"  rank{i+1}: {b[-6:]}  →  {l[-6:]}{tag}")

        for cid, label in blocks.items():
            br = base_order.index(cid) + 1 if cid in base_order else None
            lr = llm_order.index(cid) + 1 if cid in llm_order else None
            print(f"\n{label} baseline排名={br}  LLM注入后排名={lr}  "
                  f"{'→ 进入 top5 ✓' if lr and lr <= 5 else ('→ 未进 top5 ✗' if lr else '→ 未召回 ✗')}")
    finally:
        await rag.finalize_storages()


if __name__ == "__main__":
    asyncio.run(main())