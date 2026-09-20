"""M8 v3 知识库（Collection）注册表：path 布局 + CRUD + 每库最近问答。

- collection_id `default` = 现有单库（workspace `default_ws`，目录沿用现状），不落盘。
- 新库：id 兼作 workspace 与目录名 `col_<uuid8>`（满足 validate_workspace：单段、禁 / \\ . ..）。
  数据根 `data/collections/<col_id>/`。
- 注册表落盘 `data/collections.json`（只存非默认库）：`{col_id: {name, created_at}}`。
  不存在该文件时系统退化为现有单库行为（向后兼容）。
"""
from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

COLLECTIONS_ROOT = "data/collections"
REG_FILE = "data/collections.json"
RECENT_FILE = "recent_queries.json"
MAX_RECENT = 20

_DEFAULT_LAYOUT = {
    "working_dir": "data/default_ws",
    "chunks_dir": "data/chunks",
    "parse_dir": "data/parse",
    "uploads_dir": "data/uploads",
}


def collection_paths(
    proj: Path, col_id: str, default_layout: dict[str, str] | None = None
) -> dict[str, str]:
    """col_id → 相对 proj 的目录布局（working_dir/chunks_dir/parse_dir/uploads_dir）。"""
    if col_id == "default":
        dl = {**_DEFAULT_LAYOUT, **(default_layout or {})}
        return {
            "working_dir": dl["working_dir"],
            "chunks_dir": dl["chunks_dir"],
            "parse_dir": dl["parse_dir"],
            "uploads_dir": dl["uploads_dir"],
        }
    root = f"{COLLECTIONS_ROOT}/{col_id}"
    return {
        "working_dir": root,
        "chunks_dir": f"{root}/chunks",
        "parse_dir": f"{root}/parse",
        "uploads_dir": f"{root}/uploads",
    }


# ---------- 注册表 ----------

def _reg_path(proj: Path) -> Path:
    return proj / REG_FILE


def _load(proj: Path) -> dict[str, dict[str, Any]]:
    p = _reg_path(proj)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {}


def _save(proj: Path, data: dict[str, dict[str, Any]]) -> None:
    _reg_path(proj).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def list_collections(proj: Path) -> list[dict[str, Any]]:
    """恒含 default + 盘上记录，default 排最前。"""
    reg = _load(proj)
    cols = [{"id": "default", "name": "默认知识库", "created_at": ""}]
    for cid, rec in sorted(reg.items()):
        cols.append({"id": cid, "name": rec.get("name", cid), "created_at": rec.get("created_at", "")})
    return cols


def create_collection(proj: Path, name: str) -> str:
    reg = _load(proj)
    while True:
        cid = "col_" + uuid.uuid4().hex[:8]
        if cid not in reg:
            break
    reg[cid] = {"name": name or cid, "created_at": datetime.now().isoformat(timespec="seconds")}
    _save(proj, reg)
    return cid


def rename_collection(proj: Path, col_id: str, name: str) -> bool:
    """返回 False 表示无法重命名（default 或不存在）。"""
    if col_id == "default":
        return False
    reg = _load(proj)
    if col_id not in reg:
        return False
    reg[col_id]["name"] = name or col_id
    _save(proj, reg)
    return True


async def delete_collection(proj: Path, col_id: str) -> bool:
    """返回 False 表示无法删除（default 或不存在）。成功则：registry 移除 + rmtree + 清 PG 行。"""
    if col_id == "default":
        return False
    reg = _load(proj)
    if col_id not in reg:
        return False
    del reg[col_id]
    _save(proj, reg)
    wd = proj / collection_paths(proj, col_id)["working_dir"]
    shutil.rmtree(wd, ignore_errors=True)
    await _purge_workspace(col_id)
    return True


async def _purge_workspace(workspace: str) -> None:
    """best-effort 清该 workspace 的 PG 行；无 workspace 列的表跳过。残留无害（不再被查询）。"""
    import asyncpg

    conn = await asyncpg.connect(
        host="127.0.0.1", port=5432, user="postgres", password="postgres", database="postgres"
    )
    try:
        tables = await conn.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename LIKE 'lightrag\\_%'"
        )
        for r in tables:
            try:
                await conn.execute(
                    f'DELETE FROM "{r["tablename"]}" WHERE workspace=$1', workspace
                )
            except Exception:  # noqa: BLE001 —— best-effort
                pass
    finally:
        await conn.close()


# ---------- 每库最近问答 ----------

def _recent_path(proj: Path, col_id: str) -> Path:
    return proj / collection_paths(proj, col_id)["working_dir"] / RECENT_FILE


def add_recent_query(proj: Path, col_id: str, query: str) -> None:
    """答完追加一条（封顶 MAX_RECENT）。"""
    if not query.strip():
        return
    p = _recent_path(proj, col_id)
    rows: list[dict] = []
    if p.exists():
        rows = json.loads(p.read_text(encoding="utf-8"))
    rows.insert(0, {"query": query.strip(), "ts": datetime.now().isoformat(timespec="seconds")})
    del rows[MAX_RECENT:]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def load_recent_queries(proj: Path, col_id: str) -> list[dict]:
    p = _recent_path(proj, col_id)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return []