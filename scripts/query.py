"""问答：local/global/hybrid/mix 任选一个中文问题验证召回与回答质量。"""
import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from lightrag import QueryParam

from common import init_rag


async def main() -> None:
    parser = argparse.ArgumentParser(description="LightRAG 中文问答")
    parser.add_argument("query", help="中文问题")
    parser.add_argument(
        "--mode",
        default="mix",
        choices=["local", "global", "hybrid", "naive", "mix"],
        help="检索模式：local=实体遍历 / global=关系链主题 / hybrid=两者 / mix=再加原文块(推荐)",
    )
    parser.add_argument("--top-k", type=int, default=10, help="每路召回条数")
    args = parser.parse_args()

    rag = init_rag()
    await rag.initialize_storages()
    resp = await rag.aquery(
        args.query,
        param=QueryParam(mode=args.mode, top_k=args.top_k),
    )
    print(resp)


if __name__ == "__main__":
    asyncio.run(main())