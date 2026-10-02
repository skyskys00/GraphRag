"""M1 图片语义增强：MinerU image 条目 -> 视觉模型描述文本。

复用 .env 已有 DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL（官方 https://api.deepseek.com/v1），
模型 deepseek-flash（DeepSeek-V4.1-Flash，input_modalities 含 image）⇒ 无需新增凭据。

同步 HTTP（urllib）——不用 AsyncOpenAI：M7 ingest() 在 async 上下文里同步调 process_one()，
内部若 asyncio.run() 会抛 "cannot be called from a running event loop"。
与 m5_retrieve/sparse_index._sparse_encode 同风格。

产物 <doc_dir>/image_captions.json：{img_path: {"caption": str, "model": str, "error": str|None}}
缓存：已有且无 error 的 key 不重复调用（重跑幂等，省成本）。
失败：单图失败记 error、caption 留空，不抛异常（不阻塞整篇解析）。

方案依据：docs/modules/MULTIMODAL.md §4.2 / §5。
"""
from __future__ import annotations

import base64
import json
import os
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

_PROJ = Path(__file__).resolve().parents[2]

# 模型自评标记：prompt 要求无检索价值的图只输出它，上层据此丢弃 caption。
# ⚠️ 实测 deepseek-flash 从不输出这个裸标记（总填满三段格式），故真正生效的是
# _is_noise() 的第 2 字段判定；此处保留作兼容（模型偶发输出裸标记时仍能过滤）。
NO_INFO = "无有效信息"

# 装饰/图标类：自评「无可读文字」时即无检索价值（logo、纯装饰图形、方向图标）
_NOISE_TYPES = {"装饰图", "其他", "按键图标"}

_MAX_WORKERS = 5
_TIMEOUT = 120

_PROMPT = (
    "这是中文医疗器械说明书里的一张插图。请只输出一行，用竖线分隔三个字段：\n"
    "类型|是否有可读文字|描述\n"
    "- 类型：界面截图 / 按键图标 / 连接示意图 / 表格 / 产品照 / 装饰图 / 其他\n"
    "- 是否有可读文字：有 / 无\n"
    "- 描述：逐项列出图中全部可读文字（按钮名、菜单项、参数数值、报警文本、表格内容）；"
    "无可读文字时描述图中的部件、形状与用途\n"
    f"若该图既无可读文字、也无说明价值（如公司 logo、纯装饰图形），整行只输出：{NO_INFO}"
)

_env_loaded = False


def _load_env() -> None:
    """M1 CLI 不经 runner 加载 dotenv，此处兜底读 backend/.env（setdefault，不覆盖已有）。"""
    global _env_loaded
    if _env_loaded:
        return
    _env_loaded = True
    p = _PROJ / ".env"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        v = v.strip().strip('"').strip("'")
        if " #" in v:
            v = v.split(" #", 1)[0].rstrip()
        os.environ.setdefault(k.strip(), v)


def enabled() -> bool:
    _load_env()
    return os.environ.get("VISION_ENABLED", "true").lower() == "true"


def _model() -> str:
    _load_env()
    return os.environ.get("VISION_MODEL", "deepseek-flash")


def _min_area() -> int:
    _load_env()
    return int(os.environ.get("VISION_MIN_AREA", "10000"))


def _image_area(p: Path) -> int:
    """图片像素面积（读文件头，不解码全图）。"""
    from PIL import Image

    with Image.open(p) as im:
        w, h = im.size
    return w * h


def _is_noise(cap: str) -> bool:
    """视觉描述是否无检索价值（装饰图/logo/无文字图标）。

    prompt 里的 NO_INFO 裸标记逃生口实测从不触发（deepseek-flash 总填满
    `类型|有/无|描述` 三段格式）⇒ 改为解析字段判定：第 2 字段「是否有可读文字」
    为「无」且第 1 字段属 _NOISE_TYPES 时丢弃。非三段格式则退回裸标记判定。
    """
    parts = [p.strip() for p in cap.split("|")]
    if len(parts) < 2:
        return NO_INFO in cap
    return parts[1] == "无" and parts[0] in _NOISE_TYPES


def _caption_one(img_abs: Path, page_idx: int | None) -> str:
    """单图 -> 描述文本（同步 HTTP，见模块 docstring）。"""
    ext = img_abs.suffix.lstrip(".").lower()
    mime = "image/png" if ext == "png" else "image/jpeg"
    b64 = base64.b64encode(img_abs.read_bytes()).decode()
    prompt = _PROMPT if page_idx is None else f"（该图位于说明书第 {page_idx + 1} 页）\n{_PROMPT}"
    body = {
        "model": _model(),
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        "max_tokens": 1024,
        # deepseek-flash 是推理模型，不关思考 content 会为空
        # （docs/pitfalls/deepseek-thinking-mode.md）
        "thinking": {"type": "disabled"},
    }
    base = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
    req = urllib.request.Request(
        f"{base}/chat/completions", data=json.dumps(body).encode(), method="POST"
    )
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {os.environ.get('DEEPSEEK_API_KEY', '')}")
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        resp = json.loads(r.read().decode())
    return (resp["choices"][0]["message"]["content"] or "").strip()


def caption_images(doc_dir: Path, content_list: list[dict]) -> dict[str, str]:
    """对 content_list 中 image 条目批量生成描述，返回 {img_path: caption}。

    - 面积兜底：像素面积 < VISION_MIN_AREA 的图不调模型（滤小图标，实测阈值 10000 下
      样本1 滤 6/16、样本2 滤 4/38）。
    - 按 img_path 去重（MinerU sha256 命名，同图同路径）。
    - 无检索价值的图（_is_noise：装饰图/logo/无文字图标）与空串不入返回 dict
      ⇒ 上层自然退化为纯文本链路。
    """
    if not enabled():
        return {}

    min_area = _min_area()
    cache_path = doc_dir / "image_captions.json"
    cache: dict[str, dict] = {}
    if cache_path.exists():
        cache = json.loads(cache_path.read_text(encoding="utf-8"))

    targets: dict[str, int | None] = {}
    for it in content_list:
        if it.get("type") != "image":
            continue
        rel = it.get("img_path") or ""
        if not rel or rel in targets:
            continue
        p = doc_dir / rel
        if p.exists() and _image_area(p) >= min_area:
            targets[rel] = it.get("page_idx")

    todo = [rel for rel in targets if (cache.get(rel) or {}).get("error") is not None or rel not in cache]
    if todo:
        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as ex:
            futs = {ex.submit(_caption_one, doc_dir / rel, targets[rel]): rel for rel in todo}
            for fu, rel in futs.items():
                try:
                    cache[rel] = {"caption": fu.result(), "model": _model(), "error": None}
                except Exception as e:  # noqa: BLE001 - 单图失败不阻塞整篇
                    cache[rel] = {"caption": "", "model": _model(), "error": f"{type(e).__name__}: {e}"}
        cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")

    out: dict[str, str] = {}
    for rel in targets:
        cap = ((cache.get(rel) or {}).get("caption") or "").strip()
        if cap and not _is_noise(cap):
            out[rel] = cap
    return out
