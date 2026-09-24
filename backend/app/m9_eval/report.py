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

    def _avg_gr(lst: list[dict], key: str) -> float | None:
        """gold_rank 类指标的平均。"""
        vals = []
        for r in lst:
            gr = r.get("metrics", {}).get("gold_rank")
            if gr and gr.get(key) is not None:
                vals.append(gr[key])
        return round(sum(vals) / len(vals), 2) if vals else None

    def _avg_topk_recall(lst: list[dict], k: str) -> float | None:
        """平均 top_k_recall（按题平均 fact-level top-k 召回率）。"""
        vals = []
        for r in lst:
            gr = r.get("metrics", {}).get("gold_rank")
            if gr and gr.get("top_k_recall") and gr["top_k_recall"].get(k) is not None:
                vals.append(gr["top_k_recall"][k])
        return round(sum(vals) / len(vals), 4) if vals else None

    overall = {
        "context_recall": _avg(results, "context_recall"),
        "context_precision": _avg(results, "context_precision"),
        "context_precision_weighted": _avg(results, "context_precision_weighted"),
        # 双窗口指标
        "context_recall_top5": _avg(results, "context_recall_top5"),
        "context_recall_top8": _avg(results, "context_recall_top8"),
        "context_precision_top5": _avg(results, "context_precision_top5"),
        "context_precision_top8": _avg(results, "context_precision_top8"),
        "context_precision_weighted_top5": _avg(results, "context_precision_weighted_top5"),
        "context_precision_weighted_top8": _avg(results, "context_precision_weighted_top8"),
        # gold_rank
        "gold_rank_avg": _avg_gr(results, "avg_rank"),
        "gold_rank_median": _avg_gr(results, "median_rank"),
        "gold_rank_min": _avg_gr(results, "min_rank"),
        "gold_rank_max": _avg_gr(results, "max_rank"),
        "gold_rank_top1_recall": _avg_topk_recall(results, "1"),
        "gold_rank_top3_recall": _avg_topk_recall(results, "3"),
        "gold_rank_top5_recall": _avg_topk_recall(results, "5"),
        "gold_rank_top8_recall": _avg_topk_recall(results, "8"),
        # nDCG（排序质量，从 CP 的 per_chunk score 推导，零额外 LLM 成本）
        "ndcg_top5": _avg(results, "ndcg_top5"),
        "ndcg_top8": _avg(results, "ndcg_top8"),
        "ndcg": _avg(results, "ndcg"),
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

    def _build_cat_stats(lst: list[dict]) -> dict[str, Any]:
        return {
            "count": len(lst),
            "context_recall": _avg(lst, "context_recall"),
            "context_precision": _avg(lst, "context_precision"),
            "context_recall_top5": _avg(lst, "context_recall_top5"),
            "context_recall_top8": _avg(lst, "context_recall_top8"),
            "context_precision_top5": _avg(lst, "context_precision_top5"),
            "context_precision_top8": _avg(lst, "context_precision_top8"),
            "gold_rank_avg": _avg_gr(lst, "avg_rank"),
            "gold_rank_median": _avg_gr(lst, "median_rank"),
            "gold_rank_top5_recall": _avg_topk_recall(lst, "5"),
            "gold_rank_top8_recall": _avg_topk_recall(lst, "8"),
            "ndcg_top5": _avg(lst, "ndcg_top5"),
            "ndcg_top8": _avg(lst, "ndcg_top8"),
        }

    cat_stats = {}
    for cat, lst in sorted(by_category.items()):
        cat_stats[cat] = _build_cat_stats(lst)

    diff_stats = {}
    for diff, lst in sorted(by_difficulty.items()):
        diff_stats[diff] = _build_cat_stats(lst)

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
    lines.append("| 指标 | top5 | top8 |")
    lines.append("|---|---|---|")
    lines.append(f"| Context Recall | {overall['context_recall_top5']:.4f} | {overall['context_recall_top8']:.4f} |")
    lines.append(f"| Context Precision | {overall['context_precision_top5']:.4f} | {overall['context_precision_top8']:.4f} |")
    lines.append(f"| Context Precision (加权) | {overall['context_precision_weighted_top5']:.4f} | {overall['context_precision_weighted_top8']:.4f} |")
    lines.append(f"| nDCG | {overall['ndcg_top5']:.4f} | {overall['ndcg_top8']:.4f} |")
    gjf = overall.get('judge_failed_judgments', 0)
    if gjf:
        lines.append(f"| 裁判失败调用数 | {gjf} | — |")
    lines.append("")
    lines.append("## Gold Rank（事实最早出现的排名）")
    lines.append("")
    gra = overall.get('gold_rank_avg')
    grm = overall.get('gold_rank_median')
    grmin = overall.get('gold_rank_min')
    grmax = overall.get('gold_rank_max')
    if gra is not None:
        lines.append(f"- **平均 gold_rank**：{gra}")
        lines.append(f"- **中位 gold_rank**：{grm}")
        lines.append(f"- **min / max**：{grmin} / {grmax}")
        lines.append("")
        lines.append("| top-K | 事实覆盖率 |")
        lines.append("|---|---|")
        for k in ['1', '3', '5', '8']:
            v = overall.get(f'gold_rank_top{k}_recall')
            if v is not None:
                lines.append(f"| top{k} | {v:.4f} |")
    else:
        lines.append("（无 gold_rank 数据）")
    lines.append("")
    lines.append("## 按题型分布")
    lines.append("")
    lines.append("| 题型 | 题数 | Recall@5 | Recall@8 | Prec@5 | Prec@8 | nDCG@5 | GoldRank avg |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for cat, st in s["by_category"].items():
        gr_avg = st.get("gold_rank_avg", "—")
        gr_str = f"{gr_avg:.2f}" if isinstance(gr_avg, (int, float)) else "—"
        ndcg5 = st.get("ndcg_top5", 0.0)
        lines.append(
            f"| {cat} | {st['count']} | {st['context_recall_top5']:.4f} | {st['context_recall_top8']:.4f} | "
            f"{st['context_precision_top5']:.4f} | {st['context_precision_top8']:.4f} | {ndcg5:.4f} | {gr_str} |"
        )
    lines.append("")
    lines.append("## 按难度分布")
    lines.append("")
    lines.append("| 难度 | 题数 | Recall@5 | Prec@5 | GoldRank avg |")
    lines.append("|---|---|---|---|---|")
    for diff, st in s["by_difficulty"].items():
        gr_avg = st.get("gold_rank_avg", "—")
        gr_str = f"{gr_avg:.2f}" if isinstance(gr_avg, (int, float)) else "—"
        lines.append(
            f"| {diff} | {st['count']} | {st['context_recall_top5']:.4f} | "
            f"{st['context_precision_top5']:.4f} | {gr_str} |"
        )
    lines.append("")

    return "\n".join(lines)
