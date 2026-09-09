#!/usr/bin/env python3
"""parse.py：多格式解析调度器。

输入 inputs/raw/ 下混合格式文档，按扩展名路由到解析引擎，
每篇规范化输出到 parsed/<doc_id>.md + parsed/<doc_id>.md.json，末尾写 _parse_report.json。

接口规范见 p1.md §4.1；行为约定见 §3.1：路由 / 幂等 / 容错 / 元数据。
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 扩展名 → 引擎
ROUTES = {
    ".pdf": "mineru",
    ".docx": "docling",
    ".pptx": "docling",
    ".xlsx": "docling",
    ".html": "docling",
    ".epub": "docling",
    ".md": "copy",
    ".txt": "copy",
    ".markdown": "copy",
}
MINERU_BACKEND = "pipeline"  # 显式 pipeline；hybrid-engine 吃显存且慢
STAGING = "_staging"         # 引擎原始产物暂存目录，规范化后清理


def _md_headings(md_text: str) -> list[str]:
    """抓取 markdown 标题行（# 开头），供索引期标题层级切分使用。"""
    return [ln for ln in md_text.splitlines() if re.match(r"^#{1,6}\s", ln.strip())]


def _mineru_stats(middle_json: Path) -> tuple[int, int]:
    """从 mineru 的 *_middle.json 统计页数与表格数（缺文件时退化为 0,0）。"""
    try:
        info = json.loads(middle_json.read_text(encoding="utf-8")).get("pdf_info", [])
    except Exception:
        return 0, 0
    pages = len(info)
    tables = sum(
        1
        for pg in info
        for b in pg.get("preproc_blocks", [])
        if b.get("type") == "table"
    )
    return pages, tables


def _run(cmd: list[str], cwd: Path) -> tuple[int, str]:
    """跑一个引擎子进程，返回 (返回码, 合并后的输出文本)。"""
    proc = subprocess.run(
        cmd, cwd=str(cwd), capture_output=True, text=True, timeout=3600
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _find_md(root: Path, doc_id: str) -> Path | None:
    """在引擎产物树里找一个同名 md（docling/mineru 各自目录结构不同）。"""
    for f in sorted(root.rglob(f"{doc_id}.md")):
        return f
    md = next(iter([p for p in sorted(root.rglob("*.md")) if p.name != f"{doc_id}.md"]), None)
    return md


# ---------- 三个引擎处理器 ----------

def _engine_mineru(src: Path, staging: Path, doc_id: str) -> tuple[str, dict]:
    """MinerU pipeline 转 PDF → md，返回 (md 文本, 供元数据用的统计)。"""
    # 与 common.py 一致：huggingface 走镜像（模型下载阶段默认走，已改 modelscope 兜底）
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    code, out = _run(["mineru", "-p", str(src), "-o", str(staging), "-b", MINERU_BACKEND, "-m", "auto"], ROOT)
    if code != 0:
        raise RuntimeError(f"mineru 退出码 {code}：{out[-500:]}")
    md_file = _find_md(staging, doc_id)
    if md_file is None:
        raise RuntimeError(f"mineru 未产出 md（输出目录：{staging}）")
    md_text = md_file.read_text(encoding="utf-8")
    pages, tables = _mineru_stats(staging / doc_id / "auto" / f"{doc_id}_middle.json")
    return md_text, {"engine": f"mineru-{MINERU_BACKEND}", "page_count": pages, "tables_count": tables}


def _engine_docling(src: Path, staging: Path, doc_id: str) -> tuple[str, dict]:
    """Docling 转 docx/pptx/xlsx/html/epub → md。"""
    code, out = _run(["docling", "convert", str(src), "--to", "md", "--output", str(staging)], ROOT)
    if code != 0:
        raise RuntimeError(f"docling 退出码 {code}：{out[-500:]}")
    md_file = _find_md(staging, doc_id)
    if md_file is None:
        raise RuntimeError(f"docling 未产出 md（输出目录：{staging}）")
    md_text = md_file.read_text(encoding="utf-8")
    return md_text, {"engine": "docling", "page_count": 0, "tables_count": 0}


def _engine_copy(src: Path, staging: Path, doc_id: str) -> tuple[str, dict]:
    """md/txt 直通拷贝，不加改动。"""
    md_text = src.read_text(encoding="utf-8")
    return md_text, {"engine": "copy", "page_count": 0, "tables_count": 0}


ENGINES = {"mineru": _engine_mineru, "docling": _engine_docling, "copy": _engine_copy}


def parse_one(src: Path, out_dir: Path, force: bool, warning: list[str]) -> tuple[bool, dict]:
    """解析单篇。返回 (成功?, 元数据 dict)。幂等：产物已存在且源未变则跳过。"""
    doc_id = src.stem
    out_md = out_dir / f"{doc_id}.md"
    out_json = out_dir / f"{doc_id}.md.json"
    source_mtime = src.stat().st_mtime

    if not force and out_md.exists() and out_md.stat().st_mtime >= source_mtime:
        return True, {"doc_id": doc_id, "status": "skip", "reason": "已存在且源未变"}

    staging = out_dir / STAGING / doc_id
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)

    engine = ROUTES[src.suffix.lower()]
    try:
        md_text, stats = ENGINES[engine](src, staging, doc_id)
        if not md_text.strip():
            raise RuntimeError("产物为空 md（无正文）")
        meta = {
            "doc_id": doc_id,
            "source_path": str(src),
            "source_format": src.suffix.lower().lstrip("."),
            "source_mtime": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(source_mtime)),
            "parse_engine": stats["engine"],
            "page_count": stats["page_count"],
            "tables_count": stats["tables_count"],
            "headings": _md_headings(md_text),
            "span_map": [],
            "parse_warning": "; ".join(warning) or None,
        }
        out_md.write_text(md_text, encoding="utf-8")
        out_json.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        # 清理历史遗留的引擎子目录（如 mineru 的 <stem>/auto/），避免 parsed/ 重复 md
        stale = out_dir / doc_id
        if stale.is_dir():
            shutil.rmtree(stale)
        return True, meta
    except Exception as e:
        return False, {"doc_id": doc_id, "status": "failed", "reason": str(e)}
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="多格式 → 统一 Markdown 解析层")
    ap.add_argument("--raw-dir", type=Path, default=ROOT / "inputs" / "raw", help="原始上传物目录")
    ap.add_argument("--out-dir", type=Path, default=ROOT / "inputs" / "parsed", help="解析产物目录")
    ap.add_argument("--force", action="store_true", help="忽略幂等，强制全量重解析")
    ap.add_argument("--ext", default=",".join(sorted(e[1:] for e in ROUTES)), help="只处理这些扩展名（逗号分隔）")
    args = ap.parse_args()

    raw_dir, out_dir = args.raw_dir, args.out_dir
    if not raw_dir.is_dir():
        raise SystemExit(f"raw 目录不存在：{raw_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

    wanted = {("." + e.strip().lstrip(".")).lower() for e in args.ext.split(",") if e.strip()}
    files = sorted(f for f in raw_dir.iterdir() if f.is_file() and f.suffix.lower() in wanted)
    if not files:
        raise SystemExit(f"raw/ 下没有可解析的文档（扩展名：{sorted(wanted)}）")

    # doc_id 冲突检测（§4.4）：同 stem 不同后缀 → 停止该批
    seen: dict[str, Path] = {}
    for f in files:
        if f.stem in seen:
            raise SystemExit(f"doc_id 冲突：{seen[f.stem]!s} 与 {f!s} 同名不同后缀")
        seen[f.stem] = f

    started = time.time()
    ok, failed = [], []
    for f in files:
        print(f"解析 {f.name} …", flush=True)
        good, meta = parse_one(f, out_dir, args.force, warning=[])
        (ok if good else failed).append(meta)
        tag = {"skip": "skip", "failed": "failed"}.get(meta.get("status"), "ok")
        print(f"  → {tag}：{meta.get('doc_id')}", flush=True)

    report = {
        "ok": ok,
        "failed": failed,
        "elapsed_sec": round(time.time() - started, 2),
    }
    (out_dir / "_parse_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n完成：ok={len(ok)} failed={len(failed)}，report → {out_dir / '_parse_report.json'}")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()