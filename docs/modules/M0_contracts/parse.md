# 解析层产物契约 v1 —— `docs/modules/M0_contracts/parse.md`

> 定位：模块 **M1（解析层）** 的产物规约 =「输入什么 → 走哪个引擎 → 产出什么结构」。核心是 **blocks 扩展字段**（page_label / block_type / anchor），供 M2 切分聚合进 TextUnit。
> 版本：v1（2026-09-12），依据 MinerU/Docling 源码拆解（`mineru/docs/mineru.md`、`docling/docs/docling.md`）与 `docs/PARSER_COMPARISON.md` 的选型结论。
> 血缘：本契约产出 → M2 聚合 → `docs/modules/M0_contracts/textunit.md` v2 的 `page_range/anchor/block_type`。

## 1. 引擎路由（M1 采用，终态）

| 输入 | 引擎 | 参数 | 备注 |
|---|---|---|---|
| PDF / 扫描件 / 图片 | **MinerU** | `-b pipeline`（**勿用默认 hybrid-engine**，需大型 VLM） | 主力；输出走 **JSON/content_list** |
| docx / pptx / xlsx / html / epub / md / txt | **Docling** | `DocumentConverter` + office 链（零模型） | 多格式兜底 |
| 图片集 / 扫描 PDF | MinerU（内含 OCR） | pipeline 自动 | pytorchocr：PP-OCRv6 |

> 扫描件判断：可先让 pipeline 全程跑（OCR 覆盖）；复杂就第二阶段再区分。M1 首版不做独立路由，全部走对应引擎。

## 2. 输出目录规范（每文档独立目录，便于增量与删除）

```
<工作目录>/parse/<doc_id>/
├── <basename>.md            # 统一 Markdown（轻量视图，不含结构元数据）
├── content_list.json        # MinerU：结构主源（page_idx + bbox + text_level + blocks）——PDF 走这个
├── _middle.json             # MinerU：span 级细粒度侧车（可选，需要再展开）
├── images/                  # 抽取的图片/公式图（MinerU：sha256 扁平命名）
├── blocks.jsonl             # 统一块级文件（LightRAG 消费；我们在此加扩展字段，见 §3）
└── doc.meta.json            # 解析元数据（引擎、版本、耗时、警告）
```

> docx 等非 PDF：Docling 的 `DoclingDocument` JSON 需要导出为**统一 blocks.jsonl**（搬运 label/level/prov），沿用 LightRAG `parser/external/docling/` 官方链，其上补扩展字段。

## 3. blocks.jsonl 扩展字段规范（M1 的最终产物增补）

LightRAG 官方 IR block 已有：`blockid / content / heading / parent_headings / level / positions[]`。我们**在其上加三块**（对应 TextUnit 契约）：

| 扩展字段 | 取值来源 | 格式 |
|---|---|---|
| `page_label` | MinerU：`content_list[].page_idx`（0 起始）<br>Docling：PDF `prov.page_no`；PPTX=slide+1；XLSX=sheet 序号；HTML=估算页 | int（**docx 不设**，流式排版无页） |
| `block_type` | MinerU：`text_level`（doc_title→title / paragraph_title→heading）+ span 类型<br>Docling：`label`（paragraph/table/picture/formula/heading/list_item/...） | enum：`heading/title/paragraph/table/drawing/equation/list_item/mixed` |
| `anchor` | PDF：`page_idx:bbox`（content_list 内嵌 bbox，归一化 0–1000）<br>docx：**待 M1 自研**（Docling 无 paraId；方案：插桩 `msword_backend._handle_text_elements` 写 `item.meta`，或 post-process 读原始 XML `w14:paraId`） | PDF=`"{page}:{x0},{y0},{x1},{y1}"`；docx=`paraid:<w14:paraId>`（补丁后） |

**插入时机**（最小侵入）：沿用 LightRAG `parser/external/{mineru,docling}` 官方 IR 转换链，在产出 IR block 之后、`blocks.jsonl` 写盘之前，用一段轻量 `attach_extensions(block, source_record)` 附加以上字段——不改官方链内部。

## 4. 统一 Markdown 规范

- MinerU 输出 Markdown 直接在官方基础上归一：表格→HTML、公式→`$latex$`、图片→`![](images/<sha>.png)` 占位。
- Docling 非 PDF：export 到 Markdown（table→HTML 或 GFM）。
- **纪律：结构对接一律用 JSON/blocks，Markdown 仅供预览**（Parser 对比结论）。

## 5. 数据流（M1 全链）

```
输入(原文件)
  → 路由（PDF→MinerU / 非PDF→Docling）
  → 解析（引擎自身 pipeline）
  → 结构源（MinerU content_list / DoclingDocument）
  → 官方 IR（parser/external → IRBlock）
  → attach_extensions（+ page_label/block_type/anchor）→ blocks.jsonl
  → 渲染统一 Markdown + images/ + doc.meta.json
  → 解析状态：成功→PROCESSED；失败→FAILED（可重试，重试幂等，凭 doc_id 目录）
```

## 6. 与上下游契约的衔接

| 契约 | 关系 |
|---|---|
| `docs/modules/M0_contracts/textunit.md`（v2） | 本契约的 blocks 扩展字段 → M2 P 策略切分时聚合 => TextUnit 的 `page_range/anchor/block_type`（parse 是上游取值源） |
| `docs/PARSER_COMPARISON.md` | 引擎选择与 OCR 事实依据 |
| `lightrag/docs/interfaces.md` §5 | `page_range/anchor/block_type` 在 LightRAG 侧"半成品"→ 由本契约补全 |

## 7. Changelog

- **2026-09-12 · v1**：初稿。引擎路由、目录规范、blocks 扩展字段（page_label/block_type/anchor）、插入时机；docx 定位标"待 M1 自研"。
- **2026-09-12 · v1.1（冒烟实证）**：本机冒烟通过——MinerU `content_list.json` 每条目**实测内嵌** `type/text/text_level/bbox/page_idx`（page_idx 0 起始；bbox 为 PDF 点坐标）；Docling docx 实测 `prov` **全空**（无页码/无坐标/无 charspan），但 **`title`/`section_header` label 可得**（docx 的 title_path 来源）。**待确认**：MinerU `text_level` 数值语义（样例中文档标题=2，正文应=0/1），adapter 编码时实证再定映射。
- **2026-09-12 · v1.2（M1 首个实现实证）**：`app/m1_parse` 跑通 inputs/raw 8/8（PDF→mineru、其余→docling）。实测 `text_level` 语义：**文档标题=2、正文=null、目录条目=null**（标题恒 2 级、目录项不带层级）；docx：`title`→level1、`section_header`→level2、table 块 `content` 为空（单元格需另取）。待优化：表格内容渲染、docx anchor 自研补丁、text_level 深标题校准。