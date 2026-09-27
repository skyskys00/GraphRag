#!/usr/bin/env python
"""gold_rank 词汇匹配器三态库级回归诊断（探底留档，可复跑）。

在 eval_cservice_ws 全库 sparse chunks 上，对 cservice35 测试集全部 key_facts
分别用三个匹配器判定每 fact 是否能被至少一个 chunk 命中：

- ORIG   : HEAD 原始版（数字子串 + 关键词子串）
- STRICT : 首轮修复版（数字边界 + SequenceMatcher，fact 去数字 + 双分支阈值，
           已证伪：21/135 facts 归零）。
- NEW    : 当前工作区版（千分位 content 归一 + 两侧分隔符归一化 + 数字边界 +
           宽松/严格双分支）。

输出：
1. 三态 found 计数 + 每 fact 三态命中矩阵（0/1）
2. NEW vs STRICT：找回的 lost（STRICT 未命中而 NEW 命中）与新增误中（反向）
3. NEW vs ORIG 同样对比（找回过杀 + 回潮信号）

注意：gold_rank 真正的口径是检索结果 top8 内的 rank，本脚本是全库宽松口径，
只用于诊断匹配器纯度（某 fact 在库里是否存在支持块），并作为 35 题报告数字的
定性参照。库级保守度 ≠ 报告级（报告还受检索覆盖影响）。
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
SPARSE = PROJ / "data" / "eval_cservice_ws" / "m5_sparse.json"
TESTSET = PROJ / "tests" / "testsets" / "testset_cservice_35.json"


# ================= ORIG（HEAD 版，数字+关键词子串） =================
_ORIG_STOPWORDS = {
    "的", "了", "是", "在", "为", "和", "与", "及", "或", "对", "等", "中", "后", "上", "下",
    "从", "到", "于", "以", "由", "被", "把", "让", "向", "就", "而", "并", "且", "但", "如",
    "该", "其", "这", "那", "有", "无", "不", "也", "都", "很", "更", "最", "较", "来", "去",
    "主要", "相关", "以下", "一下", "包括", "包含", "需要", "可以", "进行", "通过", "方式",
    "我们", "他们", "你们", "他", "她", "它", "个", "项", "条", "次", "天", "小时", "分钟",
    "达到", "约为", "约", "左右", "以上", "以下", "之间",
    "多少", "什么", "哪个", "哪些", "怎么", "如何", "为什么",
    "比", "较", "更", "最", "相",
}

_ORIG_NUM_PATTERN = re.compile(r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?")


def _orig_extract_number_tokens(text: str) -> list[str]:
    nums = _ORIG_NUM_PATTERN.findall(text)
    result = []
    for n in nums:
        clean = n.replace(",", "")
        if clean not in result and len(clean) >= 1:
            result.append(clean)
    return result


def _orig_extract_keywords(text: str) -> list[str]:
    tokens = re.split(r"[，。、；：「」『』\"\"''（）()【】\[\] ,.?!:;~～·\-—/\\\n]", text)
    keywords = []
    for t in tokens:
        t = t.strip()
        if len(t) < 2 or t in _ORIG_STOPWORDS:
            continue
        if re.fullmatch(r"[\d,.%]+", t):
            continue
        if t not in keywords:
            keywords.append(t)
    return keywords


def orig_match(fact: str, content: str) -> bool:
    fact_nums = set(_orig_extract_number_tokens(fact))
    fact_kws = _orig_extract_keywords(fact)
    chunk_lower = content.lower()
    if fact_nums:
        num_hits = sum(1 for n in fact_nums if n in chunk_lower)
        num_hit_ratio = num_hits / len(fact_nums)
        if num_hits < 1 or num_hit_ratio < 0.4:
            return False
        kw_hits = sum(1 for kw in fact_kws if kw.lower() in chunk_lower)
        if num_hit_ratio >= 0.75 and len(fact_nums) >= 3:
            return True
        if fact_kws and kw_hits >= 1:
            return True
        if not fact_kws and num_hit_ratio >= 0.5:
            return True
        return False
    else:
        if not fact_kws:
            return False
        kw_hits = sum(1 for kw in fact_kws if kw.lower() in chunk_lower)
        return kw_hits / len(fact_kws) >= 0.6


# ================= STRICT（首轮修复版：lcs 双分支，未含千分位/累计） =================
_STRICT_NUM_PATTERN = re.compile(r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?")

_SEP = re.compile(r"[^一-龥A-Za-z0-9%]")


def _norm(text: str) -> str:
    return _SEP.sub("", text).lower()


def _strict_num_in_content(num: str, content: str) -> bool:
    return re.search(rf"(?<!\d){re.escape(num)}(?!\d)", content) is not None


def strict_match(fact: str, chunk_content: str) -> bool:
    """首轮修复版（lcs 双分支），作为回归对比 baseline。"""
    chunk_lower = chunk_content.lower()
    fact_nums = set(_orig_extract_number_tokens(fact))
    has_num = bool(fact_nums)
    if has_num:
        num_ok = sum(1 for n in fact_nums if _strict_num_in_content(n, chunk_lower)) >= 1
        if not num_ok:
            return False
    f_clean = _STRICT_NUM_PATTERN.sub("", fact.lower()).strip()
    if not f_clean:
        return True
    sm = difflib.SequenceMatcher(None, f_clean, chunk_lower, autojunk=False)
    longest = max((m.size for m in sm.get_matching_blocks()), default=0)
    if has_num:
        return longest >= min(4, len(f_clean)) or sm.ratio() >= 0.5
    return longest >= max(4, len(f_clean) // 2) or sm.ratio() >= 0.5


# ================= MATCHERS =================
MATCHERS = {
    "ORIG": orig_match,
    "STRICT": strict_match,
}


def load_data() -> tuple[list[dict], dict]:
    testset = json.load(open(TESTSET, encoding="utf-8"))
    sparse = json.load(open(SPARSE, encoding="utf-8"))
    chunks = sparse["chunks"]
    questions = [q for q in testset["questions"] if q["category"] != "unanswerable"]
    return questions, chunks


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(PROJ / "scripts" / "goldrank_regression_diag.json"))
    args = ap.parse_args()

    questions, chunks = load_data()
    print(f"库块数: {len(chunks)}  非 unanswerable 题: {len(questions)}")

    from app.m9_eval.metrics.gold_rank import _lexical_match  # NEW 版

    MATCHERS["NEW"] = _lexical_match

    # 逐 fact 判定：三态矩阵 {fact: {state: bool}}
    matrix: dict[str, dict[str, bool]] = {}
    fact_to_q = {}
    total_facts = 0
    for q in questions:
        for f in q.get("key_facts", []):
            total_facts += 1
            fact_to_q.setdefault(f, q["id"])
            matrix[f] = {}
            for state, mf in MATCHERS.items():
                hit = any(mf(f, ch["content"]) for ch in chunks.values())
                matrix[f][state] = hit

    # 汇总
    found = {s: 0 for s in MATCHERS}
    for row in matrix.values():
        for s, ok in row.items():
            if ok:
                found[s] += 1

    print(f"facts 总数: {total_facts}")
    for s in ("ORIG", "STRICT", "NEW"):
        print(f"  {s:6s} found: {found[s]} من {total_facts} = {found[s]/total_facts:.1%}")

    # NEW vs STRICT / NEW vs ORIG 差异
    def diff_matrix(a: str, b: str) -> list[dict]:
        rows = []
        for f, row in matrix.items():
            if row[a] != row[b]:
                rows.append({"fact": f, "qid": fact_to_q[f], "old": row[a], "new": row[b]})
        return rows

    print("\n=== NEW vs STRICT ===")
    ns = diff_matrix("STRICT", "NEW")
    lost = [r for r in ns if not r["new"]]
    gained = [r for r in ns if r["new"]]
    print(f"差异: {len(ns)}  其中 NEW 找回(STRICT漏→NEW中): {len(gained)}  新增漏(NEW中→NEW漏): {len(lost)}")
    for r in gained[:40]:
        print(f"  + {r['qid']}: {r['fact'][:60]}")
    for r in lost[:40]:
        print(f"  - {r['qid']}: {r['fact'][:60]}")

    print("\n=== NEW vs ORIG ===")
    no = diff_matrix("ORIG", "NEW")
    lost2 = [r for r in no if not r["new"]]
    gained2 = [r for r in no if r["new"]]
    print(f"差异: {len(no)}  其中 NEW 找回(ORIG漏→NEW中): {len(gained2)}  新增漏(NEW中→NEW漏): {len(lost2)}")

    out = {
        "stats": {"total_facts": total_facts, "found": found},
        "matrix": matrix,
        "new_vs_strict": ns,
        "new_vs_orig": no,
    }
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写 {args.out}")


if __name__ == "__main__":
    main()