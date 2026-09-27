#!/usr/bin/env python
"""垂直审计：对 ORIG中NEW漏 的 fact，定位库内支持块，三态判定 + 匹配数据。

对每个 fact：
1. 用 ORIG / STRICT / NEW 各自的判定扫全库，列出命中（或曾命中）的 chunk + content 摘录
2. 打印 NEW 匹配数据：number_tokens、num_ok 结果、f_norm、最长公共块（真正在
   chunk 侧）、need 阈值
用于判定「真回归（块真含该事实，被阈值拒）」vs「正确收紧（块不真含，ORIG 误中）」。
"""
from __future__ import annotations

import difflib
import re
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
import json

SPARSE = PROJ / "data" / "eval_cservice_ws" / "m5_sparse.json"
TESTSET = PROJ / "tests" / "testsets" / "testset_cservice_35.json"

import sys
sys.path.insert(0, str(PROJ))
from scripts.probe_goldrank_regression import orig_match, strict_match  # noqa: E402

facts_watch = [
    "情绪升级：客户威胁升级 → 客服总监",
    "v2.1 新增：知识库管理后台",
    "GraphRAG 核心：知识图谱 + 大模型融合",
    "三级投诉定义 = 不影响核心功能、情绪平稳的常规问题",
    "触发条件：超期/情绪/技术/跨部门 四种",
    "行业水平：40% → 70%+（LLM 驱动）",
    "通常 1-5 分制",
    "GraphRAG = 知识图谱 + 大模型融合的 RAG 架构",
    "ART：西南 52 秒 vs 华东 38 秒",
    "合计扣分 = 20 + 10 = 30 分",
]


def _extract_number_tokens(text: str) -> list[str]:
    pat = re.compile(r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?")
    result = []
    for n in pat.findall(text):
        clean = n.replace(",", "")
        if clean not in result:
            result.append(clean)
    return result


_SEP = re.compile(r"[^一-龥A-Za-z0-9%]")


def _norm(text: str) -> str:
    return _SEP.sub("", text).lower()


from app.m9_eval.metrics.gold_rank import _lexical_match as new_match


def main() -> None:
    sparse = json.load(open(SPARSE, encoding="utf-8"))
    chunks = sparse["chunks"]

    for fact in facts_watch:
        print("=" * 100)
        print(f"FACT: {fact}")
        fn = _extract_number_tokens(fact)
        f_norm = _norm(fact)
        need = max(2, min(4, len(f_norm))) if fn else max(4, len(f_norm) // 2)
        print(f"  num_tokens={fn}  f_norm(len{len(f_norm)})={f_norm!r}  new_need={need}")

        hits_by = {"ORIG": [], "STRICT": [], "NEW": []}
        for cid, ch in chunks.items():
            content = ch["content"]
            for state, fn_match in (("ORIG", orig_match), ("STRICT", strict_match),
                                    ("NEW", new_match)):
                try:
                    if fn_match(fact, content):
                        hits_by[state].append((cid, content))
                except Exception:
                    pass
        for state, hits in hits_by.items():
            print(f"  [{state}] 命中 {len(hits)} 块:")
            for cid, content in hits:
                sm = difflib.SequenceMatcher(None, f_norm, _norm(content), autojunk=False)
                blocks = [m.size for m in sm.get_matching_blocks()]
                longest = max(blocks, default=0)
                total = sum(blocks)
                ratio = sm.ratio()
                content_flat = re.sub(r"\s+", " ", content)[:130]
                print(f"    {cid[:26]}… lcs={longest} cum={total} ratio={ratio:.2f}")
                print(f"      {content_flat!r}")


if __name__ == "__main__":
    main()