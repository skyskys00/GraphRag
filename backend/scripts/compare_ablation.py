"""召回路数对照（ablation）对比脚本。

读 admin 30 题三组召回路数报告 + 正式基线，输出总体/类目/gold_rank/逐题一致率对比，
并将「每加一路」的增量归因为 Δ(full-vector) / Δ(vg-vector) / Δ(full-vg)。

用法：
    python scripts/compare_ablation.py [--base <baseline.json>] [--vector-only ... --vector-graph ... --full ...]
默认读 2026-09-29 三份 + 2026-09-27 finalcheck 基线。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

REPORTS = Path("tests/reports")

DEFAULT_PATHS = {
    "vector": "run_ablation_admin_vector_20260929.json",
    "vector_graph": "run_ablation_admin_vector_graph_20260929.json",
    "full": "run_ablation_admin_graph_vector_keyword_20260929.json",
    "baseline": "run_retrieval_finalcheck_admin30_20260927.json",
}

SUMMARY_KEYS = [
    ("context_recall_top5", "Recall@5"),
    ("context_recall_top8", "Recall@8"),
    ("context_precision_top5", "Prec@5"),
    ("context_precision_top8", "Prec@8"),
    ("context_precision_weighted_top5", "wPrec@5"),
    ("ndcg_top5", "nDCG@5"),
    ("gold_rank_avg", "GR avg"),
    ("gold_rank_top1_recall", "GR top1"),
    ("gold_rank_top3_recall", "GR top3"),
    ("gold_rank_top8_recall", "GR top8"),
]


def load(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def resolve(path: str) -> Path:
    """CLI 传完整路径（含 tests/）直接用；否则视为 reports/ 下的文件名。"""
    p = Path(path)
    return p if p.is_absolute() or "tests/" in str(p) else REPORTS / p


def overall_row(report: dict) -> dict[str, float]:
    return report["summary"]["overall"]


def category_rows(report: dict) -> dict[str, dict]:
    return report["summary"]["by_category"]


def print_overall(paths: dict[str, Path]) -> None:
    reports = {k: load(p) for k, p in paths.items()}
    print("\n## 总体指标（admin 30 题）\n")
    header = ["指标"] + [k for k in paths]
    print("| " + " | ".join(header) + " |")
    print("|" + "---|" * len(header))
    for key, label in SUMMARY_KEYS:
        vals = [f"{overall_row(reports[k]).get(key, 0.0):.4f}" for k in paths]
        print(f"| {label} | " + " | ".join(vals) + " |")


def print_deltas(paths: dict[str, Path], order: tuple[str, str, str, str]) -> None:
    """Delta: order 依次为 vector, vector_graph, full, baseline。"""
    reports = {k: load(p) for k, p in paths.items()}
    v, vg, full, base = order
    print("\n## 每加一路的增量（route ablation）\n")
    print("| 指标 | Δ图路(vg-vector) | Δkeyword(full-vg) | Δ联合(full-vector) | Δ完整(full-baseline) |")
    print("|---|---|---|---|---|")
    for key, label in SUMMARY_KEYS:
        gv = overall_row(reports[vg]).get(key, 0.0)
        b = overall_row(reports[full]).get(key, 0.0)
        a = overall_row(reports[v]).get(key, 0.0)
        bl = overall_row(reports[base]).get(key, 0.0)
        print(f"| {label} | {gv - a:+.4f} | {b - gv:+.4f} | {b - a:+.4f} | {b - bl:+.4f} |")


def print_category(paths: dict[str, Path]) -> None:
    reports = {k: load(p) for k, p in paths.items()}
    cats = set()
    for rep in reports.values():
        cats.update(category_rows(rep).keys())
    print("\n## 类目级 Recall@5 / GR avg\n")
    print("| 类目 | 指标 | vector | vector+graph | full |")
    print("|---|---|---|---|---|")
    for cat in sorted(cats):
        v = category_rows(reports["vector"]).get(cat, {})
        vg = category_rows(reports["vector_graph"]).get(cat, {})
        f = category_rows(reports["full"]).get(cat, {})
        for key, label in (("context_recall_top5", "Recall@5"), ("gold_rank_avg", "GR avg"), ("gold_rank_top8_recall", "GR top8")):
            print(f"| {cat} | {label} | {(v.get(key) or 0.0):.4f} | {(vg.get(key) or 0.0):.4f} | {(f.get(key) or 0.0):.4f} |")


def top5_agreement(paths: dict[str, Path]) -> None:
    reports = {k: load(p) for k, p in paths.items()}
    full_qs = {q["id"]: q["retrieval"]["top_docs"][:5] for q in reports["full"]["questions"]}
    pairs = [("vector", "纯向量"), ("vector_graph", "图+向量")]
    print("\n## 逐题 top5 与三路的差异（full vs 子集）\n")
    for k, label in pairs:
        sub_qs = {q["id"]: q["retrieval"]["top_docs"][:5] for q in reports[k]["questions"]}
        diff = [qid for qid in full_qs if full_qs[qid] != sub_qs.get(qid)]
        print(f"- **full vs {label}**：{len(full_qs) - len(diff)}/{len(full_qs)} 题 top5 完全一致，差异 {len(diff)} 题")
        for qid in diff[:10]:
            print(f"  - {qid}：full={full_qs[qid][:3]} → {label}={sub_qs[qid][:3]}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vector", default=DEFAULT_PATHS["vector"])
    parser.add_argument("--vector-graph", dest="vector_graph", default=DEFAULT_PATHS["vector_graph"])
    parser.add_argument("--full", default=DEFAULT_PATHS["full"])
    parser.add_argument("--base", default=DEFAULT_PATHS["baseline"])
    args = parser.parse_args()

    paths = {
        "vector": resolve(args.vector),
        "vector_graph": resolve(args.vector_graph),
        "full": resolve(args.full),
        "baseline": resolve(args.base),
    }
    for k, p in paths.items():
        if not p.exists():
            print(f"⚠️  缺报告: {k} → {p}")
    print_overall(paths)
    print_deltas(paths, ("vector", "vector_graph", "full", "baseline"))
    print_category(paths)
    top5_agreement(paths)


if __name__ == "__main__":
    main()