"""探针：LLM 表格摘要对 reranker 失明的上分能力（CS-TN-003 单题对照）。

对比 4 种 rerank 文本形态对同一表块的 rerank 分：
  1. 原文 content（v5.12 状态基线）
  2. 规则 NL 摘要 + content（v5.13 现值）
  3. LLM NL 摘要 + content（本次探索目标）
  4. 纯 LLM NL 摘要（诊断 cross-encoder 对自然语言数字陈述的上分）

测两个表块：chunk-003（2023=186，recal 缺口块）与 chunk-004（2026E=425）。
只读不改任何正式代码；LLM 摘要强制逐行转写原文数字、禁止新增。

用法（backend/ 下）：/opt/anaconda3/envs/graphrag/bin/python scripts/probe_llm_summary.py
"""
from __future__ import annotations

import asyncio
import json
import os

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

QUERY = "2026 年中国客服智能化市场规模预计达到多少？相比 2023 年增长了多少倍？"
CHUNKS_FILE = "data/chunks/eval_cservice/2102a87db0c1cc05.jsonl"
TARGETS = ("2102a87db0c1cc05-chunk-003", "2102a87db0c1cc05-chunk-004")

SYSTEM_PROMPT = (
    "你是表格转写助手。把 markdown 表格转写成一段自然语言，逐行描述每行数据。"
    "规则：所有数字（年份、金额、百分比）必须照抄原表；只描述表中已有的信息；"
    "禁止推导、禁止补全、禁止评价。用简洁中文输出一段话，不要加序号和前缀。"
)


def load_chunk_meta(text_unit_id: str) -> dict:
    with open(CHUNKS_FILE) as f:
        for line in f:
            u = json.loads(line)
            if u["text_unit_id"] == text_unit_id:
                return u
    raise KeyError(text_unit_id)


def rule_summary(content: str) -> str:
    from app.m5_retrieve.table_summary import generate_table_summary

    return generate_table_summary(content)


async def llm_summary(text: str) -> str:
    from app.m3_index.providers import build_llm_func

    _, query_func = build_llm_func()
    return (await query_func(text, system_prompt=SYSTEM_PROMPT)).strip()


def fmt_label(s: str, width: int = 10) -> str:
    return s[:width]


async def main() -> None:
    from app.m5_retrieve.rerank import rerank

    for tid in TARGETS:
        meta = load_chunk_meta(tid)
        content = meta["content"]
        rule = rule_summary(content)
        llm = await llm_summary(content)

        variants = [
            ("raw", content),
            ("rule+raw", rule + "\n" + content),
            ("llm+raw", llm + "\n" + content),
            ("llm-only", llm),
        ]
        docs = [(f"{tid}:{label}", text) for label, text in variants]
        scored = {r["chunk_id"]: r["score"] for r in rerank(QUERY, docs, top_n=len(docs))}

        print("=" * 70)
        print(f"block {tid}")
        print(f"  rule_summary : {rule}")
        print(f"  llm_summary  : {llm}")
        print("  rerank scores (query = CS-TN-003):")
        for label, _ in variants:
            key = f"{tid}:{label}"
            print(f"    {label:<10s} {scored[key]:.4f}")


if __name__ == "__main__":
    asyncio.run(main())