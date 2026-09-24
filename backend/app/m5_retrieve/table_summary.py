"""表格 NL 摘要：给表格块生成一句自然语言描述，注入 rerank 文本。

探底实验：验证 cross-encoder 能否通过 NL 摘要更好理解表格语义。
规则模板（零成本）：提取 caption + 列名 + 首末行数据，拼成一段描述文字。
"""
from __future__ import annotations

import re
from typing import Any


def _extract_table_info(content: str) -> dict[str, Any]:
    """从表格块 content 中提取结构化信息。

    返回: {caption, columns, first_row, last_row, row_count, has_table}
    """
    lines = content.strip().split("\n")
    result: dict[str, Any] = {
        "caption": "",
        "columns": [],
        "first_row": [],
        "last_row": [],
        "row_count": 0,
        "has_table": False,
    }

    # 找列名行（v5.10+ 格式：【列：列名1 | 列名2 | ...】）
    col_line = ""
    for line in lines:
        m = re.match(r"【列：(.+?)】", line.strip())
        if m:
            col_line = m.group(1)
            break

    if not col_line:
        return result

    result["columns"] = [c.strip() for c in re.split(r"[|｜]", col_line) if c.strip()]

    # 找 caption：列名行上方的非空行（通常是标题或上下文）
    col_idx = None
    for i, line in enumerate(lines):
        if re.match(r"【列：", line.strip()):
            col_idx = i
            break
    if col_idx is not None:
        for i in range(col_idx - 1, -1, -1):
            text = lines[i].strip()
            if text and not text.startswith("|") and not text.startswith("#"):
                # 去掉 markdown 格式标记
                text = re.sub(r"^[#*\-\d.、\s]+", "", text)
                text = re.sub(r"\*+$", "", text).strip()
                result["caption"] = text
                break

    # 提取表格数据行
    data_rows: list[list[str]] = []
    in_table = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("|---") or stripped.startswith("| ---"):
            in_table = True
            continue
        if in_table and stripped.startswith("|") and not stripped.startswith("|---"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if cells:
                data_rows.append(cells)
        elif in_table and stripped and not stripped.startswith("|"):
            # 表格结束
            break

    result["row_count"] = len(data_rows)
    if data_rows:
        result["first_row"] = data_rows[0]
        result["last_row"] = data_rows[-1]
        result["has_table"] = True

    return result


def generate_table_summary(content: str) -> str:
    """为表格块生成一句自然语言摘要。

    规则模板：「本表展示{caption}，共 N 行数据，列出{列名}等信息。
    首行：{首行数据}；末行：{末行数据}。」

    caption 缺失时用列名组合代替。
    """
    info = _extract_table_info(content)
    if not info["has_table"]:
        return ""

    cols = info["columns"]
    col_str = "、".join(cols[:4]) if cols else "多项指标"
    if len(cols) > 4:
        col_str += "等"

    caption = info["caption"]
    row_count = info["row_count"]

    parts = []
    if caption:
        parts.append(f"本表展示{caption}")
    else:
        parts.append(f"本数据表格列出{col_str}")

    if row_count:
        parts.append(f"共 {row_count} 行数据")

    parts.append(f"维度包括：{col_str}")

    # 首行 + 末行数据（信息量最大的两行）
    first_row = info["first_row"]
    last_row = info["last_row"]
    if first_row and first_row != last_row:
        first_str = "，".join(
            f"{cols[i]}：{first_row[i]}" if i < len(cols) else first_row[i]
            for i in range(min(len(first_row), 3))
        )
        parts.append(f"起始数据：{first_str}")
    if last_row and first_row != last_row:
        last_str = "，".join(
            f"{cols[i]}：{last_row[i]}" if i < len(cols) else last_row[i]
            for i in range(min(len(last_row), 3))
        )
        parts.append(f"末尾数据：{last_str}")

    return "。".join(parts) + "。"


def build_rerank_text(chunk_id: str, chunk_meta: dict[str, Any]) -> str:
    """构建 rerank 用的文本（原 content + 表格摘要，非表格块原样返回）。"""
    content = chunk_meta.get("content", "")
    block_type = chunk_meta.get("block_type", "")
    if block_type == "table" or block_type == "Table":
        summary = generate_table_summary(content)
        if summary:
            return summary + "\n" + content
    return content
