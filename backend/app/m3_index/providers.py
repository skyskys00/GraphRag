"""M3 providers：Xinference bge-m3（dense）+ DeepSeek LLM（抽取用 flash，中文 zh）。"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import numpy as np

XINFERENCE_URL = os.environ.get("XINFERENCE_URL", "http://127.0.0.1:9997")
XINFERENCE_USER = os.environ.get("XINFERENCE_USER", "admin")
XINFERENCE_PASSWORD = os.environ.get("XINFERENCE_PASSWORD", "graphrag_local")
EMBED_MODEL = os.environ.get("EMBEDDING_MODEL", "bge-m3")


def _xf_token() -> str:
    import urllib.request

    body = json.dumps({"username": XINFERENCE_USER, "password": XINFERENCE_PASSWORD}).encode()
    req = urllib.request.Request(f"{XINFERENCE_URL}/token", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())["access_token"]


def _xf_embed(texts: list[str]) -> np.ndarray:
    """同步请求 Xinference /v1/embeddings（dense，float）。"""
    import urllib.request

    token = _xf_token()
    body = json.dumps({"model": EMBED_MODEL, "input": texts, "encoding_format": "float"}).encode()
    req = urllib.request.Request(f"{XINFERENCE_URL}/v1/embeddings", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=120) as r:
        resp = json.loads(r.read().decode())
    rows = [d["embedding"] for d in sorted(resp["data"], key=lambda x: x["index"])]
    return np.array(rows, dtype=np.float32)


def build_embedding_func() -> Any:
    """LightRAG EmbeddingFunc（dense，bge-m3 via Xinference）。"""
    from lightrag.utils import EmbeddingFunc

    async def func(texts: list[str]) -> np.ndarray:
        return await asyncio.to_thread(_xf_embed, texts)

    return EmbeddingFunc(embedding_dim=1024, max_token_size=8192, func=func, model_name=f"Xinference:{EMBED_MODEL}")


def build_storage_config() -> dict:
    """M4 存储后端开关：按 STORAGE_BACKEND 选 LightRAG 存储组合。

    - local：官方默认四件套（JsonKV + NetworkX + NanoVectorDB + JsonDocStatus），文件态零依赖；
    - pg：官方 Postgres 完整组合（PGKV + PGVector + PGTableGraphStorage 纯表 + PGDocStatus），
      连接走 POSTGRES_* 环境变量，workspace 承担数据隔离。切库只改 .env，业务代码不动。
    """
    backend = os.environ.get("STORAGE_BACKEND", "local").lower()
    if backend != "pg":
        return {
            "kv_storage": "JsonKVStorage",
            "vector_storage": "NanoVectorDBStorage",
            "graph_storage": "NetworkXStorage",
            "doc_status_storage": "JsonDocStatusStorage",
        }
    return {
        "kv_storage": "PGKVStorage",
        "vector_storage": "PGVectorStorage",
        "graph_storage": "PGTableGraphStorage",
        "doc_status_storage": "PGDocStatusStorage",
    }


def _llm_config() -> dict:
    """按 LLM_BACKEND 取 LLM 连接配置（build_llm_func / build_query_stream_func 共用）。"""
    backend = os.environ.get("LLM_BACKEND", "glm").lower()
    if backend == "deepseek":
        return {
            "api_key": os.environ.get("DEEPSEEK_API_KEY", ""),
            "base_url": os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
            "extract_model": os.environ.get("EXTRACT_MODEL") or "deepseek-v4-flash",
            "query_model": os.environ.get("QUERY_MODEL") or "deepseek-v4-flash",
            # v4-flash 推理模型默认开思考，reasoning_tokens 会吞掉 max_tokens -> content 空
            "extra_kwargs": {"extra_body": {"thinking": {"type": "disabled"}}},
        }
    return {
        "api_key": os.environ.get("GLM_API_KEY", ""),
        "base_url": os.environ.get("GLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4"),
        "extract_model": os.environ.get("EXTRACT_MODEL") or "glm-4.5-flash",
        "query_model": os.environ.get("QUERY_MODEL") or "glm-4.5-flash",
        "extra_kwargs": {},
    }


def build_llm_func():
    """返回 (extract_func, query_func)。LLM_BACKEND=glm|deepseek 切换（.env 控制 key/base/默认模型）。

    DeepSeek 注意：v4-flash 是推理模型，默认开启思考模式，reasoning_tokens 占比 75-80%，
    极端情况下（如 response_format=json_object）会把 max_tokens 全部烧在思考上导致 content 为空。
    因此默认传 thinking=disabled 关闭思考，抽取/检索场景不需要思维链。
    注意：OpenAI Python SDK 不接受 thinking 作为直接关键字参数，需通过 extra_body 透传。
    """
    from lightrag.llm.openai import openai_complete_if_cache

    cfg = _llm_config()

    def make(model: str):
        async def f(prompt, system_prompt=None, history_messages=None, **kw):
            merged = {**cfg["extra_kwargs"], **kw}
            return await openai_complete_if_cache(
                model, prompt, system_prompt=system_prompt,
                history_messages=history_messages, base_url=cfg["base_url"], api_key=cfg["api_key"],
                **merged,
            )
        return f

    return make(cfg["extract_model"]), make(cfg["query_model"])


def build_query_stream_func():
    """真 token 级流式生成：AsyncIterator[str]（stream=True，thinking 已关）。

    LightRAG 的 openai_complete_if_cache 不处理 stream=True，故直接调 AsyncOpenAI；
    用于 M6 answer_stream / M7 SSE 的 token 级流式。
    """
    from openai import AsyncOpenAI

    cfg = _llm_config()
    client = AsyncOpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"])

    async def stream(prompt, system_prompt=None, history_messages=None, **kw):
        messages: list[dict] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        if history_messages:
            messages.extend(history_messages)
        messages.append({"role": "user", "content": prompt})
        merged = {**cfg["extra_kwargs"], **kw}
        resp = await client.chat.completions.create(
            model=cfg["query_model"], messages=messages, stream=True, **merged)
        async for chunk in resp:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta

    return stream