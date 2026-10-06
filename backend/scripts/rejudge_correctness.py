"""重判 10 题抽检的 correctness（裁判 prompt 校准后），输出新旧分数对照。

用法：
    cd backend
    python scripts/rejudge_correctness.py [--ids CS-FS-007,CS-FC-004]

读取 run_e2e_cservice_50.json 的 question/answer + 测试集 50 的 ground_truth，
只重算 correctness。改 prompt 后缓存 key 不含 prompt，必须先 clear_cache('correctness')。
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))

from app.m9_eval.judge import clear_cache
from app.m9_eval.metrics.correctness import compute_correctness
from app.m9_eval.runner import _load_dotenv

REPORT = PROJ / "tests/reports/e2e/history/run_e2e_cservice_50.json"
TESTSET = PROJ / "tests/testsets/testset_cservice_50.json"

ASSUMED_IDS = [
    "CS-CP-004", "CS-CP-006", "CS-FC-004", "CS-FC-007", "CS-FS-002",
    "CS-FS-007", "CS-PN-002", "CS-SM-001", "CS-TN-004", "CS-UA-001",
]


async def main(ids: list[str], full: bool = False, out: str = "") -> None:
    _load_dotenv()
    from app.m3_index.providers import build_llm_func
    _, query_func = build_llm_func()

    report = json.load(open(REPORT))
    testset = json.load(open(TESTSET))
    id_set = set(ids)

    gt_map = {q["id"]: q["ground_truth"] for q in testset["questions"]}
    q_map = {q["id"]: q for q in report["questions"]}

    missing = id_set - set(gt_map)
    if missing:
        raise SystemExit(f"测试集无这些 id: {missing}")

    rmoved = clear_cache("correctness")
    print(f"[已清 correctness 缓存 {rmoved} 条]")
    if not full:
        print(f"{'id':<10} {'旧score':<9} {'新score':<9} Δ")
        print("-" * 40)
    rows = []
    collected: list[dict] = []
    for qid in ids:
        ans = q_map[qid]
        old = ans["metrics"]["correctness"]
        res = await compute_correctness(
            query_func,
            ans["question"],
            gt_map[qid],
            ans["answer"],
        )
        new = res["score"]
        total_facts = res["total_facts"]
        correct_facts = res["correct_facts"]
        rows.append((qid, old, new))
        if full:
            import copy
            item = copy.deepcopy(ans)
            item["metrics"]["correctness"] = new
            item["metrics"]["correctness_detail"] = {
                "total_facts": total_facts,
                "correct_facts": correct_facts,
                "incorrect": res.get("incorrect", []),
                "reason": res["reason"],
            }
            collected.append(item)
        else:
            delta = new - old
            print(
                f"{qid:<10} {old:<9.3f} {new:<9.3f} {delta:+.3f} "
                f"(facts {correct_facts}/{total_facts}) {res['reason']}"
            )

    if full:
        if not out:
            out = str(PROJ / "tests/reports/_tmp_corr_fix.json")
        qid_set = set(ids)
        missing_old = {q["id"] for q in report["questions"]} - qid_set
        if missing_old:
            raise SystemExit(f"full 模式需要覆盖报告全部题,缺: {sorted(missing_old)}")
        json.dump({"questions": collected}, open(out, "w"), ensure_ascii=False, indent=2)
        print(f"fix 已写: {out} ({len(collected)} 题)")
    else:
        if rows:
            avg_old = sum(r[1] for r in rows) / len(rows)
            avg_new = sum(r[2] for r in rows) / len(rows)
            print("-" * 40)
            print(f"平均: {avg_old:.3f} -> {avg_new:.3f}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--ids", default=",".join(ASSUMED_IDS),
                        help="逗号分隔的题目 id，默认抽检 10 题")
    parser.add_argument("--full", action="store_true",
                        help=f"全量重判 {REPORT.name} 全部题并输出 fix json")
    parser.add_argument("--out", default="", help="full 模式的 fix json 输出路径")
    args = parser.parse_args()
    if args.full:
        import json as _j
        all_ids = [q["id"] for q in _j.load(open(TESTSET))["questions"]]
        asyncio.run(main(all_ids, full=True, out=args.out))
    else:
        asyncio.run(main([i.strip() for i in args.ids.split(",") if i.strip()]))