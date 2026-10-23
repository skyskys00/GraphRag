"""P0 探底（2026-10-23）：DeepSeek v4-flash 原生 tools（function calling）支持 + 与 thinking 互斥性。

三个 case：
  A. 默认（不传 thinking）——tools 是否返回 tool_calls、content 是否为空
  B. thinking=disabled（当前生产 extra_body，见 provider.py _llm_config）——tools 是否仍工作
  C. thinking=enabled（显式开思考）——确认与 tools 的组合行为/报错/吞吐

结论回填 docs/modules/M10_agent.md §10 + docs/CHANGELOG.md。不改动正式代码。
"""
import asyncio
import json
import os

try:
    from dotenv import load_dotenv
    load_dotenv()  # 从 backend/.env 读凭据（不打印 key 值）
except ImportError:
    pass

from openai import AsyncOpenAI

API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
MODEL = os.environ.get("QUERY_MODEL") or "deepseek-v4-flash"

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "tool_retrieve",
            "description": "在知识库中检索语料，返回带来源的上下文片段",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索问题"},
                    "collection": {"type": "string", "description": "知识库标识"},
                },
                "required": ["query"],
            },
        },
    }
]

MSG = [{"role": "user", "content": "帮我查一下：数据库中关于医疗器械消毒的章节在哪个文档？"}]


async def run_case(name: str, extra: dict | None):
    client = AsyncOpenAI(api_key=API_KEY, base_url=BASE_URL)
    try:
        kw = {"model": MODEL, "messages": MSG, "tools": TOOLS, "max_tokens": 200}
        if extra:
            kw["extra_body"] = extra
        resp = await client.chat.completions.create(**kw)
        ch = resp.choices[0]
        msg = ch.message
        tc = msg.tool_calls
        print(f"[{name}] finish={ch.finish_reason!r}")
        print(f"[{name}] content={repr(msg.content)[:150]}")
        for t in tc or []:
            print(f"[{name}] tool_call → {t.model_dump()['function']['name']}"
                  f" args={t.model_dump()['function'].get('arguments')}")
        print(f"[{name}] usage={resp.usage.model_dump() if resp.usage else None}")
    except Exception as e:
        print(f"[{name}] ERROR {type(e).__name__}: {str(e)[:300]}")
    finally:
        await client.close()


async def main():
    print(f"MODEL={MODEL}  BASE={BASE_URL}  KEY_SET={bool(API_KEY)}")
    await run_case("A_默认无thinking", None)
    await run_case("B_thinking_disabled_生产配置", {"thinking": {"type": "disabled"}})
    await run_case("C_thinking_enabled", {"thinking": {"type": "enabled"}})


if __name__ == "__main__":
    asyncio.run(main())