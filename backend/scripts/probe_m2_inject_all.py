"""M2 表格语义注入全量模拟：所有 table 块 content 加「父标题+兄弟小节标题」前缀后重编码 sparse 向量，
复用 M9 evaluate_retrieval 跑 30 题全量，对比 v5.19 基线（keyword 单路净效应，dense/graph 路不变）。

选择 INJECT_MODE=sib（父标题+兄弟标题）或 sib_doc（再并文档标题）。
不修改模块代码、不改 data 文件（sparse 仅在内存替换 index，meta content 原样 → rerank text 用旧内容，保守估计）。

用法：cd backend && INJECT_MODE=sib /opt/anaconda3/envs/graphrag/bin/python scripts/probe_m2_inject_all.py
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))

from app.m9_eval.runner import _build_deps, evaluate_retrieval, load_testset
from app.m9_eval.report import build_report, to_markdown
from app.m5_retrieve.sparse_index import _sparse_encode
from app.m5_retrieve.table_summary import generate_table_summary

CHUNKS_DIR = PROJ / "data" / "chunks" / "eval_admin"
WORKSPACE = "eval_admin_ws"
MODE = os.environ.get("INJECT_MODE", "sib")
# INJECT_META=1：同时替换 sparse meta content（rerank text 用注入后内容）——
# 等价正式 M2 落地产物；默认 0 保持保守估计（meta 原样，rerank 用旧文本）。
INJECT_META = os.environ.get("INJECT_META", "0") == "1"
# SKIP_META=1：跳过「修订历史」「版本历史」固定词表类元信息表（q001/q002 挤入污染块源）
#  META_GENERIC=1 时用通用启发式（标题含“修订/版本/变更/更新”+“历史/记录/日志/清单”）替代固定词表
SKIP_META = os.environ.get("SKIP_META", "0") == "1"
META_GENERIC = os.environ.get("META_GENERIC", "0") == "1"
# FLAT_DEGRADE=1：两级扁平文档（title_path 第一段=文档名，sibling=整文档目录）不展开 sibling
# 只留父标题链——for A1 办公用品/IT/会议室/考勤 这类扁平结构的字典表，防止全目录串过曝污染
FLAT_DEGRADE = os.environ.get("FLAT_DEGRADE", "0") == "1"
# NL_SUM=1：sparse 编码文本 = 表 NL 摘要（table_summary 规则模板，零成本）+ 标题注入前缀 + 内容。
# 让摘要参与召回（进池），对比仅 rerank 用摘要的现状。
NL_SUM = os.environ.get("NL_SUM", "0") == "1"

_STRIP = re.compile(r"\*\*(?P<t>[^*]+)\*\*")


def _plain(head: str) -> str:
    return _STRIP.sub(lambda m: m.group("t"), head).strip()


def build_facts(sparse: dict) -> dict:
    """cid -> {parent, leaf, siblings, flat}，按 content 对齐 chunks jsonl。"""
    by_content: dict[str, tuple[str, str]] = {}
    for jf in CHUNKS_DIR.glob("*.jsonl"):
        for line in jf.open(encoding="utf-8"):
            u = json.loads(line)
            tp = u.get("title_path")
            parts = [_plain(x) for x in tp.split(" / ")] if tp else []
            by_content[u["content"]] = parts
    sibling_map: dict[str, set[str]] = {}
    for parts in by_content.values():
        if len(parts) < 2:
            continue
        sibling_map.setdefault(parts[-2], set()).add(parts[-1])
    units: dict[str, dict] = {}
    for cid, m in sparse["chunks"].items():
        parts = by_content.get(m["content"])
        if not parts:
            continue
        parent = parts[-2] if len(parts) >= 2 else ""
        # flat:title_path 第一段不是章节编号（第X章 / X.Y / X），即父标题=文档名 → 两级扁平
        top = parts[0] if parts else ""
        flat = bool(top) and not (
            _NUM_PAT.match(top) or _CH_PAT.match(top)
        )
        units[cid] = {
            "parent": parent,
            "leaf": parts[-1] if parts else "",
            "siblings": sorted(sibling_map.get(parent, set())),
            "flat": flat,
        }
    return units


_NUM_PAT = re.compile(r"^\d+(\.\d+)*\s")
_CH_PAT = re.compile(r"^第[一二三四五六七八九十百千]+[章节篇]")


def inject_content(u: dict, content: str, doc_title: str) -> str:
    parent, leaf = u["parent"], u["leaf"]
    sibs = u.get("siblings", [])
    if MODE == "sib":
        if FLAT_DEGRADE and u.get("flat"):
            prefix = f"{parent}"  # 扁平退化：只留父标题链，不展开 sibling
        else:
            prefix = f"{parent} 本表章节含 {' '.join(sibs)}"
    else:  # sib_doc
        prefix = f"{parent} 本表章节含 {' '.join(sibs)} {doc_title}"
        if FLAT_DEGRADE and u.get("flat"):
            prefix = f"{parent}"
    return f"{prefix}\n{content}"


_META_PAT = ("修订", "版本历史")


def _is_meta(u: dict) -> bool:
    """元信息表：标题含「修订」「版本历史」（修订历史/版本历史表无业务语义，仅元信息）。

    META_GENERIC=1 时用通用启发式：标题含「修订|版本|变更|更新」+「历史|记录|日志|清单|追溯」，
    不依赖固定词表，避免为每个新文档单独配规则。
    """
    t = f"{u['leaf']} {u['parent']}"
    if META_GENERIC:
        return bool(re.search(r"(修订|版本|变更|更新|下发实施|追溯)[\s]*?(历史|记录|日志|清单|说明|情况)", t))
    return any(p in t for p in _META_PAT)


async def main() -> None:
    sparse_doc = None
    # 直接从磁盘 load sparse（_build_deps 内部会再 build/load，这里只取一次供注入）
    import app.m9_eval.runner as runner_mod
    import asyncio

    # build_deps 返回的内存 sparse 即评估使用的对象；注入前先取 units
    rag, sparse, query_func = await _build_deps(WORKSPACE)
    print(f"[deps] sparse chunks: {len(sparse.get('chunks', {}))}", flush=True)

    units = build_facts(sparse)
    # 文档标题：取每文档第一块的 leaf 作为标题近似
    doc_titles: dict[str, str] = {}
    for cid, u in units.items():
        m = sparse["chunks"][cid]
        docid = m["full_doc_id"]
        if docid not in doc_titles:
            doc_titles[docid] = u["leaf"]

    n_table = n_skip = n_injected = n_flat = 0
    for cid, m in sparse["chunks"].items():
        if m.get("block_type") != "table":
            continue
        n_table += 1
        u = units.get(cid)
        if not u:
            continue
        if (SKIP_META or META_GENERIC) and _is_meta(u):
            n_skip += 1
            continue
        new_c = inject_content(u, m["content"], doc_titles.get(m["full_doc_id"], ""))
        if NL_SUM:
            s = generate_table_summary(m["content"])
            if s:
                new_c = s + "\n" + new_c
        sparse["index"][cid] = _sparse_encode([new_c])[0]
        if INJECT_META:
            m["content"] = new_c
        n_injected += 1
        if FLAT_DEGRADE and u.get("flat"):
            n_flat += 1
    print(f"[inject] 共表{n_table} 注入{n_injected} 跳元表{n_skip} 扁平退化{n_flat}（{MODE}{', meta含注入' if INJECT_META else ', meta保守'}）", flush=True)

    ts = load_testset(PROJ / "tests" / "testsets" / "testset_admin_30.json")
    results = await evaluate_retrieval(ts, rag, sparse, query_func)
    report = build_report(ts, results, config={"mode": "retrieval", "collection": "eval_admin", "workspace": WORKSPACE})
    print("\n" + "=" * 62)
    print(to_markdown(report))

    suffix = f"{MODE}_meta" if INJECT_META else MODE
    if META_GENERIC:
        suffix += "_genmeta"
    elif SKIP_META:
        suffix += "_skpmeta"
    if FLAT_DEGRADE:
        suffix += "_flatdeg"
    if NL_SUM:
        suffix += "_nlsum"
    out = PROJ / "tests" / "reports" / f"probe_m2_inject_all_{suffix}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\n[report] save -> {out.relative_to(PROJ)}")

    # 关键题点检：q001/q002（真降恢复）+ q004/q008/q013（缺口表进池）
    for q in ["adm_q001", "adm_q002", "adm_q004", "adm_q008", "adm_q012", "adm_q013"]:
        r = next((x for x in results if x.get("id") == q), None)
        if not r:
            continue
        m = r.get("metrics", {})
        print(f"\n[{q}] rec@5={m.get('context_recall_top5')} prec@5={m.get('context_precision_top5')} nDCG@5={m.get('ndcg_top5')} nDCG@8={m.get('ndcg_top8')}")

    await rag.finalize_storages()


if __name__ == "__main__":
    asyncio.run(main())