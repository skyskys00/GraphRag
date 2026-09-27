#!/usr/bin/env python
"""预演 v2「结论位必中 + 核心比例」规则：对比当前 NEW-cum。

规则 v2：
- 弱锚排除：版本号(V/后接「版」)、年份(^19|20\d{2}$)、序数(第/问题/趋势/阶段/Q后)、
  差值(+/-前缀 或 后接 pp/百分点/百分点/倍) → 不构成 strong 数字。
- strong = 单位绑定数字(%/分/小时/分钟/次/秒/件/元/点/周/人/亿) ∪ 小数(非差值) ∪ 比较符后
- 结论位 = (等式/≈/→ 最右右值数字) ∪ (≥/≤/>/< 后数字) ∪ (无比较式时：全部 strong)
- 命中判定：若 strong 非空：
    1) 若结论位非空 → 至少一个结论位数字边界命中（any）
    2) strong 命中比例 >= 0.5
    3) 文本 cum 用「有数字档」阈值
   否则（只有弱锚/无数字）→ 无数字文本档
"""
from __future__ import annotations

import difflib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CTX = Path(__file__).resolve().parents[1] / "scripts" / "top8_contexts_cservice35.json"
from app.m9_eval.metrics.gold_rank import (_norm, _num_in_content, _extract_number_tokens)  # noqa: E402

_UNIT = ("分", "小时", "分钟", "次", "秒", "件", "元", "天", "点", "周", "人", "亿")
_ORD = ("第", "问题", "趋势", "阶段", "季度", "款", "轮", "条", "项", "列", "节")  # 序数语境
_DIFFSUF = ("pp", "个百分点", "百分点", "倍")


def _is_version(n, fact):
    if re.search(rf"[Vv]\s*{re.escape(n)}", fact):
        return True
    if re.search(rf"{re.escape(n)}\s*版", fact):
        return True
    # 形如 1.0/1.2 且在文档版本语境（含 V/版 字样的 fact）
    if "." in n and re.search(r"[Vv版]", fact):
        return True
    return False


def _is_year(n):
    return re.match(r"^(?:19|20)\d{2}$", n) is not None


def _is_diff(n, fact):
    if re.search(rf"[+-]\s*{re.escape(n)}", fact):
        return True
    return any(re.search(rf"{re.escape(n)}\s*{s}", fact) for s in _DIFFSUF)


def _is_ordinal(n, fact):
    if any(re.search(rf"{p}\s*{re.escape(n)}", fact) for p in _ORD):
        return True
    return bool(re.search(rf"[Qq]{re.escape(n)}", fact))


def _unit_bound(n, fact):
    return any(re.search(rf"{re.escape(n)}\s*{u}", fact) for u in _UNIT)


def _after_cmp(n, fact):
    return re.search(rf"[=≈≤≥<>→]\s*{re.escape(n)}", fact) is not None


def _classify(fact: str):
    nums = set(_extract_number_tokens(fact))
    strong, weak = set(), set()
    for n in nums:
        if _is_version(n, fact) or _is_year(n) or _is_diff(n, fact) or _is_ordinal(n, fact):
            weak.add(n)
        elif _unit_bound(n, fact) or "." in n or _after_cmp(n, fact):
            strong.add(n)
        else:
            weak.add(n)
    # 结论位
    concl = set()
    rvalues = [m.group(1) for m in re.finditer(r"[=≈→]\s*(\d+(?:\.\d+)?%?)", fact)]
    cmpnums = {m.group(1) for m in re.finditer(r"[≤≥<>]\s*(\d+(?:\.\d+)?%?)", fact)}
    if rvalues:
        concl.add(rvalues[-1])  # 最右一个等式右值（结果位）
    concl |= cmpnums
    if not (rvalues or cmpnums):
        concl |= strong  # 无比较式 → 全部 strong 是并列主值
    concl &= (strong | weak)
    return strong, weak, concl


def _hit_frac(nums, content):
    hn = {n: _num_in_content(n, content) for n in nums}
    return sum(hn.values()), hn


def new2_match(fact: str, content: str) -> bool:
    """v3：strong 非空时做强锚判定；否则退回 OLD 数字关；文本档按是否有任意数字。"""
    fact_nums = set(_extract_number_tokens(fact))
    strong, weak, concl = _classify(fact)
    has_num = bool(fact_nums)
    if strong:
        shits, hn = _hit_frac(strong, content)
        if shits < 1:
            return False
        if len(strong) >= 2 and shits / len(strong) < 0.5:
            return False
        if concl and not any(_num_in_content(n, content) for n in concl):
            return False
    elif has_num:
        num_hits = sum(1 for n in fact_nums if _num_in_content(n, content))
        if num_hits < 1:
            return False
        if len(fact_nums) >= 2 and num_hits / len(fact_nums) < 0.5:
            return False
    f_norm = _norm(fact)
    if not f_norm:
        return True
    chunk_norm = _norm(content)
    sm = difflib.SequenceMatcher(None, f_norm, chunk_norm, autojunk=False)
    cum = sum(m.size for m in sm.get_matching_blocks() if m.size >= 2)
    if has_num:
        return cum >= max(2, min(4, len(f_norm))) or sm.ratio() >= 0.5
    return cum >= max(4, len(f_norm) // 2) or sm.ratio() >= 0.5


def old_match(fact: str, content: str) -> bool:
    fact_nums = set(_extract_number_tokens(fact))
    has_num = bool(fact_nums)
    if has_num:
        num_hits = sum(1 for n in fact_nums if _num_in_content(n, content))
        if num_hits < 1:
            return False
        if len(fact_nums) >= 2 and num_hits / len(fact_nums) < 0.5:
            return False
    f_norm = _norm(fact)
    if not f_norm:
        return True
    chunk_norm = _norm(content)
    sm = difflib.SequenceMatcher(None, f_norm, chunk_norm, autojunk=False)
    cum = sum(m.size for m in sm.get_matching_blocks() if m.size >= 2)
    if has_num:
        return cum >= max(2, min(4, len(f_norm))) or sm.ratio() >= 0.5
    return cum >= max(4, len(f_norm) // 2) or sm.ratio() >= 0.5


def main() -> None:
    payload = json.load(open(CTX, encoding="utf-8"))
    changed_old_hit, changed_new_hit = [], []
    total = 0
    for q in payload:
        for fact in q["key_facts"]:
            total += 1
            old_found = next((c for c in q["contexts"] if old_match(fact, c["content"])), None)
            new_found = next((c for c in q["contexts"] if new2_match(fact, c["content"])), None)
            strong, weak, concl = _classify(fact)
            if bool(old_found) != bool(new_found):
                row = (q["id"], fact[:48], sorted(set(_extract_number_tokens(fact))), sorted(strong),
                       sorted(concl), sorted(weak),
                       f"rank{old_found['rank']}" if old_found else "miss",
                       f"rank{new_found['rank']}" if new_found else "miss")
                (changed_old_hit if old_found and not new_found else changed_new_hit).append(row)
    print(f"facts 总数: {total}")
    print(f"\n[旧命中 → 新拒] {len(changed_old_hit)} 条（潜在误杀，逐条审查）：")
    for r in changed_old_hit:
        print(f"  {r[0]} | {r[1]}\n      nums={r[2]} strong={r[3]} concl={r[4]} weak={r[5]} | OLD {r[6]} -> NEW {r[7]}")
    print(f"\n[旧拒 → 新命中] {len(changed_new_hit)} 条：")
    for r in changed_new_hit:
        print(f"  {r[0]} | {r[1]}\n      nums={r[2]} strong={r[3]} concl={r[4]} weak={r[5]} | OLD {r[6]} -> NEW {r[7]}")


if __name__ == "__main__":
    main()