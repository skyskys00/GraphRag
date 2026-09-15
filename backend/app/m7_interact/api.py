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

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import documents
from .bootstrap import WEB_DIRNAME, AppDeps, get_deps
from .events import sse
from .graph import collect_graph
from .respond import make_answer, stream_answer


class AnswerRequest(BaseModel):
    query: str
    history: list[dict[str, Any]] = []
    response_type: str | None = None


@asynccontextmanager
async def lifespan(fastapi_app: FastAPI):
    fastapi_app.state.deps = await get_deps()
    yield
    await fastapi_app.state.deps.dispose()


# docs_url 让给 /docs（文档管理接口）；Swagger 挪到 /swagger
app = FastAPI(title="GraphRAG M7 交互层", version="0.1.0", lifespan=lifespan,
              docs_url="/swagger", redoc_url=None)


def _deps() -> AppDeps:
    return app.state.deps


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/answer")
async def post_answer(req: AnswerRequest) -> dict[str, Any]:
    try:
        return await make_answer(_deps(), req.query, req.history, req.response_type)
    except Exception as e:  # noqa: BLE001 —— 边界：把 LLM/检索异常转成可读 500
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}") from e


@app.get("/answer/stream")
async def stream_answer_ep(
    q: str = Query(..., description="问题"),
    history: str = Query("", description="history JSON 字符串"),
    response: str | None = Query(None, description="response_type 中文要求"),
) -> StreamingResponse:
    hist: list[dict[str, Any]] = []
    if history:
        try:
            hist = json.loads(history)
        except json.JSONDecodeError as e:
            raise HTTPException(status_code=400, detail=f"history 非 JSON：{e}") from e

    async def gen() -> AsyncIterator[str]:
        try:
            async for evt in stream_answer(_deps(), q, hist, response):
                yield evt
        except Exception as e:  # noqa: BLE001
            yield sse("error", {"message": f"{type(e).__name__}: {e}"})

    return StreamingResponse(
        gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/docs")
async def upload_doc(file: UploadFile = File(...)) -> dict[str, Any]:
    """文档上传：落盘 data/uploads → 注册 processing → 后台跑 M1→M2→M3→sparse。"""
    deps = _deps()
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
async def list_docs() -> list[dict[str, Any]]:
    """文档列表（含处理状态；doc_id 在入库完成后回填）。"""
    return documents.list_docs(_deps())


@app.delete("/docs/{doc_id}")
async def delete_doc(doc_id: str) -> dict[str, Any]:
    """软删文档：注册表标记 + 文件层清除 + 检索侧过滤。"""
    if not await documents.delete_doc(_deps(), doc_id):
        raise HTTPException(status_code=404, detail=f"文档不存在: {doc_id}")
    return {"deleted": doc_id}


@app.get("/docs/{doc_id}/preview")
async def preview_doc(doc_id: str) -> dict[str, Any]:
    """文档全文预览：读 M2 产物 data/chunks/<doc_id>.jsonl 返回全部 TextUnit（按顺序）。

    配合引用来源「原文档」跳转：前端按 text_unit_id 定位 + 置信度第一片段高亮。
    """
    deps = _deps()
    rec = documents.get_doc(deps, doc_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"文档不存在或已删除: {doc_id}")
    f = deps.chunks_dir / f"{doc_id}.jsonl"
    if not f.exists():
        raise HTTPException(status_code=404, detail=f"文档未就绪(无切片): {doc_id}")
    units: list[dict[str, Any]] = []
    for line in f.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        u = json.loads(line)
        units.append({
            "text_unit_id": u.get("text_unit_id"),
            "content": u.get("content") or "",
            "title_path": u.get("title_path"),
            "page_range": u.get("page_range"),
            "file_path": u.get("file_path"),
        })
    return {"doc_id": doc_id, "filename": rec.get("filename") or "", "units": units}


@app.get("/graph")
async def graph_data(doc_id: str | None = Query(None)) -> dict[str, Any]:
    """知识图谱（服务端按软删文档集合过滤；doc_id 指定时进一步只保留该文档关联子图）。"""
    deps = _deps()
    return await collect_graph(deps.rag, deps.excluded_docs, doc_id=doc_id)


web_dir = Path(__file__).resolve().parent / WEB_DIRNAME
app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")