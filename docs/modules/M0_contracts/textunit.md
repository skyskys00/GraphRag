# TextUnit 契约 v2

> **版本：** v2.1
> **状态：** 已落地
> **更新：** 2026-09-29
> **定位：** 切分层（M2）出口统一数据模型，全系统唯一"块"标准
> **契约：** blocks.jsonl → TextUnit v2 JSONL（必填六字段 + page_range/anchor/block_type/image_path 扩展字段）
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

| 字段 | 含义（这字段是什么/谁消费它） | 类型 | 必填 | 来源/对齐 | 取值现在是否明确 |
|---|---|---|---|---|---|
| `text_unit_id` | 块唯一标识 `<doc_id>-chunk-<序号>`；全系统「块主键」——M6 引用 `[n]`、M5 检索返回、sidecar 溯源都以此对齐 | string | ✅ | LightRAG chunk_key | ✅ |
| `content` | 块正文（解析后渲染文本）；M3 bge-m3 编码与生成上下文组装的原料 | string | ✅ | LightRAG content | ✅ |
| `tokens` | 文本量粗估（中文 1 字≈1 token，M3 索引重计）；生成层上下文预算控制 | int | ✅ | LightRAG tokens | ✅ |
| `full_doc_id` | 所属文档 ID（上游 `Document.doc_id`）；跨文档检索、按文档过滤、引用分组 | string | ✅ | LightRAG full_doc_id | ✅ |
| `chunk_order_index` | 文档内块序号；引用排序与顺序稳定性依赖它 | int | ✅ | LightRAG | ✅ |
| `file_path` | 源文件相对路径；前端「点击引用定位原文」的文件层 | string | ✅ | LightRAG | ✅ |
| `heading` | 标题三级结构 `{level, heading, parent_headings}`；文档结构化与引用上下文展示 | object? | — | LightRAG，P 策略 | ✅ |
| `title_path` | 规范化标题路径 `章 / 节`（heading 合成）；引用层级展示 | string? | — | 🆕（heading 合成） | ✅ 规则定 |
| `page_range` | 本块覆盖页码区间 `[start,end]`；前端定位「页」（docx=null） | [int,int]? | — | 🆕 | ✅ 见 §4.1 |
| `anchor` | 原文定位锚 `page:bbox`；点击引用定位到原文具体位置（docx 无 paraId → null，降级为「文件+文本片段」） | string? | — | 🆕 | ⚠️ docx 需自研补丁（§4.2） |
| `block_type` | 聚合块类型 `table/drawing/paragraph/...`；路由表格/图片预览、检索数字/表格特征 | enum? | — | 🆕 | ✅ 见 §4.3 |
| `image_path` | 图片独立块的原图相对路径；前端图片预览（仅 `block_type=drawing` 存在） | string? | — | 🆕 | ✅ 见 §4.4 |
| `html` | 表格块重建的 `<table>` HTML（v1.2 表格双表示）；前端表格预览，content 仍为 Markdown 供索引/生成 | string? | — | 🆕（M2 自产，v1.2） | ✅ |
| `embedding` | dense 向量（bge-m3）；M3 索引时编码回填 | float[]? | — | LightRAG | ✅ |
| `entity_refs` | 关联实体 ID 列表（派生视图）；M3 建图时由 entity_chunks 反查回填，不冗余写回 | string[]? | — | ⚠️ 派生视图 | ✅ 反查规则定 |
| `llm_cache_list` | LLM 缓存键列表；LightRAG 实体抽取缓存控制 | string[]? | — | LightRAG | ✅ |

必填六项 = LightRAG `TextChunkSchema` 核心字段 + `text_unit_id`/`file_path`。**核心链路（建图/检索/生成）只用必填 + heading 就够**；结构化展示与引用溯源依赖 `title_path / page_range / anchor / block_type / html / image_path`；`entity_refs/embedding/llm_cache_list` 为 M3 回填预留。

> **⚠️ `sidecar` 不进字段表——同名但不是字段**：TextUnit 的 M2 产物里没有 `sidecar` 这个键（schema 中同名声明只是放行 LightRAG 上游可选属性，`additionalProperties: true` 而已，M2 不产出、PG `lightrag_doc_chunks.sidecar` 列为空）。真正的 `Sidecar` 是 **M6 自研的溯源服务**（`app/m6_generate/sidecar.py`，v5.25 落地 / v5.28.1 键修正）：从 `data/chunks/*.jsonl` 构建 `chunk_id → {file_path, title_path, page_range, anchor, block_type, image_path}` 索引，供 M6 引用标注与 M7 前端「点击引用定位原文」消费（见 `M6_generate.md` §4.2）。两者仅是术语重名，功能归属在 M6，与 TextUnit 字段无关。

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

### 4.4 `image_path` —— 图片独立块的溯源路径（v2.1 新增）

图片块（`block_type=drawing`）在 M2 **独立成块**，不与相邻正文合并（描述语义自足）。取值链路：

```
MinerU content_list[].img_path  →  M1 blocks.jsonl `img_path`（parse.md §3）
                                →  M2 图片独立块 `image_path`
```

- 路径相对 `<doc_dir>`（如 `images/371ceffd….jpg`），M6/M7 据此拼图片 URL 供前端预览；
- `content` = 该图的视觉描述文本（M1 调视觉模型生成，见 [MULTIMODAL.md](../MULTIMODAL.md) §4.2）；
- **降级**：视觉关闭 / 调用失败 / 模型自评「无有效信息」⇒ content 为空 ⇒ M2 照旧过滤掉，不产生空块（与 v2 行为逐字节一致）。

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
- **2026-09-29 · v2.1（增量，多模态）**：新增 `image_path`（§4.4）——图片独立块的原图相对路径，来源 M1 blocks.jsonl `img_path`。纯增量、不改既有字段语义；schema `$id` 不变（`additionalProperties: true` 本就放行，此次显式声明）。见 [`MULTIMODAL.md`](../MULTIMODAL.md)。
- **2026-10-24 · v2.1 字段核对修正**：字段表补 `html`（表格双表示预览字段，代码 v1.2 起产出、契约漏列，schema 同步补声明）；表与 schema 补充字段含义列；`sidecar` **移出字段表**（非 M2 产出字段），表后注明「同名不同物」——schema 放行声明（LightRAG 上游可选属性）vs **M6 自研 `Sidecar` 溯源服务**（v5.25/v5.28.1）。不改既有字段语义、`$id` 不变。