"""M7 FastAPI 壳：/health、POST /answer（JSON）、GET /answer/stream（SSE）+ 轻量 WebUI 静态挂载。

启动见 runner.py。
"""
from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import collections, conversations, documents
from .bootstrap import WEB_DIRNAME, dispose, evict_deps, get_deps
from .events import sse
from .graph import collect_document_graph, collect_graph
from .respond import make_answer, stream_answer

_PROJ = Path(__file__).resolve().parents[2]


class AnswerRequest(BaseModel):
    query: str
    history: list[dict[str, Any]] = []
    response_type: str | None = None
    collection_id: str = "default"
    conversation_id: str | None = None


def _ensure_conversation(deps: Any, conv_id: str | None) -> None:
    """会话存在性校验；None 表示无会话模式（现状行为）。"""
    if conv_id and conversations.get_conversation(deps, conv_id) is None:
        raise HTTPException(status_code=404, detail=f"会话不存在: {conv_id}")


@asynccontextmanager
async def lifespan(fastapi_app: FastAPI):
    await get_deps()  # 预热默认库（单库行为不变）
    yield
    await dispose()


# docs_url 让给 /docs（文档管理接口）；Swagger 挪到 /swagger
app = FastAPI(title="GraphRAG M7 交互层", version="0.1.0", lifespan=lifespan,
              docs_url="/swagger", redoc_url=None)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/answer")
async def post_answer(req: AnswerRequest) -> dict[str, Any]:
    try:
        deps = await get_deps(req.collection_id)
        _ensure_conversation(deps, req.conversation_id)
        return await make_answer(deps, req.query, req.history,
                                 req.response_type, req.conversation_id)
    except Exception as e:  # noqa: BLE001 —— 边界：把 LLM/检索异常转成可读 500
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}") from e


@app.get("/answer/stream")
async def stream_answer_ep(
    q: str = Query(..., description="问题"),
    history: str = Query("", description="history JSON 字符串（无会话模式兼容）"),
    response: str | None = Query(None, description="response_type 中文要求"),
    collection_id: str = Query("default"),
    conversation_id: str | None = Query(None, description="会话 id，缺省无会话模式"),
) -> StreamingResponse:
    deps = await get_deps(collection_id)
    _ensure_conversation(deps, conversation_id)
    hist: list[dict[str, Any]] = []
    if history:
        try:
            hist = json.loads(history)
        except json.JSONDecodeError as e:
            raise HTTPException(status_code=400, detail=f"history 非 JSON：{e}") from e

    async def gen() -> AsyncIterator[str]:
        try:
            async for evt in stream_answer(deps, q, hist, response, conversation_id):
                yield evt
        except Exception as e:  # noqa: BLE001
            yield sse("error", {"message": f"{type(e).__name__}: {e}"})

    return StreamingResponse(
        gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/docs")
async def upload_doc(
    file: UploadFile = File(...),
    collection_id: str = Form("default"),
) -> dict[str, Any]:
    """文档上传：落盘 <collection>/uploads → 注册 processing → 后台跑 M1→M2→M3→sparse。"""
    deps = await get_deps(collection_id)
    filename = Path(file.filename or "upload").name  # 去路径，防目录穿越
    deps.uploads_dir.mkdir(parents=True, exist_ok=True)
    dest = deps.uploads_dir / f"{uuid.uuid4().hex[:8]}_{filename}"
    content = await file.read()
    dest.write_bytes(content)
    task_id = uuid.uuid4().hex
    documents.register_processing(deps, task_id, filename)
    asyncio.get_running_loop().create_task(
        documents.ingest_task(deps, dest, task_id, filename)
    )
    return {"task_id": task_id, "filename": filename, "status": "processing"}


@app.get("/docs")
async def list_docs(collection_id: str = Query("default")) -> list[dict[str, Any]]:
    """文档列表（含处理状态；doc_id 在入库完成后回填）。"""
    return documents.list_docs(await get_deps(collection_id))


@app.delete("/docs/{doc_id}")
async def delete_doc(doc_id: str, collection_id: str = Query("default")) -> dict[str, Any]:
    """软删文档：注册表标记 + 文件层清除 + 检索侧过滤。"""
    if not await documents.delete_doc(await get_deps(collection_id), doc_id):
        raise HTTPException(status_code=404, detail=f"文档不存在: {doc_id}")
    return {"deleted": doc_id}


@app.get("/docs/{doc_id}/preview")
async def preview_doc(doc_id: str, collection_id: str = Query("default")) -> dict[str, Any]:
    """文档全文预览：读 M2 产物 <collection>/chunks/<doc_id>.jsonl 返回全部 TextUnit。

    配合引用来源「原文档」跳转：前端按 text_unit_id 定位 + 置信度第一片段高亮。
    filename 优先从文档注册表取，没有则从第一个 chunk 的 file_path 推断。
    """
    deps = await get_deps(collection_id)
    f = deps.chunks_dir / f"{doc_id}.jsonl"
    if not f.exists():
        raise HTTPException(status_code=404, detail=f"文档未就绪(无切片): {doc_id}")
    units: list[dict[str, Any]] = []
    filename = ""
    for line in f.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        u = json.loads(line)
        img = u.get("image_path")
        units.append({
            "text_unit_id": u.get("text_unit_id"),
            "content": u.get("content") or "",
            "title_path": u.get("title_path"),
            "page_range": u.get("page_range"),
            "file_path": u.get("file_path"),
            "block_type": u.get("block_type") or "paragraph",
            "html": u.get("html") or None,
            "image_url": f"/docs/{doc_id}/{img}" if img else None,
        })
        if not filename and u.get("file_path"):
            filename = Path(u["file_path"]).name
    # 注册表有记录则以它的 filename 为准（上传时的原始文件名更准确）
    rec = documents.get_doc(deps, doc_id)
    if rec and rec.get("filename"):
        filename = rec["filename"]
    return {"doc_id": doc_id, "filename": filename, "units": units}


@app.get("/docs/{doc_id}/images/{name}")
async def doc_image(doc_id: str, name: str, collection_id: str = Query("default")):
    """多模态图片块的原图：serve <parse_dir>/<doc_id>/images/<name>。

    doc_id/name 仅允许单一路径段，且解析后须落在 images/ 之下，防目录穿越。
    """
    deps = await get_deps(collection_id)
    for seg in (doc_id, name):
        if not seg or seg in (".", "..") or "/" in seg or "\\" in seg:
            raise HTTPException(status_code=404, detail="非法路径")
    images_dir = (deps.parse_dir / doc_id / "images").resolve()
    target = (images_dir / name).resolve()
    if images_dir not in target.parents or not target.is_file():
        raise HTTPException(status_code=404, detail=f"图片不存在: {name}")
    return FileResponse(target)


@app.get("/graph")
async def graph_data(
    level: str = Query("entity", pattern="^(entity|document)$"),
    doc_id: str | None = Query(None),
    top_n: int = Query(0, ge=0),
    collection_id: str = Query("default"),
) -> dict[str, Any]:
    """知识图谱。level=entity 实体级（默认，向下兼容）；level=document 文档级（概念关联+话题聚类）。

    doc_id 仅 level=entity 时生效（单文档子图过滤）。
    top_n 仅 level=entity 时有效：>0 时按 PageRank 只保留 top_n 个核心节点（节点少于 40 时不筛选）。
    """
    deps = await get_deps(collection_id)
    if level == "document":
        # 文档元数据（filename / created_at）从文档注册表取
        doc_meta: dict[str, dict[str, Any]] = {}
        for rec in documents.list_docs(deps):
            did = rec.get("doc_id")
            if did:
                doc_meta[did] = {
                    "filename": rec.get("filename") or did,
                    "created_at": rec.get("created_at") or "",
                }
        return await collect_document_graph(
            deps.rag, deps.excluded_docs, deps.allowed_docs,
            workspace=deps.workspace, doc_meta=doc_meta,
            chunks_dir=deps.chunks_dir,
        )
    return await collect_graph(
        deps.rag, deps.excluded_docs, doc_id=doc_id, allowed_docs=deps.allowed_docs,
        workspace=deps.workspace, top_n=top_n,
    )


# ---------- M8 v3 多知识库 + 仪表盘 ----------

@app.get("/collections")
async def list_collections() -> list[dict[str, Any]]:
    """知识库列表（default 恒在首位）；doc_count = 该库 documents.json 未删 ready 数。"""
    items = collections.list_collections(_PROJ)
    for c in items:
        deps = await get_deps(c["id"])
        c["doc_count"] = sum(1 for r in documents.list_docs(deps) if r.get("status") == "ready")
    return items
    

@app.post("/collections")
async def create_collection(name: str = Query(...)) -> dict[str, str]:
    """新建知识库：生成 col_<uuid8>（兼作 workspace 与目录名）。"""
    cid = collections.create_collection(_PROJ, name.strip())
    return {"id": cid}


@app.patch("/collections/{col_id}")
async def rename_collection(col_id: str, name: str = Query(...)) -> dict[str, str]:
    if not collections.rename_collection(_PROJ, col_id, name.strip()):
        if col_id == "default":
            raise HTTPException(status_code=409, detail="默认知识库不可重命名")
        raise HTTPException(status_code=404, detail=f"知识库不存在: {col_id}")
    return {"id": col_id}


@app.delete("/collections/{col_id}")
async def delete_collection(col_id: str) -> dict[str, str]:
    if col_id == "default":
        raise HTTPException(status_code=409, detail="默认知识库不可删除")
    if not await collections.delete_collection(_PROJ, col_id):
        raise HTTPException(status_code=404, detail=f"知识库不存在: {col_id}")
    await evict_deps(col_id)
    return {"deleted": col_id}


# ---------- M8 v5 多会话 ----------

@app.get("/conversations")
async def list_conversations(collection_id: str = Query("default")) -> list[dict[str, Any]]:
    """会话列表（Collection 作用域），按 updated_at 倒序。"""
    return conversations.list_conversations(await get_deps(collection_id))


@app.post("/conversations")
async def create_conversation(
    collection_id: str = Query("default"),
    title: str | None = Query(None),
) -> dict[str, str]:
    """新建空会话；标题缺省「新对话」（首问自动命名）。"""
    deps = await get_deps(collection_id)
    cid = conversations.create_conversation(deps, title.strip() if title else None)
    return {"conversation_id": cid}


@app.get("/conversations/{conv_id}")
async def get_conversation(conv_id: str, collection_id: str = Query("default")) -> dict[str, Any]:
    """会话明细（完整 messages，含 citations/meta），进会话时拉取。"""
    deps = await get_deps(collection_id)
    conv = conversations.get_conversation(deps, conv_id)
    if conv is None:
        raise HTTPException(status_code=404, detail=f"会话不存在: {conv_id}")
    return conv


@app.patch("/conversations/{conv_id}")
async def rename_conversation(
    conv_id: str, collection_id: str = Query("default"), title: str = Query(...),
) -> dict[str, str]:
    deps = await get_deps(collection_id)
    if not conversations.rename_conversation(deps, conv_id, title.strip()):
        raise HTTPException(status_code=404, detail=f"会话不存在: {conv_id}")
    return {"conversation_id": conv_id}


@app.delete("/conversations/{conv_id}")
async def delete_conversation(conv_id: str, collection_id: str = Query("default")) -> dict[str, str]:
    deps = await get_deps(collection_id)
    if not await conversations.delete_conversation(deps, conv_id):
        raise HTTPException(status_code=404, detail=f"会话不存在: {conv_id}")
    return {"deleted": conv_id}


@app.get("/stats")
async def stats(collection_id: str = Query("default")) -> dict[str, Any]:
    """仪表盘聚合：文档/实体/关系计数 + 最近问答。"""
    deps = await get_deps(collection_id)
    doc_count = sum(1 for r in documents.list_docs(deps) if r.get("status") == "ready")

    import asyncpg
    conn = await asyncpg.connect(
        host="127.0.0.1", port=5432, user="postgres", password="postgres", database="postgres")
    try:
        nodes = await conn.fetchval(
            "SELECT count(*) FROM lightrag_graph_nodes WHERE workspace=$1", deps.workspace)
        edges = await conn.fetchval(
            "SELECT count(*) FROM lightrag_graph_edges WHERE workspace=$1", deps.workspace)
    finally:
        await conn.close()

    return {
        "doc_count": doc_count,
        "node_count": nodes,
        "edge_count": edges,
        "recent_queries": collections.load_recent_queries(_PROJ, collection_id),
    }


web_dir = Path(__file__).resolve().parent / WEB_DIRNAME
app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")