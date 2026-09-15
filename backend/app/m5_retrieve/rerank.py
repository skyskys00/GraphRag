"""M5 精排：Xinference /v1/rerank（bge-reranker-v2-m3）。"""
from __future__ import annotations

import json
import urllib.request
from typing import Any

from app.m3_index.providers import XINFERENCE_URL, _xf_token

RERANK_MODEL = "bge-reranker-v2-m3"


def rerank(query: str, docs: list[tuple[str, str]], top_n: int) -> list[dict[str, Any]]:
    """对 (chunk_id, content) 候选按 query 重排，返回 [{chunk_id, score}]（top_n 外丢弃）。"""
    if not docs:
        return []
    token = _xf_token()
    body = json.dumps(
        {
            "model": RERANK_MODEL,
            "query": query,
            "documents": [c for _, c in docs],
            "top_n": min(top_n, len(docs)),
        }
    ).encode()
    req = urllib.request.Request(f"{XINFERENCE_URL}/v1/rerank", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=180) as r:
        resp = json.loads(r.read().decode())
    out = []
    for item in sorted(resp["results"], key=lambda x: x["index"]):
        out.append({"chunk_id": docs[item["index"]][0], "score": float(item["relevance_score"])})
    return out