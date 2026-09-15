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

from app.m3_index.providers import XINFERENCE_URL, _xf_token

SPARSE_FILE = "m5_sparse.json"


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
            return [{"id": r["id"], "content": r["content"], "full_doc_id": r["full_doc_id"]} for r in rows]
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


def build(workspace: str, out_path: Path) -> dict:
    """构建稀疏索引并落盘，返回索引元数据。"""
    chunks = _pg_chunks(workspace)
    if not chunks:
        raise SystemExit(f"[sparse] workspace={workspace} 无 chunk，检查 STORAGE/POSTGRES_WORKSPACE")
    index: dict[str, dict[str, float]] = {}
    meta: dict[str, dict] = {}
    B = 16
    for i in range(0, len(chunks), B):
        batch = chunks[i : i + B]
        sp = _sparse_encode([c["content"] for c in batch])
        for c, s in zip(batch, sp):
            index[c["id"]] = s
            meta[c["id"]] = {"content": c["content"], "full_doc_id": c["full_doc_id"]}
    doc = {"index": index, "chunks": meta}
    out_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    print(f"[sparse] built {len(index)} chunks -> {out_path}")
    return doc


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def score(query_text: str, doc: dict, top_k: int = 40) -> list[tuple[str, float]]:
    """query sparse 与索引内积排序，返回 [(chunk_id, score)]。"""
    q = _sparse_encode([query_text])[0]
    qn = sum(x * x for x in q.values()) ** 0.5
    scored = []
    for cid, w in doc["index"].items():
        mi = 0.0
        for token, qv in q.items():
            if token in w:
                mi += qv * w[token]
        scored.append((cid, mi))
    scored.sort(key=lambda kv: kv[1], reverse=True)
    # 归一化到 [0,1]（稀疏模归一），防 RRF 无界
    norm = qn or 1.0
    return [(cid, s / norm) for cid, s in scored[:top_k]]


if __name__ == "__main__":
    import os

    ws = os.environ.get("POSTGRES_WORKSPACE", "lightrag_m4")
    out = Path("data/lightrag_m4") / SPARSE_FILE
    build(ws, out)