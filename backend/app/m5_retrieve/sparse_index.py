"""M5 关键词路：bge-m3 sparse（经 Xinference）+ 稀疏索引。

职责：
- build：从 PG lightrag_doc_chunks 读全量 (id, content, full_doc_id)，分批 sparse 编码，
  落盘 <working_dir>/m5_sparse.json（索引 + content/full_doc_id 元数据，溯源一并解决）。
- score：query sparse → 与索引内积排序（corpus 为个人规模，暴力交集即够）。

注意：bge-m3 需以 return_sparse=true 加载（Xinference），同一个实例不传 return_sparse
即返回 dense，与 M3 通路互不影响。
"""
from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from pathlib import Path

from lightrag.utils import sanitize_text_for_encoding

from app.m3_index.providers import XINFERENCE_URL, _xf_token

SPARSE_FILE = "m5_sparse.json"


def content_key(content: str) -> str:
    """关联 M2 与 PG 的 content 口径。

    LightRAG 落 PG 前对 chunk 做过 sanitize_text_for_encoding（strip + html.unescape +
    去控制字符），故 M2 侧须过同一函数才能与 PG 的 content 逐字节对上。
    """
    return sanitize_text_for_encoding(content)


def _pg_chunks(workspace: str) -> list[dict]:
    """PG lightrag_doc_chunks 全量 (id, content, full_doc_id)。"""
    import asyncpg

    async def _go() -> list[dict]:
        conn = await asyncpg.connect(
            host="127.0.0.1", port=5432, user="postgres", password="postgres", database="postgres"
        )
        try:
            rows = await conn.fetch(
                "SELECT id, content, full_doc_id FROM lightrag_doc_chunks "
                "WHERE workspace = $1 AND content <> '' ORDER BY full_doc_id, chunk_order_index",
                workspace,
            )
            return [
                {"id": r["id"], "content": r["content"], "full_doc_id": r["full_doc_id"]}
                for r in rows
            ]
        finally:
            await conn.close()

    return asyncio.run(_go())


def _sparse_encode(texts: list[str]) -> list[dict[str, float]]:
    """一批文本 → [{token_id: score}]。token_id 为字符串 key（Xinference 返回）。"""
    token = _xf_token()
    body = json.dumps({"model": "bge-m3", "input": texts, "return_sparse": True}).encode()
    req = urllib.request.Request(f"{XINFERENCE_URL}/v1/embeddings", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=180) as r:
        resp = json.loads(r.read().decode())
    rows = sorted(resp["data"], key=lambda x: x["index"])
    return [{k: float(v) for k, v in row["embedding"].items()} for row in rows]


def build(workspace: str, out_path: Path, chunks_dir: Path | None = None) -> dict:
    """构建稀疏索引并落盘，返回索引元数据。

    chunks_dir: 可选的 M2 输出目录（每文档一个 jsonl），传了则从 jsonl 读 block_type
                等元数据，按 full_doc_id + content 对齐写进 chunks meta。
    """
    chunks = _pg_chunks(workspace)
    if not chunks:
        raise SystemExit(f"[sparse] workspace={workspace} 无 chunk，检查 STORAGE/POSTGRES_WORKSPACE")

    # 从 M2 jsonl 加载元数据（block_type 等），按 (full_doc_id, content) 索引。
    # 不能按 (full_doc_id, chunk_order_index) 对齐：LightRAG 的 chunk id = hash(doc_id, content)，
    # 同一文档内 content 完全相同的重复块会被丢弃 ⇒ PG 的 chunk_order_index 相对 M2 整体前移，
    # 按序号关联会错位。content 是 LightRAG 生成 id 的输入，按它对齐必命中。
    extra_meta: dict[tuple[str, str], dict] = {}
    if chunks_dir and chunks_dir.exists():
        for jf in chunks_dir.glob("*.jsonl"):
            with jf.open(encoding="utf-8") as f:
                for line in f:
                    u = json.loads(line)
                    key = (u["full_doc_id"], content_key(u["content"]))
                    # setdefault：与 LightRAG 同文档去重（保留首次出现）语义一致
                    extra_meta.setdefault(key, {"block_type": u.get("block_type", "paragraph")})
        print(f"[sparse] loaded block_type from {chunks_dir}: {len(extra_meta)} units")

    index: dict[str, dict[str, float]] = {}
    meta: dict[str, dict] = {}
    B = 16
    for i in range(0, len(chunks), B):
        batch = chunks[i : i + B]
        sp = _sparse_encode([c["content"] for c in batch])
        for c, s in zip(batch, sp):
            index[c["id"]] = s
            entry = {"content": c["content"], "full_doc_id": c["full_doc_id"]}
            extra = extra_meta.get((c["full_doc_id"], c["content"]))
            if extra:
                entry.update(extra)
            meta[c["id"]] = entry
    doc = {"index": index, "chunks": meta}
    out_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    print(f"[sparse] built {len(index)} chunks -> {out_path}")
    return doc


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def score(query_text: str, doc: dict, top_k: int = 40, boost: dict[str, float] | None = None) -> list[tuple[str, float]]:
    """query sparse 与索引内积排序，返回 [(chunk_id, score)]。

    boost: 可选的额外 term→权重映射，这些 term 单独编码后乘以权重加到 query 向量上，
           用于数字感知检索等场景（精确数字 token 加权提升表格行匹配度）。
    """
    q = _sparse_encode([query_text])[0]
    if boost:
        boost_terms = list(boost.keys())
        if boost_terms:
            boost_vecs = _sparse_encode(boost_terms)
            for term, vec in zip(boost_terms, boost_vecs):
                w = boost.get(term, 1.0)
                for tid, val in vec.items():
                    q[tid] = q.get(tid, 0.0) + val * w
    qn = sum(x * x for x in q.values()) ** 0.5
    scored = []
    for cid, w in doc["index"].items():
        mi = 0.0
        for token, qv in q.items():
            if token in w:
                mi += qv * w[token]
        scored.append((cid, mi))
    scored.sort(key=lambda kv: kv[1], reverse=True)
    norm = qn or 1.0
    return [(cid, s / norm) for cid, s in scored[:top_k]]


if __name__ == "__main__":
    import os

    ws = os.environ.get("POSTGRES_WORKSPACE", "default_ws")
    out = Path("data/default_ws") / SPARSE_FILE
    build(ws, out)