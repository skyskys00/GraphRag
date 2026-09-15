# LightRAG v1.5.7 图构建与实体抽取拆解

> 拆解对象：`lightrag/source/lightrag/`（下文中所有「相对路径」均指该目录）。
> 面向：GraphRAG 重做项目 M3（图构建）。目标 = 复用主流程 + 改造中文实体抽取（`language=zh` / 自定义 prompt）。
> 版本基准：v1.5.7 源码 SNAPSHOT（`__post_init__` 里已废弃 `ENTITY_TYPES` 环境变量，走 prompt 模板/profile）。
> 补充问答（`heading?/sidecar?` 可选字段含义）：见 [qa-notes.md](qa-notes.md#C)。

---

## 1. 定位与职责

| 模块 | 职责 |
|---|---|
| `prompt.py`（900 行） | 全库 prompt 模板的集中注册地（`PROMPTS` 字典）。实体抽取 system/user/continue prompt、实体类型引导文本、摘要合并 prompt、抽取结果示例、分隔符常量。另有 YAML profile 加载（`ENTITY_TYPE_PROMPT_FILE`）与 profile 合并逻辑。**不产生调用，只供 operate 消费** |
| `chunk_schema.py`（383 行） | chunk 标题层级规范化的副作用工具：`normalize_chunk_heading` / `format_heading_context`（生成 `---Section Context---` 面包屑）、`strip_internal_multimodal_markup_for_extraction`（剥离 `<cite refid>` / `<drawing id>` 等解析器内部标记，让抽取 LLM 看到干净文本）。只有抽取 prompt 用清洗后文本，存储的 chunk 内容从不改动 |
| `operate.py`（6889 行，核心） | 抽取编排（`extract_entities`）、抽取结果解析（文本分隔符 / JSON 两种）、图合并（`merge_nodes_and_edges` + `_merge_nodes_then_upsert` / `_merge_edges_then_upsert`）、描述 map-reduce 摘要（`_handle_entity_relation_summary` / `_summarize_descriptions`）、基于 LLM 缓存的重建（`rebuild_knowledge_from_chunks`）、多模态实体注入 |
| `lightrag.py`（6953 行） | `LightRAG` 主类：存储实例化（`initialize_storages`）、`_process_extract_entities` 薄壳、`_insert_done` 落盘、自定义 chunk 插入（`ainsert_custom_chunks`）、删除/清理（`adelete_by_doc_id` / `_purge_kg_contributions`）、`_build_global_config`（把抽取用到的运行时配置打包成 dict） |
| `pipeline.py`（7782 行） | 文档入库流水线：`apipeline_process_enqueue_documents` 内完成「切分 → 写 chunk → 抽取 → 合并 → 落盘 → PROCESSED」的编排（约 5400–5600 行） |
| `utils_graph.py`（2547 行） | 手工图操作（`acreate_entity` / `acreate_relation` / `aedit_*` / `amerge_entities` / `adelete_by_entity`）与关系 weight 校验，属于「编辑侧」，非抽取主链 |
| `utils.py`（7533 行） | 无状态工具：实体名/描述清洗（`sanitize_and_normalize_extracted_text` / `normalize_entity_name` / `normalize_extracted_info`，5371 行起）、`merge_source_ids`（6423）、`apply_source_ids_limit`（6484）、LLM 缓存读写（`handle_cache` 4145 / `use_llm_func_with_cache` 5155）、`compute_mdhash_id`（887） |
| `kg/` 写入侧 | 四类存储实例。**默认**（无 kv/vector/graph 后端时）：KV=JsonKVStorage、图=NetworkXStorage、向量=FaissVectorDBStorage、doc_status=JsonDocStatusStorage |

抽取输入侧（chunk）来源是 `chunker/`（`chunking_by_token_size` 等），在 pipeline 中经 `chunk_options` 调度（pipeline.py:4935–5160），产出 `TextChunkSchema` 字典喂给抽取。

---

## 2. 关键函数/路径索引

### 2.1 Prompt 模板（prompt.py）
| 项 | 位置 | 说明 |
|---|---|---|
| 默认分隔符 `DEFAULT_TUPLE_DELIMITER = "<\|#\|>"`、`DEFAULT_COMPLETION_DELIMITER = "<\|COMPLETE\|>"` | prompt.py:14-15 | 文本模式的行内字段分隔符 / 完成标记 |
| `default_entity_types_guidance` | prompt.py:20-34 | 默认实体类型税单：Person/Creature/Organization/Location/Event/Concept/Method/Content/Data/Artifact/NaturalObject，**全英文** |
| `entity_extraction_system_prompt` | prompt.py:56-125 | 文本模式 system prompt，占位符：`{tuple_delimiter}` `{max_total_records}` `{max_entity_records}` `{completion_delimiter}` `{entity_types_guidance}` `{examples}` `{language}` |
| `entity_extraction_user_prompt` | prompt.py:127-143 | 文本模式 user prompt，占位符还有 `{heading_context_block}` `{input_text}`；第 135 行 `Ensure the output language is {language}` |
| `entity_continue_extraction_user_prompt` | prompt.py:145-161 | gleaning（补充抽取）prompt |
| `entity_extraction_section_context` | prompt.py:51-54 | 章节面包屑包裹块，占位符 `{heading_path}`；**文本模式用户可自定义 prompt 覆盖** |
| JSON 系列（system/user/continue/examples） | prompt.py:175-295 | `entity_extraction_json_*`，字段为 `name/type/description`、`source/target/keywords/description` |
| `summarize_entity_descriptions` | prompt.py:297-328 | 描述合并 map-reduce 的 LLM prompt，占位符 `{description_type}` `{description_name}` `{description_list}` `{summary_length}` `{language}` |
| profile 解析（YAML） | prompt.py:526-900 | `get_default_entity_extraction_prompt_profile`（532）、`resolve_entity_type_prompt_path`（630）、`load_entity_extraction_prompt_profile`（752）、`resolve_entity_extraction_prompt_profile`（811）。环境变量 `PROMPT_DIR` + `ENTITY_TYPE_PROMPT_FILE` 指定自定义 YAML，字段 `entity_types_guidance` / `entity_extraction_examples` / `entity_extraction_json_examples` |

### 2.2 抽取与合并（operate.py）
| 函数 | 位置 | 说明 |
|---|---|---|
| `extract_entities` | operate.py:3941-4543 | 抽取编排：按 chunk 并发（信号量=llm_model_max_async，4443）、逐 chunk 调 LLM（首次 + gleaning）、两个模式分派 |
| `_process_single_content` | operate.py:4083-4417 | 单个 chunk 的完整抽取体：清洗 content、拼 section context、拼 user prompt、LLM 调用、JSON/text 解析、gleaning 合并、多模态实体注入 |
| `_process_extraction_result` | operate.py:1507-1645 | **文本模式**响应解析：按 `\n`/completion delimiter 切记录 → 容错修复格式 → 每行按 `tuple_delimiter` 切字段 → 实体/关系校验 |
| `_process_json_extraction_result` | operate.py:906-1098 | **JSON 模式**响应解析：`tolerant_load_json_dict` 容错 → 实体/关系数组 → 同样清洗路径 |
| `_handle_single_entity_extraction` | operate.py:709-770 | 单实体行校验（4 字段）+ 清洗 + 产出 `entity_name/entity_type/description/source_id/chunk_key/file_path/timestamp` |
| `_handle_single_relationship_extraction` | operate.py:773-850 | 单关系行校验（5 字段）+ 清洗 + **weight 固定 1.0** + 产出 `src_id/tgt_id/weight/description/keywords/source_id/file_path/timestamp` |
| `_normalize_text_extraction_record_attributes` | operate.py:853-872 | 修复「关系行误用 entity 前缀」的已知 LLM 输出错误 |
| `merge_nodes_and_edges` | operate.py:3513-3940 | 图合并编排（Phase 0 write-ahead 锚点 → Phase 1 实体并发 → Phase 2 关系并发） |
| `_merge_nodes_then_upsert` | operate.py:2428-2779 | 单实体合并：先读图旧节点 → 并 source_id（去重）→ `apply_source_ids_limit` → 去重描述 → LLM 摘要 → upsert_node + 实体 VDB |
| `_merge_edges_then_upsert` | operate.py:2781-3473 | 单关系合并 + **缺端实体补建**（3158-3369）+ upsert_edge + 关系 VDB（写入前 delete 正反两个旧向量，3444-3465） |
| `_handle_entity_relation_summary` | operate.py:372-530 | 描述列表 map-reduce 摘要策略（含 token 预算判定） |
| `_summarize_descriptions` | operate.py:532-650 | 单次 LLM 摘要调用（用 `PROMPTS["summarize_entity_descriptions"]`），cache_type=`summary` |
| `collect_kg_merge_candidates` | operate.py:3476 | 从 chunk_results 收集候选实体/关系集合（write-ahead 锚点用） |
| `rebuild_knowledge_from_chunks` | operate.py:1101-1413 | 删除后的「选择性重建」：从 LLM 缓存重放存活 chunk 的抽取结果，**不重新调 LLM** |
| `_get_cached_extraction_results` | operate.py:1414-1505 | 取 chunk 的缓存抽取结果（靠 text_chunks 的 `llm_cache_list` 反向索引） |
| `_rebuild_from_extraction_result` | operate.py:1648 | 缓存重放时的结果解析（JSON 优先，容错回退文本） |
| `_truncate_vdb_content` | operate.py:297-346 | 实体/关系写入 VDB 前的内容 token 截断（对齐 embedding 限制） |

### 2.3 编排壳（lightrag.py / pipeline.py）
| 函数 | 位置 | 说明 |
|---|---|---|
| `LightRAG.initialize_storages` | lightrag.py:1431-1500 | 按 `NameSpace`（namespace.py:7-22）建 11 个存储实例：`full_docs` `text_chunks` `llm_response_cache` `full_entities` `full_relations` `entity_chunks` `relation_chunks` + `chunk_entity_relation` 图 + `entities` `relationships` `chunks` 三个向量库 + `doc_status` |
| `_build_global_config` | lightrag.py:1200-1235 | 把 LightRAG 全部配置打包 dict 下传，注入 `tokenizer` `addon_params` `role_llm_funcs` `llm_cache_identities` |
| `_refresh_addon_params_cache` | lightrag.py:1171-1189 | `language` → `_resolved_summary_language`；解析 entity extraction prompt profile |
| `_process_extract_entities` | lightrag.py:3243-3272 | 调 `extract_entities` 的薄壳（错误时写 pipeline_status） |
| `_insert_done` | lightrag.py（`_flush_storages([...])` 系列） | 落盘：flush graph/向量/KV 缓冲 |
| `_purge_kg_contributions` | lightrag.py:5010-5290 | 选择性删除/重建原语（详见 §6） |
| `adelete_by_doc_id` | lightrag.py:5718 | 文档删除入口（委托 `_purge_kg_contributions`） |
| 流水线切分+抽取编排 | pipeline.py:4900-5508 | `chunk_options` 调度 chunker → Stage1 写 chunk → Stage2 `_process_extract_entities` → Stage3 `merge_nodes_and_edges` |

---

## 3. 核心机制与数据流（文档 → 图 全链）

### 3.1 链路总览

```
文档内容(parsed)
  → chunker（pipeline.py:4935-5160, chunker/*.py）
  → chunks: dict[chunk_id, TextChunkSchema{content, tokens, full_doc_id, chunk_order_index, file_path, heading?, sidecar?}]
      ↓ pipeline.py:5403-5412（chunks_vdb+text_chunks upsert, doc_status PROCESSING）
      ↓ _process_extract_entities → extract_entities（operate.py:3941）
  → chunk_results: list[(maybe_nodes, maybe_edges)]
      ↓ merge_nodes_and_edges（operate.py:3513）
  → Graph + entities_vdb + relationships_vdb + entity_chunks/relation_chunks + full_entities/full_relations 锚点
      ↓ _insert_done() 落盘 → doc_status PROCESSED
```

### 3.2 抽取阶段细节（per chunk）

1. **输入**：`(chunk_id, chunk_dp)`。`content = strip_internal_multimodal_markup_for_extraction(chunk_dp["content"])`（operate.py:4097）；`file_path` 取 chunk 字段或 `"unknown_source"`。
2. **可选章节上下文**：`format_heading_context(chunk_dp)`（chunk_schema.py:172）产出 `h1 → h2 → h3` 面包屑 → `_truncate_section_context`（operate.py:249）按 `MAX_SECTION_CONTEXT_TOKENS`（默认 256，constants.py:43）预算化 → 有标题才注入 `---Section Context---` 块，**无标题时 user prompt 与无上下文版逐字节一致**（operate.py:4109-4120）。
3. **拼 prompt**：文本模式 `entity_extraction_system_prompt` 全部 chunk 复用（先 format 一次），user prompt 逐 chunk format 注入 `{input_text}` `{heading_context_block}`（operate.py:4070-4078, 4168-4179）。`language`、`entity_types_guidance`、`max_total_records`、`max_entity_records` 都在 `context_base` 里（operate.py:4035-4043）。
4. **调 LLM**：`use_llm_func_with_cache(user_prompt, system_prompt=..., cache_type="extract", chunk_id=chunk_key, response_format={"type":"json_object"} if JSON 模式)`（operate.py:4181-4191）。role 是 `extract`（`global_config["role_llm_funcs"]["extract"]`）。JSON 模式额外要求 provider 支持 `response_format`。
5. **gleaning（补充抽取）**：`entity_extract_max_gleaning`（env `MAX_GLEANING`，默认 1）>0 时，把上一轮 user/assistant 作为 history 再发一次 continue prompt（operate.py:4255-4269）。结果按「描述更长者胜」合并（operate.py:4291-4328）。有 `MAX_EXTRACT_INPUT_TOKENS` 预检（operate.py:4220-4253）。
6. **解析**：
   - 文本：`_process_extraction_result`（operate.py:1507）→ 按 `\n`/`<|COMPLETE|>` 拆记录 → `fix_tuple_delimiter_corruption` 容错 → 4/5 字段校验 → `_handle_single_entity_extraction` / `_handle_single_relationship_extraction`。
   - JSON：`_process_json_extraction_result`（operate.py:906）→ `tolerant_load_json_dict` 容错解析。
   - 产出结构均为 `maybe_nodes: dict[entity_name, list[记录]]`、`maybe_edges: dict[(src,tgt), list[记录]]`（元组键无序，`_process_extraction_result` 直接以 (source,target) 为键；合并前再排成有序键，operate.py:3651-3653）。
7. **多模态实体注入**：sidecar 为 drawing/table/equation 的 chunk，自动补实体（实体名=sidecar id，type=侧栏类型，description=完整 chunk content）并连到该 chunk 其它实体（operate.py:4337-4398）。
8. **chunk 缓存索引**：把本次 `cache_type="extract"` 的 cache key 追加到 chunk 的 `llm_cache_list` 字段（`update_chunk_cache_list`，utils.py:4833；调用点 operate.py:4400-4407）——**这是后面选择性重建的关键反向索引**。

### 3.3 合并阶段细节

`merge_nodes_and_edges`（operate.py:3513）分三阶段，全部先写后读锁保证并发安全（`get_storage_keyed_lock`）：

- **Phase 0（write-ahead 锚点）**：写入 `full_entities[doc_id] = {entity_names, count}`、`full_relations[doc_id] = {relation_pairs, count}` 并 flush，**图变更前**先落盘（operate.py:3665-3711）。锚点=该文档贡献的候选全景，是删除/重建 fail-closed 的恢复依据。
- **Phase 1 实体**：`_merge_nodes_then_upsert`（operate.py:2428）。同名实体 = 同一节点，聚合流程：
  1. 读图旧节点，拆分 `source_id`（`GRAPH_FIELD_SEP="<SEP>"` 连接，constants.py:49）。
  2. `entity_chunks[entity_name]`（**全量 chunk 追踪行**）存在则以其为准；否则退化为图 `source_id` 视图（operate.py:2487-2508）。
  3. `merge_source_ids` 并集去重 → `apply_source_ids_limit` 限流（`max_source_ids_per_entity` 默认 200，KEEP 模式丢尾部超量；FIFO 丢头部，constants.py:71-82）。
  4. 描述按 (timestamp, -len) 排序、跨文档去重（`_combine_descriptions_dedup`）→ 描述过多时 LLM map-reduce 摘要 → `entity_type` 取出现次数最多者（operate.py:2576-2582）。
  5. `file_path` 限制 75 个（`max_file_paths`）并打 `"...truncated..."` 占位。
  6. 写入图 `upsert_node`：`{entity_id, entity_type, description, source_id, file_path, created_at, truncate}`（operate.py:2727-2762）。
  7. 实体 VDB：`entities_vdb.upsert({ "ent-"+md5(entity_name): {entity_name, entity_type, content, source_id, file_path} })`，**content = `f"{entity_name}\n{description}"`**，先经 `_truncate_vdb_content` 截断（operate.py:2742-2757）。
- **Phase 2 关系**：`_merge_edges_then_upsert`（operate.py:2781）。
  1. 读图旧边，weight 合并：`weight = Σ(新 source 的去重 weight 1.0) + 已存 weight`，并抬升到「去重 source_id 计数」保底（operate.py:2955-2999，见 Relation weight contract）。
  2. keywords 并集、描述摘要同实体。
  3. `relation_chunks[make_relation_chunk_key(src,tgt)]` 追踪行同样处理。
  4. **缺端实体补建**：`src_id` / `tgt_id` 任一不存在 → 以 `entity_type="UNKNOWN"` 建节点（description 用边的描述）+ 实体 VDB（operate.py:3158-3367）。
  5. 关系 VDB：`content = f"{keywords}\t{vdb_src_id}\n{vdb_tgt_id}\n{description}"`，id= `"rel-"+md5(sorted(src,tgt))`（双向同 id，search 时正反都能命中），**写前先 delete 正反两 id 旧向量再 upsert**（operate.py:3386-3465）。
  6. 图写入 `upsert_edge(edge_key, {weight, description, keywords, source_id, file_path, created_at, truncate})`（operate.py:3411-3423）。

### 3.4 落盘

`_index_storages`（lightrag.py:3274-3293）在 `_insert_done` 被调用，flush：`full_docs/doc_status/text_chunks/full_entities/full_relations/entity_chunks/relation_chunks/llm_response_cache/entities_vdb/relationships_vdb/chunks_vdb/chunk_entity_relation_graph`。

默认存储物理形态（`working_dir/[workspace/]`）：
- KV：`kv_store_<namespace>.json`（json_kv_impl.py:152）。
- 图：`graph_chunk_entity_relation.graphml`（networkx_impl.py:262，GraphML 是 XML，非法控制字符会炸序列化 → 全链有 `sanitize_text_for_encoding`）。
- 向量：Faiss 实现 `upsert` 只缓存，**embedding 在 `index_done_callback` 时才批量算**（faiss_impl.py:389-410，「deferred-embedding」，见 class docstring）。承接 3.3 的 content，`self.embedding_func([...], context="document")`。

### 3.5 关键常量与 env

| 配置 | 默认/env | 用途/生效处 |
|---|---|---|
| `language` | `SUMMARY_LANGUAGE`→English（addon_params.py:48，lightrag.py:1172） | 注入抽取 & 摘要 prompt 的 `{language}`；**查询侧不依赖它** |
| `entity_extract_max_gleaning` | `MAX_GLEANING`=1（lightrag.py:470-472） | 补充抽取轮数，operate.py:4218 |
| `entity_extract_max_records` / `entity_extract_max_entities` | `MAX_EXTRACTION_RECORDS`=100 / `MAX_EXTRACTION_ENTITIES`=40（lightrag.py:475-487） | prompt 里的 `{max_total_records}` `{max_entity_records}`（operate.py:4010-4011） |
| `entity_extraction_use_json` | `ENTITY_EXTRACTION_USE_JSON`（lightrag.py:743） | 切 JSON 模式，operate.py:3989 |
| `force_llm_summary_on_merge` | `FORCE_LLM_SUMMARY_ON_MERGE`=8 | 描述片多于该值才考虑 LLM 摘要，operate.py:416 |
| `summary_context_size` / `summary_max_tokens` / `summary_length_recommended` | 12000 / 1200 / 600（constants.py:32-36） | map-reduce 预算 / 摘要输出上限 / 摘要推荐长度 |
| `source_ids_limit_method` | KEEP（constants.py:78） | 实体/关系 source_id 限流策略 |
| `max_source_ids_per_entity` / `..._relation` | 200（constants.py:71-72） | source_id 上限 |
| `temperature` | 1.0 | `extract` role LLM 调用 |
| `llm_model_max_async` | 4 | chunk 并发信号量（operate.py:4443） |

---

## 4. 五个必答问题的落点

### Q1 实体/关系抽取完整调用链

- **入口**：`pipeline.py:5426` `_process_extract_entities(chunks)` → `lightrag.py:3251` `extract_entities(...)`（operate.py:3941）。
- **输入**：`chunks: dict[chunk_id, TextChunkSchema]`（切分后落 pre-persist 的 chunk 集合），每 chunk 含 `content/tokens/full_doc_id/chunk_order_index/file_path`（可选 `heading`、`sidecar`）。
- **prompt**：见 §2.1。文本模式下 system+user 双 prompt；模板由 `prompt.py` 提供，`entity_types_guidance`/examples 可被 `addon_params` 或 `ENTITY_TYPE_PROMPT_FILE` 覆盖。
- **解析结构**：`maybe_nodes: dict[规范化实体名, list[{entity_name, entity_type, description, source_id(chunk_id), file_path, timestamp}]]`、`maybe_edges: dict[(src,tgt), list[{src_id, tgt_id, weight=1.0, description, keywords, source_id, file_path, timestamp}]]`。
- **写到哪**：
  - 图存储 `graph_chunk_entity_relation`（默认 `graph_chunk_entity_relation.graphml`）：`upsert_node` / `upsert_edge`。
  - 实体**文本**侧：`entity_chunks` KV（entity_name→chunk_ids 全量追踪）、`full_entities` KV（doc_id→候选实体名锚点）。
  - 关系文本侧：`relation_chunks` KV（`(src,tgt)`→chunk_ids）、`full_relations` KV。
  - **实体 embedding**：`entities` 向量库（default Faiss；`entities_vdb.upsert({id: {entity_name, entity_type, content="name\n描述", source_id, file_path}})`，content 在 flush 时由 `embedding_func` 向量化，id=`"ent-"+md5(entity_name)`）。
  - 关系 embedding：`relationships` 向量库，id=`"rel-"+md5(sorted(src+tgt))`。

### Q2 `language=zh` 的确切生效位置；只改它够不够

- **生效路径**：`SUMMARY_LANGUAGE` env / `addon_params["language"]` → `lightrag.py:1172` `_refresh_addon_params_cache` 写入 `_resolved_summary_language` → `_build_global_config`（lightrag.py:1200）带进 `global_config` → `extract_entities`（operate.py:3996）与 `_summarize_descriptions`（operate.py:571）读它 → 作为 `{language}` 填入：
  - `entity_extraction_system_prompt`（prompt.py:108「The entire output … must be written in `{language}`」）
  - `entity_extraction_user_prompt`（prompt.py:135）
  - `summarize_entity_descriptions`（prompt.py:316）
  - 查询侧 `keywords_extraction`（prompt.py:503）也会用 `language`（operate.py:4997-5029）。
- **回滚覆盖传参**：JSON 也用同一 `language` 变量（operate.py:4019）。
- **结论：想让实体名/描述/关系关键词输出中文，只设 `language=zh` 即可生效**（模板本身已带「输出语言 = {language}」指令）。**但**「实体类型」取值不受 language 完全约束：类型税单 `entity_types_guidance` 内建是英文（Person/Organization…），弱模型可能照抄英文类型。若要求类型也中文、或换一套中文税单（人物/组织/地点/事件/概念/方法…），需要**自定义 `addon_params["entity_types_guidance"]`**（字符串，拼进 prompt.py 的 `---Entity Types---` 段）；更彻底的改造是 `ENTITY_TYPE_PROMPT_FILE` YAML（profile 字段 `entity_types_guidance` + `entity_extraction_examples`，prompt.py:752-808）。**注意**：`entity_type` 值会落入图节点 `entity_type` 字段 + 实体 VDB，WebUI KG 视图按类型着色，类型体系变动会影响 UI 语义，需与前端颜色映射对齐。
- **再提醒**：`language` 只改 LLM 指令，不改 `chunker` 切分器、不改 embedding 模型。中文切分默认已 i18n 化（constants.py:99-137）。

### Q3 `MAX_ENTITY_TOKENS`、`ENABLE_LLM_CACHE` 生效位置

- **`MAX_ENTITY_TOKENS`（默认 6000）**：**是查询侧参数，与抽取/建图无关**。
  - 配置：`lightrag.py:437-438`（`max_entity_tokens: int = get_env_value("MAX_ENTITY_TOKENS", DEFAULT_MAX_ENTITY_TOKENS, int)`），属于 `QueryParam`。
  - 生效处：`_apply_token_truncation`（operate.py:5390-5477），在 `_perform_kg_search` 检索出实体后，对「实体上下文」按 token 截断（`atruncate_list_by_token_size(..., max_token_size=max_entity_tokens)`）再拼进 LLM 答案 prompt。 对应 `MAX_RELATION_TOKENS`（8000）。
  - 若想限制**抽取输入**，看的是 `MAX_EXTRACT_INPUT_TOKENS`（20480，operate.py:3981）——但它只作用于 gleaning 预算预检，不截断首次抽取 chunk 文本。
- **`ENABLE_LLM_CACHE`（默认 true，lightrag.py:791；抽取专用开关 `enable_llm_cache_for_entity_extract`，lightrag.py:794，API 层对应 env `ENABLE_LLM_CACHE_FOR_EXTRACT`，api/config.py:752-754）**：作用于 LLM 缓存读写。
  - 读侧：`handle_cache`（utils.py:4145-4177），`mode=="default"`（非查询模式）走 `enable_llm_cache_for_entity_extract`（utils.py:4164）；查询模式走 `enable_llm_cache`（utils.py:4161）。
  - 写侧：`use_llm_func_with_cache`（utils.py:5155-5352）。抽取调用所有 `cache_type="extract"`；**被截断（finish_reason=length）的抽取响应不落缓存**（utils.py:5299-5307）——这是重试/重建可重跑的关键保证。摘要调用 `cache_type="summary"` 也受同一开关。
  - 缓存存 `kv_store_llm_response_cache.json`，键 = `mode:cache_type:hash(prompt+system+history+response_format+llm_identity)`（utils.py:962-973；生成在 5242-5252）。
  - 取消缓存后 `use_llm_func_with_cache` 直接调 LLM（utils.py:5326）。

### Q4 增量更新与「选择性删除」

**图是跨文档共享的，LightRAG 没有增量 NER，删除走「锚点证明 + purge + 从缓存重建」原语**（issue #3400 fail-closed，详见 `graph.md` 前置 AGENTS、以及 `_purge_kg_contributions` lightrag.py:5010 / `adelete_by_doc_id` lightrag.py:5718）：

1. **删除整文档**：`adelete_by_doc_id`（Web/CLI 的 `/documents/{id}`）。核心 `_purge_kg_contributions`：
   - 候选集合来自 `full_entities[doc_id]` / `full_relations[doc_id]` 锚点（或显式候选）。
   - 对每个候选实体/关系，把「本文档 chunk_ids」与 `entity_chunks` / `relation_chunks` 追踪行 + 图 `source_id` 求交 → 分类为 **彻底删除**（无其它来源）或 **重建保留**（其它文档仍引用）。
   - 彻底删除：删图、删向量、删追踪行 → flush。
   - 重建保留：调 `rebuild_knowledge_from_chunks`（operate.py:1101）——用 `_get_cached_extraction_results` 重放存活 chunk 的 LLM 缓存抽取结果（**不发 LLM 请求**），重算并重写描述/关键词/weight。
   - 最后才删 chunk 本体（`chunks_vdb` + `text_chunks`），再删锚点行。**顺序硬保证：图对象绝不指向已删 chunk**。
2. **fail-closed**：无锚点证明（`RecoveryAnchorMissingError`→HTTP 409）宁可不动。`kg_write_state`（`pre_graph`/`graph_mutation_started`）从 enqueue 时打标，证明「该文档从未碰图」才允许只删 chunk 不碰图。
3. **结论**：删单文档 → 图同步是**现成路径**（不是整库重建），且对「多文档贡献同一实体」的场景是**精确到 chunk 的正向/反向调整**。增量插入天然支持（`merge_source_ids` 并集、weight 按去重 source 叠加、描述跨文档去重 + LLM 摘要收敛）。**代价**：依赖 LLM 缓存未清（清了缓存 → 重建会缺片段 → `best_effort` 策略降级保留旧值，`rollback` 策略结构重建 provenance）。

### Q5 实体合并/去重机制

- **同名即同节点**：实体以 `normalize_entity_name`（utils.py:5391 → `normalize_extracted_info`，全角转半角、去中英间空格、去引号）后的字符串为键合并。`maybe_nodes[entity_name]` 天然聚合同 chunk 重复；`merge_nodes_and_edges` 二次跨 chunk 聚合（operate.py:3647）。
- **描述去重**：`_combine_descriptions_dedup`（operate.py:2383）+ 排序（timestamp, -len desc）→ 多片描述交给 LLM 摘要成一个（`_handle_entity_relation_summary`）。上一版已存描述与新增做集合去重，防重跑累积（issue #3367）。
- **type 合并**：计数最高者胜（operate.py:2576）。
- **source_id 记录**：实体/关系字段 `source_id` 以 `GRAPH_FIELD_SEP("<SEP>")` 连接多个 chunk id，上限 200（`max_source_ids_*`），KEEP 保旧/FIFO 保新。**接入 3.3 的 `.tracking` 行** `entity_chunks`/`relation_chunks` 里的 `chunk_ids` 才是全量权威列表，图内 `source_id` 只是截断视图（`apply_source_ids_limit`，utils.py:6484）。
- **关系方向**：undirected。`maybe_edges` 键在合并前排成 `tuple(sorted(edge_key))`（operate.py:3651-3653），`_merge_edges_then_upsert` 起始 `src_id==tgt_id` 直接拒绝（operate.py:2807）。
- **额外去重**：图底层 NetworkX `get_edge` 有 weight/knowledge 两条记录机制，合并逻辑按 `already_edge` 读回。

---

## 5. 输入输出汇总

| 阶段 | 输入 | 输出 |
|---|---|---|
| chunker | 文档全文（`full_docs` content）+ 解析器产物 | `chunks: dict[chunk_id, TextChunkSchema]` |
| extract_entities | chunks + `global_config`（含 `role_llm_funcs.extract`、`_resolved_summary_language`、`_entity_extraction_prompt_profile`、分隔符/限额） | `chunk_results: list[(maybe_nodes, maybe_edges)]`；副作用：LLM 缓存 `extract` 条目 + chunk `llm_cache_list` 更新 |
| merge_nodes_and_edges | chunk_results + 图/向量/追踪/锚点存储 | 图节点&边、`ent-*`/`rel-*` VDB 缓存、`entity_chunks`/`relation_chunks` 行、`full_entities`/`full_relations` 锚点 |
| _insert_done | 上述所有存储 | 落盘（默认 `.graphml` + `kv_store_*.json` + Faiss dump） |
| purge/rebuild | doc_id + chunk_ids + 候选锚点（或显式候选） | 删除/重建后的图+VDB+追踪一致态 + `KGRebuildReport` |

---

## 6. 「内建 vs 自研」边界（M3 复用视野）

**内建（本阶段直接复用，勿重写）：**
- 图合并、source_id 去重/限流、描述摘要、weight 契约、fail-closed 删除/重建、embedding 写入编排 —— 这些与语言无关，`language=zh` 下照常工作。
- chunk→图管线编排（pipeline.py 5400-5510）、`_insert_done`、默认存储栈。

**内建但建议替换/覆盖（M3 重点改造区）：**
- **抽取 prompt 体系**：用 `addon_params["entity_types_guidance"]` 换中文类型税单；或 `ENTITY_TYPE_PROMPT_FILE` 整份换 YAML（改 examples 对齐中文效果）。**系统 prompt 不可从 addon_params 覆盖**（`resolve_entity_extraction_prompt_profile` 只覆盖 guidance + examples，prompt.py:811-862）；要换整条 system/user 模板得直接改 `PROMPTS` 字典注入（`PROMPTS["entity_extraction_system_prompt"] = "...zh..."`），或接受模板中语言指令。
- **实体类型体系**：中文化的类型集影响 type color（WebUI）与 `entity_type` 字段聚合（`_normalize_and_validate_entity_type` operate.py:661 有保留名防御 —— 不要用 `__proto__` 之类污染类型）。
- **抽取结果解析（`_process_extraction_result` / `_process_json_extraction_result`）**：若自研 LLM 输出 schema（如直接输出 JSON 三元组、带 span 偏移），这是稳定的替换点；注意保持 `maybe_nodes[key].append(record)` 的返回结构以兼容 merge。

**「结构化=LLM 抽取」的替代集成点**：`ainsert_custom_kg`（lightrag.py）与 `acreate_entity/acreate_relation/amerge_entities/aedit_entity（utils_graph.py 手写实体/边 API）支持非 LLM 来源直接建图，可作自研 NER 的落图口。

---

## 7. 已知坑（踩坑清单）

1. **忘记 `initialize_storages`**：`AttributeError: __aenter__` / `KeyError: 'history_messages'`。任何 LightRAG 实例必须 `await rag.initialize_storages()`。
2. **`ENTITY_TYPES` env 已废弃**：设了直接 `SystemExit`（lightrag.py:1258）。改走 addon_params / prompt profile。
3. **实体/关系名长度**：`DEFAULT_ENTITY_NAME_MAX_LENGTH=256` 字符 + `DEFAULT_ENTITY_NAME_MAX_BYTES=512` 字节（constants.py:18-23）；**Milvus 等按字节计上限**，CJK 名称易踩 VARCHAR max_length。`_truncate_entity_identifier` 硬截断（operate.py:209）。
4. **embedding 模型更换必须清库**：`ent-*`/`rel-*` 向量坐标空间变了会全错。切 embedding 时删数据目录（可留 `kv_store_llm_response_cache.json`）。
5. **GraphML 是 XML**：描述的非法控制字符（含 `\x1c`-`\x1f`）会让落盘崩溃。全链已有 `sanitize_text_for_encoding`（含 LLM 摘要路径 operate.py:635），**自研解析器必须过同一清洗**，否则绕过防御。
6. **LLM 缓存与重建的耦合**：清了 `llm_response_cache` 后做删除 → rebuild 拿不到缓存片段 → `best_effort` 保旧值 / `rollback` 结构重建。生产上删除前若在意精确性，先保留缓存。
7. **删除不发 LLM 但重建依赖缓存**：`rebuild_knowledge_from_chunks` 只重放缓存，**重新调 LLM 的只有新增文档**。想纯增量重抽某文件，需先把该文件 chunk 的 `llm_cache_list` 指向的 extract 缓存条目删掉再重插。
8. **关系 weight 下限契约**：`weight >= 去重真实 source_id 数`；手工建关系 `source_id` 为空或 `manual_creation/UNKNOWN` 不计证据（constants.py:54）。要低于证据数必须显式传空 `source_id`。改了写路径要同步 `ProgramingWithCore.md` 等文档（AGENTS 明令）。
9. **JSON 模式 ≠ 所有 provider 都支持 `response_format`**：OpenAI 系 OK，Ollama/Gemini 有映射，不支持的 provider 会静默剥离（utils.py:5188-5192 说明）；文本分隔符模式是最大兼容面。
10. **多线程并发写**：Faiss/PG 等向量库的缓冲写 `index_done_callback` 才落盘（base.py:319-333、faiss_impl.py:389）。多 worker（gunicorn）下另一个 worker 读不到未 flush 的写，跨 worker 读需要显式 `index_done_callback`。
11. **`_process_single_content` 里 `sidecar` 注入会**给 drawing/table/equation chunk 自动造实体，自研时要决定是否保留（图会多出 `tb-<doc>-NNNN` 一类节点，`strip_internal_multimodal_markup_for_extraction` 已把这些 id 从 prompt 里剥掉，但实体注入仍会建；不想建可关 sidecar 流）。
12. **chunk 排序依赖 `/chunk_order_index`**：`_process_extraction_result` 结果按 chunk 顺序收集（operate.py:4483-4491 有专文解释为何从 tasks 列表按序取，而不是从 done 集合），乱序会造成 source_id/file_path 排列不稳定——**不要改成按 done 集合取结果**。

---

## 8. M3 采用建议

1. **主流程直接复用**：`extract_entities(operate.py:3941)` → `merge_nodes_and_edges(operate.py:3513)` → `_insert_done` 这条链是语言无关的；把 `language=zh`（或 `SUMMARY_LANGUAGE=zh`）作为基线即可让实体名/描述/关键词输出中文。**改动面上限见 Q2**。
2. **自定义入口优先 `addon_params["entity_types_guidance"]`**（一行字符串换中文税单），比换 YAML profile 轻；要连示例一起换才用 `ENTITY_TYPE_PROMPT_FILE`（必须含与模式匹配的 examples 字段，否则 ValueError，prompt.py:825-836）。仍按英文模板的 `{language}` 指令驱动中文输出即可。
3. **若追求更高抽质**：开 `ENTITY_EXTRACTION_USE_JSON=true`（`{"type":"json_object"}` 结构化输出，减少分隔符解析容错成本）。中文命名一致性由模板第 63 行「consistent naming」约束 + `normalize_extracted_info` 全角转半角兜底，够用。
4. **保留 fail-closed 删除/重建原语**（`_purge_kg_contributions` + `rebuild_knowledge_from_chunks`）：这是「选择性删除某文件后图同步」的现成答案，别重写；只需保证**不主动清 LLM 缓存**。
5. **自研 NER 或结构化 JSON 抽取落地口**：保持 `maybe_nodes[规范化名]→list[record]` 返回契约，直接插进 `_process_single_content`；或走 `ainsert_custom_kg` / `utils_graph.acreate_*` 手工建图。**必须过** `normalize_entity_name` + `sanitize_text_for_encoding`。
6. **配置定位**：抽取限额用 `MAX_EXTRACT_INPUT_TOKENS`（不是 `MAX_ENTITY_TOKENS`，后者只管查询上下文截断）；`MAX_GLEANING` 控制补充抽取轮（中文长文档建议 ≥1 补召回）；换 embedding 前先备份 `kv_store_llm_response_cache.json`。
7. **WebUI 类型颜色**：中文化类型集若与内建英文类型名不一致，需同步 `lightrag_webui` 的 graphColor 映射，避免 `__proto__`/未知类型色解析崩（operate.py:653-658 已防门）。