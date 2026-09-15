"""M7 SSE 事件编码 + 流式应答事件线（retrieved → delta ×N → citations → done）。

delta 为真 token 级增量（M0 build_query_stream_func → M6 answer_stream），
不再是整段回放。
"""
from __future__ import annotations

import json
from typing import Any


def sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"