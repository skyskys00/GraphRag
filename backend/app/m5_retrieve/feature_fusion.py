"""M5 精排：多路特征融合排序。

在 reranker 语义分基础上，融合数字匹配、数字密度、sparse 内积、召回位次等特征，
解决 cross-encoder 对表格/数值型数据理解弱的问题。

特征清单（按权重从大到小）：
- rerank_score：cross-encoder 语义相关性（主特征，0.50）
- numeric_match：query 数字 token 在 chunk 中的命中比例（0.15）
- sparse_score：bge-m3 sparse 内积（keyword 路原始分数，0.15）
- numeric_density：chunk 中数字的密度（数字型问题时，表格行 > 普通段落，0.10）
- rrf_score：三路召回 RRF 融合分（综合位次，0.10）

融合方式：加权线性和，各特征归一化到 [0, 1] 后加权求和。
"""
from __future__ import annotations

from typing import Any

from .query_preprocess import is_numeric_query, numeric_terms


def _min_max_norm(scores: dict[str, float]) -> dict[str, float]:
    """最小-最大归一化到 [0, 1]。全相同值时全部映射为 0。"""
    if not scores:
        return {}
    vals = list(scores.values())
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-9:
        return {k: 0.0 for k in scores}
    return {k: (v - lo) / (hi - lo) for k, v in scores.items()}


def _extract_number_tokens(text: str) -> list[str]:
    """从文本中提取纯数字 token（整数和小数），用于表格数字匹配。

    表格里的数字常不带单位（单位在列名里），需要纯数字层面的匹配。
    过滤掉 1 位数字（太常见，噪声多）。
    """
    import re
    nums = re.findall(r"\d[\d,]*(?:\.\d+)?", text)
    result = []
    for n in nums:
        clean = n.replace(",", "")
        if "." in clean or len(clean) >= 2:
            result.append(clean)
    return list(dict.fromkeys(result))


def _normalize_numeric_term(term: str) -> str:
    """从数字+单位 token 中提取纯数字部分，用于去重。

    '2023 年' → '2023'，'1250 亿元' → '1250'，'3.14%' → '3.14'
    """
    import re
    m = re.match(r"([\d,]+(?:\.\d+)?)", term.strip())
    if m:
        return m.group(1).replace(",", "")
    return term.strip()


def _numeric_match_score(
    query: str,
    chunk_contents: dict[str, str],
) -> dict[str, float]:
    """计算每个 chunk 的数字匹配度：query 中的数字在 chunk 中命中的比例。

    按数字值去重计数（"2023 年" 和纯数字 "2023" 算同一个数字），
    每个数字的得分：完整 token 命中得 1.0，仅纯数字命中得 0.5。
    总分 = 命中数字得分和 / 去重后数字总数。

    返回 {chunk_id: 0.0~1.0}。query 无数字时全部返回 0。
    """
    full_terms = numeric_terms(query)
    pure_nums_query = _extract_number_tokens(query)

    # 按数字值去重：key 是纯数字字符串，value 是完整 token（有则用，无则 None）
    unique_numbers: dict[str, str | None] = {}
    for term in full_terms:
        num_val = _normalize_numeric_term(term)
        if num_val not in unique_numbers:
            unique_numbers[num_val] = term
    for num in pure_nums_query:
        if num not in unique_numbers:
            unique_numbers[num] = None

    if not unique_numbers:
        return {cid: 0.0 for cid in chunk_contents}

    total = len(unique_numbers)
    result: dict[str, float] = {}
    for cid, content in chunk_contents.items():
        if not content:
            result[cid] = 0.0
            continue
        chunk_pure_nums = set(_extract_number_tokens(content))
        score = 0.0
        for num_val, full_term in unique_numbers.items():
            if full_term and full_term in content:
                score += 1.0  # 完整 token 命中（强信号）
            elif num_val in chunk_pure_nums:
                score += 0.5  # 仅纯数字命中（弱信号，表格中常见）
        result[cid] = score / total
    return result


def _numeric_density(content: str) -> float:
    """计算 chunk 中数字字符占总字符数的比例。

    表格行的数字密度通常远高于普通段落（数据表格 > 描述性文本），
    在数字型问题中这是一个有效的区分信号。
    """
    if not content:
        return 0.0
    digit_count = sum(1 for c in content if c.isdigit())
    return digit_count / len(content)


def fuse_and_rank(
    query: str,
    chunk_ids: list[str],
    chunk_meta: dict[str, dict[str, Any]],
    rerank_scores: dict[str, float],
    sparse_scores: dict[str, float],
    rrf_scores: dict[str, float],
    top_n: int,
    weights: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    """多特征融合排序，返回 top_n 个 {chunk_id, score, features}。

    特征：
    - rerank: cross-encoder 语义相似度（主特征）
    - numeric_match: query 数字在 chunk 中的命中比例（0~1）
    - numeric_density: chunk 数字密度（数字型问题时有效）
    - sparse: bge-m3 sparse 内积
    - rrf: 三路召回 RRF 融合分

    Args:
        query: 原始查询
        chunk_ids: 候选 chunk id 列表
        chunk_meta: sparse_doc["chunks"] 元数据，含 content / block_type 等
        rerank_scores: {chunk_id: cross_encoder 分数}
        sparse_scores: {chunk_id: sparse 内积分数}
        rrf_scores: {chunk_id: RRF 融合分}
        top_n: 返回前 N 个
        weights: 各特征权重，默认见下
    """
    w = weights or {
        "rerank": 0.50,
        "numeric_match": 0.15,
        "numeric_density": 0.10,
        "sparse": 0.15,
        "rrf": 0.10,
    }

    valid_ids = [cid for cid in chunk_ids if cid in chunk_meta]
    if not valid_ids:
        return []

    is_numeric = is_numeric_query(query)
    contents = {cid: chunk_meta[cid].get("content", "") for cid in valid_ids}

    # 计算各特征
    feat_rerank = {cid: rerank_scores.get(cid, 0.0) for cid in valid_ids}
    feat_sparse = {cid: sparse_scores.get(cid, 0.0) for cid in valid_ids}
    feat_rrf = {cid: rrf_scores.get(cid, 0.0) for cid in valid_ids}
    feat_numeric_match = _numeric_match_score(query, contents) if is_numeric else {cid: 0.0 for cid in valid_ids}
    feat_num_density = {cid: _numeric_density(contents[cid]) for cid in valid_ids} if is_numeric else {cid: 0.0 for cid in valid_ids}

    # 归一化到 [0, 1]
    norm_rerank = _min_max_norm(feat_rerank)
    norm_sparse = _min_max_norm(feat_sparse)
    norm_rrf = _min_max_norm(feat_rrf)
    # numeric_match 已经是 [0,1]，不归一化（否则全 0 时也会被拉成 0 没问题，但匹配少时会被放大）
    # numeric_density 归一化（在候选池内相对排名）
    norm_num_density = _min_max_norm(feat_num_density) if is_numeric else {cid: 0.0 for cid in valid_ids}

    # 加权融合
    fused: list[tuple[str, float, dict[str, float]]] = []
    for cid in valid_ids:
        score = (
            w["rerank"] * norm_rerank.get(cid, 0.0)
            + w["numeric_match"] * feat_numeric_match.get(cid, 0.0)
            + w["numeric_density"] * norm_num_density.get(cid, 0.0)
            + w["sparse"] * norm_sparse.get(cid, 0.0)
            + w["rrf"] * norm_rrf.get(cid, 0.0)
        )
        features = {
            "rerank": feat_rerank.get(cid, 0.0),
            "numeric_match": feat_numeric_match.get(cid, 0.0),
            "numeric_density": feat_num_density.get(cid, 0.0),
            "sparse": feat_sparse.get(cid, 0.0),
            "rrf": feat_rrf.get(cid, 0.0),
        }
        fused.append((cid, score, features))

    fused.sort(key=lambda x: x[1], reverse=True)

    return [
        {
            "chunk_id": cid,
            "score": score,
            "features": feats,
        }
        for cid, score, feats in fused[:top_n]
    ]
