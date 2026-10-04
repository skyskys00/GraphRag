"""探针：验证对比 query 的「文档本地化」能否恢复 per-doc 检索召回。

背景（v5.30 泄漏治理后 comparison 诚实口径 0.375@5）：生产 M7 compare.py 逐 doc 用完整
对比 question 检索，question 里另一型号名会把本文档向量召回打到 cosine 阈值以下
（probe_perdoc_subquery.py 已证）。本探针测试同一思路的生产可复现版本：

localize_query = 把 query 中「属于其他文档的型号名」剔除后再喂给目标 doc 检索。
剔除所用信息只有两样生产数据，与评测试题/key_facts 零交集：
  L1. 文档文件名提炼的型号 token + 品牌短名单
  L2. L1 ∪ 图实体表（query 预处理同源），归属判定 =「型号名是否出现在目标 doc 内容」
        —— 出现在目标 doc 文本的型号保留（本文档型号），否则视为外来型号删除。

对 DV-CP 4 题逐 target doc 跑三行：baseline(完整 question) / L1 / L2，
输出检索条数 + 判别 token 命中（判别 token 从 key_facts 手抽，属评测侧信号，非检索输入）。

跑法：cd backend && python3 scripts/probe_compare_localize.py
需要 PG + Xinference（`./dev.sh status`）。
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.m9_eval.runner import _build_deps, _resolve_collection  # noqa: E402

COLLECTION = "col_b7b876b1"
TESTSET = Path(__file__).resolve().parents[1] / "tests/testsets/testset_device_30.json"

# 品牌短名单（从这批器械语料的封面/文件名归纳，属语料固有属性）
BRANDS = ["融柏", "铭昇", "博声", "英菲泰克", "中芯启恒", "瑞创", "YASEE", "雅斯"]
# 型号形正则：含连字符/数字的字母数字串（抓 LSP-1C / H2-5000IBP / KE-2000 / IDEM1000-0N）
_MODEL_RE = re.compile(
    r"[A-Za-z][A-Za-z0-9+]*(?:[-_][A-Za-z0-9+-]+)+"   # LSP-1C / IDEM1000-0N
    r"|[A-Z]{2,}[0-9][A-Za-z0-9-]*"                   # KE-2000 / PO-50B
)
# 实体表里「型号形」的过滤：含字母且（数字或连字符），且长度 ≥ 3
_ENTITY_MODEL_RE = re.compile(r"[A-Za-z0-9+-]{3,}")
_ENTITY_MODEL_HAS_TOKEN = (
    lambda e: any(c.isalpha() for c in e)
    and (any(c.isdigit() for c in e) or "-" in e or "_" in e)
)

# 每题每 doc 的判别 token（从 key_facts 手工抽取关键语义串，非检索输入）
JUDGE_TOKENS: dict[str, dict[str, tuple[str, ...]]] = {
    "DV-CP-001": {
        "融柏恒流泵_LSP-1C系列注射泵_说明书.pdf": ("行程", "运行模式"),
        "瑞创RSP系列注射泵.pdf": ("双通道", "140"),
    },
    "DV-CP-002": {
        "铭昇电子_H2-5000IBP有创血压模拟仪_操作手册.pdf": ("收缩压", "255"),
        "多参数检测仪KE-2000 说明书.pdf": ("量程", "279"),
    },
    "DV-CP-003": {
        "脉搏血氧仪.pdf": ("99%", "±2"),
        "多参数检测仪KE-2000 说明书.pdf": ("100%", "±3"),
    },
    "DV-CP-004": {
        "英菲泰克动态心电记录仪.pdf": ("蓝牙", "24 小时"),
        "血氧仪使用手册.pdf": ("会诊", "APP"),
    },
}


def _filename_candidates(filename: str) -> list[str]:
    """Level 1：从文件名提炼型号 token + 品牌。"""
    stem = Path(filename).stem
    out = _MODEL_RE.findall(stem)
    out += [b for b in BRANDS if b in stem]
    return sorted(set(out), key=len, reverse=True)


def localize_l1(query: str, cands_by_doc: dict[str, list[str]], target_doc_id: str) -> str:
    """只删「query 中出现、但不属于目标 doc」的文件名候选（贪心最长优先）。"""
    foreign = [c for did, cs in cands_by_doc.items() if did != target_doc_id for c in cs if c in query]
    q = query
    for c in sorted(set(foreign), key=len, reverse=True):
        q = q.replace(c, " ")
    return " ".join(q.split())


def localize_l2(query: str, doc_text: str, entity_cands: list[str]) -> str:
    """L1 之上：实体表候选，归属判定 = 型号名是否出现在目标 doc 文本。"""
    q = query
    for e in entity_cands:
        if e in doc_text:      # 本文档型号 → 保留
            continue
        q = q.replace(e, " ")
    return " ".join(q.split())


def hit(content_join: str, tokens: tuple[str, ...]) -> bool:
    return any(t in content_join for t in tokens)


async def main() -> None:
    from app.m5_retrieve.query_preprocess import load_entities_async
    from app.m5_retrieve.retriever import retrieve
    from app.m5_retrieve.sparse_index import SPARSE_FILE, load as load_sparse

    working_dir, workspace = _resolve_collection(COLLECTION)
    rag, sparse, _query_func, entities = await _build_deps(working_dir, workspace)

    # 文档名映射 + doc 全文（型号归属判定）
    reg = json.load(open(working_dir / "documents.json"))
    name_to_id: dict[str, str] = {}
    id_to_name: dict[str, str] = {}
    for rec in reg.values():
        did, fn = rec.get("doc_id"), rec.get("filename")
        if did and fn and not rec.get("deleted"):
            name_to_id[fn] = did
            id_to_name[did] = fn
    doc_text: dict[str, str] = {}
    for cid, meta in sparse.get("chunks", {}).items():
        doc_text.setdefault(meta["full_doc_id"], []).append(meta.get("content", ""))
    doc_text = {d: "\n".join(t) for d, t in doc_text.items()}

    cands_by_doc = {did: _filename_candidates(fn) for did, fn in id_to_name.items()}

    # 实体表里「型号形」的过滤：含字母且（数字或连字符），长度 ≥ 3 —— 与生产 localize_query L2 同口径，
    # 排除 APP/100/SDK 等通用英文数字词与纯数字（电话号/日期/流水号，2026-10-04 收紧，防子串破坏型号）
    qs = [q["question"] for q in json.load(open(TESTSET))["questions"] if q["category"] == "comparison"]
    entity_cands = sorted(
        {e for e in (entities or [])
         if len(e) >= 3 and _ENTITY_MODEL_RE.fullmatch(e) and _ENTITY_MODEL_HAS_TOKEN(e)
         and any(e in qq for qq in qs)},
        key=len, reverse=True,
    )
    print(f"实体表型号候选（出现在 CPI 对比 query 中）: {entity_cands}")
    print(f"文件名候选: {cands_by_doc}")
    print()

    for q in json.load(open(TESTSET))["questions"]:
        if q["category"] != "comparison":
            continue
        print(f"== {q['id']} | {q['question'][:60]}...")
        judges = JUDGE_TOKENS[q["id"]]
        for fn in q.get("source_docs") or []:
            did = name_to_id.get(fn)
            if not did:
                print(f"   !! 无 doc: {fn}"); continue
            q_l1 = localize_l1(q["question"], cands_by_doc, did)
            q_l2 = localize_l2(q_l1, doc_text.get(did, ""), [e for e in entity_cands])
            toks = judges[fn]
            rows = []
            for tag, sq in (("baseline", q["question"]), ("L1", q_l1), ("L2", q_l2)):
                r = await retrieve(rag, sq, sparse, entities=entities, allowed_docs=[did])
                res = r.get("results", [])
                cj = " | ".join(x["content"] for x in res[:5])
                rows.append((tag, len(res), hit(cj, toks), sq))
            for tag, n, h, sq in rows:
                print(f"   [{did[:8]}] {tag}: {n} 条 hit={h}  ← {sq}")
        print()


if __name__ == "__main__":
    asyncio.run(main())