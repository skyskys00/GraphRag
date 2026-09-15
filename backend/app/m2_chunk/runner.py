"""M2 切分层 CLI：data/parse/<doc_id>/ -> data/chunks/<doc_id>.jsonl（TextUnit v2）。

运行：conda run -n graphrag python -m app.m2_chunk.run -s data/parse -o data/chunks
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .chunker import chunk_blocks
from .textunit import write_chunks_jsonl


def process_document(doc_dir: Path, out_root: Path) -> dict:
    blocks_path = doc_dir / "blocks.jsonl"
    if not blocks_path.exists():
        raise FileNotFoundError(f"{doc_dir}/blocks.jsonl 缺失")
    blocks = [json.loads(line) for line in blocks_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    meta = {}
    meta_path = doc_dir / "doc.meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))

    doc_id = meta.get("doc_id") or doc_dir.name
    file_path = meta.get("source") or f"{doc_dir.name}.unknown"
    units = chunk_blocks(blocks, doc_id, file_path)
    out = write_chunks_jsonl(doc_id, units, out_root)
    return {"doc_id": doc_id, "blocks_in": len(blocks), "units_out": len(units), "out": str(out)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="m2-chunk", description="M2 切分层（textunit v2）")
    ap.add_argument("-s", "--source", default="data/parse", help="parse 根目录（每文档一子目录）")
    ap.add_argument("-o", "--output", default="data/chunks", help="chunks 输出根目录")
    args = ap.parse_args(argv)

    src = Path(args.source)
    out_root = Path(args.output)
    doc_dirs = sorted(d for d in src.iterdir() if d.is_dir())
    if not doc_dirs:
        print(f"[warn] {src} 下没有 parse 产物")
        return 1

    ok, fail, tot_units = 0, 0, 0
    rows = []
    for d in doc_dirs:
        try:
            r = process_document(d, out_root)
            rows.append(r); ok += 1; tot_units += r["units_out"]
        except Exception as e:  # noqa: BLE001
            print(f"[fail] {d.name}: {type(e).__name__}: {e}", file=sys.stderr); fail += 1
    for r in rows:
        print(f"[ok] {r['doc_id']:<18} blocks={r['blocks_in']:<4} units={r['units_out']}")
    print(f"\n=== M2 完成：ok={ok} fail={fail} 总TextUnit={tot_units} ===")
    return 0 if fail == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())