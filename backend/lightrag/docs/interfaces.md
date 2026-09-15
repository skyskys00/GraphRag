# LightRAG v1.5.7 模块间接口规范与数据模型拆解

> 面向 GraphRAG 重做项目 M0–M2（TextUnit 契约 / 解析适配 / 切分适配）。
> 源码位置：`lightrag/source/lightrag/`，版本 `1.5.7`（`_version.py`）。
> 全部行号基于当前源码。`chunk_schema.py` 实际为 384 行（非 383），核心内容在 41–373 行。
> 补充问答（TextUnit 契约够用与否、PDF 页码、标题路径、对齐建议白话版）：见 [qa-notes.md](qa-notes.md#E)。

---

## 1. 数据模型定义

LightRAG **没有** pydantic 的 `Document` / `Chunk` 类。除少量 `@dataclass` / `TypedDict` 外，数据模型都是 **dict**，通过“键名 + 写入点”约定成契，注释反而最密（docstring 即 schema）。

### 1.1 Document —— full_docs 记录（KV 存储，`full_docs`）

入队时在 `apipeline_enqueue_documents` 内构建（`pipeline.py:1040-1066`，持久化 `pipeline.py:1460-1484`）：

| 字段 | 类型 | 说明 | 来源 |
|---|---|---|---|
| `content` | str | 正文。RAW=原文；`pending_parse` 时为空串、parse 后由解析器写入；合成格式带 `{{LRdoc}}` 前缀（`utils_pipeline.py:1120 make_lightrag_doc_content`），切片时被 `strip_lightrag_doc_prefix` 剥掉 | enqueue / parse |
| `file_path` | str | 规范化 basename（`normalize_document_file_path` 剥 `[hint]` 段，`utils_pipeline.py:248`）；无来源为 `"unknown_source"` | enqueue |
| `parse_format` | str | `"raw"` 或 `"pending_parse"`（`FULL_DOCS_FORMAT_RAW/PENDING_PARSE`） | enqueue |
| `content_hash` | str | 去重用内容哈希（RAW 时算，`utils_pipeline.py:947`） | enqueue |
| `parse_engine` | str | 已用/目标解析引擎，可带 hint 参数如 `mineru(page_range=1-3)`（`parser/routing.py` 的 `decode/encode_parse_engine`） | enqueue |
| `process_options` | str | 处理选项串，一次字符一开关：`i`/`t`/`e`（多模态）、`!`（skip_kg）、`F`/`R`/`V`/`P`/`C`（切分策略选择器） | enqueue |
| `chunk_options` | dict | **切分参数快照**（enqueue 时冻结，`_chunk_options_at` `pipeline.py:936-984`，经 `default_chunker_config`/`slim_chunk_options`） | enqueue |
| `source_file` | str | `pending_parse` 上传原始文件名（可选） | enqueue |

> 关键语义：`file_path` 是展示/去重/确定性 doc_id 种子，**不是**磁盘定位（磁盘定位靠 `source_file` + resolver）。

### 1.2 文档状态记录 —— doc_status（`DocProcessingStatus`，`pipeline.py:1094-1131` 初始形状）

状态机：`PENDING → PARSING → ANALYZING → PROCESSING → PROCESSED`，任一步失败 → `FAILED`。

字段：`status / content_summary / content_length / created_at / updated_at / file_path / track_id / content_hash`，后续阶段追加 `chunks_count / chunks_list`，以及 `metadata` 音轨：
- `metadata.kg_write_state`：写前恢复锚（`pre_graph` → `graph_mutation_started`，单调），关停断言用（`lightrag.py`/`AGENTS.md` Purge recovery contract）。
- `metadata.parse_*`（`parse_start/end_time / parse_format / parse_engine / parse_warnings / parse_stage_skipped`）、`metadata.chunk_method / chunk_opts`（实际使用的切分策略与参数串）、`metadata.process_start/end_time`。

### 1.3 解析器输出 —— ParseResult（`parser/base.py:113-152`）

```
doc_id, file_path, parse_format, content, blocks_path
parse_engine? , parse_stage_skipped? , parse_warnings? , smartheading_llm_cache_ids?
```

`blocks_path` 指向 HTML/格栅侧的 `<base>.blocks.jsonl`（结构化块侧车文件，见 §2.3）。无页边距结构时为空串。

### 1.4 Chunk —— 切分器输出与落库记录

**4 层契约并存**（这是最重要的接口事实）：

1. **切分器最小输出**（`chunker/token_size.py:96-111 _make_chunk`、`recursive_character.py:539-546`）：
   ```json
   {"tokens": int, "content": str, "chunk_order_index": int,
    "_source_span": {"start": int, "end": int}}   // 私有回溯，仅在 F/R 显式开启 _emit_source_span 时出现
   ```
2. **TypedDict 核心契约** `TextChunkSchema`（`base.py:79-83`）：`tokens / content / full_doc_id / chunk_order_index` —— `extract_entities` 的入参类型（`operate.py:3942`）。
3. **P 策略输出**（`chunker/paragraph_semantic.py:2371-2382`）：在最小输出基础上加
   ```json
   {"heading": {"level": int, "heading": str, "parent_headings": [str]},
    "sidecar": {"type": "block", "id": blockid, "refs": [{"type":"block","id":...}]}}
   ```
4. **落库记录**（`utils_pipeline.py:144-201 build_chunks_dict_from_chunking_result`）：生成稳定 `chunk_key`（`<doc_id>-chunk-<NNN>`，有 `chunk_id` 时用 `chunk_id`，碰撞回退 hash），并把 `_source_span` 剔除后写：
   ```json
   { ..., "full_doc_id": doc_id, "file_path": file_path,
     "llm_cache_list": [str], "heading"? , "sidecar"? }
   ```

多模态 chunk（`pipeline.py:7760-7780 _build_mm_chunks_from_sidecars`）：`chunk_id: "<doc_id>-mm-<kind>-NNN"`，`sidecar.type ∈ {drawing, table, equation}`，content 为 VLM 生成的结构化描述（`[Image Name]…` 等）。

**字段归属差异（重要）**：`chunks_vdb`（向量）构造时 `meta_fields={"full_doc_id","content","file_path"}`（`lightrag.py:1495`）——**heading / sidecar 不保证进向量存储**；全量字段只保证进 `text_chunks`（KV，`lightrag.py:1495` 附近同源写入）。查询期的标题回溯依赖 `text_chunks` 反查（`_attach_content_headings`，`operate.py:5539`）。

### 1.5 块级结构化文件 —— `*.blocks.jsonl`（sidecar/writer.py 输出）

首行为 meta 头（含 `document_name/format/…`），之后每行一个块（`writer.py:271-289`）：

```json
{"type": "content", "blockid": "<md5(doc_id:block_index:heading:content)>",
 "format": "plain_text", "content": "<渲染文本，含 <table>/<drawing>/<equation> 占位符>",
 "heading": str, "parent_headings": [str], "level": int,
 "session_type": "body", "table_slice": "none",
 "positions": [{"type": "paraid|bbox|heading|absolute", "anchor": ?, "range": [?], "charspan": [int,int], "origin":?}],
 "table_header"? , "is_title_block"?}
```

- `positions` 是本格式的“定位层”：**docx 用 `type="paraid"`（`w:paraId`），PDF（MinerU/Docling）用 `type="bbox"`，其 `anchor` 即页码 `page_idx`**。这也直接回答了“页码问题”——见 §5。
- 对应 IR 实为 `sidecar/ir.py:171-192 IRBlock`（`content_template/heading/level/parent_headings/session_type/table_slice/table_header/is_title_block/positions/tables/drawings/equations`）。

### 1.6 抽取中间结果与合并落库

**抽取**（`extract_entities` `operate.py:3941`，输出 `chunk_results: list[(maybe_nodes, maybe_edges)]`）：
- node：`{entity_name, entity_type, description, source_id(=`<chunk_key>`), file_path, timestamp}`（`operate.py:1006-1012`）
- edge：`{src_id, tgt_id, weight, description, keywords, source_id, file_path, timestamp}`（`operate.py:1080-1092`）

**合并**（`merge_nodes_and_edges` `operate.py:3513`，Phase0 写前锚 → Phase1 实体 → Phase2 关系）写入 5 处：
- `full_entities[doc_id] = {"entity_names":[...], "count":N}`（`operate.py:3685`）
- `full_relations[doc_id] = {"relation_pairs":[[src,tgt]...], "count":N}`（`operate.py:3693`）
- `entity_chunks[entity_name]` / `relation_chunks[(src,tgt)]` = 跟踪各行，权威 chunk 列表（`{chunk_ids:[...], count:N}`）
- 图：`chunk_entity_relation_graph` 节点/边（networkx/neo4j/…，node 为 node_data + `chunk_ids` 等）
- 向量：`entities_vdb`/`relationships_vdb` 记录（meta_fields 见 `lightrag.py:1483/1489`，embedding 对 `content`）。

> 与 TextUnit 的“实体引用”对应的就是 `entity_chunks` / `relation_chunks` 两个 KV —— 反向索引在实体侧，chunk 本身**不写回**实体引用列表。

---

## 2. 各环节输入输出规范（形状清单）

### 2.1 查询参数 —— QueryParam（`base.py:89-189`）

`mode ∈ {local, global, hybrid, naive, mix, bypass}`（默认 `mix`）；关键字段：`top_k(默认40) / chunk_top_k(20) / max_entity_tokens(6000) / max_relation_tokens(8000) / max_total_tokens(30000) / enable_rerank / only_need_context / only_need_prompt / user_prompt / stream / conversation_history`。

### 2.2 检索回收 —— 4 阶段上下文（`_build_query_context operate.py:5895`）

| 阶段 | 函数 | 输入 → 输出 |
|---|---|---|
| 1 搜索 | `_perform_kg_search`（5149） | query+kw → `{final_entities[], final_relations[], vector_chunks[], chunk_tracking{}, query_embedding}` |
| 2 截断 | `_apply_token_truncation`（5370） | 上表 → `{entities_context[], relations_context[], filtered_entities[], filtered_relations[], id 映射}` |
| 3 合并 | `_merge_all_chunks`（5585） | 过滤后实体/关系 + `vector_chunks` → `merged_chunks[{content,file_path,chunk_id}]`（round-robin 去重；`enable_content_headings` 时附加 `content_headings`） |
| 4 组装 | `_build_context_str`（5693） | merged_chunks + 实体/关系上下文 → `context: str`（kg_query_context 模板渲染）+ `raw_data` |

`chunk_tracking`（`{chunk_id: {source: C/E/R, frequency, order}}`）只用于日志溯源。

### 2.3 渲染进 prompt 的三个模板（`prompt.py`）

- `rag_response`（`prompt.py:334`）：`{response_type}/{user_prompt}/{context_data}` 三插槽。
- `kg_query_context`（`prompt.py:442`）：`{entities_str}/{relations_str}/{text_chunks_str}/{reference_list_str}` —— `text_chunks_str` 是每行一个 JSON：`{"reference_id","content"(,"content_headings"?)}`（`render_chunks_context_text utils.py:7041`）。
- `entity_extraction_system_prompt`（`prompt.py:56`）：消费 `{tuple_delimiter}/{completion_delimiter}/{entity_types_guidance}/{examples}/{language}/{max_total_records}/{max_entity_records}`；可选 `---Section Context---` 块（由 `format_heading_context` + `_truncate_section_context` 生成，`operate.py:4101-4115`），把标题链 `h1 → h2 → h3` 作为背景注入抽取。

### 2.4 最终 LLM 调用（`kg_query` 终点 `operate.py:4611-4700`）

```
llm_func(query, system_prompt=rag_response.format(...), history_messages=conversation_history, enable_cot=True, stream=param.stream)
```
前有 `compute_args_hash(...)` + `handle_cache(...)`（`operate.py:4660-4730`）；非流式响应还会做一次 `replace()` 剥离系统提示的清洗。`raw_data` 结构 = `convert_to_user_format`（`utils.py:6851`）：
`{status,message,data:{entities[]:{entity_name,entity_type,description,source_id,file_path,created_at}, relationships[]{src_id,tgt_id,description,keywords,weight,source_id,file_path,created_at}, chunks[]{reference_id,content,file_path,chunk_id}, references[]{reference_id,file_path}}, metadata:{query_mode,keywords,processing_info}}`。

`QueryResult`（`base.py:1796`）：`content / response_iterator / raw_data / is_streaming / llm_generated`。

---

## 3. 主流程数据流转

### 3.1 insert 全流程

```
insert()/ainsert()  lightrag.py:1756/1794
  └─[SDK] ainsert 固定走 F 策略：resolve_chunk_options → apipeline_enqueue_documents → apipeline_process_enqueue_documents
apipeline_enqueue_documents  pipeline.py:664
  1. 规范化 file_paths / 广播 ids·engines·options / 校验计数
  2. _add_content（946-1066）：算 content_hash、doc_id（known_source→md5(file_path)；raw→md5(content)；否则 md5(file_path+track_id+idx)）
  3. 批内 + 库内去重（filename / content_hash），重复写 FAILED dup 记录
  4. full_docs.upsert → doc_status.upsert(PENDING, metadata.kg_write_state=pre_graph)
  5. 发布 pipeline_ingress 消息（doc_status 才是真源）
apipeline_process_enqueue_documents：
  └─ _parse_worker  pipeline.py:4173   PENDING→PARSING
      解析器（按文件扩展名+parse_engine 经 parser/registry 解析：native / mineru / docling / legacy）
      → ParseResult → full_docs 持久化解析正文（合成格式带 {{LRdoc}} + blocks_path 侧车文件）
      正文从队列载荷剔除，只留轻量 dict 入 q_analyze
  └─ _analyze_worker  pipeline.py:4569   PARSING→ANALYZING
      多模态侧车（drawings/tables/equations.json）+ VLM 分析，结果回写侧车
  └─ _process_worker → process_single_document  pipeline.py:4755   ANALYZING→PROCESSING→PROCESSED
      a. 重读 full_docs 正文（strip_lightrag_doc_prefix）
      b. 解析 process_options.chunking 选择切分策略（pipeline.py:4933-5330：C/F/R/V/P/legacy）
      c. chunking_result = [{tokens, content, chunk_order_index, _source_span?}]
      d. _build_mm_chunks_from_sidecars（多模态 chunk 附加，序号续接）
      e. enforce_chunk_token_limit_before_embedding（embedding 前硬切超长块）
      f. backfill_chunk_sidecars（sidecar/backfill.py）：_source_span → blocks.jsonl 块级 provenance → 附加 sidecar blockid
      g. build_chunks_dict_from_chunking_result（加 full_doc_id/file_path/llm_cache_list，chunk_key）
      h. PROCESSING + chunks_vdb.upsert + text_chunks.upsert（并行）
      i. _process_extract_entities → extract_entities（operate.py:3941）→ chunk_results
      j. merge_nodes_and_edges（operate.py:3512，Phase0 锚 → 实体 → 关系）
      k. _insert_done() flush 全部派生存储 → PROCESSED 写入
```

**形状演化**：`文件/原文(str)` →(解析)→ `full_docs.content(str) + blocks.jsonl` →(切分)→ `[{tokens,content,chunk_order_index,_source_span}]` →(+heading/sidecar 回填 +full_doc_id/file_path)→ `{chunk_key: 全字段记录}`（并写 text_chunks + chunks_vdb）→(抽取)→ `[(maybe_nodes,maybe_edges)]` →(合并)→ `图 + full_entities/full_relations + entity_chunks/relation_chunks + entities_vdb/relationships_vdb`。

要点：**content 字符串是唯一载体**贯穿切分，`_source_span`(字符偏移) 是切分与块级结构之间的唯一对账凭据；heading 只由 P 策略自带或 F/R/V 靠 backfill 回填。

### 3.2 query 全流程

```
query()/aquery()  lightrag.py:3916/3940 → aquery_llm（4211）
mode∈{local,global,hybrid,mix} → kg_query（operate.py:4588）
  ├─ get_keywords_from_query（LLM 或 hl/ll 关键词，operate.py:4844）
  ├─ _build_query_context（4 阶段，见 §2.2）
  ├─ only_need_context → 直接返回 context 字符串
  ├─ only_need_prompt → 返回 system+query 拼装
  └─ 否则：sys_prompt = rag_response.format(...) → llm_func(query, system_prompt, history, enable_cot, stream)
mode=naive → naive_query（operate.py:6591）：直接 chunks_vdb.query → 同样进 process_chunks_unified / 模板渲染
mode=bypass → 不经检索直接 llm_func(query)
最终返回 QueryResult{content|iterator, raw_data}
```

`naive_query` 用 `naive_rag_response` 模板（`prompt.py:388`，插槽`{content_data}`）；`kg_query` 用 `rag_response` + `kg_query_context`。

---

## 4. chunker/ 四种切分与默认

| 标识 | 实现 | 文件:行号 | 算法 | 默认参数 |
|---|---|---|---|---|
| **F**（默认） | `chunking_by_token_size` / `chunking_by_fixed_token` | `chunker/token_size.py:133/278` | 定长 token 滑动窗口，可选 `split_by_character` 预切分 | `chunk_token_size=1200`、`chunk_overlap_token_size=100` |
| R | `chunking_by_recursive_character` | `chunker/recursive_character.py:436` | 镜像 LangChain `RecursiveCharacterTextSplitter`（分隔符级联；默认级联含 CJK 句末标点，`constants.DEFAULT_R_SEPARATORS`），需 `langchain-text-splitters` | 1200 / 100 |
| V | `chunking_by_semantic_vector` | `chunker/semantic_vector.py`（async） | 包 LangChain `SemanticChunker`：句级 embedding 相似度断点 | 1200（CHUNK_V_SIZE） |
| P | `chunking_by_paragraph_semantic` | `chunker/paragraph_semantic.py:2048` | 消费 `blocks.jsonl`：heading 驱动初始分块、表格行级拆分（TableRowSplit）、长块锚拆（AnchorSplit）、自底向上标题层级合并（LevelMerge）。输出带 `heading` + `sidecar(blockid)`；无侧车时**回退 R** | `chunk_token_size=2000`（`constants.DEFAULT_CHUNK_P_SIZE`） |

**默认选择规则**（`pipeline.py:4933-5050`）：
- **默认 F**。`ainsert` 不传 `process_options`，走 legacy pattern → `self.chunking_func`（内置默认就是 `chunking_by_token_size`，固定用 F）。
- 文件管线在 `process_options` 显式含 `F/R/V/P/C` 选择器时派发对应策略；`C` 调自定义回调（失败警告回退 F）。
- 参数优先级链：per-doc `chunk_options` 快照 > 策略专用 env（`CHUNK_F_SIZE`/`CHUNK_R_SIZE`/`CHUNK_P_SIZE`/`CHUNK_V_SIZE`…）> 构造字段 > 全局 env（`CHUNK_SIZE`/`CHUNK_OVERLAP_SIZE`）> 函数签名默认。
- 切分后统一硬截断：`embedding_token_limit` 之下超块再做一次 `enforce_chunk_token_limit_before_embedding`（`utils.py`，保留 heading 分层）。

---

## 5. 与我们的 TextUnit 契约逐字段对比

假定 TextUnit 契约：`text / doc_id / 页码 / 标题路径 / 块类型 / embedding / 实体引用`。

| 字段 | LightRAG 现成对位 | 现成度 | 差距与补法 |
|---|---|---|---|
| `text` | `chunk["content"]`（渲染文本，含 `<table>/<drawing>/<equation>` 占位符；多模态 chunk 为 VLM 描述文本） | ✅ | 几乎是原文；注意它是“解析后渲染文本”，非扫描原始字符序列——引用溯源需靠 `_source_span` 字符偏移或 `sidecar` blockid 回到 blocks.jsonl |
| `doc_id` | `chunk["full_doc_id"]`（doc_key）+ `Document["file_path"]` | ✅ | 现成。mapping：TextUnit.doc_id = full_doc_id；粒度之上再加 file_path |
| 页码 | **无统一字段**。PDF 引擎（MinerU/Docling）把 `page_idx + bbox → IRPosition(type="bbox", anchor=page)`（`parser/external/mineru/ir_builder.py:38`）；docx 无页码，用 `type="paraid"`（`w:paraId`）定位（`parser/docx/parse_document.py`、`sidecar/ir.py:40-41`） | ❌ | 页码只存在于 blocks.jsonl 的 `positions`，**不进 chunk 记录**。需 M1 在 sidecar 写入时归一化 `page_label`（PDF）+`paraid`（docx）进块行，M2 切分时聚合成 chunk 级 `page_range` |
| 标题路径 | `chunk["heading"] = {level, heading, parent_headings}`；格式化 `h1 → h2 → h3` 走 `format_heading_context`（`chunk_schema.py:172`） | ⚠️ | 已有等价物（三级结构 → breadcrumb，含 CJK 清洗与每级字符上限）；但无章节编号、无严格 path 树、F/R/V 需 backfill。可在 M2 将 `heading` 升级为结构化 `title_path` |
| 块类型 | 无统一 chunk 字段。散在：blocks.jsonl `session_type`（当前恒 `"body"`）、`table_slice`、`is_title_block`；sidecar `type ∈ {block, drawing, table, equation}`；P 内部 `table_chunk_role ∈ {none, head, first, body, middle, tail}`（不导出） | ⚠️ | 有素材但未归一。M1 在 IRBlock → blocks.jsonl 时定 `block_type`（heading/paragraph/table/drawing/equation/title）；M2 透传到 chunk |
| `embedding` | vector 记录对 `content` 进行 embedding；`EmbeddingFunc{embedding_dim, func, max_token_size, sends_dimensions, model_name, supports_asymmetric}`（`utils.py:633-690`） | ✅ | 现成。注意：换 embedding 模型必须清库；`context="document"/"query"` 参数可做不对称编码 |
| `实体引用` | 反向索引**在实体侧**：`entity_chunks[entity_name]`(=chunk_ids)（`operate.py:2490-2514` 等），chunk 记录不含 `entities[]` | ⚠️ | 两种补法：(a) 沿用 LightRAG 反查——从 chunk_key 在 entity_chunks/relation_chunks 中反查；(b) merge 完成后把 `entity_names` 写回 text_chunks 记录。建议 M0 先定 (a)，代价最低且与 purge 一致性约束兼容 |

**最重要的核实结论**：**LightRAG 的 Chunk 没有页码、没有显式块类型、没有标题路径字段**——三者都以“半成品”形态存在于 `blocks.jsonl + sidecar` 层，切分只把 `heading`/`sidecar(blockid)` 透传进 chunk 记录，页码与块类型在 chunks 落库前被丢弃。我们的 M0/M2 正是要在这一层做“结构化透传”，而无需改动抽取/查询主链路。

---

## 6. 对齐建议（按环节）

1. **M0 定义 TextUnit**：直接以 LightRAG 落库记录为基底扩展（`chunk_key / content / tokens / full_doc_id / file_path / heading / sidecar` 全复用），新增 `page_range`（PDF）/`anchor`（docx paraid）、`block_type`、`title_path`（替代 loose heading）、`entity_refs`（M2 后填）。保持 TextChunkSchema 语义上向后兼容，让 `extract_entities` 与查询侧零改动。
2. **M1（解析→侧车，最合理补点）**：页码/块类型信息在解析时最完整，就在 `sidecar/writer.py:271-289` 写 blocks.jsonl 时同时写 `page_label`、`block_type` 字段；IRBlock（`sidecar/ir.py:171`）加两个字段即可，不需要动切分器。PDF 的 `positions(type=bbox).anchor` → `page_label`（值化页码），docx 的 `type=paraid` → `anchor=paraid`。
3. **M2（切分→chunk）**：以 P 策略为 TextUnit 主干（heading 驱动 + 表格整体 + blockid 溯源，天然产出我们需要的语义块），扩展 `_new_block` / 输出组装（`paragraph_semantic.py:2360-2382`）把块级 `page_label/block_type/title_path` 聚合成 chunk 级字段；F/R/V 路径的 `backfill_chunk_sidecars`（`sidecar/backfill.py`）在 `_source_span` 对账后同样把 `page_range/block_type` 回填。**收口点是 `build_chunks_dict_from_chunking_result`（`utils_pipeline.py:144`）**——在这里保证新字段进 `text_chunks` 与 `chunks_vdb`（如需进向量，记得把新字段补进 `chunks_vdb` 的 `meta_fields`，`lightrag.py:1495`）。
4. **实体引用**：沿用 `entity_chunks`/`relation_chunks` 反查，M0 的 TextUnit 里 `entity_refs` 定义为“可由 chunk_key反查的派生视图”，不在 chunk 记录里冗余写回（与 purge 的 fail-closed 一致性约束天然兼容）。

---

## 7. 已知坑（移植/适配时必看）

1. **页码只是 bbox anchor，不是 span**：PDF 的块定位是 `positions[].range=[x0,y0,x1,y1]` + `anchor=页号`（MinerU，`ir_builder.py:38`）；要得到“字符跨度在哪些页”必须自己做 bbox→页内字符映射回算。docx 无页码是**格式本质**（没有这类值可算），只能走 paraid。
2. **`_source_span` 对账失败 = 整篇 FAILED**：F/R/V 的 block 回填做 byte 级精确匹配（`sidecar/backfill.py` span contract），不可定位即抛 `ChunkBlockMatchError` → doc FAILED；唯一豁免是载有 U+FFFD 的坏块（降级无 sidecar）。自定义切分函数（`C`）不参与回填。
3. **heading 不保证进向量库**：`chunks_vdb.meta_fields` 只保证 `{full_doc_id,content,file_path}`；查询侧标题靠 `text_chunks` 逐条 `get_by_ids` 反查（`_attach_content_headings`，`operate.py:5539`）。若 TextUnit 需要向量记录含标题，必须扩 meta_fields。
4. **切分参数冻结**：`chunk_options` 在 enqueue 时快照进 `full_docs[doc_id]`；之后改 `addon_params['chunker']` 只影响新入队文档。
5. **换 embedding 模型必须清数据目录**（另有 `KV` JSON 可保留），否则向量维度/空间不匹配静默劣化。
6. **`ainsert` 只走 F**；P/R/V/SDK 自定义切分必须走 `apipeline_enqueue_documents + apipeline_process_enqueue_documents` 且 process_options 显式含策略选择器。
7. **多模态 content 是"分析描述"不是原文**：`<drawing>/<table>/<equation>` 在正文中为占位符，正文切分的 chunk 与多模态 chunk（VLM 产物）是两类记录（`chunk_id` 前缀 `-chunk-` vs `-mm-`）。做实体抽取/引证时注意区分。
8. **blocks.jsonl 首行是 meta**：按 `type=="content"` 过滤行（`sidecar/backfill.py:74-88`、`paragraph_semantic.py:193-209`），别把 meta 当正文；正文行拼 merged text 用 `"\n\n"` 连接（这个分隔符是 chunker 的输入契约，改动态。

---

## 8. 采用建议

> 建议：**TextUnit = LightRAG 落库 chunk 记录的字段超集**，且以 **P 策略切片 + blocks.jsonl 侧车**作为 M1/M2 的主干链路。

具体分三步落地：
1. **M0 只定契约、不改源码内核**：在 lightrag 之外定义 TextUnit schema（复用 `content/full_doc_id/heading/sidecar`，新增 `page_range/block_type/title_path/entity_refs`），并明确 `entity_refs` 为 `entity_chunks/relation_chunks` 反查的派生视图。
2. **M1 在 `sidecar/writer.py:271` 一行内扩展**：写 blocks.jsonl 时带上 `page_label` 与 `block_type`（PDF 从 `positions.bbox.anchor` 取页号、docx 记 `paraid`）——这是信息最完整、改动面最小（一个写点、无切分器改动）的补法。
3. **M2 以 P 策略为切分主干**：把块级 `page_label/block_type/title_path` 聚合进 chunk 输出，并给 F/R/V 路径的 `backfill_chunk_sidecars` 补同样的透传；统一在 `build_chunks_dict_from_chunking_result` 收口，必要时扩 `chunks_vdb.meta_fields`。抽取（`extract_entities`）、合并（`merge_nodes_and_edges`）、查询（4 阶段上下文）主链路全可不改。

这样 M0–M2 的改动都落在“Block/Chunk 结构化层”，不侵入 LightRAG 的 KG 建图与检索核心，后续换数据源/换解析器也只需保证侧车产出同一份 `blocks.jsonl + 扩展字段`。