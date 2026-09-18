"""M8 v4.0 图谱导出：实体级（原）+ 文档级（新增）双层图谱。

实体级（level=entity）：LightRAG 图存储全量 + 软删过滤 + 6 类关系关键词分类。
文档级（level=document）：节点=文档，边=概念关联（Jaccard）+ 引用关系，话题聚类（社区发现）。

文档级数据完全从实体归属关系 + TextUnit 文本派生，不新增表，与 collection 隔离天然兼容。
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import asyncpg
import networkx as nx
from networkx.algorithms.community import greedy_modularity_communities

SEP = "<SEP>"


async def _chunk_to_doc(workspace: str) -> dict[str, str]:
    """chunk_id → full_doc_id。连接范式与 app.m5_retrieve.sparse_index._pg_chunks 一致。"""
    conn = await asyncpg.connect(
        host="127.0.0.1", port=5432, user="postgres", password="postgres", database="postgres"
    )
    try:
        rows = await conn.fetch(
            "SELECT id, full_doc_id FROM lightrag_doc_chunks WHERE workspace = $1", workspace
        )
        return {r["id"]: r["full_doc_id"] for r in rows}
    finally:
        await conn.close()


def _as_list(source_id: Any) -> list[str]:
    if not source_id:
        return []
    if isinstance(source_id, (list, tuple)):
        return [str(s) for s in source_id]
    return [s for s in str(source_id).split(SEP) if s]


def _docs_of(source_ids: list[str], chunk_to_doc: dict[str, str]) -> set[str]:
    return {chunk_to_doc[c] for c in source_ids if c in chunk_to_doc}


def _keep(doc_set: set[str], excluded: set[str], allowed: set[str] | None) -> bool:
    """归属文档过滤：
    - allowed 有值：至少一个归属文档在白名单内 → 保留
    - excluded 有值：全部归属文档都在排除集合 → 剔除
    - 无法归因（doc_set 空）→ 保守保留
    """
    if not doc_set:
        return True
    if allowed is not None and not doc_set.intersection(allowed):
        return False
    if excluded and doc_set.issubset(excluded):
        return False
    return True


async def collect_graph(
    rag: Any,
    excluded_docs: set[str],
    doc_id: str | None = None,
    allowed_docs: set[str] | None = None,
    workspace: str = "lightrag_m4",
    top_n: int = 0,
) -> dict[str, Any]:
    """图数据导出。doc_id 指定时仅保留该文档贡献的实体（按文档维度过滤，见 M8 v2.3）。
    allowed_docs 白名单：如有，则只保留归属至少一个白名单文档的实体/边。
    workspace 见 M8 v3：按 collection 传各自 workspace（默认 lightrag_m4）。
    top_n：Level=entity 时有效，>0 且节点总数 >= 40 时只保留 PageRank top_n 的节点
    （及其之间的边）；0 或节点少时返回全部。
    """
    chunk_to_doc = await _chunk_to_doc(workspace)
    excluded = set(excluded_docs or ())

    raw_nodes = await rag.chunk_entity_relation_graph.get_all_nodes()
    raw_edges = await rag.chunk_entity_relation_graph.get_all_edges()

    keep_ids: set[str] = set()
    nodes: list[dict[str, Any]] = []
    for n in raw_nodes:
        source_ids = _as_list(n.get("source_id"))
        doc_set = _docs_of(source_ids, chunk_to_doc)
        if not _keep(doc_set, excluded, allowed_docs):
            continue
        if doc_id is not None and doc_id not in doc_set:
            continue
        keep_ids.add(n["id"])
        raw_type = n.get("entity_type") or "unknown"
        nodes.append({
            "id": n["id"],
            "entity_type": raw_type,
            # 归一到 7 大类，供前端类型过滤
            "entity_group": normalize_entity_type(raw_type),
            "description": n.get("description") or "",
            "docs": sorted(doc_set),
            # chunks 保留原始 chunk-id 列表，供前端「引用 → 图谱」反查高亮
            "chunks": sorted(source_ids),
        })

    edges: list[dict[str, Any]] = []
    node_edges: list[tuple[str, str, float]] = []
    for e in raw_edges:
        src, tgt = e.get("source"), e.get("target")
        if src in keep_ids and tgt in keep_ids:
            w = float(e.get("weight") or 0)
            desc = e.get("description") or e.get("relation_name") or ""
            rel_type, rel_type_name = classify_relation(desc)
            node_edges.append((src, tgt, w))
            edges.append({
                "source": src,
                "target": tgt,
                "relation": desc,
                "weight": w,
                "rel_type": rel_type,
                "rel_type_name": rel_type_name,
            })

    # 核心节点筛选：节点多且 top_n>0 时按 PageRank 取 top_n，只保留它们之间的边
    if top_n > 0 and len(nodes) >= 40 and len(nodes) > top_n:
        pr = pagerank_scores([n["id"] for n in nodes], node_edges)
        for n in nodes:
            n["pagerank"] = round(pr.get(n["id"], 0.0), 6)
        nodes.sort(key=lambda n: pr.get(n["id"], 0.0), reverse=True)
        top_ids = {n["id"] for n in nodes[:top_n]}
        nodes = [n for n in nodes if n["id"] in top_ids]
        edges = [e for e in edges if e["source"] in top_ids and e["target"] in top_ids]

    return {
        "level": "entity",
        "nodes": nodes,
        "edges": edges,
        "meta": {"node_count": len(nodes), "edge_count": len(edges)},
    }


# ---------- 文档级图谱（M8 v4.0 Phase 1 ----------

# 话题聚类调色板（8 色，柔和暖色系，与整体浅暖风格统一
_CLUSTER_PALETTE = [
    "#e8a87c",  # 暖橙
    "#7fb3d5",  # 柔蓝
    "#98d5b0",  # 薄荷绿
    "#d4a5d4",  # 淡紫
    "#f0c987",  # 杏黄
    "#f29999",  # 珊瑚粉
    "#a8d8a0",  # 草绿
    "#c9b6e4",  # 薰衣草
]

# 概念关联边最小 Jaccard 阈值（低于此值的边不画，避免全连接毛球）
_JACCARD_THRESHOLD = 0.05


def _doc_entity_sets(nodes: list[dict[str, Any]]) -> dict[str, set[str]]:
    """从实体节点列表提取「文档 → 实体集合」映射。"""
    doc_entities: dict[str, set[str]] = defaultdict(set)
    for n in nodes:
        for doc_id in n.get("docs", []):
            doc_entities[doc_id].add(n["id"])
    return doc_entities


def _doc_entity_typed(nodes: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    """从实体节点提取「文档 → {实体名: 实体大类}」映射，供话题聚类加权。"""
    doc_entities: dict[str, dict[str, str]] = defaultdict(dict)
    for n in nodes:
        group = n.get("entity_group") or normalize_entity_type(n.get("entity_type") or "")
        for doc_id in n.get("docs", []):
            doc_entities[doc_id][n["id"]] = group
    return doc_entities


# 关键词聚类：不同实体类型的权重（核心概念类权重高，通用类权重低）
_ENTITY_GROUP_WEIGHT = {
    "组织": 2.0,
    "产品/项目": 2.0,
    "事件": 2.0,
    "概念": 1.5,
    "人物": 1.0,
    "地点": 0.8,
    "其他": 0.5,
}
# 文档名/标题关键词的额外权重（用户要求：文档名有实际意义时加权）
_DOC_NAME_WEIGHT = 4.0
# 话题聚类相似度阈值（低于此值的文档对不入聚类图）
_CLUSTER_SIM_THRESHOLD = 0.10
# 簇名停用词：时间碎片、通用组织词、过于泛化的词（不参与命名，但仍参与相似度计算）
_CLUSTER_NAME_STOPWORDS = {
    "年", "月", "日", "季度", "上季度", "本季度",
    "第一季度", "第二季度", "第三季度", "第四季度",
    "一季度", "二季度", "三季度", "四季度",
    "上半年", "下半年", "月底", "月初",
    "公司", "集团", "有限", "部门", "部", "组", "中心", "处", "科", "室",
    "产品", "项目", "会议", "报告", "复盘", "方案",
}


def _normalize_keyword(kw: str) -> str:
    """归一化关键词：去标点/空白/常见后缀，便于跨文档匹配近似词。"""
    kw = kw.strip().lower()
    # 去常见标点与空白
    kw = re.sub(r"[，。、；：""''（）\[\]【】《》·\s\-_/\\.]+", "", kw)
    # 纯日期词直接丢弃（如 "2024年10月"、"第三季度"、"10月5日"）
    if re.fullmatch(r"\d*年\d*月\d*日?", kw):
        return ""
    if re.fullmatch(r"[第\d一二三四五六七八九十]+季度", kw):
        return ""
    if re.fullmatch(r"\d*月\d*[日号]?", kw) and len(kw) <= 6:
        return ""
    # 去通用组织/部门后缀
    for suf in ("公司", "有限", "集团", "部", "组", "中心", "处", "科", "室"):
        if len(kw) > 2 and kw.endswith(suf):
            kw = kw[: -len(suf)]
    # 去数字/百分比开头
    kw = re.sub(r"^\d+%?", "", kw)
    # 去掉以"年/月/日"开头的残留碎片（剥掉数字后产生）
    if re.fullmatch(r"[年月日]\w*", kw) and len(kw) <= 6:
        return ""
    return kw if len(kw) >= 2 else ""


def _keywords_from_doc_name(filename: str) -> list[str]:
    """从文档文件名提取有意义的关键词（去扩展名、按常用分隔符切分）。"""
    name = Path(filename).stem if "." in filename else filename
    # 按分隔符切段
    parts = re.split(r"[_\-\s·.]+", name)
    kws: list[str] = []
    for p in parts:
        if not p:
            continue
        # 跳过纯数字/太短
        if re.fullmatch(r"[\d\.%]+", p):
            continue
        if len(p) < 2:
            continue
        kws.append(p)
        # 长词再按中文常见词切（简单 n-gram）
        if len(p) >= 4:
            kws.append(p[:2])
            kws.append(p[-2:])
    return kws


def _doc_keyword_weights(
    doc_id: str,
    entities: dict[str, str],  # entity_name -> entity_group
    doc_label: str,
) -> Counter[str]:
    """为单篇文档构建加权关键词计数器。"""
    weights: Counter[str] = Counter()
    # 1) 实体关键词（按类型加权 + 归一化）
    for ent_name, group in entities.items():
        kw = _normalize_keyword(ent_name)
        if not kw:
            continue
        w = _ENTITY_GROUP_WEIGHT.get(group, 1.0)
        weights[kw] += w
    # 2) 文档名/标题关键词（高权重）
    if doc_label and not re.fullmatch(r"[0-9a-f]{8,}", Path(doc_label).stem):
        for kw_raw in _keywords_from_doc_name(doc_label):
            kw = _normalize_keyword(kw_raw)
            if kw:
                weights[kw] += _DOC_NAME_WEIGHT
    return weights


def _cosine_sim(a: Counter[str], b: Counter[str]) -> float:
    """两个权重 Counter 的余弦相似度。"""
    keys = a.keys() & b.keys()
    if not keys:
        return 0.0
    dot = sum(a[k] * b[k] for k in keys)
    norm_a = sum(v * v for v in a.values()) ** 0.5
    norm_b = sum(v * v for v in b.values()) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _concept_edges(doc_entities: dict[str, set[str]]) -> list[dict[str, Any]]:
    """计算文档间 Jaccard 相似度，返回概念关联边列表。

    返回边属性：source / target / type=concept / weight=Jaccard / shared_entities=[前5个]
    """
    doc_ids = sorted(doc_entities.keys())
    edges: list[dict[str, Any]] = []
    for i in range(len(doc_ids)):
        a = doc_ids[i]
        set_a = doc_entities[a]
        if not set_a:
            continue
        for j in range(i + 1, len(doc_ids)):
            b = doc_ids[j]
            set_b = doc_entities[b]
            if not set_b:
                continue
            inter = set_a & set_b
            union = set_a | set_b
            if not union:
                continue
            jaccard = len(inter) / len(union)
            if jaccard < _JACCARD_THRESHOLD:
                continue
            # 共享实体按出现频次排序（两边文档内都有的核心概念），取前 5 个
            shared_sorted = sorted(inter)[:5]
            edges.append({
                "source": a,
                "target": b,
                "type": "concept",
                "weight": round(jaccard, 4),
                "shared_entities": shared_sorted,
            })
    return edges


# ---------- 引用关系检测（Phase 2）----------

def _normalize_doc_name(filename: str) -> list[str]:
    """从文件名提取可搜索的关键词列表。

    返回多个变体（原名 / 去扩展名 / 去标点 / 编号提取），提高召回率。
    短名（<2 字）直接跳过，避免误匹配。
    """
    name = Path(filename).stem  # 去扩展名
    variants: list[str] = []
    # 原文件名（去扩展后）
    if len(name) >= 2:
        variants.append(name)
    # 去标点和空格后的纯文本
    cleaned = re.sub(r"[^\w一-鿿]", "", name)
    if len(cleaned) >= 2 and cleaned != name:
        variants.append(cleaned)
    # 编号提取：如 PRD-V2.1、SOP-001、Q3-复盘报告 → 提取 PRD、SOP-001 等
    pattern_matches = re.findall(r"[A-Za-z]+[-_]?[\d.]+", name)
    for m in pattern_matches:
        if len(m) >= 3:
            variants.append(m)
    return variants


def _load_doc_text(chunks_dir: Path, doc_id: str) -> list[tuple[str, str]]:
    """加载文档的所有 TextUnit 文本，返回 [(chunk_id, content), ...]。"""
    f = chunks_dir / f"{doc_id}.jsonl"
    if not f.exists():
        return []
    units: list[tuple[str, str]] = []
    for line in f.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            u = json.loads(line)
            content = u.get("content") or ""
            unit_id = u.get("text_unit_id") or ""
            if content:
                units.append((unit_id, content))
        except json.JSONDecodeError:
            continue
    return units

def _infer_doc_filename(chunks_dir: Path | None, doc_id: str) -> str:
    """从 M2 切片首条 file_path 推断文档名（注册表缺失时的回退）。"""
    if chunks_dir is not None:
        f = chunks_dir / f"{doc_id}.jsonl"
        if f.exists():
            try:
                for line in f.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        fp = json.loads(line).get("file_path")
                        if fp:
                            return Path(fp).name
            except json.JSONDecodeError:
                pass
    return doc_id


def _citation_edges(
    doc_ids: list[str],
    doc_meta: dict[str, dict[str, Any]],
    chunks_dir: Path,
) -> list[dict[str, Any]]:
    """检测文档间的引用关系，返回有向 citation 边列表。

    对每个文档 A 的 TextUnit 文本，搜索其他文档 B 的名字/编号变体。
    匹配到则生成边 A→B（A 引用 B），含引用次数和首个 snippet。
    """
    # 为每个文档构建搜索关键词
    doc_keywords: dict[str, list[str]] = {}
    for doc_id in doc_ids:
        meta = doc_meta.get(doc_id, {})
        filename = meta.get("filename") or doc_id
        kws = _normalize_doc_name(filename)
        if kws:
            doc_keywords[doc_id] = kws

    if not doc_keywords:
        return []

    edges: list[dict[str, Any]] = []
    for src_id in doc_ids:
        units = _load_doc_text(chunks_dir, src_id)
        if not units:
            continue
        src_text_all = " ".join(c for _, c in units)
        for tgt_id, keywords in doc_keywords.items():
            if tgt_id == src_id:
                continue
            # 统计匹配次数 + 找到第一个匹配片段
            total_count = 0
            first_snippet = ""
            for kw in keywords:
                # 中文/混合词用子串匹配，英文编号用词边界
                if re.search(r"[A-Za-z]", kw):
                    pattern = re.compile(re.escape(kw), re.IGNORECASE)
                else:
                    pattern = re.compile(re.escape(kw))
                count = len(pattern.findall(src_text_all))
                if count > 0 and not first_snippet:
                    # 找第一个匹配的 snippet（前后各取 20 字）
                    m = pattern.search(src_text_all)
                    if m:
                        start = max(0, m.start() - 20)
                        end = min(len(src_text_all), m.end() + 20)
                        snippet = src_text_all[start:end].strip()
                        if start > 0:
                            snippet = "…" + snippet
                        if end < len(src_text_all):
                            snippet = snippet + "…"
                        first_snippet = snippet
                total_count += count
            if total_count > 0:
                edges.append({
                    "source": src_id,
                    "target": tgt_id,
                    "type": "citation",
                    "weight": float(total_count),
                    "snippet": first_snippet,
                })
    return edges


# ---------- 实体级关系分类（6 类 + 关键词规则，Phase 2 方案 A）----------

RELATION_TYPES: list[tuple[int, str, list[str]]] = [
    (1, "归属/组成", ["属于", "组成", "下设", "分公司", "部门", "隶属于", "包含", "成员", "归属", "分为", "包括"]),
    (2, "动作/执行", ["负责", "处理", "执行", "推进", "开展", "管理", "运营", "生产", "销售", "研发", "制定", "组织", "实施", "主导", "配合"]),
    (3, "因果/影响", ["导致", "带来", "提升", "影响", "因为", "所以", "促进", "推动", "使得", "造成", "提高", "降低", "引发", "从而"]),
    (4, "时间/先后", ["之前", "之后", "随后", "接着", "下一步", "首先", "然后", "最后", "期间", "同时", "此前", "此后", "先后"]),
    (5, "同义/相关", ["又称", "简称", "即", "也就是", "又名", "亦称", "俗称", "即指", "所谓"]),
    (6, "属性/数值", ["为", "达到", "等于", "占比", "共计", "总计", "金额", "数量", "比例", "高达", "约", "大约"]),
]


def classify_relation(description: str) -> tuple[int, str]:
    """根据关系描述文本的关键词，归入 6 类之一。

    命中第一个匹配类型即返回（按优先级从高到低）。未命中返回 (0, "未分类")。
    """
    if not description:
        return 0, "未分类"
    for type_id, type_name, keywords in RELATION_TYPES:
        for kw in keywords:
            if kw in description:
                return type_id, type_name
    return 0, "未分类"


# ---------- 实体类型归一化 + PageRank 筛选（Phase 3）----------

# LightRAG 抽的类型名可能不统一（organization/org/组织），归一到 7 大类
_ENTITY_TYPE_GROUPS: list[tuple[str, list[str]]] = [
    ("组织", ["organization", "org", "组织", "公司", "企业", "机构", "部门", "团队", "单位"]),
    ("人物", ["person", "people", "人物", "人", "员工", "成员", "个人", "owner", "负责人"]),
    ("产品/项目", ["product", "project", "产品", "项目", "服务", "工具", "系统", "平台"]),
    ("概念", ["concept", "concept, theory", "概念", "理论", "方法", "术语", "知识", "指标", "metric"]),
    ("事件", ["event", "事件", "活动", "会议", "行动", "事故发生"]),
    ("地点", ["location", "地点", "地区", "区域", "位置", "城市", "国"]),
    ("其他", []),
]

_DEFAULT_ENTITY_GROUP = "其他"


def normalize_entity_type(entity_type: str) -> str:
    """实体类型归一到 7 大类（组织/人物/产品项目/概念/事件/地点/其他）。

    输入 LightRAG 抽取的类型名（中英混杂、大小写不一），输出统一大类中文名。
    """
    if not entity_type:
        return _DEFAULT_ENTITY_GROUP
    t = entity_type.strip().lower()
    for group_name, keywords in _ENTITY_TYPE_GROUPS:
        if t in keywords or any(kw == t for kw in keywords):
            return group_name
    return _DEFAULT_ENTITY_GROUP


def pagerank_scores(
    node_ids: list[str],
    node_edges: list[tuple[str, str, float]],
) -> dict[str, float]:
    """在实体子图上跑 PageRank，返回 node_id → 分数。

    无向图（实体关系本质无向），weight 用边的 weight。无法构建图（孤点）时返回均分。
    """
    g = nx.Graph()
    g.add_nodes_from(node_ids)
    for src, tgt, w in node_edges:
        if src in g and tgt in g:
            g.add_edge(src, tgt, weight=w if w > 0 else 1.0)
    if g.number_of_edges() == 0:
        return {n: 1.0 / len(node_ids) if node_ids else 0.0 for n in node_ids}
    pr = nx.pagerank(g, weight="weight")
    return {n: pr.get(n, 0.0) for n in node_ids}


def _cluster_docs(
    doc_ids: list[str],
    doc_entities_typed: dict[str, dict[str, str]],
    doc_labels: dict[str, str],
) -> tuple[dict[str, int], list[dict[str, Any]],
]:
    """基于关键词加权相似度的话题聚类。

    算法：
    1) 每篇文档构建加权关键词向量（实体按类型加权 + 文档名/标题关键词高权重，关键词归一化便于匹配近似词）。
    2) 文档间算余弦相似度，≥阈值连边（边权=相似度）。
    3) 在相似度图上跑 greedy_modularity_communities 社区发现。
    4) 簇名取该簇 top 关键词（文档名关键词优先）拼接。

    doc_entities_typed: doc_id -> {entity_name: entity_group}
    doc_labels: doc_id -> filename（用于文档名加权 + 节点 label）
    """
    # 1) 构建每篇文档的加权关键词
    doc_kws: dict[str, Counter[str]] = {}
    for d in doc_ids:
        label = doc_labels.get(d, "")
        doc_kws[d] = _doc_keyword_weights(d, doc_entities_typed.get(d, {}), label)

    # 2) 构建相似度图
    g = nx.Graph()
    for d in doc_ids:
        g.add_node(d)

    sim_edges: list[tuple[str, str, float]] = []
    for i in range(len(doc_ids)):
        a = doc_ids[i]
        if not doc_kws[a]:
            continue
        for j in range(i + 1, len(doc_ids)):
            b = doc_ids[j]
            if not doc_kws[b]:
                continue
            sim = _cosine_sim(doc_kws[a], doc_kws[b])
            if sim >= _CLUSTER_SIM_THRESHOLD:
                g.add_edge(a, b, weight=sim)
                sim_edges.append((a, b, sim))

    # 3) 社区发现
    if g.number_of_edges() == 0 or len(doc_ids) <= 1:
        clusters_list = [{
            "id": 0,
            "name": "全部文档",
            "doc_count": len(doc_ids),
            "color": _CLUSTER_PALETTE[0],
        }]
        return {d: 0 for d in doc_ids}, clusters_list

    communities = greedy_modularity_communities(g, weight="weight")
    doc_to_cluster: dict[str, int] = {}
    clusters_list: list[dict[str, Any]] = []

    for cid, comm in enumerate(communities):
        comm_docs = list(comm)
        # 4) 簇命名：合并簇内所有文档关键词权重 → 去冗余 → 取 top 2-3
        cluster_kws: Counter[str] = Counter()
        for d in comm_docs:
            cluster_kws.update(doc_kws.get(d, {}))
        # 排序后过滤：去停用词 → 去被包含的短词 → 取 top 3
        sorted_kws = sorted(cluster_kws.items(), key=lambda x: -x[1])
        selected: list[str] = []
        for kw, _ in sorted_kws:
            if len(kw) < 2:
                continue
            if kw in _CLUSTER_NAME_STOPWORDS:
                continue
            # 跳过已选词的子串或超串（避免"季度"和"季度销售业绩复盘"同时出现）
            if any(kw in s or s in kw for s in selected):
                continue
            selected.append(kw)
            if len(selected) >= 3:
                break
        name = "-".join(selected) if selected else f"话题 {cid + 1}"

        clusters_list.append({
            "id": cid,
            "name": name,
            "doc_count": len(comm_docs),
            "color": _CLUSTER_PALETTE[cid % len(_CLUSTER_PALETTE)],
        })
        for doc_id in comm_docs:
            doc_to_cluster[doc_id] = cid

    # 孤立节点（相似度低于阈值的文档）归到"其他"
    unclustered = [d for d in doc_ids if d not in doc_to_cluster]
    if unclustered:
        cid = len(clusters_list)
        doc_to_cluster.update({d: cid for d in unclustered})
        clusters_list.append({
            "id": cid,
            "name": "其他",
            "doc_count": len(unclustered),
            "color": "#a89b8d",
        })

    return doc_to_cluster, clusters_list


async def collect_document_graph(
    rag: Any,
    excluded_docs: set[str],
    allowed_docs: set[str] | None,
    workspace: str,
    doc_meta: dict[str, dict[str, Any]] | None = None,
    chunks_dir: Path | None = None,
) -> dict[str, Any]:
    """文档级图谱：节点=文档，边=概念关联（Jaccard）+ 引用关系，话题聚类（社区发现）。

    doc_meta: doc_id -> {filename, created_at, ...}，可选。用于节点 label/时间，
    且作为「可见文档」白名单：节点只保留注册表有记录的文档（幽灵文档/重复索引不显示）。
    不传（纯离线建库、无注册表）则回退全量显示，label 从 chunks file_path 推断。
    chunks_dir: TextUnit JSONL 目录，传了才检测引用关系边。
    """
    # 复用实体级的节点提取+过滤逻辑，拿到过滤后的实体节点（含 docs 归属）
    chunk_to_doc = await _chunk_to_doc(workspace)
    excluded = set(excluded_docs or ())

    raw_nodes = await rag.chunk_entity_relation_graph.get_all_nodes()

    keep_nodes: list[dict[str, Any]] = []
    for n in raw_nodes:
        source_ids = _as_list(n.get("source_id"))
        doc_set = _docs_of(source_ids, chunk_to_doc)
        if not _keep(doc_set, excluded, allowed_docs):
            continue
        keep_nodes.append({
            "id": n["id"],
            "docs": sorted(doc_set),
            "entity_type": n.get("entity_type") or "unknown",
            "entity_group": normalize_entity_type(n.get("entity_type") or ""),
        })

    # 文档 → 实体集合（用于 Jaccard 概念边）
    doc_entities = _doc_entity_sets(keep_nodes)
    # 文档 → {实体名: 类型大类}（用于关键词加权聚类）
    doc_entities_typed = _doc_entity_typed(keep_nodes)

    # 关键修复：文档级节点只保留「文档管理可见文档」（doc_meta 注册表有记录）——
    # 幽灵文档（chunk 索引残留但从未走上传接口、注册表缺失）无法预览/删除，且与
    # 注册文档可能重复索引（同文档建库两遍），不应作为文档节点出现，与实体级白名单过滤对齐。
    # doc_meta 为空（纯离线建库、无注册表）时回退全量显示。
    meta_ids = set((doc_meta or {}).keys())
    if meta_ids:
        doc_entities = {d: es for d, es in doc_entities.items() if d in meta_ids}
        doc_entities_typed = {d: es for d, es in doc_entities_typed.items() if d in meta_ids}

    # 概念关联边（Jaccard，纯展示用；话题聚类改为关键词加权独立计算）
    concept_edges = _concept_edges(doc_entities)

    # 话题聚类：基于关键词加权余弦相似度（实体按类型加权 + 文档名/标题高权重）
    doc_ids = sorted(doc_entities.keys())
    doc_labels: dict[str, str] = {}
    for d in doc_ids:
        meta = (doc_meta or {}).get(d, {})
        doc_labels[d] = meta.get("filename") or _infer_doc_filename(chunks_dir, d)
    doc_to_cluster, clusters = _cluster_docs(doc_ids, doc_entities_typed, doc_labels)

    # 组装节点
    nodes: list[dict[str, Any]] = []
    for doc_id in doc_ids:
        meta = (doc_meta or {}).get(doc_id, {})
        # label 优先注册表 filename，其次从 M2 chunk file_path 推断
        filename = meta.get("filename") or _infer_doc_filename(chunks_dir, doc_id)
        nodes.append({
            "id": doc_id,
            "label": filename,
            "entity_count": len(doc_entities[doc_id]),
            "cluster_id": doc_to_cluster.get(doc_id, -1),
            "created_at": meta.get("created_at") or "",
        })

    edges: list[dict[str, Any]] = list(concept_edges)

    # 引用关系边（chunks_dir 传了才做）
    if chunks_dir is not None:
        citation_edges = _citation_edges(doc_ids, doc_meta or {}, chunks_dir)
        edges.extend(citation_edges)

    return {
        "level": "document",
        "nodes": nodes,
        "edges": edges,
        "clusters": clusters,
        "meta": {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "cluster_count": len(clusters),
        },
    }