"""M10 可观测埋点（v6.2 / P0）：run + span 落 PG，字段语义对齐 OTel GenAI。

分层约定（M10 §11.1）：
- 内核（agent.py）只采集 spans，不碰数据库；
- API 层（m7_interact/api.py）负责 start/finish 落库；评测路径（m9_eval）不写库；
- 写入 best-effort：PG 异常只告警，不阻塞对话主链。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime
from typing import Any

import asyncpg

log = logging.getLogger(__name__)

_PG = dict(host="127.0.0.1", port=5432, user="postgres", password="postgres", database="postgres")

_DDL = [
    """CREATE TABLE IF NOT EXISTS agent_runs (
        run_id text PRIMARY KEY,
        conversation_id text,
        collection_id text,
        question text,
        status text NOT NULL,
        model text,
        started_at timestamptz NOT NULL DEFAULT now(),
        duration_ms int,
        tokens_in int,
        tokens_out int,
        tool_calls int,
        error text
    )""",
    """CREATE TABLE IF NOT EXISTS agent_spans (
        run_id text NOT NULL,
        span_id text NOT NULL,
        node text NOT NULL,
        tool text,
        started_at timestamptz NOT NULL,
        duration_ms int NOT NULL,
        tokens_in int,
        tokens_out int,
        detail jsonb,
        PRIMARY KEY (run_id, span_id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_agent_runs_started ON agent_runs (started_at DESC)",
]

_schema_ready = False
_schema_lock = asyncio.Lock()
_starts: dict[str, float] = {}


async def ensure_schema() -> bool:
    """幂等建表；PG 不可用时返回 False（下次调用重试），不抛异常。"""
    global _schema_ready
    if _schema_ready:
        return True
    async with _schema_lock:
        if _schema_ready:
            return True
        try:
            conn = await asyncpg.connect(**_PG, timeout=3)
            try:
                for sql in _DDL:
                    await conn.execute(sql)
            finally:
                await conn.close()
            _schema_ready = True
        except Exception:  # noqa: BLE001 —— 埋点故障不得影响主链
            log.warning("agent telemetry: 建表失败（PG 不可用？），本次进程不落库", exc_info=True)
    return _schema_ready


async def start_run(*, collection_id: str, conversation_id: str | None, question: str) -> str | None:
    """插入 running 行并返回 run_id；不可用时返回 None（运行照常，只是不落库）。"""
    if not await ensure_schema():
        return None
    run_id = uuid.uuid4().hex
    try:
        conn = await asyncpg.connect(**_PG, timeout=3)
        try:
            await conn.execute(
                "INSERT INTO agent_runs (run_id, conversation_id, collection_id, question, status) "
                "VALUES ($1, $2, $3, $4, 'running')",
                run_id, conversation_id, collection_id, question[:2000],
            )
        finally:
            await conn.close()
    except Exception:  # noqa: BLE001
        log.warning("agent telemetry: start_run 写入失败", exc_info=True)
        return None
    _starts[run_id] = time.perf_counter()
    return run_id


def finish_run(
    run_id: str | None,
    status: str,
    *,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """收尾落库（非阻塞，可在 CancelledError 中安全调用）：更新 run 终态 + 批量写 spans。"""
    if not run_id:
        return
    started = _starts.pop(run_id, None)
    duration_ms = int((time.perf_counter() - started) * 1000) if started is not None else None
    try:
        asyncio.get_running_loop().create_task(
            _finish(run_id, status, result or {}, error, duration_ms)
        )
    except RuntimeError:  # 无事件循环（脚本收尾）——放弃写入
        pass


async def _finish(
    run_id: str,
    status: str,
    result: dict[str, Any],
    error: str | None,
    duration_ms: int | None,
) -> None:
    spans = result.get("spans") or []
    tokens_in = sum(s.get("tokens_in") or 0 for s in spans) or None
    tokens_out = sum(s.get("tokens_out") or 0 for s in spans) or None
    tool_calls = sum(1 for s in spans if s.get("node") == "execute_tools")
    try:
        conn = await asyncpg.connect(**_PG, timeout=3)
        try:
            await conn.execute(
                "UPDATE agent_runs SET status=$2, duration_ms=$3, tokens_in=$4, tokens_out=$5, "
                "tool_calls=$6, error=$7, model=$8 WHERE run_id=$1",
                run_id, status, duration_ms, tokens_in, tokens_out, tool_calls,
                (error or "")[:2000] or None, result.get("model"),
            )
            if spans:
                await conn.executemany(
                    "INSERT INTO agent_spans "
                    "(run_id, span_id, node, tool, started_at, duration_ms, tokens_in, tokens_out, detail) "
                    "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb) ON CONFLICT DO NOTHING",
                    [
                        (
                            run_id, s["span_id"], s["node"], s.get("tool"),
                            datetime.fromisoformat(s["started_at"]),
                            s["duration_ms"], s.get("tokens_in"), s.get("tokens_out"),
                            json.dumps(s["detail"], ensure_ascii=False) if s.get("detail") else None,
                        )
                        for s in spans
                    ],
                )
        finally:
            await conn.close()
    except Exception:  # noqa: BLE001
        log.warning("agent telemetry: finish_run 写入失败", exc_info=True)
