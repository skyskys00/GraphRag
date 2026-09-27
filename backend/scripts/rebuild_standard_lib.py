#!/usr/bin/env python
"""标准库全链路重建（M3_index §3.5 命令链脚本化）：M2 重切 → wipe PG → M3 建图入库 → M5 sparse。

触发场景：M2 切分逻辑 / chunker 参数 / 表格双表示 / 列名前缀 / NL 摘要等索引层改动后，
必须走完整重建链重建标准库，禁止为验证另建旁路 workspace（探底纪律第 4 条）。

用法：
    cd backend
    # 预览（默认，不碰任何库）
    python scripts/rebuild_standard_lib.py --workspace default_ws
    # 执行（wipe PG 该 workspace 的 13 表行 + 重跑 M2/M3/M5）
    python scripts/rebuild_standard_lib.py --workspace default_ws --execute

    # 其他库（如客服评测库，含列名前缀最终态）：
    python scripts/rebuild_standard_lib.py --workspace eval_cservice_ws \
        --parse data/parse/eval_cservice --chunks data/chunks/eval_cservice \
        --working-dir data/eval_cservice_ws --execute

失败即停（不 wipe / 不落盘），不产出半截库。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))  # 确保 `app.*` 可导入（不依赖 cwd）

DEFAULT_LAYOUT = {
    "default_ws": (PROJ / "data/parse", PROJ / "data/chunks", PROJ / "data/default_ws"),
}


def _fail(msg: str) -> None:
    print(f"[FAIL] {msg}", file=sys.stderr)
    raise SystemExit(1)


def _wipe_workspace(workspace: str) -> None:
    """清空 PG 中该 workspace 的全部轻量表行（13 表，凡含 workspace 列者）。"""
    import asyncpg

    async def _go() -> None:
        conn = await asyncpg.connect(
            host="127.0.0.1", port=5432, user="postgres", password="postgres", database="postgres"
        )
        try:
            tables = await conn.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename LIKE 'lightrag\\_%'"
            )
            for r in tables:
                await conn.execute(f'DELETE FROM "{r["tablename"]}" WHERE workspace=$1', workspace)
        finally:
            await conn.close()

    asyncio.run(_go())


def _read_units(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _run_m2(parse_dir: Path, chunks_dir: Path) -> None:
    """M2 重切：parse 每文档子目录 → chunks jsonl。"""
    from app.m2_chunk.runner import process_document

    doc_dirs = sorted(
        d for d in parse_dir.iterdir() if d.is_dir() and (d / "blocks.jsonl").exists()
    )
    if not doc_dirs:
        _fail(f"M2 源为空：{parse_dir}")
    chunks_dir.mkdir(parents=True, exist_ok=True)
    for d in doc_dirs:
        r = process_document(d, chunks_dir)
        print(f"  [M2] {d.name:<24} blocks={r['blocks_in']:<4} units={r['units_out']}")


def _run_m3(working_dir: Path, workspace: str, chunks_dir: Path) -> None:
    """M3 建图入库：build_rag + 逐文档 ainsert_custom_chunks。"""
    from app.m3_index.runner import _load_dotenv, build_rag

    _load_dotenv(PROJ / ".env")
    rag = asyncio.run(build_rag(working_dir, workspace=workspace))
    docs = []
    for f in sorted(chunks_dir.glob("*.jsonl")):
        units = _read_units(f)
        if not units:
            continue
        doc_id = units[0]["full_doc_id"]
        texts = [u["content"] for u in units if u.get("content", "").strip()]
        docs.append({"doc_id": doc_id, "full_text": "\n\n".join(texts), "text_chunks": texts})

    async def _insert_all() -> None:
        ok = 0
        for d in docs:
            await rag.ainsert_custom_chunks(d["full_text"], d["text_chunks"], doc_id=d["doc_id"])
            print(f"  [M3] {d['doc_id'][:12]} chunks={len(d['text_chunks'])}")
            ok += 1
        await rag.finalize_storages()
        print(f"  [M3] 完成 ok={ok}/{len(docs)}")

    asyncio.run(_insert_all())


def _run_m5(workspace: str, working_dir: Path, chunks_dir: Path) -> Path:
    """M5 sparse 重建：asyncio.to_thread(build_sparse, ...) 落盘 m5_sparse.json。"""
    from app.m5_retrieve.sparse_index import SPARSE_FILE, build as build_sparse

    out = working_dir / SPARSE_FILE

    async def _go() -> Path:
        await asyncio.to_thread(build_sparse, workspace, out, chunks_dir)
        return out

    return asyncio.run(_go())


def _pg_count(workspace: str) -> int:
    """PG 中该 workspace 的非空 doc_chunks 行数。"""
    import asyncpg

    async def _go() -> int:
        conn = await asyncpg.connect(
            host="127.0.0.1", port=5432, user="postgres", password="postgres", database="postgres"
        )
        try:
            return await conn.fetchval(
                "SELECT COUNT(*) FROM lightrag_doc_chunks WHERE workspace=$1 AND content <> ''",
                workspace,
            )
        finally:
            await conn.close()

    return asyncio.run(_go())


def _align_check(workspace: str, working_dir: Path, chunks_dir: Path) -> None:
    """对齐校验：PG doc_chunks 数 == chunks jsonl units 数 == sparse chunks 数。"""
    from app.m5_retrieve.sparse_index import SPARSE_FILE

    n_pg = _pg_count(workspace)
    n_units = sum(len(_read_units(f)) for f in chunks_dir.glob("*.jsonl"))
    sp = working_dir / SPARSE_FILE
    n_sp = len(json.loads(sp.read_text(encoding="utf-8"))["chunks"]) if sp.exists() else 0
    print(f"  对齐: PG={n_pg}  chunks_jsonl={n_units}  sparse={n_sp}")
    if n_pg == n_units == n_sp:
        print(f"  [OK] 三源对齐 {n_pg}")
    else:
        _fail(f"对齐失败：PG={n_pg} chunks_jsonl={n_units} sparse={n_sp}")


def main() -> None:
    ap = argparse.ArgumentParser(prog="rebuild-standard-lib", description="M3 §3.5 标准库全链路重建")
    ap.add_argument("--workspace", required=True, help="目标 workspace（PG 行 + sparse 落盘用）")
    ap.add_argument("--parse", default=None, help="parse 根目录；默认按 workspace 匹配内置布局")
    ap.add_argument("--chunks", default=None, help="chunks 输出根目录；默认同 parse")
    ap.add_argument("--working-dir", default=None, help="LightRAG working_dir（sparse 落盘 + M3）；默认内置")
    ap.add_argument("--execute", action="store_true", help="真正执行（wipe PG + 重跑 M2/M3/M5）；缺省仅预览")
    args = ap.parse_args()

    if args.parse or args.chunks or args.working_dir:
        if not (args.parse and args.chunks and args.working_dir):
            _fail("自定义布局须三者全给：--parse/--chunks/--working-dir")
        parse_dir, chunks_dir, working_dir = Path(args.parse), Path(args.chunks), Path(args.working_dir)
    else:
        try:
            parse_dir, chunks_dir, working_dir = DEFAULT_LAYOUT[args.workspace]
        except KeyError:
            # 未内置布局时按 <ws> 目录推断（如 eval_cservice_ws → data/parse/eval_cservice 等）
            stem = args.workspace.removesuffix("_ws")
            parse_dir = PROJ / "data/parse" / stem
            chunks_dir = PROJ / "data/chunks" / stem
            working_dir = PROJ / "data" / args.workspace

    parse_dir, chunks_dir = parse_dir.resolve(), chunks_dir.resolve()
    working_dir = working_dir.resolve()

    print(f"重建目标: workspace={args.workspace}")
    print(f"  parse      : {parse_dir}")
    print(f"  chunks     : {chunks_dir}")
    print(f"  working_dir: {working_dir}")

    if args.execute:
        print("\n[步骤 1] M2 重切")
        _run_m2(parse_dir, chunks_dir)
        print("\n[步骤 2] wipe PG workspace 行（13 表）")
        _wipe_workspace(args.workspace)
        print(f"  [wipe] {args.workspace} 已清空")
        print("\n[步骤 3] M3 建图入库")
        _run_m3(working_dir, args.workspace, chunks_dir)
        print("\n[步骤 4] M5 sparse 重建")
        out = _run_m5(args.workspace, working_dir, chunks_dir)
        print(f"  [sparse] -> {out}")
        print("\n=== rebuild 完成 ===")
        _align_check(args.workspace, working_dir, chunks_dir)
    else:
        n_pg = _pg_count(args.workspace)
        n_units = sum(len(_read_units(f)) for f in chunks_dir.glob("*.jsonl"))
        print(f"\n[预览] workspace={args.workspace} 现状：PG doc_chunks={n_pg}  chunks_jsonl={n_units}")
        print(f"[预览] 将执行 M2 重切 → wipe PG → M3 建图入库 → M5 sparse 重建")
        print(f"[预览] 加 --execute 才真正执行（会清空该 workspace 的 PG 行并重建）")


if __name__ == "__main__":
    main()