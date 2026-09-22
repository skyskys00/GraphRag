"""测试集加载与校验。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REQUIRED_FIELDS = {
    "id", "category", "question", "ground_truth",
    "key_facts", "must_have_docs", "must_not_have_docs",
    "difficulty", "source_docs", "tags",
}

VALID_CATEGORIES = {
    "fact_single", "fact_cross_doc", "proper_noun",
    "comparison", "table_numeric", "summary", "unanswerable",
}

VALID_DIFFICULTIES = {"easy", "medium", "hard"}


def load_testset(path: str | Path) -> dict[str, Any]:
    """加载并校验测试集 JSON。返回 {meta, questions}。"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"测试集不存在: {p}")

    with open(p, "r", encoding="utf-8") as f:
        raw = json.load(f)

    qs = raw.get("questions", [])
    if not qs:
        raise ValueError("测试集为空")

    errors: list[str] = []
    seen_ids: set[str] = set()
    for q in qs:
        qid = q.get("id", "?")
        missing = REQUIRED_FIELDS - set(q.keys())
        if missing:
            errors.append(f"{qid}: 缺少字段 {missing}")
        if qid in seen_ids:
            errors.append(f"{qid}: ID 重复")
        seen_ids.add(qid)
        if q.get("category") not in VALID_CATEGORIES:
            errors.append(f"{qid}: 无效 category={q.get('category')}")
        if q.get("difficulty") not in VALID_DIFFICULTIES:
            errors.append(f"{qid}: 无效 difficulty={q.get('difficulty')}")

    if errors:
        raise ValueError("测试集校验失败:\n  " + "\n  ".join(errors))

    return {
        "testset_id": raw.get("testset_id", p.stem),
        "collection": raw.get("collection", "default"),
        "domain": raw.get("domain", ""),
        "version": raw.get("version", "v1.0"),
        "description": raw.get("description", ""),
        "corpus_docs": raw.get("corpus_docs", []),
        "questions": qs,
    }


def group_by_category(questions: list[dict]) -> dict[str, list[dict]]:
    """按 category 分组。"""
    result: dict[str, list[dict]] = {}
    for q in questions:
        cat = q["category"]
        result.setdefault(cat, []).append(q)
    return result


def stats(questions: list[dict]) -> dict[str, Any]:
    """统计题目分布。"""
    from collections import Counter

    by_cat = Counter(q["category"] for q in questions)
    by_diff = Counter(q["difficulty"] for q in questions)
    return {
        "total": len(questions),
        "by_category": dict(by_cat),
        "by_difficulty": dict(by_diff),
    }
