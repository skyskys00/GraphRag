"""LLM 裁判封装：DeepSeek flash + 本地文件缓存 + 重试。

用同一个 query_func（DeepSeek v4-flash）当裁判，提示词中文化。
缓存以 (question_hash, answer_hash, context_hash, metric) 为 key 存 JSON。
"""
from __future__ import annotations

import hashlib
import json
import random
import asyncio
from pathlib import Path
from typing import Any, Callable

CACHE_DIR = Path(__file__).parent / "cache"
MAX_RETRIES = 3          # 重试次数上限（首次调用不算）
RETRY_BASE_DELAY = 2.0   # 指数退避基数（秒）：2 → 4 → 8
RETRY_MAX_DELAY = 20.0   # 单次退避上限（秒）
RETRY_JITTER = 0.5       # 退避随机抖动（±秒），避免同批调用同步重试
CONCURRENCY = 5          # 全局 LLM 裁判并发上限：每题 20+ chunk 并发直连会互相踩
# 4xx 客户端错误类名（openai SDK），重试无意义
_NON_RETRYABLE_NAMES = (
    "BadRequestError", "AuthenticationError", "PermissionDeniedError",
    "NotFoundError", "UnprocessableEntityError", "ConflictError",
)
_NON_RETRYABLE_CODES = ("400", "401", "403", "404", "409", "413", "422")

# 全局缓存（内存 + 磁盘双层）
_mem_cache: dict[str, float] = {}
_semaphore = asyncio.Semaphore(CONCURRENCY)


def _should_retry(err: Exception) -> bool:
    """哪些异常值得重试：连接 / 超时 / 429 / 5xx 重试；4xx(非429) 立即失败。

    服务端拥塞（本项目 Connection Error 根因）表现为连接偶发失败或 5xx，
    重试有效；参数错误等 4xx 重试只会空耗退避时间。
    """
    if type(err).__name__ in _NON_RETRYABLE_NAMES:
        return False
    msg = str(err).upper()
    for code in _NON_RETRYABLE_CODES:
        if f"STATUS CODE: {code}" in msg or f"HTTP {code}" in msg:
            return False
    return True


def _backoff(attempt: int) -> float:
    """指数退避 + 抖动。attempt 从 0 开始：2s → 4s → 8s（上限 20s）。"""
    delay = min(RETRY_MAX_DELAY, RETRY_BASE_DELAY * (2 ** attempt))
    return max(0.0, delay + random.uniform(-RETRY_JITTER, RETRY_JITTER))


def _hash(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8", errors="replace"))
    return h.hexdigest()[:16]


def _cache_path(key: str, metric: str) -> Path:
    return CACHE_DIR / f"{metric}_{key}.json"


def _cache_read(key: str, metric: str) -> dict | None:
    if key in _mem_cache:
        # 内存缓存只存分数，磁盘缓存有完整理由
        # 内存命中也回磁盘读一次获取完整信息
        pass
    p = _cache_path(key, metric)
    if p.exists():
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            _mem_cache[key] = data.get("score", 0.0)
            return data
        except (json.JSONDecodeError, OSError):
            return None
    return None


_EXTRA_FIELDS = ("total_facts", "correct_facts", "incorrect")


def _cache_write(key: str, metric: str, result: dict[str, Any]) -> None:
    score = result["score"]
    reason = result["reason"]
    _mem_cache[key] = score
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    p = _cache_path(key, metric)
    payload = {"score": score, "reason": reason}
    for f in _EXTRA_FIELDS:
        if f in result:
            payload[f] = result[f]
    payload["raw"] = result.get("raw", "")
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def _use_cached(cached: dict) -> dict[str, Any]:
    """把磁盘缓存展开回 judge 的返回结构（score/reason + 额外字段）。"""
    payload = {"score": cached["score"], "reason": cached.get("reason", ""), "cached": True}
    for f in _EXTRA_FIELDS:
        if f in cached:
            payload[f] = cached[f]
    if "raw" in cached:
        payload["raw"] = cached["raw"]
    return payload


async def judge(
    query_func: Callable,
    metric: str,
    prompt: str,
    *,
    cache_key_parts: list[str] | None = None,
    system_prompt: str | None = None,
) -> dict[str, Any]:
    """调用 LLM 裁判，返回 {score, reason, cached, 额外字段...}。

    约定：裁判 prompt 要求模型输出 JSON 格式 {"score": 0-1 float, "reason": "..."}，
    可附带 total_facts / correct_facts / incorrect 等诊断字段。
    如果解析失败，返回 score=0 + raw_text 供排查。
    """
    key = _hash(*(cache_key_parts or [prompt]))

    cached = _cache_read(key, metric)
    if cached is not None:
        return _use_cached(cached)

    async with _semaphore:
        last_err: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                raw = await query_func(prompt, system_prompt=system_prompt)
                result = _parse_judge_output(raw)
                result["raw"] = raw
                _cache_write(key, metric, result)
                result["cached"] = False
                return result
            except Exception as e:
                last_err = e
                if not _should_retry(e):
                    break
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(_backoff(attempt))

    return {
        "score": 0.0,
        "reason": f"裁判调用失败: {last_err}",
        "cached": False,
        "error": str(last_err) if last_err else "",
    }


def _parse_judge_output(raw: str) -> dict[str, Any]:
    """从 LLM 输出中解析 JSON。兼容 Markdown 代码块包裹。"""
    text = raw.strip()

    # 去掉 ```json ... ``` 包裹
    if text.startswith("```"):
        lines = text.split("\n")
        # 去掉首行 ```json 或 ```
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        # 去掉末行 ```
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    # 尝试直接解析
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # 尝试从文本中提取第一个 {...} 块
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                data = {"score": 0.0, "reason": f"无法解析JSON: {raw[:200]}"}
        else:
            data = {"score": 0.0, "reason": f"无法解析JSON: {raw[:200]}"}

    score = float(data.get("score", 0.0))
    score = max(0.0, min(1.0, score))
    reason = str(data.get("reason", ""))

    payload: dict[str, Any] = {"score": score, "reason": reason}
    for f in _EXTRA_FIELDS:
        if f in data:
            payload[f] = data[f]
    return payload


def clear_cache(metric: str | None = None) -> int:
    """清空缓存，返回删除的文件数。"""
    if not CACHE_DIR.exists():
        return 0
    count = 0
    for p in CACHE_DIR.iterdir():
        if p.is_file():
            if metric is None or p.stem.startswith(f"{metric}_"):
                p.unlink()
                count += 1
    _mem_cache.clear()
    return count
