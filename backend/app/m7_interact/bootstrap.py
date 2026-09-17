"""M7 依赖装配：按 collection 构建 RAG/稀疏索引/实体/溯源 sidecar/query_func，dict 缓存。

复用 m6_generate.runner 的构建路径（都与 M4 PG 库、M2 chunk 产物对接），
M7 只负责把依赖备齐后交给 orchestrator.answer，不新增检索/生成逻辑。

collection = workspace 值（M8 v3）：默认库 `default` → workspace `lightrag_m4`；
新库 id 兼作 workspace（`col_<uuid8>`）。每个库一个独立 AppDeps（自己的 rag/sparse/
实体/上传白名单）→ 检索/图谱/文档逐库隔离，respond 零改动。

env 注意：backend/.env 已移除 `POSTGRES_WORKSPACE`——postgres_impl import 时会
load_dotenv(".env") 重新注入，pop 无效；移除后 db.workspace=None，各实例自带的
workspace 生效（见 m3_index.runner._load_dotenv 修复 + spike 实证）。
"""
from __future__ import annotations

import asyncio
import json
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
from .collections import collection_paths

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
    collection_id: str
    workspace: str
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


# 默认库布局可用 -w/--chunks 覆盖（dev 用）；新库固定 data/collections/<id>/。
_config: dict[str, str] = {"working_dir": "data/lightrag_m4", "chunks_dir": "data/chunks"}
_PROJ = Path(__file__).resolve().parents[2]
_deps_map: dict[str, AppDeps] = {}


def configure(working_dir: str | None = None, chunks_dir: str | None = None) -> None:
    if working_dir:
        _config["working_dir"] = working_dir
    if chunks_dir:
        _config["chunks_dir"] = chunks_dir


def _default_layout() -> dict[str, str]:
    return {
        "working_dir": _config["working_dir"],
        "chunks_dir": _config["chunks_dir"],
        "parse_dir": "data/parse",
        "uploads_dir": "data/uploads",
    }


async def _ensure_sparse(ws: str, path: Path) -> dict[str, Any]:
    """构建稀疏索引；空 workspace 落空索引（新库无文档时 retrieval 返回空）。"""
    if path.exists():
        return load_sparse(path)
    try:
        await asyncio.to_thread(build_sparse, ws, path)
    except SystemExit:  # build_sparse 对空库 raise；落空索引而非报错
        path.write_text(json.dumps({"index": {}, "chunks": {}}), encoding="utf-8")
    return load_sparse(path)


async def build_deps(proj: Path, col_id: str) -> AppDeps:
    ws = "lightrag_m4" if col_id == "default" else col_id
    paths = collection_paths(proj, col_id, _default_layout())

    rag = await build_rag(proj / paths["working_dir"], ws)
    sparse = await _ensure_sparse(ws, proj / paths["working_dir"] / SPARSE_FILE)

    entities: list[str] | None = None
    try:
        entities = await load_entities_async(ws)
    except Exception:  # noqa: BLE001 —— 实体名只是 query 预处理的可选项
        entities = None

    sidecar = load_sidecar(proj / paths["chunks_dir"])
    _, query_func = build_llm_func()
    query_stream_func = build_query_stream_func()

    wd = proj / paths["working_dir"]
    for sub in (wd, proj / paths["chunks_dir"], proj / paths["parse_dir"], proj / paths["uploads_dir"]):
        sub.mkdir(parents=True, exist_ok=True)

    deps = AppDeps(
        rag=rag, sparse=sparse, entities=entities, sidecar=sidecar,
        query_func=query_func, query_stream_func=query_stream_func, proj=proj,
        collection_id=col_id, workspace=ws,
        working_dir=wd, chunks_dir=proj / paths["chunks_dir"],
        parse_dir=proj / paths["parse_dir"], uploads_dir=proj / paths["uploads_dir"],
        excluded_docs=set(), allowed_docs=None,
    )
    deps.excluded_docs = documents.excluded_doc_ids(deps)
    deps.allowed_docs = documents.allowed_doc_ids(deps)
    return deps


async def get_deps(collection_id: str = "default") -> AppDeps:
    """按 collection 缓存构建；default 缺省即现有单库行为。"""
    if collection_id not in _deps_map:
        _deps_map[collection_id] = await build_deps(_PROJ, collection_id)
    return _deps_map[collection_id]


async def evict_deps(collection_id: str) -> None:
    """回收单库 deps（删库后调用，避免持活连接）。"""
    deps = _deps_map.pop(collection_id, None)
    if deps is not None:
        await deps.dispose()


async def dispose() -> None:
    for deps in list(_deps_map.values()):
        await deps.dispose()
    _deps_map.clear()