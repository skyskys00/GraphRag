"""M7 文档入库编排：上传文件 → M1 解析 → M2 切分 → M3 增量建图 → 稀疏索引重建 → 就绪。

注册表落盘 `<working_dir>/documents.json`：`{task_id: {doc_id, filename, status, error, created_at, deleted}}`。
- status: processing | ready | failed
- 删除为**软删**：注册表标记 deleted + 文件层清除（parse/chunks），PG 共享图数据保留，
  检索侧按 excluded_doc_ids 过滤，保证删除后不再召回（见 retriever.retrieve 的 exclude_docs）。
- 入库是全局资源（rag.ainsert + sparse 重建），用模块级 asyncio.Lock 串行化。
"""
from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any

from app.m1_parse.run import process_one
from app.m2_chunk.runner import process_document
from app.m5_retrieve.query_preprocess import load_entities_async
from app.m5_retrieve.sparse_index import SPARSE_FILE, build as build_sparse, load as load_sparse
from app.m6_generate.sidecar import load as load_sidecar

DOCS_FILE = "documents.json"

_ingest_lock = asyncio.Lock()


# ---------- 注册表 ----------

def _reg_path(deps: Any) -> Path:
    return deps.working_dir / DOCS_FILE


def _load_registry(deps: Any) -> dict[str, dict[str, Any]]:
    p = _reg_path(deps)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {}


def _save_registry(deps: Any, reg: dict[str, dict[str, Any]]) -> None:
    _reg_path(deps).write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")


def list_docs(deps: Any) -> list[dict[str, Any]]:
    """未删除文档列表，按创建时间倒序。doc_id 在入库完成后回填。"""
    reg = _load_registry(deps)
    rows = [r for r in reg.values() if not r.get("deleted")]
    rows.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return rows


def excluded_doc_ids(deps: Any) -> set[str]:
    """软删文档集合（检索侧过滤用）。"""
    reg = _load_registry(deps)
    return {r["doc_id"] for r in reg.values() if r.get("deleted") and r.get("doc_id")}


def allowed_doc_ids(deps: Any) -> set[str] | None:
    """白名单文档集合（检索侧限定范围用）。

    注册表有记录（即通过上传功能入库）且未删除的文档 = 允许检索的范围。
    若注册表为空（纯离线建库、未走上传流程），返回 None 表示不限定（全库检索），
    避免首次部署时因注册表为空导致全部检索不到。
    """
    reg = _load_registry(deps)
    ids = [r["doc_id"] for r in reg.values() if not r.get("deleted") and r.get("doc_id")]
    if not ids:
        return None
    return set(ids)


def get_doc(deps: Any, doc_id: str) -> dict[str, Any] | None:
    """按 doc_id 查未删除文档记录（预览接口取 filename 用）。"""
    reg = _load_registry(deps)
    for rec in reg.values():
        if rec.get("doc_id") == doc_id and not rec.get("deleted"):
            return rec
    return None


def register_processing(deps: Any, task_id: str, filename: str) -> dict[str, Any]:
    reg = _load_registry(deps)
    now = __import__("datetime").datetime.now().isoformat(timespec="seconds")
    rec = {"task_id": task_id, "doc_id": None, "filename": filename,
           "status": "processing", "error": None, "created_at": now, "deleted": False}
    reg[task_id] = rec
    _save_registry(deps, reg)
    return rec


def mark_doc(deps: Any, task_id: str, *, status: str, doc_id: str | None = None, error: str | None = None) -> None:
    reg = _load_registry(deps)
    rec = reg.get(task_id)
    if rec is None:
        return
    rec["status"] = status
    if doc_id:
        rec["doc_id"] = doc_id
    if error:
        rec["error"] = error
    _save_registry(deps, reg)


# ---------- 入库编排 ----------

def _single_doc_texts(chunks_root: Path, doc_id: str) -> tuple[str, list[str]]:
    f = chunks_root / f"{doc_id}.jsonl"
    if not f.exists():
        raise FileNotFoundError(f"{f} 缺失")
    units = [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]
    texts = [u["content"] for u in units if u.get("content", "").strip()]
    return "\n\n".join(texts), texts


async def build_workspace_deps(deps: Any) -> None:
    """重建 dependent 内存态：稀疏索引（读到 PG 最新全量 chunk）+ sidecar + 实体表。

    稀疏索引是上传后新文档能被检索的先决条件（启动时只在文件缺失才构建）。
    """
    ws = deps.workspace
    sparse_path = deps.working_dir / SPARSE_FILE
    await asyncio.to_thread(build_sparse, ws, sparse_path)
    deps.sparse = load_sparse(sparse_path)
    deps.sidecar = load_sidecar(deps.chunks_dir)
    try:
        deps.entities = await load_entities_async(ws)
    except Exception:  # noqa: BLE001 —— 实体名只是查询预处理的可选项
        deps.entities = None


async def ingest(deps: Any, src_path: Path, filename: str) -> str:
    """完整入库：M1 → M2 → M3 增量建图 → 重建 sparse/sidecar/entities。返回 doc_id。"""
    async with _ingest_lock:
        doc_dir = process_one(src_path, deps.parse_dir, engine=None)
        doc_id = doc_dir.name
        process_document(doc_dir, deps.chunks_dir)
        full_text, text_chunks = _single_doc_texts(deps.chunks_dir, doc_id)
        await deps.rag.ainsert_custom_chunks(full_text, text_chunks, doc_id=doc_id)
        await build_workspace_deps(deps)
        # 白名单同步：新文档加入允许检索集合
        if deps.allowed_docs is None:
            deps.allowed_docs = {doc_id}
        else:
            deps.allowed_docs.add(doc_id)
        return doc_id


async def ingest_task(deps: Any, src_path: Path, task_id: str, filename: str) -> None:
    """后台任务包装：登记结果 + 失败置 failed + 清理上传临时文件。"""
    try:
        doc_id = await ingest(deps, src_path, filename)
        mark_doc(deps, task_id, status="ready", doc_id=doc_id)
    except Exception as e:  # noqa: BLE001 —— 边界：任务失败要落 failed 状态
        mark_doc(deps, task_id, status="failed", error=f"{type(e).__name__}: {e}")
    finally:
        src_path.unlink(missing_ok=True)


# ---------- 删除（软删） ----------

async def delete_doc(deps: Any, doc_id: str) -> bool:
    reg = _load_registry(deps)
    for task_id, rec in reg.items():
        if rec.get("doc_id") == doc_id and not rec.get("deleted"):
            rec["deleted"] = True
            _save_registry(deps, reg)
            # 文件层清除；PG 共享图数据保留，检索靠 excluded 过滤
            shutil.rmtree(deps.parse_dir / doc_id, ignore_errors=True)
            (deps.chunks_dir / f"{doc_id}.jsonl").unlink(missing_ok=True)
            deps.excluded_docs.add(doc_id)
            if deps.allowed_docs is not None:
                deps.allowed_docs.discard(doc_id)
            deps.sidecar = load_sidecar(deps.chunks_dir)
            return True
    return False