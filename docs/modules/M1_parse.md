# M1 模块记录：解析层

> 状态：**初版跑通**（2026-09-12，inputs/raw 8/8）。后续迭代：A 项完善见 §7。
> 契约：`docs/modules/M0_contracts/parse.md`（v1.2）｜ 选型依据：`docs/PARSER_COMPARISON.md`

## 1. 定位与职责

把原始文档（PDF/图片 + docx/pptx/xlsx/html/...）转成**统一 parse 产物**（内容结构 + 块级 blocks.jsonl + 扩展字段 page_label/block_type/anchor），供 M2 切分聚合出 TextUnit，**不依赖 LLM/Xinference**（为纯本地解析）。

引擎分工（实测确定）：
- **PDF / 扫描件 / 图片 → MinerU**（`-b pipeline`、`-l ch`；勿用默认 hybrid-engine）；
- **docx / pptx / xlsx / html / epub / md / txt → Docling**（office 链零模型）。

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
3. **text_level 深标题校准（暂缓）**：先以跑通为目标（标题=2 / 正文=null）；若切分/检索效果不足或需细分层级，再在多文档上校准 0/1/2 语义。

## 8. 变更记录

- **2026-09-12 · v0.1**：M1 骨架首跑 8/8；修正 `write_meta` 参数名；text_level 语义实测入 parse.md v1.2。
- **2026-09-12 · v0.2**：A 项完成——① 表格 content 渲染为 HTML；② docx anchor 确认降级（null，引用退化为文件+文本片段，paraId 补丁按后续效果再决定）；③ text_level 深标题校准暂缓（先跑通，效果不足再细分）。
- **2026-09-14 · v0.3（联动补记）**：`run.py` 的 `process_one(src, out_root, engine=None)` 被 M7 文档上传管线（`app/m7_interact/documents.py`）以库函数方式复用 —— 上传单文件 → 返回 parse 目录，目录名即 doc_id，实现业务人员自助入库的一环。M1 自身逻辑未改动。
- （后续在此追加）