"""建图索引：把 inputs/parsed/ 下的统一 md 入库（切分 → 向量化 → LLM 建图）。

parsed/ 由 scripts/parse.py 产出，每篇 <doc_id>.md + <doc_id>.md.json（p1.md §4）。
"""
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from common import init_rag

PARSED_DIR = ROOT / "inputs" / "parsed"


async def main() -> None:
    rag = init_rag()
    await rag.initialize_storages()

    # 只扫 parsed/ 顶层：parse.py 保证每篇产物就是顶层一份 md + 一份 json
    files = sorted(PARSED_DIR.glob("*.md"))
    if not files:
        raise SystemExit(f"{PARSED_DIR}/ 下没有可入库的 md（先跑 scripts/parse.py）")

    texts, ids, paths = [], [], []
    for f in files:
        try:
            doc_id = json.loads(f.with_suffix(".md.json").read_text(encoding="utf-8"))["doc_id"]
        except Exception:
            doc_id = f.stem  # 元数据缺失时回退文件名
        texts.append(f.read_text(encoding="utf-8"))
        ids.append(doc_id)
        paths.append(str(f))

    print(f"开始入库 {len(files)} 篇文档（切分→向量→LLM 建图，抽取阶段耗时较长）…")
    # ids 固定为 doc_id：增量更新与去重的唯一依据（p1.md §4.3）
    await rag.ainsert(texts, ids=ids, file_paths=paths)
    print("入库完成。可运行 scripts/query.py 提问。")


if __name__ == "__main__":
    asyncio.run(main())