"""nDCG（归一化折损累计增益）：排序质量的标准对比指标。

从 context_precision 的 per_chunk 结果推导，零额外 LLM 成本。
- 相关度：直接用 LLM 裁判给的 per-chunk score（0.0~1.0 连续值）
- DCG@k = Σ (2^rel_i - 1) / log2(i+1)  （i 从 1 到 k，按当前排序）
- IDCG@k = 理想排序下的 DCG（所有 chunk 按相关度降序排列，取前 k）
- nDCG@k = DCG@k / IDCG@k

输出 0.0~1.0，越高排序越好。
"""
from __future__ import annotations

import math
from typing import Any


def _dcg(relevances: list[float]) -> float:
    """给定向量的 DCG（按当前顺序）。"""
    total = 0.0
    for i, rel in enumerate(relevances):
        rank = i + 1
        if rank == 1:
            total += rel
        else:
            total += rel / math.log2(rank + 1)
    return total


def ndcg_at_k(
    per_chunk: list[dict[str, Any]],
    k: int,
    score_key: str = "score",
) -> float | None:
    """计算 nDCG@k。

    Args:
        per_chunk: 每个 chunk 的评测结果，含 rank + score（相关度 0~1）
        k: 截断位置
        score_key: 相关度分数字段名

    Returns:
        nDCG@k (0.0~1.0)，没有有效 chunk 时返回 None
    """
    # 过滤掉裁判失败的（score 为 None）
    valid = [c for c in per_chunk if c.get(score_key) is not None]
    if not valid:
        return None

    # 取前 k 个（按当前 rank 排序）
    valid_sorted = sorted(valid, key=lambda c: c.get("rank", 999))
    top_k = valid_sorted[:k]
    relevances = [float(c[score_key]) for c in top_k]

    dcg = _dcg(relevances)

    # IDCG：所有有效 chunk 按相关度降序排列，取前 k 的 DCG
    ideal = sorted(valid, key=lambda c: float(c[score_key]), reverse=True)
    ideal_top_k = ideal[:k]
    ideal_relevances = [float(c[score_key]) for c in ideal_top_k]
    idcg = _dcg(ideal_relevances)

    if idcg < 1e-9:
        return 0.0

    return round(dcg / idcg, 4)


def compute_ndcg(
    per_chunk: list[dict[str, Any]],
    ks: list[int] | None = None,
) -> dict[str, float | None]:
    """计算多组 k 的 nDCG。

    Returns: {f"ndcg_at_{k}": value, ...}
    """
    if ks is None:
        ks = [5, 8]
    result = {}
    for k in ks:
        result[f"ndcg_at_{k}"] = ndcg_at_k(per_chunk, k)
    return result
