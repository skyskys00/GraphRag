"""办公行政库（eval_admin）建库脚本：把测试语料三份文档完整入库。

复用 m7 documents.ingest 生产链路（M1 解析 → M2 切分 → M3 图/向量入库 →
稀疏索引重建 → sidecar/实体表），与客服库（eval_cservice）建库方式一致。

产物：
- data/eval_admin_ws/       LightRAG 向量/图索引 + m5_sparse.json + documents.json
- data/chunks/eval_admin/   chunk JSONL
- data/parse/eval_admin/    M1 解析产物

用法：
    cd backend
    python scripts/build_eval_admin.py
"""
from __future__ import annotations

import asyncio
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]

CORPUS = PROJ / "inputs" / "testset_corpus" / "eval_admin"
FILES = ["A1_办公用品领用管理办法.pdf", "A2_员工差旅报销管理制度.docx", "A3_IT设备管理与领用规范.md"]


async def main() -> None:
    import sys
    sys.path.insert(0, str(PROJ))

    from app.m3_index.runner import _load_dotenv, build_rag
    from app.m2_chunk.runner import process_document
    from app.m1_parse.run import process_one
    from app.m5_retrieve.sparse_index import SPARSE_FILE
    from app.m7_interact.documents import ingest, build_workspace_deps

    _load_dotenv(PROJ / ".env")

    ws = "eval_admin_ws"
    working_dir = PROJ / "data" / ws
    chunks_dir = PROJ / "data" / "chunks" / "eval_admin"
    parse_dir = PROJ / "data" / "parse" / "eval_admin"
    for d in (working_dir, chunks_dir, parse_dir):
        d.mkdir(parents=True, exist_ok=True)

    rag = await build_rag(working_dir, workspace=ws)

    class _Deps:
        pass

    deps = _Deps()
    deps.workspace = ws
    deps.working_dir = working_dir
    deps.chunks_dir = chunks_dir
    deps.parse_dir = parse_dir
    deps.rag = rag
    deps.excluded_docs = set()
    deps.allowed_docs = None

    total_units = 0
    for fname in FILES:
        src = CORPUS / fname
        if not src.exists():
            raise FileNotFoundError(f"语料缺失: {src}")
        doc_id = await ingest(deps, src, fname)
        n = len(list(chunks_dir.glob(f"{doc_id}.jsonl")))
        units = len([l for l in (chunks_dir / f"{doc_id}.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()])
        total_units += units
        print(f"[OK] {fname} → doc_id={doc_id} chunks={units}", flush=True)

    await build_workspace_deps(deps)
    print(f"\n完成：{len(FILES)} 份文档入库，共 {total_units} 个 chunk")
    print(f"workspace: {working_dir}")
    print(f"sparse: {working_dir / SPARSE_FILE}")


if __name__ == "__main__":
    asyncio.run(main())