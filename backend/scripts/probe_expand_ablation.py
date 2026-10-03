"""探针：M5 query 扩展 on/off 消融（不改生产代码，monkeypatch preprocess）。

背景：`query_preprocess.preprocess()` 的 A2「实体名反向模糊匹配」会往 `expanded`
里拼进噪声实体名——器械库实测「融柏」→ 保定融柏恒流泵制造有限公司/保险/特点/简介。
而 `expanded` 同时喂 vector + keyword 两路（retriever.py:65-66），这些通用词稀释了
真正相关块的 sparse 分数（目标块 keyword 排名 53 → 关掉扩展后 39）。

本脚本把 `expanded` 强制等于 `original`（= 关掉 query 扩展），复用 M9 runner 全流程
跑检索指标，与线上（扩展 on）对比。**不改生产代码**，符合探底纪律。

⚠️ **2026-10-03 探底结论：关闭扩展不采纳、已回滚**——生产代码 `retriever.py` 维持
`q_vec/q_kw = prep.expanded`（ON 行为），故本脚本 `--expand on` = 线上行为、
`--expand off` = 探底分支，两者都可用。结论（检索排序净升但 e2e 生成下降 ⇒ 不采纳）
与报告见 CHANGELOG v5.29「探底：关闭 M5 query 扩展」。

跑法：
  cd backend && python3 scripts/probe_expand_ablation.py \
      --testset tests/testsets/testset_admin_30.json --collection eval_admin \
      --expand off --report tests/reports/run_ablation_admin30_expOFF_20261003.json
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.m5_retrieve import retriever as ret_mod  # noqa: E402
from app.m9_eval import runner  # noqa: E402

# 自检用的「必然触发扩展」样本：实体名与 query 片段互为子串
_SELF_Q = "融柏 LSP-1C 注射泵的行程范围"
_SELF_ENTS = ["融柏恒流泵", "保定融柏恒流泵制造有限公司", "保险"]


def _install_no_expand() -> None:
    """把 preprocess 的 expanded 强制为 original（关掉 query 扩展）。"""
    orig = ret_mod.preprocess_query

    def _no_expand(query: str, entities):
        p = orig(query, entities)
        p.expanded = p.original
        return p

    ret_mod.preprocess_query = _no_expand


def _selfcheck() -> None:
    """确认 patch 生效：同一 query，on 时 expanded 含扩展词，off 时应被抹平。"""
    p = ret_mod.preprocess_query(_SELF_Q, _SELF_ENTS)
    assert p.expanded == p.original, f"patch 未生效！expanded={p.expanded!r}"
    print("[自检] 扩展已关闭：expanded == original ✓\n", flush=True)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--testset", required=True)
    ap.add_argument("--collection", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--expand", choices=["on", "off"], default="off")
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()

    if a.expand == "off":
        _install_no_expand()
        _selfcheck()
    else:
        # 确认线上行为下扩展确实生效（对照锚点）
        p = ret_mod.preprocess_query(_SELF_Q, _SELF_ENTS)
        assert p.expanded != p.original, "对照异常：扩展未生效，样本需换"
        print(f"[自检] 扩展保持开启（线上行为），样本 expanded 尾部={p.expanded[len(p.original):]!r}\n", flush=True)

    args = argparse.Namespace(
        testset=a.testset, mode="retrieval", collection=a.collection,
        workspace=None, report=a.report, limit=a.limit, reranker="standard",
        ablation_routes="graph,vector,keyword",
    )
    await runner.main_async(args)


if __name__ == "__main__":
    asyncio.run(main())
