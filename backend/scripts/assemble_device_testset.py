"""组装器械库评测题集 v2：合并各文档 agent 出题结果 → 校验 → 重新编号 → 落盘。

用法：python3 scripts/assemble_device_testset.py [--src /tmp/device_chunks]
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
COL = PROJ / "data/collections/col_b7b876b1"

# 期望难度分配（按文档 chunk 体量加权，总计 25/15/10）
EXPECT = {
    "e10aa9a02b70ae74": {"easy": 5, "medium": 2, "hard": 1},  # 智能便携式检测仪
    "8a22bfff101b31f1": {"easy": 3, "medium": 2, "hard": 1},  # 融柏恒流泵
    "ff11a8dc007af2d9": {"easy": 2, "medium": 2, "hard": 1},  # 血氧仪使用手册
    "f33baf8f00190aa4": {"easy": 2, "medium": 1, "hard": 1},  # 瑞创RSP
    "0d5c7f07d607e9c4": {"easy": 2, "medium": 1, "hard": 1},  # 多参数KE-2000
    "447207be927c51ff": {"easy": 2, "medium": 1, "hard": 1},  # 英菲泰克
    "9134cbc78eca13e9": {"easy": 2, "medium": 1, "hard": 1},  # 脉搏血氧仪
    "54f036800deb9dff": {"easy": 2, "medium": 1, "hard": 1},  # 电除颤
    "a07ebcfa1b858095": {"easy": 2, "medium": 1, "hard": 1},  # 铭昇H2-5000IBP
    "ad3dd5b95dd4221b": {"easy": 1, "medium": 1, "hard": 1},  # 数字差压计
    "ac6d599e803fb549": {"easy": 1, "medium": 1, "hard": 0},  # pptx
    "268dffae2382a83a": {"easy": 1, "medium": 1, "hard": 0},  # 骨科docx
}

CATS = {"fact_single", "table_numeric", "image_only", "summary"}
DIFFS = {"easy", "medium", "hard"}
CAT_PREFIX = {"fact_single": "FS", "table_numeric": "TN", "image_only": "IO", "summary": "SUM"}


def load_chunks(did: str) -> list[dict]:
    p = COL / "chunks" / f"{did}.jsonl"
    return [json.loads(l) for l in p.open(encoding="utf-8")]


def fact_hit(fact: str, contents: list[str]) -> tuple[bool, str]:
    """key_fact 改写自语料，故用宽松判据：数字/英文 token 必须全命中（数字不会改写），
    中文按 2-gram 覆盖率 ≥0.5（容忍 paraphrase，拒绝编造）。"""
    blob = "\n".join(contents)
    nums = re.findall(r"\d+(?:\.\d+)?", fact) + re.findall(r"[A-Za-z][A-Za-z0-9\-\.]{2,}", fact)
    missing = [t for t in nums if t not in blob]
    cjk = re.findall(r"[一-鿿]", fact)
    grams = {cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1)}
    cov = (sum(1 for g in grams if g in blob) / len(grams)) if grams else 1.0
    ok = (not missing) and (cov >= 0.5)
    return ok, f"nums_miss={missing} cjk2gram_cov={cov:.2f}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="/tmp/device_chunks")
    ap.add_argument("--out", default=str(PROJ / "tests/testsets/testset_device_v2.json"))
    args = ap.parse_args()
    src = Path(args.src)

    docs = json.load(open(COL / "documents.json", encoding="utf-8"))
    id2fn = {d["doc_id"]: d["filename"] for d in docs.values() if not d.get("deleted")}

    problems: list[str] = []
    per_doc: dict[str, list[dict]] = defaultdict(list)
    for did, fn in id2fn.items():
        f = src / f"out_{did}.json"
        if not f.exists():
            problems.append(f"MISSING 出题文件: {f.name}")
            continue
        try:
            qs = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            problems.append(f"BAD JSON {f.name}: {e}")
            continue
        chunks = load_chunks(did)
        contents = [c.get("content", "") for c in chunks]
        for q in qs:
            for k in ["category", "question", "ground_truth", "key_facts",
                      "must_have_docs", "difficulty", "source_docs", "tags"]:
                if k not in q:
                    problems.append(f"{did} 题缺字段 {k}: {str(q.get('question', '?'))[:30]}")
            if q.get("category") not in CATS:
                problems.append(f"{did} 非法 category: {q.get('category')}")
            if q.get("difficulty") not in DIFFS:
                problems.append(f"{did} 非法 difficulty: {q.get('difficulty')}")
            if q.get("must_have_docs") != [fn]:
                problems.append(f"{did} must_have_docs 不符: {q.get('must_have_docs')} != [{fn}]")
            idx = q.get("_evidence_idx") or []
            bad = [i for i in idx if not (isinstance(i, int) and 0 <= i < len(chunks))]
            if bad:
                problems.append(f"{did} evidence_idx 越界: {bad} (chunks={len(chunks)})")
            for kf in q.get("key_facts", []):
                ok, info = fact_hit(kf, contents)
                if not ok:
                    problems.append(f"{did} key_fact 未命中: 「{kf}」 ({info})")
            per_doc[did].append(q)

    print("=== 每文档难度数量 vs 期望 ===")
    for did, fn in id2fn.items():
        c = Counter(q["difficulty"] for q in per_doc[did])
        exp = EXPECT.get(did, {})
        flag = "" if all(c.get(d, 0) == exp.get(d, 0) for d in DIFFS) else "  <-- 不符"
        print(f"{fn[:32]:32s} easy={c.get('easy',0)}/{exp.get('easy',0)} "
              f"medium={c.get('medium',0)}/{exp.get('medium',0)} "
              f"hard={c.get('hard',0)}/{exp.get('hard',0)}{flag}")

    counters: dict[str, int] = defaultdict(int)
    for did in id2fn:
        for q in per_doc[did]:
            cat = q["category"]
            counters[cat] += 1
            q["id"] = f"DV-{CAT_PREFIX[cat]}-{counters[cat]:03d}"

    questions = [q for did in id2fn for q in per_doc[did]]
    for q in questions:
        q.pop("_evidence_idx", None)

    n = len(questions)
    c_cnt = Counter(q["difficulty"] for q in questions)
    cat_cnt = Counter(q["category"] for q in questions)
    out = {
        "testset_id": "testset_device_v2",
        "collection": "col_b7b876b1",
        "domain": "家用/医用器械说明书库（12 份，含注射泵、血压、血氧、心电、除颤、水质检测 + 骨科手术器械审评规范 + 护理不良事件上报）",
        "version": "2026-10-05",
        "created": "2026-10-05",
        "description": (
            f"器械垂直场景 {n} 题评测集（v5.34 重建）。覆盖全部 12 份文档，每份文档至少 easy+medium 两档、"
            f"内容充足的 10 份覆盖 hard。难度 {c_cnt.get('easy',0)} 易 / {c_cnt.get('medium',0)} 中 / "
            f"{c_cnt.get('hard',0)} 难（≈5:3:2）。题型："
            + " / ".join(f"{k} {v}" for k, v in cat_cnt.most_common())
            + "。全部为单文档题；跨文档 comparison 与 unanswerable 由留档的旧题集覆盖。"
        ),
        "corpus_docs": list(id2fn.values()),
        "questions": questions,
    }
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== 汇总 ===")
    print("总题数:", n)
    print("difficulty:", dict(c_cnt))
    print("category:", dict(cat_cnt))
    print("写出:", args.out)
    print(f"\n=== 问题 ({len(problems)}) ===")
    for p in problems:
        print(" -", p)


if __name__ == "__main__":
    main()
