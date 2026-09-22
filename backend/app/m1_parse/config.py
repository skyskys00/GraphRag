"""M1 解析层配置（契约 parse.md v1）。"""
from __future__ import annotations

from pathlib import Path

# 输出根（可由 CLI -o 覆盖），每文档独立 <doc_id>/ 目录
DEFAULT_PARSE_ROOT = Path("data/parse")

# 引擎路由：扩展名 -> 引擎
# 实测 v3.4.5：MinerU 原生支持 pdf/图片 + docx/pptx/xlsx；HTML/EPUB/MD 等走 Docling
MINERU_EXTS = {".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".docx", ".pptx", ".xlsx"}
DOCLING_EXTS = {".html", ".htm", ".epub", ".md", ".txt", ".rst", ".doc"}

# MinerU 引擎参数（实测约束，见 PARSER_COMPARISON）
MINERU_BACKEND = "pipeline"      # 勿用默认 hybrid-engine（需大 VLM）
MINERU_LANG = "ch"

# docx 定位：Docling 不提供 paraId（实测 prov 全空），anchor 此处留空，待自研补丁
ALLOW_DOCX_ANCHOR = False