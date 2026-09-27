"""探针·链路验证：LLM listwise 排序能否把 rerank 压掉的表块救回 top5。

问题背景（v5.20 遗留）：cross-encoder(bge-reranker-v2-m3) 对数值/专名表块系统性低分，
表块能进 fused 候选池（FUSED_TOP=40）但被融合排序压出生产窗口 top5。
本探针验证 RankGPT 式思路：把候选池 topN 交给 LLM listwise 排序，看锚点表块能否进 LLM top5，
从而证明「终审权从 cross-encoder 移交给 LLM」是否有效。

方法：
  1. 每题用生产口径(RERANK_TOP=5)跑正式检索，取 fusion.fused_top40 作为候选池（前 POOL=20）；
  2. 对每题预标「期望答案块」指纹（content 子串），池内命中该指纹的块 = 锚点块；
  3. 把 query + 池内每块(content 截断)喂给 LLM，要求输出最相关 top5 编号（listwise）；
  4. 对照：锚点块 baseline fused 排名 vs LLM top5 内是否出现。

用法（backend/ 下）：
  /opt/anaconda3/envs/graphrag/bin/python -m scripts.probe_rankgpt_listwise
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from dotenv import load_dotenv

PROJ = Path(__file__).resolve().parents[1]
load_dotenv(PROJ / ".env")

POOL = 20  # 候选池大小（fusion.fused_top40 前 N）
TOP_K = 5  # LLM 输出 topK
CONTENT_MAX = 300  # 每块 content 截断字符数

# 每题：workspace + question + 期望答案块指纹（content 子串，命中即锚）
CASES: list[dict] = [
    {
        "id": "CS-TN-003",
        "workspace": "eval_cservice_ws",
        "question": "2026 年中国客服智能化市场规模预计达到多少？相比 2023 年增长了多少倍？",
        "anchors": ["| 2023 | 186 |", "| 2026E | 425 |"],
    },
    {
        "id": "adm_q004",
        "workspace": "eval_admin_ws",
        "question": "出差到上海，普通员工的住宿标准是多少元一晚？",
        "anchors": ["上海", "一类城市"],
    },
    {
        "id": "adm_q012",
        "workspace": "eval_admin_ws",
        "question": "普通员工去北京（一类城市）和去合肥（三类城市）出差，住宿标准和餐饮补助分别有什么差别？",
        "anchors": ["北京", "合肥"],
    },
    {
        "id": "adm_q013",
        "workspace": "eval_admin_ws",
        "question": "一个25人的部门，每月可以领多少箱A4打印纸？",
        "anchors": ["20-50", "4箱"],
    },
    {
        "id": "adm_q020",
        "workspace": "eval_admin_ws",
        "question": "新员工入职次日的培训在哪里举行？该会议室有什么特点、预约有什么要求？",
        "anchors": ["3F大会议室", "40人"],
    },
    {
        "id": "adm_q008",
        "workspace": "eval_admin_ws",
        "question": "员工出差期间，公司配备的笔记本电脑如果丢失了，应该怎么处理？涉及哪些制度？",
        "anchors": ["丢失", "5000元"],
    },
]

SYSTEM_PROMPT = (
    "你是文档检索重排器。给定一条用户查询和一组带编号的候选文本片段，"
    "判断每条候选与查询的相关性，输出最相关的"
    + str(TOP_K)
    + "个候选的编号。只输出 JSON 数组，形如 [3, 17, 2, 9, 5]，"
    "编号按相关性从高到低排列，不要输出任何其他文字。"
)


def _truncate(content: str, n: int = CONTENT_MAX) -> str:
    return content.replace("\n", " ⏎ ")[:n]


def _parse_ranking(text: str) -> list[int]:
    """从 LLM 输出解析编号数组。容忍 markdown fence / 尾部文字。"""
    m = re.search(r"\[([0-9,\s]+)\]", text)
    if not m:
        return []
    return [int(x) for x in re.findall(r"\d+", m.group(1))]


async def _llm_listwise(query_func, question: str, pool: list[dict]) -> list[int]:
    """把 query + 池内块交给 LLM，返回其给出的重排顺序（排名→池内下标）。"""
    lines = [f"查询：{question}\n", "候选片段："]
    for i, blk in enumerate(pool):
        lines.append(f"{i + 1}. {_truncate(blk['content'])}")
    prompt = "\n".join(lines)
    raw = (await query_func(prompt, system_prompt=SYSTEM_PROMPT)).strip()
    return _parse_ranking(raw)


def _summarize(retr: dict) -> dict:
    """提取 fused_top40 候选池（含 rerank/sparse/rrf/fused 分数）+ 生产 top5。"""
    fusion = retr.get("fusion", {})
    rs = fusion.get("rerank_scores", {})
    ss = fusion.get("sparse_scores", {})
    rr = fusion.get("rrf_scores", {})
    pool = fusion.get("fused_top40", [])
    meta = retr.get("_meta", {})
    blobs = []
    for p in pool[:POOL]:
        cid = p["chunk_id"]
        blobs.append(
            {
                "i": len(blobs) + 1,
                "chunk_id": cid,
                "content": meta.get(cid, {}).get("content", ""),
                "block_type": meta.get(cid, {}).get("block_type", ""),
                "fused": p["score"],
                "rerank": rs.get(cid),
                "sparse": ss.get(cid, 0.0),
                "rrf": rr.get(cid, 0.0),
            }
        )
    top5 = [b["chunk_id"] for b in blobs][:TOP_K]
    return {"pool": blobs, "top5": top5}


async def _run_case(rag, sparse, query_func, case: dict) -> dict:
    from app.m5_retrieve import retriever as ret_mod

    orig = ret_mod.RERANK_TOP
    ret_mod.RERANK_TOP = TOP_K
    try:
        retr = await ret_mod.retrieve(rag, case["question"], sparse)
    finally:
        ret_mod.RERANK_TOP = orig
    retr["_meta"] = sparse.get("chunks", {})

    sum_ = _summarize(retr)
    pool = sum_["pool"]

    # 锚点块：content 命中任一指纹
    anchor_idx = [
        b["i"] - 1 for b in pool if any(a in b["content"] for a in case["anchors"])
    ]

    # LLM listwise 重排
    order = await _llm_listwise(query_func, case["question"], pool)
    llm_top5_idx = [i - 1 for i in order[:TOP_K] if 1 <= i <= len(pool)]

    shown = {}
    for b in pool:
        shown[b["chunk_id"]] = b
    lines = [f"\n=== {case['id']} · workspace={case['workspace']} ==="]
    lines.append(f"Q: {case['question']}")
    lines.append(f"[baseline top5] fused 序: " + " | ".join(sum_["top5"]))
    lines.append(f"[pool 命中锚点 {len(anchor_idx)} 块]")
    for ai in anchor_idx:
        b = pool[ai]
        state = "被压出top5" if (ai + 1) > TOP_K else f"基线已在top5(#{ai+1})"
        print(
            f"  锚#{ai+1} {b['chunk_id'][-8:]} rerank={b['rerank'] and round(b['rerank'],4)} "
            f"sparse={round(b['sparse'],4)} fused={round(b['fused'],4)} [{b['block_type']}] {state}"
        )
        print(f"     content: {_truncate(b['content'], 160)}")
    lines.append(f"[LLM 重排 order] = {order}  → LLM top5 = {[pool[i]['chunk_id'] for i in llm_top5_idx]}")
    for ai in anchor_idx:
        b = pool[ai]
        pos = llm_top5_idx.index(ai) + 1 if ai in llm_top5_idx else None
        verdict = f"LLM 救回 top5 → #{pos} ✓" if pos else "LLM 仍未救回 ✗"
        print(f"  锚#{ai+1} {b['chunk_id'][-8:]}: {verdict}")
        lines.append(f"  锚#{ai+1} {b['chunk_id'][-8:]}: {verdict}")
    print("\n".join(lines) + "\n")

    return {
        "id": case["id"],
        "pool": [{"i": b["i"], "chunk_id": b["chunk_id"], "block_type": b["block_type"],
                  "rerank": b["rerank"], "sparse": round(b["sparse"], 4), "fused": round(b["fused"], 4),
                  "content": b["content"][:CONTENT_MAX]} for b in pool],
        "anchor_idx": anchor_idx,
        "llm_order": order,
        "llm_top5": [pool[i]["chunk_id"] for i in llm_top5_idx],
    }


async def main() -> None:
    from app.m9_eval.runner import _build_deps

    by_ws: dict[str, list] = {}
    for c in CASES:
        by_ws.setdefault(c["workspace"], []).append(c)

    all_out = []
    for ws, cases in by_ws.items():
        print(f"\n初始化 deps: {ws} ...", flush=True)
        rag, sparse, query_func = await _build_deps(ws)
        print(f"  chunks: {len(sparse.get('chunks', {}))}")
        try:
            for c in cases:
                all_out.append(await _run_case(rag, sparse, query_func, c))
        finally:
            await rag.finalize_storages()

    out_path = PROJ / "tests" / "reports" / "probe_rankgpt_listwise.json"
    out_path.write_text(json.dumps(all_out, ensure_ascii=False, indent=2))
    print(f"\n已存 {out_path}")

    # 汇总判定
    rescued = 0
    total = 0
    for r in all_out:
        pool = r["pool"]
        llm_top5_ids = set(r["llm_top5"])
        for ai in r["anchor_idx"]:
            b = pool[ai]
            in_base = (ai + 1) <= TOP_K
            in_llm = b["chunk_id"] in llm_top5_ids
            if in_base:
                continue  # 基线已在 top5，非缺口
            total += 1
            if in_llm:
                rescued += 1
            print(f"[判定] {r['id']} 锚#{ai+1} {b['chunk_id'][-8:]}: "
                  f"基线#{ai+1}(被压) → LLM {'救回✓' if in_llm else '未救回✗'}")
    print(f"\n汇总: 缺口锚点 {total} 个，LLM 救回 {rescued} 个 ({rescued}/{total})")


if __name__ == "__main__":
    asyncio.run(main())