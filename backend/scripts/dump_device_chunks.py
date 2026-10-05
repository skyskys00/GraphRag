"""dump 器械库各文档的 chunk 内容，供题集重建时人工/agent 出题取材。

用法：python3 scripts/dump_device_chunks.py [--out /tmp/device_chunks]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

COL = Path(__file__).resolve().parent.parent / "data/collections/col_b7b876b1"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/device_chunks")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    docs = json.load(open(COL / "documents.json", encoding="utf-8"))
    for d in docs.values():
        if d.get("deleted"):
            continue
        did, fn = d["doc_id"], d["filename"]
        src = COL / "chunks" / f"{did}.jsonl"
        if not src.exists():
            print(f"SKIP {fn}: no chunks file")
            continue
        parts = [f"# 文档: {fn}", f"# doc_id: {did}", ""]
        n = 0
        with src.open(encoding="utf-8") as f:
            for i, ln in enumerate(f):
                c = json.loads(ln)
                parts.append(
                    f"=== [{i}] {c.get('text_unit_id')} | type={c.get('block_type')} "
                    f"| heading={c.get('heading')} | title_path={c.get('title_path')} ==="
                )
                parts.append(c.get("content", ""))
                parts.append("")
                n = i + 1
        text = "\n".join(parts)
        (out / f"{did}__{fn}.txt").write_text(text, encoding="utf-8")
        print(f"{fn}: {n} chunks, {len(text)} chars")
    print(f"\n输出目录: {out}")


if __name__ == "__main__":
    main()
