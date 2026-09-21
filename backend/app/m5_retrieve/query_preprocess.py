"""M5 query 预处理（A+B）：同义词扩展 + 专名识别加权。

A. 同义词扩展：领域词典 + 实体名反向模糊匹配 → 扩展 query
B. 专名识别：正则 + 实体名匹配 → 输出加权词表，注入 ll_keywords

只支持 PG 后端（正式库）；从 lightrag_graph_nodes 拿全量实体名，启动时缓存。
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field

SYNONYMS: dict[str, list[str]] = {
    # 口语/缩写 → 标准术语（按当前 5 文档领域归纳）
    "客服": ["客户服务", "一线客服人员", "客服人员"],
    "客服系统": ["智能客服系统", "客服系统"],
    "投诉": ["客户投诉", "客户投诉处理", "投诉处理"],
    "一级投诉": ["一级投诉", "紧急投诉"],
    "二级投诉": ["二级投诉", "重要投诉"],
    "三级投诉": ["三级投诉", "一般投诉"],
    "华东": ["华东大区"],
    "华北": ["华北大区"],
    "华南": ["华南大区"],
    "华中": ["华中大区"],
    "西南": ["西南大区"],
    "西北": ["西北大区"],
    "智能硬件": ["智能硬件产品线"],
    "智能家居": ["智能家居产品线"],
    "企业服务": ["企业服务产品线"],
    "PRD": ["产品需求文档", "产品需求文档（PRD）"],
    "产品需求": ["产品需求文档"],
    "SOP": ["标准作业流程", "标准作业流程（SOP）"],
    "会议纪要": ["智能客服系统迭代会议纪要"],
    "迭代会议": ["智能客服系统迭代会议", "迭代会议"],
    "研发": ["研发组"],
    "产品": ["产品组", "产品部"],
    "运营": ["运营组"],
    "销售": ["销售团队", "销售人员"],
    "季度": ["第三季度", "2026年第三季度"],
    "Q3": ["第三季度", "2026年第三季度"],
    "三季度": ["第三季度", "2026年第三季度"],
}

# 日期/编号/等级正则
_DATE_PATTERNS = [
    re.compile(r"\d{4}年第[一二三四]季度"),
    re.compile(r"\d{4}年\d{1,2}月\d{1,2}日"),
    re.compile(r"\d{1,2}月\d{1,2}日"),
    re.compile(r"第[一二三四]季度"),
    re.compile(r"Q[1-4]"),
]
_CODE_PATTERNS = [
    re.compile(r"[A-Z]{2,}-[A-Z]{2,}-\d{4}-\d{3,}"),  # CS-SOP-2026-014
    re.compile(r"V\d+\.\d+"),  # V2.1 / V2.3
]
_LEVEL_PATTERNS = [
    re.compile(r"[一二三四]级"),
    re.compile(r"P[0-5]"),
]
# 复合专名：X系统/X平台/X产品…（连续中文 + 后缀）整体视为专名，比子串实体更具体，
# 如「智能客服系统」应整体加权，而不是退化成「客服」「客服系统」「系统」等共享词
_COMPOUND_SUFFIX_RE = re.compile(
    r"([一-鿿]{2,10})(系统|平台|产品|项目|方案|引擎|中心|部门|工作组|大区|模块)"
)


@dataclass
class PreprocessedQuery:
    original: str
    expanded: str
    ll_keywords: list[str] = field(default_factory=list)
    hl_keywords: list[str] = field(default_factory=list)
    weighted_terms: dict[str, float] = field(default_factory=dict)
    matched_entities: list[str] = field(default_factory=list)


def _pg_entity_names(workspace: str) -> list[str]:
    """从 PG lightrag_graph_nodes 读全量实体名（同步入口）。"""
    return asyncio.run(_pg_entity_names_async(workspace))


async def _pg_entity_names_async(workspace: str) -> list[str]:
    """从 PG lightrag_graph_nodes 读全量实体名（async 入口，已在 loop 内时用）。"""
    import asyncpg

    conn = await asyncpg.connect(
        host="127.0.0.1", port=5432, user="postgres", password="postgres", database="postgres"
    )
    try:
        rows = await conn.fetch(
            "SELECT id FROM lightrag_graph_nodes WHERE workspace = $1 AND namespace = 'chunk_entity_relation'",
            workspace,
        )
        return [r["id"] for r in rows if r["id"]]
    finally:
        await conn.close()


def _fuzzy_match(term: str, entities: list[str], max_dist: int = 2) -> list[str]:
    """term 在实体名列表里的模糊匹配（子串 + 编辑距离）。"""
    hits: list[tuple[int, str]] = []
    t = term.lower()
    for e in entities:
        el = e.lower()
        if t == el:
            hits.append((0, e))
        elif t in el or el in t:
            hits.append((1, e))
        else:
            d = _edit_distance(t, el)
            if d <= max_dist and len(t) >= 2:
                hits.append((d + 2, e))
    hits.sort(key=lambda x: x[0])
    return [e for _, e in hits[:5]]


def _edit_distance(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
        prev = cur
    return prev[-1]


def _extract_proper_nouns(query: str, entities: list[str]) -> dict[str, float]:
    """识别专名并加权。返回 {term: weight}。"""
    weighted: dict[str, float] = {}
    for pat in _DATE_PATTERNS:
        for m in pat.findall(query):
            weighted[m] = max(weighted.get(m, 0), 2.5)
    for pat in _CODE_PATTERNS:
        for m in pat.findall(query):
            weighted[m] = max(weighted.get(m, 0), 3.0)
    for pat in _LEVEL_PATTERNS:
        for m in pat.findall(query):
            weighted[m] = max(weighted.get(m, 0), 2.0)
    # 实体名列表精确/子串匹配
    for e in entities:
        if e in query and len(e) >= 2:
            weighted[e] = max(weighted.get(e, 0), 2.0)
    # 复合专名整体加权（3.0 > 子串实体 2.0）
    for m in _COMPOUND_SUFFIX_RE.finditer(query):
        term = m.group(0)
        if len(term) >= 4:
            weighted[term] = max(weighted.get(term, 0), 3.0)
    return weighted


def preprocess(query: str, entities: list[str]) -> PreprocessedQuery:
    """主入口：原 query + 实体名列表 → 预处理结果。"""
    # A1. 词典同义词扩展
    extra_terms: set[str] = set()
    for k, vs in SYNONYMS.items():
        if k in query:
            for v in vs:
                if v not in query:
                    extra_terms.add(v)

    # A2. 实体名反向模糊匹配（对 query 里的名词片段做）
    # 简单做法：按常见分隔符切片，对每个 ≥2字 的片段查实体
    fragments = [f for f in re.split(r"[，。、；：？！,;:\?\s]+", query) if len(f) >= 2]
    matched_entity_hits: list[str] = []
    for frag in fragments:
        hits = _fuzzy_match(frag, entities, max_dist=2)
        for h in hits:
            if h not in query and h not in extra_terms:
                extra_terms.add(h)
                matched_entity_hits.append(h)

    expanded = query
    if extra_terms:
        expanded = query + "；" + "，".join(sorted(extra_terms))

    # B. 专名识别 + 加权
    weighted = _extract_proper_nouns(query, entities)
    # 扩展出的实体也给中等权重
    for e in matched_entity_hits[:8]:
        weighted[e] = max(weighted.get(e, 0), 1.8)

    # ll_keywords = 加权词（给 LightRAG 图检索种子用）；专名优先，且把已选更具体
    # 专名当作泛化子串丢弃（有「智能客服系统」就不再带「客服」「客服系统」「系统」）
    ranked = sorted(weighted.items(), key=lambda kv: -kv[1])
    ll_kw: list[str] = []
    for t, _ in ranked:
        if any(t in kept or kept in t for kept in ll_kw):
            continue
        ll_kw.append(t)
        if len(ll_kw) >= 6:
            break
    hl_kw: list[str] = []  # 高级关键词留给 LightRAG keyword LLM

    return PreprocessedQuery(
        original=query,
        expanded=expanded,
        ll_keywords=ll_kw,
        hl_keywords=hl_kw,
        weighted_terms=weighted,
        matched_entities=matched_entity_hits,
    )


def load_entities(workspace: str) -> list[str]:
    """对外暴露：加载实体名列表（同步入口，缓存由调用方负责）。"""
    return _pg_entity_names(workspace)


async def load_entities_async(workspace: str) -> list[str]:
    """async 入口：已在 event loop 内时用。"""
    return await _pg_entity_names_async(workspace)