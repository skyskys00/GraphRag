"""M2 语义注入探底：验证「table 块 content 注入文档结构语义」能否让编码映射表进 keyword 池。

背景：q004（城市分级表/住宿费表）、q008（A3 6.2 赔偿表）的表格块是编码/映射表，
词面与 query 无重叠（共享 token 1~2，dot 0.06），进不了 keyword top40 池。
本探针在 sparse 索引层模拟 M2 切分时给 table 块 content 加「章节结构前缀」后的效果：
重编码注入 content → 替换该 chunk 的稀疏向量 → 用 score() 看它是否进 top40 + 对比内积。

不修改任何模块代码。与 M9 baseline 一致（entities=None、无 allowed/exclude）。

用法：cd backend && /opt/anaconda3/envs/graphrag/bin/python scripts/probe_m2_inject.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))

from app.m5_retrieve.sparse_index import SPARSE_FILE, _sparse_encode, load, score

WORKSPACE = "eval_admin_ws"
WORKING_DIR = PROJ / "data" / WORKSPACE
CHUNKS_DIR = PROJ / "data" / "chunks" / "eval_admin"

# gap 考题：query -> 关注 chunk -> 说明
CASES = {
    "adm_q004": {
        "query": "出差到上海，普通员工的住宿标准是多少元一晚？",
        "chunks": [
            ("chunk-47930c608e5f8ce0fb7ad8e005adb8bd", "A2 3.1 城市分级表（上海=一类）"),
            ("chunk-4d323af7021bf938844ac5098be0a9c9", "A2 3.2 住宿费标准表（普通员工一类=350）"),
        ],
    },
    "adm_q008": {
        "query": "员工出差期间，公司配备的笔记本电脑如果丢失了，应该怎么处理？涉及哪些制度？",
        "chunks": [
            ("chunk-522ca76443e15487b3eeba2c4f400540", "A3 6.2 赔偿标准表（疏忽大意30%）"),
            ("chunk-6a37820369f8b15dce09224d82ecfde6", "A3 6.2 赔偿比例明细表（故意100%）"),
        ],
    },
}

_STRIP = re.compile(r"\*\*(?P<t>[^*]+)\*\*")


def _plain(head: str) -> str:
    """去掉 **加粗** 标记。"""
    return _STRIP.sub(lambda m: m.group("t"), head).strip()


def build_facts(sparse: dict) -> dict:
    """读 chunks jsonl，与 sparse chunks 按 content 精确对齐，返回 {chunk_id: {...结构信息}}。"""
    by_content: dict[str, dict] = {}
    for jf in CHUNKS_DIR.glob("*.jsonl"):
        for line in jf.open(encoding="utf-8"):
            u = json.loads(line)
            tp = u.get("title_path")
            tp_parts = [_plain(x) for x in tp.split(" / ")] if tp else []
            by_content[u["content"]] = {
                "content": u["content"],
                "tp": tp_parts,
            }
    # 收集每父标题下的兄弟标题
    sib: dict[str, set[str]] = {}
    for u in by_content.values():
        tp = u["tp"]
        if not tp:
            continue
        parent = tp[-2] if len(tp) >= 2 else "(root)"
        sib.setdefault(parent, set()).add(tp[-1])
    units: dict[str, dict] = {}
    matched = 0
    for cid, m in sparse["chunks"].items():
        u = by_content.get(m["content"])
        if not u:
            continue
        matched += 1
        tp = u["tp"]
        u["parent"] = tp[-2] if len(tp) >= 2 else "(root)"
        u["leaf"] = tp[-1] if tp else ""
        u["siblings"] = sorted(sib.get(u["parent"], set()))
        units[cid] = u
    print(f"[facts] chunks jsonl 与 sparse 按 content 对齐 {matched}/{len(sparse['chunks'])}")
    return units


def variants_for(u: dict, doc_title: str) -> dict[str, str]:
    """构造注入 content 变体。base = 原 content（对照）。"""
    base = u["content"]
    parent, leaf, sibs = u["parent"], u["leaf"], u["siblings"]
    chain = (" ".join(u["tp"])) if u["tp"] else ""
    p_structure = f"{chain}"
    p_sib = f"{parent} 本章含 {' '.join(sibs)}"
    return {
        "base": base,
        "p_title(父链)": f"{p_structure}\n{base}",
        "p_title+sib": f"{p_sib}\n{base}",
        "p_title+sib+doc": f"{p_sib} {doc_title}\n{base}",
    }


def main() -> None:
    sparse = load(WORKING_DIR / SPARSE_FILE)
    units = build_facts(sparse)
    # 文档标题：取该文档第一个块的 content 首行（各文档开头是标题/表头块）
    doc_titles: dict[str, str] = {}
    for cid, u in units.items():
        docid = sparse["chunks"][cid]["full_doc_id"]
        doc_titles.setdefault(docid, _plain(u["content"].splitlines()[0][:30]))

    for qid, case in CASES.items():
        q = case["query"]
        print(f"\n========== {qid} | {q} ==========", flush=True)
        base_rank = score(q, sparse, top_k=40)
        base_rank_of = {cid: (i, s) for i, (cid, s) in enumerate(base_rank)}

        for cid, label in case["chunks"]:
            if cid not in units:
                print(f"  [skip] {label}: {cid} 不在 chunks jsonl", flush=True)
                continue
            u = units[cid]
            docid = sparse["chunks"][cid]["full_doc_id"]
            print(f"  >> {label}  (doc={docid[:8]}, parent=「{u['parent']}」, siblings={len(u['siblings'])})")
            base_i, base_s = base_rank_of.get(cid, (None, 0.0))
            print(f"     base 原content: rank={base_i}  内积={base_s:.4f}  共享token=?", flush=True)
            for vname, vcontent in variants_for(u, doc_titles.get(docid, "")).items():
                injected = _sparse_encode([vcontent])[0]
                backup = sparse["index"][cid]
                sparse["index"][cid] = injected
                try:
                    ranks = score(q, sparse, top_k=40)
                    rank, s = next(((i, sc) for i, (cc, sc) in enumerate(ranks) if cc == cid), (None, 0.0))
                finally:
                    sparse["index"][cid] = backup
                shared = len(set(injected) & set(_sparse_encode([q])[0]))
                inpool = "进池" if rank is not None and rank < 40 else "未进"
                print(f"     {vname:<16}: pool#{rank if rank is not None else '-'}  dot={s:.4f}  共享{shared}  {inpool}", flush=True)


if __name__ == "__main__":
    main()