"""从 LangGraph checkpoint 恢复丢失的会话记录（v6.4.3 断流丢轮的善后）。

场景：SSE 断流时旧代码跳过落库，但 checkpointer 已保存 state.messages。
本脚本读 checkpoint → 过滤成可展示对话 → 写回 conversations/<id>.json。

只处理 conversations 明细为空（或指定 --force）的会话，不覆盖已有记录。
用法：
  python -m scripts.recover_conversations --workspace data/eval_admin_ws            # 预览
  python -m scripts.recover_conversations --workspace data/eval_admin_ws --apply    # 写入
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncio

from app.m10_agent import telemetry
from app.m10_agent.agent import _messages_to_history


def _fmt(history: list[dict]) -> list[dict]:
    return [{"role": h["role"], "content": h["content"], "ts": "", "citations": [], "meta": None}
            for h in history]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True, help="会话所在目录，如 data/eval_admin_ws")
    ap.add_argument("--apply", action="store_true", help="实际写入（默认只预览）")
    ap.add_argument("--force", action="store_true", help="覆盖已有 messages")
    args = ap.parse_args()

    ws = Path(args.workspace)
    conv_dir = ws / "conversations"
    if not conv_dir.exists():
        print(f"目录不存在: {conv_dir}")
        return

    saver = await telemetry.open_checkpointer()
    if saver is None:
        print("checkpointer 不可用")
        return

    for path in sorted(conv_dir.glob("conv_*.json")):
        cid = path.stem
        detail = json.loads(path.read_text(encoding="utf-8"))
        existing = detail.get("messages") or []
        if existing and not args.force:
            continue
        state = await saver.aget({"configurable": {"thread_id": cid}})
        if not state:
            print(f"{cid}: 无 checkpoint")
            continue
        raw_messages = (state.get("channel_values") or {}).get("messages") or []
        history = _messages_to_history(raw_messages)
        print(f"{cid}: 现有 {len(existing)} 条 → checkpoint 可恢复 {len(history)} 条")
        for h in history[:6]:
            print(f"    {h['role']}: {h['content'][:50]}")
        if args.apply and history:
            detail["messages"] = _fmt(history)
            path.write_text(json.dumps(detail, ensure_ascii=False, indent=2), encoding="utf-8")
            reg_path = ws / "conversations.json"
            reg = json.loads(reg_path.read_text(encoding="utf-8"))
            if cid in reg:
                reg[cid]["message_count"] = len(history)
                reg[cid]["preview"] = next(
                    (h["content"][:60] for h in reversed(history) if h["role"] == "user"), "")
                first_user = next((h["content"] for h in history if h["role"] == "user"), "")
                if first_user and reg[cid].get("title") in ("", "新对话"):
                    reg[cid]["title"] = first_user[:30]
                reg_path.write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"    → 已写入 {len(history)} 条")

    await telemetry.close_checkpointer()


if __name__ == "__main__":
    asyncio.run(main())
