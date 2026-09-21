"""M7 v7 会话（Conversation）注册表：Collection 作用域，注册表 + 明细分离。

- 会话归属 collection：注册表 `<working_dir>/conversations.json`（只存元数据），
  明细 `<working_dir>/conversations/<conv_id>.json`（完整 messages，含 citations/meta）。
- conv_id = `conv_<uuid8>`（与 col_id 风格一致）。
- 首问自动命名：标题为占位「新对话」时，用第一条 user 消息前 MAX_TITLE 字覆盖。
- 多轮历史由 respond 按会话读取（见 respond.py），build_query_with_history 注入只取最近 4 条。

删除知识库 → rmtree working_dir 即连带清空会话，无需额外处理。
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

CONVS_FILE = "conversations.json"
CONVS_DIR = "conversations"
MAX_TITLE = 30
MAX_PREVIEW = 60

_write_lock = asyncio.Lock()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------- 路径 & 注册表 ----------

def _reg_path(deps: Any) -> Path:
    return deps.working_dir / CONVS_FILE


def _conv_dir(deps: Any) -> Path:
    return deps.working_dir / CONVS_DIR


def _conv_path(deps: Any, conv_id: str) -> Path:
    return _conv_dir(deps) / f"{conv_id}.json"


def _load(deps: Any) -> dict[str, dict[str, Any]]:
    p = _reg_path(deps)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {}


def _save(deps: Any, reg: dict[str, dict[str, Any]]) -> None:
    _reg_path(deps).write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------- CRUD ----------

def list_conversations(deps: Any) -> list[dict[str, Any]]:
    """注册表全量，按 updated_at 倒序。"""
    reg = _load(deps)
    rows = [{"conversation_id": cid, **rec} for cid, rec in reg.items()]
    rows.sort(key=lambda r: r.get("updated_at", ""), reverse=True)
    return rows


def create_conversation(deps: Any, title: str | None = None) -> str:
    """新建空会话；标题缺省「新对话」（首问自动命名）。"""
    reg = _load(deps)
    while True:
        cid = "conv_" + uuid.uuid4().hex[:8]
        if cid not in reg:
            break
    now = _now()
    t = (title or "").strip() or "新对话"
    reg[cid] = {"title": t[:MAX_TITLE], "created_at": now, "updated_at": now,
                "message_count": 0, "preview": ""}
    _save(deps, reg)
    _conv_dir(deps).mkdir(parents=True, exist_ok=True)
    _conv_path(deps, cid).write_text(
        json.dumps({"id": cid, "title": reg[cid]["title"], "messages": []},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    return cid


def get_conversation(deps: Any, conv_id: str) -> dict[str, Any] | None:
    """读取明细（含完整 messages），并入注册表标题。不存在返回 None。"""
    p = _conv_path(deps, conv_id)
    if not p.exists():
        return None
    detail = json.loads(p.read_text(encoding="utf-8"))
    rec = _load(deps).get(conv_id)
    title = (rec or {}).get("title") or detail.get("title") or conv_id
    return {"conversation_id": conv_id, "title": title, "messages": detail.get("messages", [])}


def rename_conversation(deps: Any, conv_id: str, title: str) -> bool:
    """返回 False 表示不存在。标题为空则忽略。"""
    t = (title or "").strip()
    if not t:
        return True
    reg = _load(deps)
    if conv_id not in reg:
        return False
    reg[conv_id]["title"] = t[:MAX_TITLE]
    _save(deps, reg)
    return True


async def delete_conversation(deps: Any, conv_id: str) -> bool:
    reg = _load(deps)
    if conv_id not in reg:
        return False
    del reg[conv_id]
    _save(deps, reg)
    _conv_path(deps, conv_id).unlink(missing_ok=True)
    return True


# ---------- 回答落库 ----------

async def append_round(
    deps: Any,
    conv_id: str,
    *,
    user: str,
    assistant: dict[str, Any],
) -> bool:
    """把一轮 user + assistant 写回明细并更新注册表。返回 False 表示会话已不存在。"""
    async with _write_lock:
        path = _conv_path(deps, conv_id)
        if not path.exists():
            return False
        detail = json.loads(path.read_text(encoding="utf-8"))
        msgs = detail.setdefault("messages", [])
        ts = _now()
        msgs.append({"role": "user", "content": user, "ts": ts})
        msgs.append({"role": "assistant", **assistant, "ts": ts})

        reg = _load(deps)
        rec = reg.get(conv_id)
        if rec is None:
            return False

        # 首问自动命名（仅占位标题才覆盖）
        if rec.get("title") == "新对话" and msgs and msgs[0].get("role") == "user":
            t = (msgs[0]["content"] or "").strip()[:MAX_TITLE]
            if t:
                rec["title"] = t
                detail["title"] = t

        rec["updated_at"] = ts
        rec["message_count"] = len(msgs)
        last_user = next((m.get("content") or "" for m in reversed(msgs)
                          if m.get("role") == "user"), "")
        rec["preview"] = last_user[:MAX_PREVIEW]

        _conv_path(deps, conv_id).write_text(
            json.dumps(detail, ensure_ascii=False, indent=2), encoding="utf-8")
        _save(deps, reg)
        return True