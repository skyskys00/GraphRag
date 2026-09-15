"""M6 生成：中文 RAG prompt + M0 query_func（DeepSeek flash，thinking 已关）。"""
from __future__ import annotations

SYSTEM_PROMPT = (
    "你是企业内部知识库问答助手。请仅依据给定的【检索材料】回答用户问题，"
    "严禁编造材料中没有的信息。每个论断后用方括号标注依据的材料编号，如 [1]、[2]；"
    "一个论断可叠加多个编号；编号必须是材料列表里出现的编号。"
    "如果材料不足以回答，请直接说明缺什么，不要猜测。"
    "回答使用与问题一致的语言（中文问题用中文回答）。"
)


def build_prompt(query: str, context: str, response_type: str | None = None) -> str:
    output_req = response_type or "请分段回答，先总后分，条理清晰。"
    return (
        f"【输出要求】{output_req}\n\n"
        f"【检索材料】\n{context}\n\n"
        f"【问题】\n{query}\n"
    )


async def generate(query_func, query: str, context: str, response_type: str | None = None) -> str:
    """query_func = M0 providers.build_llm_func()[1]（单 prompt → 文本）。"""
    user = build_prompt(query, context, response_type)
    text = await query_func(user, system_prompt=SYSTEM_PROMPT)
    return (text or "").strip()


async def generate_stream(query_stream_func, query: str, context: str, response_type: str | None = None):
    """真流式生成：yield 每次文本增量（query_stream_func = M0 build_query_stream_func()）。"""
    user = build_prompt(query, context, response_type)
    async for delta in query_stream_func(user, system_prompt=SYSTEM_PROMPT):
        if delta:
            yield delta