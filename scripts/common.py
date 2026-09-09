"""LightRAG 共享初始化：DeepSeek LLM + 本地 bge embedding。"""
import asyncio
import os
import sys
from pathlib import Path

import numpy as np

from lightrag import LightRAG
from lightrag.llm.openai import openai_complete_if_cache
from lightrag.utils import wrap_embedding_func_with_attrs

ROOT = Path(__file__).resolve().parent.parent
WORKING_DIR = ROOT / "storage"


def load_dotenv_simple(path: Path) -> None:
    """极简 .env 解析（避免额外依赖，只注入未设置的环境变量）。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def _pick_device() -> str:
    """嵌入设备。默认 CPU：M3 上 torch MPS 编码 bge 曾触发 segfault。
    如需用 GPU 加速可设环境变量 EMBED_DEVICE=mps（自行评估稳定性）。"""
    return os.environ.get("EMBED_DEVICE", "cpu")


def build_llm_model_func() -> object:
    """DeepSeek（OpenAI 兼容）作为统一 LLM 入口。"""
    api_key = os.environ["DEEPSEEK_API_KEY"]
    base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
    # DeepSeek 2026-04 起模型为 deepseek-v4-flash / deepseek-v4-pro；
    # 旧名 deepseek-chat / deepseek-reasoner 已于 2026-07-24 停用。
    model = (
        os.environ.get("DEEPSEEK_MODEL")
        or os.environ.get("EXTRACT_MODEL")
        or os.environ.get("QUERY_MODEL")
        or "deepseek-v4-flash"
    )

    async def llm_model_func(
        prompt, system_prompt=None, history_messages=None, keyword_extraction=False, **kwargs
    ) -> str:
        return await openai_complete_if_cache(
            model,
            prompt,
            system_prompt=system_prompt,
            history_messages=history_messages or [],
            base_url=base_url,
            api_key=api_key,
            **kwargs,
        )

    return llm_model_func


def build_embedding_func(model_name: str) -> object:
    """本地 sentence-transformers embedding（bge 系列），MPS/CPU 自适应。"""
    from sentence_transformers import SentenceTransformer

    device = _pick_device()
    model = SentenceTransformer(model_name, device=device)
    dim = model.get_sentence_embedding_dimension()

    @wrap_embedding_func_with_attrs(
        embedding_dim=dim, max_token_size=8192, model_name=model_name
    )
    async def embedding_func(texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, dim), dtype=np.float32)
        return await asyncio.to_thread(model.encode, texts, normalize_embeddings=True)

    return embedding_func


def init_rag() -> LightRAG:
    """加载配置并初始化（或打开已有）LightRAG 实例。"""
    load_dotenv_simple(ROOT / ".env")
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise SystemExit("未配置 DEEPSEEK_API_KEY，请先 cp .env.example .env 并填入 key")

    # 本机直连 huggingface.co 不通，统一走国内镜像；用户显式设置 HF_ENDPOINT 时优先。
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

    # 让实体/关系/总结输出中文
    os.environ.setdefault("SUMMARY_LANGUAGE", "zh")

    rag = LightRAG(
        working_dir=str(WORKING_DIR),
        llm_model_func=build_llm_model_func(),
        embedding_func=build_embedding_func(
            os.environ.get("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
        ),
        addon_params={"language": "zh"},
    )
    return rag