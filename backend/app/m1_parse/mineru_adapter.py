"""MinerU adapter：PDF/图片 + docx/pptx/xlsx -> parse/<doc_id>/（content_list + blocks.jsonl + 扩展字段）。

契约：docs/modules/M0_contracts/parse.md §2-§3；实测 v3.4.5：
- PDF/图片 产物结构 = <out>/<basename>/auto/<basename>_content_list.json（pipeline 后端）
- docx/pptx/xlsx 产物结构 = <out>/<basename>/office/<basename>_content_list.json（office 后端，零模型）
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import config, vision
from ._util import make_doc_id, write_jsonl, write_meta
from .blocks_builder import build_blocks_from_mineru


def _locate_output(tmp: Path, src_stem: str) -> Path:
    direct = list(Path(tmp).glob(f"{src_stem}/auto")) + list(Path(tmp).glob(f"{src_stem}/office"))
    if direct:
        return direct[0]
    cands = sorted(Path(tmp).rglob("auto"), key=lambda p: p.as_posix())
    if cands:
        return cands[0]
    cands = sorted(Path(tmp).rglob("office"), key=lambda p: p.as_posix())
    if cands:
        return cands[0]
    raise RuntimeError(f"MinerU 产物目录未找到: {tmp}")


def parse_with_mineru(src: Path, out_root: Path) -> Path:
    doc_id = make_doc_id(src)
    doc_dir = out_root / doc_id
    doc_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="m1_mineru_") as tmp_s:
        cmd = ["mineru", "-p", str(src), "-o", tmp_s,
               "-b", config.MINERU_BACKEND, "-l", config.MINERU_LANG]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(
                f"mineru failed rc={r.returncode}\n---stdout---\n{r.stdout[-1500:]}\n---stderr---\n{r.stderr[-1500:]}"
            )

        auto = _locate_output(Path(tmp_s), src.stem)
        cl = next((p for p in auto.glob("*content_list.json") if "_v2" not in p.name), None)
        mid = next(auto.glob("*_middle.json"), None)
        md = next(auto.glob("*.md"), None)
        images = auto / "images"

        if cl is None:
            raise RuntimeError(f"content_list.json 未找到（auto={auto}）")
        shutil.copyfile(cl, doc_dir / "content_list.json")
        if mid is not None:
            shutil.copyfile(mid, doc_dir / "_middle.json")
        if md is not None:
            shutil.copyfile(md, doc_dir / f"{src.stem}.md")
        if images.is_dir():
            if (doc_dir / "images").exists():
                shutil.rmtree(doc_dir / "images")
            shutil.copytree(images, doc_dir / "images")

    # blocks.jsonl（统一块级 + 扩展字段，见 parse.md §3）
    data = json.loads((doc_dir / "content_list.json").read_text(encoding="utf-8"))
    # 图片语义增强（MULTIMODAL.md §4.2）：VISION_ENABLED=false 时返回 {}，完全退化为纯文本链路
    captions = vision.caption_images(doc_dir, data)
    blocks = build_blocks_from_mineru(data, doc_id, captions)
    write_jsonl(doc_dir / "blocks.jsonl", blocks)

    write_meta(doc_dir, engine="mineru", src_name=src.name, parse_schema="parse-v1.1")
    return doc_dir


# 便于 CLI 可见
def _main() -> None:
    from .run import main
    raise SystemExit(main())


if __name__ == "__main__":
    _main()