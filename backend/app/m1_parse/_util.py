"""共享小助手：doc_id、写 doc.meta.json、copy 归一。"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path


def make_doc_id(src: Path) -> str:
    """doc_id 规则（契约 Document 部分）：已知源 = md5(规范化路径)。"""
    norm = str(src.resolve())
    return hashlib.md5(norm.encode("utf-8")).hexdigest()[:16]


def write_meta(doc_dir: Path, *, engine: str, src_name: str, parse_schema: str, note: str = "") -> None:
    meta = {
        "doc_id": doc_dir.name,
        "engine": engine,
        "source": src_name,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "parse_schema": parse_schema,
        "note": note,
    }
    (doc_dir / "doc.meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")