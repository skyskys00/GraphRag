"""对比两份 retrieval 评测报告，输出类目级和逐题差异。"""
import json
import sys


def load(p):
    with open(p) as f:
        return json.load(f)


def summarize(report):
    overall = report["summary"]["overall"]
    by_cat = report["summary"]["by_category"]
    return overall, by_cat


def main():
    base_p = sys.argv[1]
    new_p = sys.argv[2]
    base = load(base_p)
    new = load(new_p)

    b_overall, b_cat = summarize(base)
    n_overall, n_cat = summarize(new)

    print("=== 总体 ===")
    print(f"{'指标':<20} {'基线':>10} {'新':>10} {'Δ':>10}")
    for k in ["context_recall", "context_precision", "context_precision_weighted"]:
        bv = b_overall.get(k, 0)
        nv = n_overall.get(k, 0)
        diff = nv - bv
        print(f"{k:<20} {bv:>10.4f} {nv:>10.4f} {diff:>+10.4f}")

    print("\n=== 按类目 Precision ===")
    print(f"{'类目':<25} {'基线':>10} {'新':>10} {'Δ':>10}")
    all_cats = sorted(set(b_cat.keys()) | set(n_cat.keys()))
    for cat in all_cats:
        bp = b_cat.get(cat, {}).get("context_precision", 0)
        np_ = n_cat.get(cat, {}).get("context_precision", 0)
        diff = np_ - bp
        marker = " ⬆" if diff > 0.01 else " ⬇" if diff < -0.01 else ""
        print(f"{cat:<25} {bp:>10.4f} {np_:>10.4f} {diff:>+10.4f}{marker}")

    print("\n=== 按类目 Recall ===")
    print(f"{'类目':<25} {'基线':>10} {'新':>10} {'Δ':>10}")
    for cat in all_cats:
        br = b_cat.get(cat, {}).get("context_recall", 0)
        nr = n_cat.get(cat, {}).get("context_recall", 0)
        diff = nr - br
        marker = " ⬆" if diff > 0.01 else " ⬇" if diff < -0.01 else ""
        print(f"{cat:<25} {br:>10.4f} {nr:>10.4f} {diff:>+10.4f}{marker}")

    print("\n=== 逐题 Precision（只列变化的）===")
    b_qmap = {q["id"]: q for q in base["questions"]}
    n_qmap = {q["id"]: q for q in new["questions"]}
    for qid in sorted(b_qmap.keys()):
        bq = b_qmap[qid]
        nq = n_qmap.get(qid)
        if not nq:
            continue
        bp = bq.get("metrics", {}).get("context_precision", 0)
        np_ = nq.get("metrics", {}).get("context_precision", 0)
        diff = np_ - bp
        if abs(diff) > 1e-6:
            cat = bq.get("category", "?")
            print(f"  {qid:<12} {cat:<20} {bp:>6.3f} → {np_:>6.3f} ({diff:+.3f})")


if __name__ == "__main__":
    main()
