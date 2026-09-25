# M1 模块记录：解析层

> **版本：** v1.4
> **状态：** 已落地 + 实测复核（2026-09-21）+ 章级校准（2026-09-26，v5.20）
> **更新：** 2026-09-26
> **定位：** MinerU + Docling 双引擎解析，输出统一 blocks.jsonl
> **契约：** 原始文档 → blocks.jsonl（page_label / block_type / anchor 扩展字段），见 [`M0_contracts/parse.md`](M0_contracts/parse.md)
> **上游：** 原始文档（PDF / DOCX / PPTX / HTML / …） | **下游：** [M2 切分层](M2_chunk.md)
> **依据：** [`PARSER_COMPARISON.md`](../PARSER_COMPARISON.md) v1.2（§10 实测复核）｜ [`ARCHITECTURE.md`](../ARCHITECTURE.md) §2.2
> **运行：** `cd backend && python -m app.m1_parse.run -s <源文件/目录> -o data/parse`
> **变更历史：** 见 [`CHANGELOG.md`](../CHANGELOG.md)

## 1. 定位与职责

把原始文档（PDF/图片 + docx/pptx/xlsx/html/...）转成**统一 parse 产物**（内容结构 + 块级 blocks.jsonl + 扩展字段 page_label/block_type/anchor），供 M2 切分聚合出 TextUnit，**不依赖 LLM/Xinference**（为纯本地解析）。

引擎分工（实测确定，v1.3 修订）：
- **PDF / 扫描件 / 图片 → MinerU**（`-b pipeline`、`-l ch`；勿用默认 hybrid-engine）；
- **docx / pptx / xlsx → MinerU**（office 后端，原生解析零模型，表格结构优于 Docling）；
- **html / epub / md / txt / rst / doc → Docling**（MinerU 不支持）。

**实测复核（2026-09-21，PARSER_COMPARISON §10）**：
- PDF：正文 Docling 覆盖 ~95%、表格 100% 一致，但 Docling 整段丢 bullet 列表项；MinerU 图形化大标题会整丢。
- DOCX：MinerU 结构更准（Docling 把表格表头拆成独立 paragraph 块，块数虚高内容重复），纯文本内容一致，均零模型。
- HTML：Docling 可用，结构完整。
- anchor：仅 MinerU PDF 有 `page:bbox`；docx 两家都不给 paraId。
- 产物留档 `backend/data/parse_cmp/` + `src_cmp/`。

## 2. 代码结构（`app/m1_parse/`）

| 文件 | 职责 |
|---|---|
| `run.py` | CLI 入口：`-s 文件/目录`、`-o 输出根`（默认 `data/parse`）、`--engine` 强制；逐文件隔离失败 |
| `config.py` | 引擎路由表（扩展名→MINERU_EXTS/DOCLING_EXTS）、MinerU 引擎参数、docx 定位策略 |
| `mineru_adapter.py` | 子进程调 mineru CLI → 定位 `content_list/_middle/md/images` → 归一拷贝 → 调 blocks_builder 产 blocks.jsonl |
| `docling_adapter.py` | `DocumentConverter` 转换 → iterate_items 产 blocks.jsonl + export md + 保留 doc.json |
| `blocks_builder.py` | MinerU content_list → 统一 block（含契约扩展字段），与 LightRAG IR block shape 对齐（type/blockid/heading/positions…） |
| `_util.py` | `make_doc_id`（md5 规范化路径）、`write_meta`、`write_jsonl` |

**设计要点**：未依赖 LightRAG `parser/external/*`，而是自建 adapter 直接产出我们规格的 blocks.jsonl（shape 对齐官方，扩展字段自加）——与 LightRAG 内核彻底解耦（M2 可直接吃我们的 blocks）。

## 3. 与 parse.md 契约对照

- 目录规范（§2）✅：`parse/<doc_id>/{content_list.json, <basename>.md, blocks.jsonl, doc.meta.json, images/}`（_middle.json 保留）；
- 扩展字段（§3）✅：`page_label`（PDF=page_idx / docx=null）、`block_type`（type/text_level/label 映射）、`anchor`（PDF=`page_idx:bbox` / docx=null）；
- **实测 text_level 语义**（v1.2 changelog）：MinerU 文档标题=2、正文=null、目录条目=null；docx title→1、section_header→2、table 块原 content 为空（已修复见 §7.1）。

## 4. 复现命令

```bash
# 工作根 = backend/（2026-09-14 前后端重排：app/ data/ inputs/ 等移入 backend/，先 cd 再执行）
cd backend
conda run -n graphrag python -m app.m1_parse.run -s inputs/raw -o data/parse
# 单文件 / 强制引擎：
conda run -n graphrag python -m app.m1_parse.run -s inputs/raw/Mini-OpenClaw.pdf -o data/parse
```

## 5. 验收清单（当前）

- [x] 8 文档全跑通、失败逐文件隔离（run.py 返回码聚合）；
- [x] PDF blocks 含 page_label/block_type/anchor；
- [x] docx blocks 含 title/section_header 标题层级；page/anchor=null（符合降级策略）；
- [x] 目录规范 & doc.meta.json。

## 6. 已知坑

- MinerU 产物结构 = `<out>/<basename>/auto/<basename>_content_list.json`（带文件名前缀，glob 别写死 `<basename>`）；
- MinerU bbox 三套单位（content_list=PDF 点 0–1000 归一化？实际首样例是 PDF 点坐标、需以 content_list 实际为准）；anchor 格式 `page:x0:y0:x1:y1` 为单位敏感设计；
- `text_level` 语义是主要校准点（见 §7.3）；
- 模型权重需预下载（本机 modelscope 已缓存 PDF-Extract-Kit-1.0；HF 直连不通用镜像）。

## 7. 待完善（A 项，2026-09-12 决策）

1. ✅ **表格 content 渲染**：docx 表格块 content 用 `TableItem.export_to_html(doc)` 渲染为完整 `<table>` HTML（实测已含表头/单元格内容）；兜底从 `data_table` 拼文本行。
2. **docx anchor 降级**（已定）：不写 paraId 补丁，anchor 保持 null，引用退化为「文件 + 文本片段」；后续按实际效果再决定是否补 `msword_backend` 插桩。
3. **text_level 深标题校准（PDF 章级已落地，v5.20）**：MinerU PDF 链路模型推断 text_level 常把「第X章」误判为与「X.Y」节同级（都=2），`blocks_builder` 已用 `_CHAPTER_RE`（`^第[…]…[章节篇卷]`）强制 level=1，chunker 弹栈后 title_path 恢复章→节结构；docx 章值本为 1 不参与。多级深标题（节下再分层）仍是模型推断，未做进一步细分——待切分/检索效果不足时再校准 0/1/2 语义。

## 8. 版本

- **v0.3**（2026-09-14）：`process_one` 被 M7 上传管线复用（doc_id=目录名）。
- 变更记录：**逐条版本历史见 `docs/CHANGELOG.md`**（v0.1 骨架首跑 8/8 → v0.2 A 项表格渲染/anchor 降级 → v0.3 联动复用）。本文件不再维护历史流水。