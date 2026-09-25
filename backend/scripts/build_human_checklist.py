"""人工抽检表生成：从评测报告 join 测试集真值，生成人工判定清单。

用法：python3 scripts/build_human_checklist.py <report.json> <testset.json> <ids...>
输出：stdout 打印抽检条目的浓缩 JSON（question/ground_truth/key_facts/answer/裁判 reason），
并保存一份 markdown 到 tests/reports/human_checklist_<report名>.md 供用户打开对照判定。
"""
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent


def main() -> None:
    report_path = BASE / sys.argv[1]
    testset_path = BASE / sys.argv[2]
    wanted = sys.argv[3:] if len(sys.argv) > 3 else None

    report = json.loads(report_path.read_text(encoding="utf-8"))
    testset = json.loads(testset_path.read_text(encoding="utf-8"))

    # id -> 题（优先报告自带 ground_truth，缺则从测试集 join）
    by_id = {q["id"]: q for q in report["questions"]}
    truth_by_id = {q["id"]: q for q in testset["questions"]}

    ids = wanted if wanted else list(by_id.keys())
    rows = []
    for qid in ids:
        q = by_id[qid]
        t = truth_by_id.get(qid, {})
        gt = q.get("ground_truth") or t.get("ground_truth", "")
        kf = q.get("key_facts") or t.get("key_facts", [])
        m = q.get("metrics", {})
        cd = m.get("correctness_detail") or {}
        rows.append({
            "id": qid,
            "category": q.get("category"),
            "difficulty": q.get("difficulty"),
            "question": q.get("question"),
            "ground_truth": gt,
            "key_facts": kf,
            "answer": q.get("answer", "")[:600],
            "correctness": m.get("correctness"),
            "judge_reason": cd.get("reason", ""),
        })

    out = report_path.parent / f"human_checklist_{report_path.stem}.md"
    lines = ["# 人工抽检表（裁判一致性核对）", ""]
    for i, r in enumerate(rows, 1):
        lines += [
            f"## {i}. {r['id']} [{r['category']} / {r['difficulty']}] LLM 判定 correctness={r['correctness']}",
            f"**问题**：{r['question']}",
            f"**标准答案**：{r['ground_truth']}",
            f"**key_facts**：" + "；".join(r["key_facts"]),
            f"**系统答案**：{r['answer']}",
            f"**裁判理由**：{r['judge_reason']}",
            "",
            f"**人工判定（正/误/无法判断）**：______　一致？（是/否）______",
            "",
            "---",
            "",
        ]
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"written: {out}  rows={len(rows)}")


if __name__ == "__main__":
    main()