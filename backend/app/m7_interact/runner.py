"""M7 交互层启动：python -m app.m7_interact.runner [--host 127.0.0.1] [--port 8787] [-w 工作目录] [--chunks 目录]"""
from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from app.m3_index.runner import _load_dotenv

from .api import app


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="m7-interact", description="M7 交互层（FastAPI/SSE + WebUI）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("-w", "--working-dir", default="data/default_ws")
    ap.add_argument("--chunks", default="data/chunks")
    args = ap.parse_args(argv)

    proj = Path(__file__).resolve().parents[2]
    _load_dotenv(proj / ".env")

    from .bootstrap import configure
    configure(working_dir=args.working_dir, chunks_dir=args.chunks)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())