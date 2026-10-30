"""验证 DeepSeek 是否真的响应 temperature：同一 prompt 在不同温度下各跑 N 次，
看输出集合是否相同。若 t=0 / t=1 / t=2 的输出去重后数量相当，说明服务端忽略该参数。
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openai import AsyncOpenAI

MODEL = os.environ.get("QUERY_MODEL") or "deepseek-v4-flash"
N = 5
PROMPTS = [
    "用一句话说明什么是检索增强生成。",
    "请判断这句话是否被支持：'办公用品分三类'。只答是或否。",
]


async def main() -> None:
    client = AsyncOpenAI(
        api_key=os.environ["DEEPSEEK_API_KEY"],
        base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
    )
    for pi, prompt in enumerate(PROMPTS):
        print(f"\n=== prompt {pi}: {prompt}")
        for temp in (0.0, 0.7, 1.3):
            outs = []
            for _ in range(N):
                r = await client.chat.completions.create(
                    model=MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=temp,
                    max_tokens=200,
                    extra_body={"thinking": {"type": "disabled"}},
                )
                outs.append((r.choices[0].message.content or "").strip())
            uniq = len(set(outs))
            print(f"  t={temp}: 不同输出 {uniq}/{N}")
            if uniq > 1:
                for u in list(dict.fromkeys(outs))[:3]:
                    print(f"     - {u[:70]}")


if __name__ == "__main__":
    asyncio.run(main())
