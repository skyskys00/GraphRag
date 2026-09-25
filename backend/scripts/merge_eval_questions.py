"""把修正重跑的逐题结果 merge 回全量报告并重聚 summary。

用法：python3 scripts/merge_eval_questions.py <report.json> <fix.json>
报告 questions 按 id 被 fix 逐题替换，再走 build_report 重算 overall/by_category/by_difficulty。
"""
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "app"))
from m9_eval.report import build_report  # noqa: E402


def main() -> None:
    report_path = BASE / sys.argv[1]
    fix_path = BASE / sys.argv[2]

    report = json.loads(report_path.read_text(encoding="utf-8"))
    fix = json.loads(fix_path.read_text(encoding="utf-8"))
    report_bak_time = report.get("summary", {}).get("total_time_s")

    by_id = {q["id"]: q for q in report["questions"]}
    replaced: dict[str, str] = {}
    for q in fix["questions"]:
        qid = q["id"]
        if qid in by_id:
            replaced[qid] = by_id[qid]["metrics"]["correctness"]  # 旧分
            by_id[qid] = q
        else:
            print(f"WARN: fix 里 {qid} 不在报告中，跳过")
    if not replaced:
        print("no replacement: nothing changed")
        return

    new_questions = [by_id[q["id"]] for q in report["questions"]]
    meta = {
        "testset_id": report.get("testset_id", ""),
        "domain": report.get("domain", ""),
        "version": report.get("version", ""),
    }
    rebuilt = build_report(meta, new_questions, report.get("config"))

    rebuilt.pop("timestamp", None)
    rebuilt["timestamp"] = report.get("timestamp")
    report["questions"] = new_questions
    report["summary"] = rebuilt["summary"]
    # build_report 不算 total_time_s（runner 在调用处手动补），merge 时沿用旧报告值
    report["summary"]["total_time_s"] = report_bak_time

    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for qid, old in replaced.items():
        print(f"{qid}: correctness {old} -> {by_id[qid]['metrics']['correctness']}")
    print(f"merged. questions={len(new_questions)} -> {report_path}")
    print(
        "overall correctness: "
        f"{report['summary']['overall']['correctness']}, "
        f"faithfulness={report['summary']['overall']['faithfulness']}, "
        f"citation_accuracy={report['summary']['overall']['citation_accuracy']}"
    )


if __name__ == "__main__":
    main()