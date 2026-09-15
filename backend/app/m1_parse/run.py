"""M1 解析层：源文件 -> 统一 parse/<doc_id>/ 产物。

契约：docs/modules/M0_contracts/parse.md
产物：<basename>.md / content_list.json / blocks.jsonl / doc.meta.json / images/
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import DEFAULT_PARSE_ROOT, DOCLING_EXTS, MINERU_EXTS
from .docling_adapter import parse_with_docling
from .mineru_adapter import parse_with_mineru


def _report(src: Path, out: Path, engine: str) -> None:
    print(f"[ok] engine={engine:7s} {src.name} -> {out}")

def _route(ext: str, engine: str | None) -> str:
    if engine:
        return engine
    if ext.lower() in MINERU_EXTS:
        return "mineru"
    if ext.lower() in DOCLING_EXTS:
        return "docling"
    raise ValueError(f"unsupported ext: {ext}")

def process_one(src: Path, out_root: Path, engine: str | None) -> Path:
    eng = _route(src.suffix.lower(), engine)
    out_root.mkdir(parents=True, exist_ok=True)
    if eng == "mineru":
        out = parse_with_mineru(src, out_root)
    elif eng == "docling":
        out = parse_with_docling(src, out_root)
    else:
        raise ValueError(eng)
    _report(src, out, eng)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="m1-parse", description="M1 解析层（parse.md 契约）")
    ap.add_argument("-s", "--src", required=True, help="文件或目录")
    ap.add_argument("-o", "--output", default=str(DEFAULT_PARSE_ROOT), help="输出根目录（每文档一个子目录）")
    ap.add_argument("--engine", choices=["mineru", "docling"], default=None, help="强制引擎（默认按扩展名路由）")
    args = ap.parse_args(argv)

    src = Path(args.src)
    out_root = Path(args.output)
    sources = [src] if src.is_file() else sorted(p for p in src.iterdir() if p.is_file() and not p.name.startswith("."))
    if not sources:
        print(f"[warn] 目录为空: {src}", file=sys.stderr)
        return 1

    ok, fail = 0, 0
    for p in sources:
        try:
            process_one(p, out_root, args.engine)
            ok += 1
        except Exception as e:  # noqa: BLE001 - 批量任务逐文件隔离
            print(f"[fail] {p.name}: {type(e).__name__}: {e}", file=sys.stderr)
            fail += 1
    print(f"\n=== M1 完成：ok={ok} fail={fail} ===")
    return 0 if fail == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())