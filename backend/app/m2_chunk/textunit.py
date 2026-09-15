"""TextUnit 序列化与契约校验（textunit.md v2）。"""
from __future__ import annotations

import json
from pathlib import Path

REQUIRED = ("text_unit_id", "content", "tokens", "full_doc_id", "chunk_order_index", "file_path")


def validate(unit: dict) -> list[str]:
    errs = []
    for k in REQUIRED:
        if k not in unit:
            errs.append(f"missing {k}")
    if "content" in unit and not str(unit.get("content", "")).strip():
        errs.append("empty content")
    if "page_range" in unit and unit["page_range"] is not None and len(unit["page_range"]) != 2:
        errs.append("page_range 非法")
    return errs


def write_chunks_jsonl(doc_id: str, units: list[dict], out_root: Path) -> Path:
    out_root.mkdir(parents=True, exist_ok=True)
    path = out_root / f"{doc_id}.jsonl"
    bad = 0
    with open(path, "w", encoding="utf-8") as f:
        for u in units:
            errs = validate(u)
            if errs:
                bad += 1
                continue
            f.write(json.dumps(u, ensure_ascii=False) + "\n")
    if bad:
        raise RuntimeError(f"{doc_id}: {bad} 个不合规 TextUnit 被跳过")
    return path