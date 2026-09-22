"""评测报告生成：汇总指标 → JSON + Markdown。"""
from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Any


def build_report(
    testset_meta: dict[str, Any],
    results: list[dict[str, Any]],
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """从逐题结果汇总总报告。"""
    total = len(results)

    # 分类汇总
    by_category: dict[str, list[dict]] = defaultdict(list)
    by_difficulty: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        cat = r.get("category", "unknown")
        diff = r.get("difficulty", "unknown")
        by_category[cat].append(r)
        by_difficulty[diff].append(r)

    def _avg(lst: list[dict], key: str) -> float:
        vals = [r.get("metrics", {}).get(key, 0.0) for r in lst if r.get("metrics", {}).get(key) is not None]
        return round(sum(vals) / len(vals), 4) if vals else 0.0

    overall = {
        "context_recall": _avg(results, "context_recall"),
        "context_precision": _avg(results, "context_precision"),
        "context_precision_weighted": _avg(results, "context_precision_weighted"),
    }

    # LLM 裁判失败统计（失败项已从均分排除，单列供追溯）
    judge_failed_judgments = 0
    judge_failed_questions = 0
    for r in results:
        m = r.get("metrics", {})
        n = (m.get("context_recall_detail") or {}).get("failed_facts", 0)
        n += (m.get("context_precision_detail") or {}).get("failed_chunks", 0)
        if n:
            judge_failed_questions += 1
        judge_failed_judgments += n
    overall["judge_failed_judgments"] = judge_failed_judgments

    cat_stats = {}
    for cat, lst in sorted(by_category.items()):
        cat_stats[cat] = {
            "count": len(lst),
            "context_recall": _avg(lst, "context_recall"),
            "context_precision": _avg(lst, "context_precision"),
        }

    diff_stats = {}
    for diff, lst in sorted(by_difficulty.items()):
        diff_stats[diff] = {
            "count": len(lst),
            "context_recall": _avg(lst, "context_recall"),
            "context_precision": _avg(lst, "context_precision"),
        }

    summary = {
        "total_questions": total,
        "overall": overall,
        "by_category": cat_stats,
        "by_difficulty": diff_stats,
        "judge_failed_questions": judge_failed_questions,
    }

    return {
        "testset_id": testset_meta.get("testset_id", ""),
        "domain": testset_meta.get("domain", ""),
        "version": testset_meta.get("version", ""),
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "config": config or {},
        "summary": summary,
        "questions": results,
    }


def save_report(report: dict[str, Any], path: str | Path) -> Path:
    """保存 JSON 报告。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return p


def to_markdown(report: dict[str, Any]) -> str:
    """生成人类可读的 Markdown 摘要。"""
    s = report["summary"]
    overall = s["overall"]

    lines = []
    lines.append(f"# 评测报告：{report.get('testset_id', '')}")
    lines.append("")
    lines.append(f"- **领域**：{report.get('domain', '')}")
    lines.append(f"- **题目数**：{s['total_questions']}")
    lines.append(f"- **时间**：{report.get('timestamp', '')}")
    lines.append(f"- **配置**：{json.dumps(report.get('config', {}), ensure_ascii=False)}")
    lines.append("")
    lines.append("## 总体指标")
    lines.append("")
    lines.append("| 指标 | 分值 |")
    lines.append("|---|---|")
    lines.append(f"| Context Recall | {overall['context_recall']:.4f} |")
    lines.append(f"| Context Precision | {overall['context_precision']:.4f} |")
    lines.append(f"| Context Precision (加权) | {overall['context_precision_weighted']:.4f} |")
    gjf = overall.get('judge_failed_judgments', 0)
    if gjf:
        lines.append(f"| 裁判失败调用数 | {gjf} |")
    lines.append("")
    lines.append("## 按题型分布")
    lines.append("")
    lines.append("| 题型 | 题数 | Context Recall | Context Precision |")
    lines.append("|---|---|---|---|")
    for cat, st in s["by_category"].items():
        lines.append(f"| {cat} | {st['count']} | {st['context_recall']:.4f} | {st['context_precision']:.4f} |")
    lines.append("")
    lines.append("## 按难度分布")
    lines.append("")
    lines.append("| 难度 | 题数 | Context Recall | Context Precision |")
    lines.append("|---|---|---|---|")
    for diff, st in s["by_difficulty"].items():
        lines.append(f"| {diff} | {st['count']} | {st['context_recall']:.4f} | {st['context_precision']:.4f} |")
    lines.append("")

    return "\n".join(lines)
