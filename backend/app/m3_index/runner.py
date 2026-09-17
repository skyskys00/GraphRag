"""M3 索引层：TextUnit -> LightRAG 图 + bge-m3 dense 向量。

路径：data/chunks/<doc_id>.jsonl -> LightRAG.ainsert_custom_chunks（抽取/建图，DeepSeek zh）
embedding：Xinference bge-m3（encoding_format=float，dense 1024）
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from lightrag import LightRAG
from lightrag.utils import EmbeddingFunc

from .providers import build_llm_func, build_embedding_func, build_storage_config


def _load_dotenv(path: Path) -> None:
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        v = v.strip().strip('"').strip("'")
        if " #" in v:
            v = v.split(" #", 1)[0].rstrip()
        os.environ.setdefault(k.strip(), v)


def _read_units(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_rag_data(chunks_root: Path) -> list[dict]:
    """返回 [{doc_id, full_text, text_chunks: [content...]}]。"""
    docs = []
    for f in sorted(chunks_root.glob("*.jsonl")):
        units = _read_units(f)
        if not units:
            continue
        doc_id = units[0]["full_doc_id"]
        texts = [u["content"] for u in units if u.get("content", "").strip()]
        docs.append({"doc_id": doc_id, "full_text": "\n\n".join(texts), "text_chunks": texts})
    return docs


async def build_rag(working_dir: Path, workspace: str = "lightrag_m4") -> LightRAG:
    embed = build_embedding_func()
    extract, query = build_llm_func()
    from lightrag.llm_roles import RoleLLMConfig

    rag = LightRAG(
        working_dir=str(working_dir),
        workspace=workspace,
        llm_model_func=query,
        embedding_func=embed,
        llm_model_kwargs={"temperature": 0.1, "max_tokens": 8000},
        addon_params={"language": "zh"},
        role_llm_configs={
            "extract": RoleLLMConfig(func=extract, max_async=2),
        },
        **build_storage_config(),
    )
    await rag.initialize_storages()
    return rag


async def run(chunks_root: Path, working_dir: Path, limit: int | None, only: str | None = None) -> int:
    docs = load_rag_data(chunks_root)
    if only:
        docs = [d for d in docs if d["doc_id"].startswith(only)]
    if limit:
        docs = docs[:limit]
    if not docs:
        print("[warn] 没有可索引的 chunks")
        return 1

    rag = await build_rag(working_dir)
    ok, fail = 0, 0
    for i, d in enumerate(docs, 1):
        try:
            await rag.ainsert_custom_chunks(d["full_text"], d["text_chunks"], doc_id=d["doc_id"])
            print(f"[ok {i}/{len(docs)}] {d['doc_id'][:12]} chunks={len(d['text_chunks'])}")
            ok += 1
        except Exception as e:  # noqa: BLE001
            print(f"[fail] {d['doc_id'][:12]}: {type(e).__name__}: {e}", file=sys.stderr)
            fail += 1
    await rag.finalize_storages()
    print(f"\n=== M3 完成：ok={ok} fail={fail} ===")
    return 0 if fail == 0 else 2


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="m3-index", description="M3 索引层：TextUnit -> LightRAG 图+向量")
    ap.add_argument("-c", "--chunks", default="data/chunks", help="chunks 根目录")
    ap.add_argument("-w", "--working-dir", default="data/lightrag", help="LightRAG 存储目录")
    ap.add_argument("--limit", type=int, default=None, help="只处理前 N 篇（调试）")
    ap.add_argument("--only", default=None, help="只处理指定 doc_id 前缀（调试）")
    args = ap.parse_args(argv)

    proj = Path(__file__).resolve().parents[2]
    _load_dotenv(proj / ".env")
    return asyncio.run(run(Path(args.chunks), Path(args.working_dir), args.limit, args.only))


if __name__ == "__main__":
    raise SystemExit(main())