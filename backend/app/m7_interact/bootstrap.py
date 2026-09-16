"""M7 依赖装配：一次性构建 RAG/稀疏索引/实体/溯源 sidecar/query_func，模块级缓存。

复用 m6_generate.runner 的构建路径（都与 M4 PG 库、M2 chunk 产物对接），
M7 只负责把依赖备齐后交给 orchestrator.answer，不新增检索/生成逻辑。
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.m3_index.providers import build_llm_func, build_query_stream_func
from app.m3_index.runner import _load_dotenv, build_rag
from app.m5_retrieve.query_preprocess import load_entities_async
from app.m5_retrieve.sparse_index import SPARSE_FILE, build as build_sparse, load as load_sparse
from app.m6_generate.sidecar import load as load_sidecar

from . import documents

WEB_DIRNAME = "web"


@dataclass
class AppDeps:
    rag: Any
    sparse: dict[str, Any]
    entities: list[str] | None
    sidecar: Any
    query_func: Any
    query_stream_func: Any
    proj: Path
    # v2：文档路径 + 软删排除集合（见 documents.py / retriever.exclude_docs）
    working_dir: Path
    chunks_dir: Path
    parse_dir: Path
    uploads_dir: Path
    excluded_docs: set[str]
    # v2.3.1：白名单模式——只检索已上传（未删除）的文档
    allowed_docs: set[str] | None

    async def dispose(self) -> None:
        await self.rag.finalize_storages()


_config: dict[str, str] = {"working_dir": "data/lightrag_m4", "chunks_dir": "data/chunks"}
_deps: AppDeps | None = None


def configure(working_dir: str | None = None, chunks_dir: str | None = None) -> None:
    if working_dir:
        _config["working_dir"] = working_dir
    if chunks_dir:
        _config["chunks_dir"] = chunks_dir


async def build_deps(proj: Path, working_dir: str, chunks_dir: str) -> AppDeps:
    rag = await build_rag(proj / working_dir)

    ws = os.environ.get("POSTGRES_WORKSPACE", "lightrag_m4")
    sparse_path = proj / working_dir / SPARSE_FILE
    if not sparse_path.exists():
        await asyncio.to_thread(build_sparse, ws, sparse_path)
    sparse = load_sparse(sparse_path)

    entities: list[str] | None = None
    try:
        entities = await load_entities_async(ws)
    except Exception:  # noqa: BLE001 —— 实体名只是 query 预处理的可选项
        entities = None

    sidecar = load_sidecar(proj / chunks_dir)
    _, query_func = build_llm_func()
    query_stream_func = build_query_stream_func()
    wd = proj / working_dir
    deps = AppDeps(
        rag=rag, sparse=sparse, entities=entities, sidecar=sidecar,
        query_func=query_func, query_stream_func=query_stream_func, proj=proj,
        working_dir=wd, chunks_dir=proj / chunks_dir,
        parse_dir=proj / "data" / "parse", uploads_dir=proj / "data" / "uploads",
        excluded_docs=set(), allowed_docs=None,
    )
    deps.excluded_docs = documents.excluded_doc_ids(deps)
    deps.allowed_docs = documents.allowed_doc_ids(deps)
    return deps


async def get_deps() -> AppDeps:
    global _deps
    if _deps is None:
        proj = Path(__file__).resolve().parents[2]
        _load_dotenv(proj / ".env")
        _deps = await build_deps(proj, _config["working_dir"], _config["chunks_dir"])
    return _deps