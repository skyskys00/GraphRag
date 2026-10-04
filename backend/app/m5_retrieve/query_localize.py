"""对比检索 query 文档本地化：删 query 中「属于其他文档」的型号名。

背景（v5.30 泄漏治理后 comparison 诚实口径 0.375@5，DEVICE_SCENARIO §5.4/§11.1）：
生产 M7 compare.py 逐 doc 用完整对比 question 检索，question 里另一型号名会把本文档
向量召回打到 cosine 阈值以下（probe_perdoc_subquery.py 已证）。本模块提供生产可复现的
自动去噪——`localize_query` 对目标 doc 剔除 query 中「其他文档的型号名」再检索。

**信息红线合规**：所用信息只有生产已存在的数据，与评测试题/key_facts 零交集：
  L1. 文档文件名提炼的型号 token + 品牌短名单（documents.json 固有属性）
  L2. 图实体表（query 预处理同源）——型号名出现在目标 doc 内容 → 保留，否则视为外来删除

**关键不变量**：非对比 query（无型号名命中）→ q_doc == query，零回退。
compare 语义：同一 query 打所有 doc；localize 只影响「query 里提到别家型号」的情形。

探针实证（2026-10-04，scripts/probe_compare_localize.py）：L1 单独不够（型号 token
可能不在文件名字面），L2 + 归属判定恢复 2/3 的 0 召回。生产走 L1→L2 全链。
"""
from __future__ import annotations

import re

# 品牌短名单（从这批器械语料的封面/文件名归纳，属语料固有属性）
BRANDS = ["融柏", "铭昇", "博声", "英菲泰克", "中芯启恒", "瑞创", "YASEE", "雅斯"]
# 型号形正则：含连字符/数字的字母数字串（抓 LSP-1C / H2-5000IBP / KE-2000 / IDEM1000-0N）
_MODEL_RE = re.compile(
    r"[A-Za-z][A-Za-z0-9+]*(?:[-_][A-Za-z0-9+-]+)+"   # LSP-1C / IDEM1000-0N
    r"|[A-Z]{2,}[0-9][A-Za-z0-9-]*"                   # KE-2000 / PO-50B
)
# 实体表「型号形」过滤：长度 ≥3、含字母，且「含数字或连字符」——排除 APP/100/SDK 等
# 通用英文数字词、纯数字（电话号 0312-5893777 / 日期 01-17 / 长流水号 123456789）。
# 纯数字若放行，会出现子串破坏风险：query「IDEM1000-0N」里的「100」被整词替换成空格
# 会把型号炸成「IDEM 0-0N」（探针宽正则 + 仅数字/连字符判定有此隐患）。完整型号都是
# 字母+数字形态（LSP-1C/Cchippump-2），「含字母」不误伤。
_ENTITY_MODEL_RE = re.compile(r"^[A-Za-z0-9+-]{3,}$")
_ENTITY_MODEL_HAS_TOKEN = (
    lambda e: any(c.isalpha() for c in e)
    and (any(c.isdigit() for c in e) or "-" in e or "_" in e)
)


def filename_candidates(filename: str) -> list[str]:
    """Level 1：从文件名提炼型号 token + 品牌。"""
    stem = filename.rsplit(".", 1)[0]
    out = _MODEL_RE.findall(stem)
    out += [b for b in BRANDS if b in stem]
    return sorted(set(out), key=len, reverse=True)


def build_side_inputs(
    sparse: dict,
    id_to_filename: dict[str, str],
) -> tuple[dict[str, list[str]], dict[str, str]]:
    """构建 localize 所需的 (cands_by_doc, doc_text)，输入均为生产数据。

    - cands_by_doc: doc_id -> 文件名提炼候选（型号/品牌 token）
    - doc_text:     doc_id -> 该文档全部 chunk 内容拼接（型号归属判定用）
    """
    cands_by_doc = {did: filename_candidates(fn) for did, fn in id_to_filename.items()}
    per_doc: dict[str, list[str]] = {}
    for meta in (sparse.get("chunks") or {}).values():
        did = meta.get("full_doc_id")
        if did:
            per_doc.setdefault(did, []).append(meta.get("content", ""))
    doc_text = {d: "\n".join(parts) for d, parts in per_doc.items()}
    return cands_by_doc, doc_text


def localize_query(
    query: str,
    target_doc_id: str,
    cands_by_doc: dict[str, list[str]],
    doc_text: dict[str, str],
    entities: list[str] | None,
) -> str:
    """对目标 doc 剔除 query 中「属于其他文档」的型号名。非对比 query 原样返回。

    L1：文件名候选 —— 只删「query 中出现、但不属于目标 doc」的候选（贪心最长优先）。
    L2：实体表候选 —— L1 结果上，型号名出现在目标 doc 内容则保留，否则删除。
    """
    # L1
    foreign = [
        c
        for did, cands in cands_by_doc.items()
        if did != target_doc_id
        for c in cands
        if c in query
    ]
    q = query
    for c in sorted(set(foreign), key=len, reverse=True):
        q = q.replace(c, " ")
    # L2
    target_text = doc_text.get(target_doc_id, "")
    for e in (entities or []):
        if not _ENTITY_MODEL_RE.fullmatch(e) or not _ENTITY_MODEL_HAS_TOKEN(e):
            continue
        if e in target_text:
            continue  # 本文档型号 → 保留
        q = q.replace(e, " ")
    return " ".join(q.split())