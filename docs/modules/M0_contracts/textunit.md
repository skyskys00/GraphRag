# TextUnit 契约 v2

> **版本：** v2
> **状态：** 已落地
> **更新：** 2026-09-12
> **定位：** 切分层（M2）出口统一数据模型，全系统唯一"块"标准
> **契约：** blocks.jsonl → TextUnit v2 JSONL（必填六字段 + page_range/anchor/block_type 三扩展字段）
> **上游：** [parse 契约](parse.md)（M1 产物） | **下游：** [M3 索引层](../M3_index.md) / [M5 检索层](../M5_retrieve.md) / [M6 生成层](../M6_generate.md)
> **依据：** LightRAG 接口 + MinerU/Docling 源码拆解 + [`PARSER_COMPARISON.md`](../../PARSER_COMPARISON.md)
> **运行：** `textunit.schema.json` JSON Schema 机器校验（`$id`= `/textunit/v2`）
> **变更历史：** 见 [`../CHANGELOG.md`](../CHANGELOG.md)

## 1. 这个契约是干什么的

TextUnit = 切分层（M2）出口的统一数据模型，全系统唯一"块"标准。三个消费者全部只认它：
- **M3 索引**：LightRAG 建图（content / heading / entity_refs 反查）+ bge-m3 编码（content → embedding）；
- **M5 检索**：向量检索返回的是 TextUnit（带 source 与排序）；
- **M6 引用标注**：答案引用 `text_unit_id` + `file_path` + `page_range/anchor`（定位原文）。

## 2. Document 简述（上游，非重点校验对象）

Document = 文档级记录：`doc_id / file_path / content_hash / parse_engine / parse_format(raw|pending_parse) / process_options / chunk_options / blocks_path`。切分前必须断言 `doc_id` 已生成（规则：已知源 `md5(file_path)`、原始内容 `md5(content)`，详见 interfaces.md §3.1）。

## 3. TextUnit 字段表

图例：✅ = LightRAG 现成对齐 ｜ ⚠️ = 半成品/需聚合 ｜ 🆕 = 我们新增 ｜ 取值 ≈ 解析器已给、M1 接一下

| 字段 | 类型 | 必填 | 来源/对齐 | 取值现在是否明确 |
|---|---|---|---|---|
| `text_unit_id` | string | ✅ | LightRAG chunk_key | ✅ |
| `content` | string | ✅ | LightRAG content | ✅ |
| `tokens` | int | ✅ | LightRAG tokens | ✅ |
| `full_doc_id` | string | ✅ | LightRAG full_doc_id | ✅ |
| `chunk_order_index` | int | ✅ | LightRAG | ✅ |
| `file_path` | string | ✅ | LightRAG | ✅ |
| `heading` | object? | — | LightRAG，P 策略 | ✅ |
| `title_path` | string? | — | 🆕（heading 合成） | ✅ 规则定 |
| `page_range` | [int,int]? | — | 🆕 | ✅ 见 §4.1 |
| `anchor` | string? | — | 🆕 | ⚠️ docx 需自研补丁（§4.2） |
| `block_type` | enum? | — | 🆕 | ✅ 见 §4.3 |
| `sidecar` | object? | — | LightRAG，P/多模态 | ✅ |
| `embedding` | float[]? | — | LightRAG | ✅ |
| `entity_refs` | string[]? | — | ⚠️ 派生视图 | ✅ 反查规则定 |
| `llm_cache_list` | string[]? | — | LightRAG | ✅ |

必填六项 = LightRAG `TextChunkSchema` 核心字段 + `text_unit_id`/`file_path`。**核心链路（建图/检索/生成）只用必填 + heading 就够**；结构化展示与引用溯源依赖 `title_path / page_range / anchor / block_type / entity_refs`。

## 4. 取值机制（v2 补全，基于 MinerU/Docling 拆解）

### 4.1 `page_range` —— 解析器已内置，M1 接一下即可

| 来源 | 页码从哪取 | 说明 |
|---|---|---|
| PDF（MinerU 主） | `content_list.json` 每项内嵌 `page_idx`（0 起始） | **LightRAG 已内置消费链**（`lightrag/parser/external/mineru/ir_builder.py` 读 content_list v1 产 blocks.jsonl）→ M1 几乎零改配可得。MinerU 引擎用 **`-b pipeline`**（非默认 hybrid-engine，后者依赖大型 VLM 本机跑不了） |
| PDF（Docling 兜底） | `prov.page_no` | 阅读顺序模型写 prov |
| PPTX / XLSX | slide+1 / sheet 序号 | Docling |
| HTML | 几何估算页 | Docling，跨浏览器不稳，引用定位弱 |
| docx | **无页码** | 流式排版本质，不设此字段（走 anchor） |

### 4.2 `anchor` —— docx 定位是**唯一需自研**的点

- PDF：`page_idx:bbox`（content_list 内嵌 bbox，单位为归一化 0–1000；细粒度定位用此）。
- **docx：Docling 不提供任何 paraId**（全仓库无 `w14:paraId` 读取；`msword_backend.py` 无 `prov`、`supports_pagination()==False`）。对策二选一（在 M1 落地时定）：
  1. **自研补丁**：fork/插桩 `msword_backend.py` 的 `_handle_text_elements`，把原始 docx 的 `w14:paraId` 写进 `item.meta`；或
  2. **post-process**：直接读原始 docx XML 重算 paraId 对齐。
  在此之前，docx 的 `anchor` 可先不填（引用定位退化为"文件 + 文本片段"），不影响核心链路。

### 4.3 `block_type` —— 解析器已给结构标签，M2 聚合

- MinerU：`text_level`（`doc_title`→`title` / `paragraph_title`→`heading`）+ span 类型（`image`/`table`/`inline_equation`/`interline_equation`/...）映射；
- Docling：`label`（`paragraph`/`table`/`picture`/`formula`/`heading`/`list_item`/...）映射；
- 一个 chunk 由多块组成时，M2 取主导类型（或标 `mixed`）。

## 5. 与拆解文档的联系

| 拆解文档 | 与本契约的关系 |
|---|---|
| `lightrag/docs/interfaces.md` | 字段对齐依据；`build_chunks_dict_from_chunking_result` 收口补点 |
| `mineru/docs/mineru.md` | `page_range/block_type` 取值来源（content_list）；`-b pipeline` 选型 |
| `docling/docs/docling.md` | `anchor`（docx 无 paraId，自研补丁点）+ PPTX/XLSX/HTML 页码 |
| `lightrag/docs/{storage,graph,retriever,llm}.md` | TextUnit 落库 / 抽取入参 / 检索返回 / LLM 链路 |
| `lightrag/docs/qa-notes.md` | §E 契约三字段够用边界与补点大白话 |

## 6. 版本与变更策略

- **v1**（2026-09-12）：字段结构定稿（基于 LightRAG 拆解）。
- **v2**（2026-09-12，本次）：补 `page_range/anchor/block_type` 取值机制（MinerU/Docling 拆解后）；判定 docx 需自研定位补丁。
- 未来 v3 预期（M1 实现后）：若 docx 自研补丁落地，固化 `anchor` 的 paraId 取值规则；必要时新增 `parse/` 契约文件（blocks.jsonl 扩展字段规范）。
- 变更原则：**只加字段、不改既有字段语义**；每个版本留 changelog。
- 校验：M0 阶段提供 `docs/modules/M0_contracts/validate.py`（jsonschema 跑样例）。

## 7. Changelog

- **2026-09-12 · v1**：初稿；字段结构基于 LightRAG v1.5.7 拆解；结构化补点标注"待拆"。
- **2026-09-12 · v2**：MinerU/Docling 拆解完成 → 补全取值机制（§4）；`page_range`（PDF 经 content_list/`prov.page_no`）、`block_type`（text_level/label 映射）、`anchor`（docx 无 paraId → 自研补丁点）。schema `$id`→`/textunit/v2`。