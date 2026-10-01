# M2 模块记录：切分层

> **版本：** v1.4
> **状态：** 已落地（v1.4 图片独立成块，2026-09-29）
> **更新：** 2026-09-29
> **定位：** 标题驱动切块 + 表格整块（层级 0 双表示 content=MD/html + 层级 2 行级切分 + v1.3 列名前缀增强）+ 图片独立成块 → TextUnit v2
> **契约：** blocks.jsonl → TextUnit v2 JSONL，见 [`M0_contracts/textunit.md`](M0_contracts/textunit.md)
> **上游：** [M1 解析层](M1_parse.md) | **下游：** [M3 索引层](M3_index.md)
> **依据：** [`FRAMEWORK_NOTES.md`](../FRAMEWORK_NOTES.md) §3
> **运行：** `cd backend && python -m app.m2_chunk.runner -s data/parse -o data/chunks`
> **变更历史：** 见 [`CHANGELOG.md`](../CHANGELOG.md)

## 1. 定位与职责

把 M1 产出的 blocks.jsonl 按**标题层级**聚合为 TextUnit v2（契约 textunit v2，聚合处补 `page_range/anchor/block_type`）。纯本地规则切分，**不依赖 LLM/embedding**。

三个核心决策：
- **标题驱动分组**：`heading`/`title`（`block_id` 带 `level>=1`）开新块，正文归入当前标题链并维护 `title_path`；
- **表格整块**：`block_type=table` 单独成块（仍带当前标题上下文）；标题刚开即紧接表格时，标题+表格并入同一 TextUnit 保上下文。**v1.2 起双表示**：content 为 Markdown（embedding/生成用干净文本）+ 新增 `html` 字段（重建 `<table>`，预览用）；大表按 `TABLE_SPLIT_ROWS=5` 行级切分组，每组重复表头，caption 附首组、footnote 附末组；
- **聚合扩展字段**：`page_range`（PDF 块列表页码 min/max）、`anchor`（取首个）、`block_type`（主导类型多数派，平票取先出现者）。
- **图片独立成块**（v1.4，多模态）：`block_type=drawing` 且 content 非空（视觉描述或过滤后的 MinerU 真图注）时单独成块，不与相邻正文合并（描述语义自足），仍带当前标题链上下文，新增 `image_path` 字段。content 为空走不到此分支 ⇒ 被末尾过滤丢弃。见 [`MULTIMODAL.md`](MULTIMODAL.md) §4.3。

## 2. 代码结构（`app/m2_chunk/`）

| 文件 | 职责 |
|---|---|
| `chunker.py` | 核心切分 `chunk_blocks()`（遍历 blocks、维护标题路径、表格旁路、图片独立成块）+ 表格双表示 `split_table_block()` / `_build_split_unit()`（**层级 0** content=Markdown + html 字段、**层级 2** 行级切分组）+ 图片终态 `_build_image_unit()`（v1.4）+ 字段聚合 `_build_textunit()` + `approx_tokens`（中文 1 字≈1 token 粗估） |
| `runner.py` | CLI 入口：`-s data/parse`、`-o data/chunks`；逐文档失败隔离，返回码聚合 |
| `textunit.py` | 契约序列化 + 校验 `validate()`（REQUIRED 六字段 / content 非空 / page_range 长度 2）；不合规单元跳过并 raise |

## 3. 切分规则细读

- **标题路径维护**：遇 heading 出新块，先弹栈弹出 `level >= 当前` 的祖先再入栈；`title_path` = 栈内标题 `" / "` 连接，供引用层级展示；
- **正文归属**：非标题非表格块挂到当前 `cur`；若在标题前出现（文件头无标题），归入空路径块；
- **表格并块特判**：仅在 `cur` 刚开且只含标题块时并入；否则表格独立成块（`is_table=True`），后续正文继续挂标题。v1.1 修复：**并块时同样置 `cur["is_table"]=True`**，否则 `_dominant_type()` 按多数派会把 block_type 退化为 paragraph，表格单元类型丢失（M1 修复后索引内容不受影响，但预览表格渲染失效）；
- **空内容过滤**：输出前丢弃 `content.strip()` 为空的单元（如只有元数据无正文）。
- **图片独立成块**（v1.4）：`block_type=drawing` 且 `content` 非空时，先 flush 当前块再 append 一个终态图片 TextUnit（`_build_image_unit`，带 `image_path`），后续正文继续挂原标题链；语义自足故不与正文合并。

## 4. 与 textunit.md v2 契约对照

- 必填六项（`text_unit_id/content/tokens/full_doc_id/chunk_order_index/file_path`）✅；
- 聚合三字段✅：`page_range`（PDF=min/max 页码，docx=null）、`anchor`（PDF=`page:bbox`，docx=null）、`block_type`（table/多数派，空则 paragraph）；
- 预留字段✅：`embedding=null`、`entity_refs=[]`、`llm_cache_list=[]`（M3 索引时回填）；
- 图片字段✅（v1.4）：`image_path`（仅 `block_type=drawing` 时存在，指向 `<doc_dir>/images/<sha>.jpg`）。

## 5. 复现命令

```bash
# 工作根 = backend/（2026-09-14 前后端重排：app/ data/ inputs/ 等移入 backend/，先 cd 再执行）
cd backend
conda run -n graphrag python -m app.m2_chunk.runner -s data/parse -o data/chunks
```

## 6. 实测统计（2026-09-13 复现）

| doc_id | blocks | TextUnit |
|---|---|---|
| 035cfc6c09de40ac | 23 | 5 |
| 0d02abac3b71ee19 | 55 | 15 |
| 7497ed754e640dd4 | 23 | 5 |
| 80bc0c8cd24d2368 | 4 | 1 |
| 98533c53822b85e4 | 7 | 4 |
| **合计** | **112** | **30** |

与 DeepSeek 正式索引库 `data/lightrag_deepseek` 的 `vdb_chunks`（30 records）一一对应。注意：当前 `data/parse` 仅保留 5 份正式语料，早前「8 文档 66 块」为中间态，已不含此目录。

## 7. 已知坑

- runner.py 顶部 docstring 写的 `-m app.m2_chunk.run` 为旧模块名（现为 `.runner`）——复现以 §5 为准；
- `approx_tokens` 是长度粗估，真实 token 数由 M3 索引（LightRAG 侧）计；
- docx 文档 `page_range/anchor=null` 是设计降级（流式排版无页码），引用定位退化为「文件+文本片段」。

## 8. 版本

- **v1**（2026-09-13）：闭环补记（代码+产物实测重建）；`process_document` 后被 M7 上传管线复用（联动补记）。
- 变更记录：**逐条版本历史见 `docs/CHANGELOG.md`**。本文件不再维护历史流水。