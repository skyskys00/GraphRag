# LightRAG v1.5.7 检索链路拆解（供 M5 重建参考）

> 版本：LightRAG v1.5.7，源码位于 `lightrag/source/lightrag/`
> 行号以该版本源码为准。撰写日期：2026-09-11
> 一句话结论：**LightRAG 内建了「本地/全局双路向量检索 + 可选 chunk 重排」，但没有 RRF、没有 BM25/稀疏关键词检索；`mix` 只是「两路 KG + 一路纯向量 chunk」的按序并集，融合逻辑是 round-robin，不是打分融合。`rerank.py` 确实进入 query 路径（chunk 阶段），但它只是 provider 适配层，默认未配置。**
> 补充问答（五模式异同、RRF/rerank 先后）：见 [qa-notes.md](qa-notes.md#B)。

---

## 1. 定位与职责

检索链路横跨 4 个文件，职责边界清晰：

| 文件 | 角色 |
|---|---|
| `lightrag.py` | **入口与分发**：`query/aquery/query_data/aquery_llm` 全部在这里；`aquery_llm:4211` 是唯一模式分发点（dispatch） |
| `operate.py` | **检索实现本体**：`kg_query:4588`（local/global/hybrid/mix）、`naive_query:6591`（naive）、关键词抽取、三路检索原语（`_get_node_data` / `_get_edge_data` / `_get_vector_context`）、chunk 关联与上下文组装 |
| `base.py` | 数据契约：`QueryParam:90`（检索参数 dataclass）、`QueryResult:1796` / `QueryContextResult:1848`（统一返回结构） |
| `utils.py` | 检索后处理工具：`process_chunks_unified:6295`（重排+截断）、`apply_rerank_if_enabled:6164`、`convert_to_user_format:6851`（输出格式） |
| `rerank.py` | **可选 rerank provider 适配层**：Jina/Cohere/Aliyun 三家 rerank API 封装；只被 API 服务器 `api/lightrag_server.py:2336` import，核心库不 import 它 |
| `utils_graph.py` | 图算法工具（本链路用到的 `pick_by_*` 其实在 `utils.py`，`utils_graph.py` 主要服务插入流程） |
| `query_validation.py` | 查询文本校验（非空、英文 ≥3 字符 / 东亚字按 2 计） |

**入口调用链（aquery 完整形态）：**

```
LightRAG.aquery (lightrag.py:3940, 仅解包 LLM content)
  └─ LightRAG.aquery_llm (lightrag.py:4211)          ← 模式分发中枢
       ├─ mode∈{local,global,hybrid,mix} → kg_query  (operate.py:4588)
       ├─ mode==naive                  → naive_query (operate.py:6591)
       └─ mode==bypass                 → 直连 LLM（无检索）
```

另有 `aquery_data:3998`（结构化数据、不走 LLM，内部用 `only_need_context=True` 复用同一套 kg_query/naive_query）和 `query_data/aquery_data` 的同步壳。

---

## 2. 关键函数/路径（相对路径 + 函数名/行区间）

### 2.1 入口与分发（lightrag.py）

| 函数 | 行号 | 说明 |
|---|---|---|
| `LightRAG.query` | 3916 | 同步版，包 `aquery` |
| `LightRAG.aquery` | 3940 | 异步，向后兼容壳，只返回 LLM content 或流 |
| `LightRAG.query_data` / `aquery_data` | 3973 / 3998 | 结构化检索结果（不含 LLM 生成），`only_need_context=True` 路径 |
| `LightRAG.aquery_llm` | 4211 | **分发中枢**：4245 行按 mode 分发到 kg_query / naive_query / bypass；4340 行取 `raw_data` + LLM 响应打包 |
| `LightRAG.query_llm` | 4370 | 同步版 |

### 2.2 五模式实现（operate.py）

| 模式 | 入口 | 检索路径 |
|---|---|---|
| local | `kg_query:4588` → `_perform_kg_search:5235` | `_get_node_data:6020`：entities_vdb 向量检索（top_k）→ 图取节点+度 → `_find_most_related_edges_from_entities:6080` 取关联边（按 degree+weight 排序），**只用低层关键词 ll_keywords** |
| global | `kg_query` → `_perform_kg_search:5246` | `_get_edge_data:6295`：relationships_vdb 向量检索（top_k）→ 图取边属性 → `_find_most_related_entities_from_relationships:6354` 反向取实体，**只用高层关键词 hl_keywords** |
| hybrid | `kg_query` → `_perform_kg_search:5257` | **local + global 两路都跑**（ll + hl），随后 round-robin 按位去重合并 |
| mix | `kg_query` → `_perform_kg_search:5280` | **local + global + `_get_vector_context:5092`**（chunks_vdb 纯向量检索，top_k=chunk_top_k），三路 round-robin 合并 |
| naive | `naive_query:6591` | 只用 `_get_vector_context:5092`（chunks_vdb），无图、无关键词、entities/relationships 恒为空 |

### 2.3 检索后处理（operate.py + utils.py）

| 函数 | 行号 | 职责 |
|---|---|---|
| `get_keywords_from_query` | operate.py:4844 | hl/ll 关键词获取；若 `QueryParam.hl_keywords/ll_keywords` 已给则直接用，否则 LLM 抽取（`extract_keywords_only`，prompt 模板 `PROMPTS["keywords_extraction"]` prompt.py:484）并缓存 |
| `_perform_kg_search` | operate.py:5149 | **Stage 1 纯搜索**：预计算 query/ll/hl 三个 embedding（一次性批处理 5198-5232），按模式调三路原语，最后 round-robin 去重合并实体/关系（5301-5355） |
| `_apply_token_truncation` | operate.py:5370 | **Stage 2 token 截断**：entities/relations 分别按 `max_entity_tokens`/`max_relation_tokens` 截 |
| `_merge_all_chunks` | operate.py:5585 | **Stage 3 chunks 合并**：`_find_related_text_unit_from_entities:6136` + `_find_related_text_unit_from_relations:6387` + 已有 vector_chunks，三源 round-robin 按 chunk_id 去重 |
| `_build_context_str` | operate.py:5693 | **Stage 4 上下文组装**：动态 token 预算（`max_total_tokens` − 系统prompt − KG上下文 − query − 200 buffer），**5803 行调 `process_chunks_unified` 做重排+截断**，然后 formatting + `convert_to_user_format:5878` |
| `_build_query_context` | operate.py:5895 | 4 阶段编排壳，返回 `QueryContextResult(context, raw_data)` |
| `process_chunks_unified` | utils.py:6295 | chunk 统一处理：① 重排 ② min_rerank_score 过滤 ③ chunk_top_k 截断 ④ token 最终截断 |
| `pick_by_vector_similarity` / `pick_by_weighted_polling` | utils.py:5965 / 5883 | KG→chunk 的两种选取策略（VECTOR/WEIGHT，由 `KG_CHUNK_PICK_METHOD` 决定，默认 VECTOR） |

### 2.4 数据契约（base.py）

- `QueryParam:90`：全部检索参数的唯一入口（见 §4）。
- `QueryResult:1796`：`content` / `response_iterator` / `raw_data` / `is_streaming` / `llm_generated`。
- `QueryContextResult:1848`：`context` + `raw_data`。

---

## 3. 核心机制与数据流

### 3.1 一图流

```
用户 query
  │
  ▼
aquery_llm (lightrag.py:4211) ── validate_query_not_empty / validate_rag_query (query_validation.py)
  │
  ▼ 模式分发
  └──bypass → 直接 LLM
  └──naive  → naive_query (operate.py:6591)
              └─ _get_vector_context (chunks_vdb, chunk_top_k or top_k)
              └─ process_chunks_unified (utils.py:6295: 重排→过滤→截断)
              └─ PROMPTS["naive_rag_response"] (prompt.py:388)
  └──local/global/hybrid/mix → kg_query (operate.py:4588)
              ├─ get_keywords_from_query (LLM 抽取 hl/ll 关键词, operate.py:4844)
              ├─ _build_query_context (operate.py:5895)
              │   ├─ S1 _perform_kg_search (operate.py:5149)
              │   │    ├─ local:  _get_node_data    (entities_vdb.top_k → 图 node+edge)
              │   │    ├─ global: _get_edge_data    (relationships_vdb.top_k → 图 edge+node)
              │   │    ├─ mix 额外: _get_vector_context (chunks_vdb)
              │   │    └─ round-robin 两路实体/关系去重合并
              │   ├─ S2 _apply_token_truncation (max_entity/max_relation_tokens)
              │   ├─ S3 _merge_all_chunks (实体source_id/关系source_id/chunk向量 三源合并)
              │   └─ S4 _build_context_str (动态 token 预算)
              │        └─ process_chunks_unified → apply_rerank_if_enabled → rerank_model_func
              │        └─ convert_to_user_format → raw_data.data.{entities,relationships,chunks,references}
              └─ PROMPTS["rag_response"] (prompt.py:334) 组装 → LLM
```

### 3.2 关键机制细节

**关键词抽取是图检索的前提**：local/hybrid/mix 用 `ll_keywords`、global/hybrid/mix 用 `hl_keywords` 做 embedding 查询图谱向量库；chunk 向量路用原始 `query` 的 embedding。三个 embedding 在 `_perform_kg_search:5198-5232` **一批**算完，避免 2~3 次串行调用。若 hl/ll 全空，则回退 `ll_keywords=[原query]`（query < 50 字符时），否则直接失败返回 `fail_response`（operate.py:4654-4659）。

**「最后一跳 chunk」是 WEIGHT/VECTOR 二选一**（`_find_related_text_unit_from_entities:6219-6264`）：
- 默认 `KG_CHUNK_PICK_METHOD=VECTOR`：从实体 `source_id` 收集候选 chunk，用 query embedding 在 chunks_vdb 里取相似度 Top-N（`num_of_chunks = related_chunk_number × 实体数 / 2`）。
- `WEIGHT`：按实体重要度线性梯度轮询（`pick_by_weighted_polling`，每实体配额从 `related_chunk_number` 递减到 1）。
- 关系路径同构（`_find_related_text_unit_from_relations:6511-6549`）。

**mix 的「融合」就是并集**：实体/关系是 local+global 两条排好序列表的 round-robin（operate.py:5301-5355）；chunk 是 [vector_chunks, entity_chunks, relation_chunks] 三份列表按序轮询 + chunk_id 去重（operate.py:5632-5678）。优先级 implicit：vector 每一项优先于 entity、entity 优先于 relation 进入最终列表。**没有任何打分、加权或 RRF。**

---

## 4. 输入输出结构

### 4.1 QueryParam（base.py:90-189）——所有检索参数唯一入口

| 字段 | 默认值 | 来源 |
|---|---|---|
| `mode` | `"mix"` | Literal["local","global","hybrid","naive","mix","bypass"] |
| `top_k` | 40 | env `TOP_K`，常量 `DEFAULT_TOP_K`（constants.py:57） |
| `chunk_top_k` | 20 | env `CHUNK_TOP_K`，`DEFAULT_CHUNK_TOP_K`（constants.py:58） |
| `max_entity_tokens` | 6000 | env `MAX_ENTITY_TOKENS`（constants.py:59） |
| `max_relation_tokens` | 8000 | env `MAX_RELATION_TOKENS`（constants.py:60） |
| `max_total_tokens` | 30000 | env `MAX_TOTAL_TOKENS`（constants.py:61） |
| `enable_rerank` | **True** | env `RERANK_BY_DEFAULT`，默认 "true"（base.py:166） |
| `response_type` | `"Multiple Paragraphs"` | — |
| `stream` | False | — |
| `hl_keywords` / `ll_keywords` | `[]` | 预置关键词则跳过 LLM 抽取 |
| `only_need_context` / `only_need_prompt` | False | 调试开关（只取上下文 / 只取 prompt） |
| `conversation_history` / `user_prompt` / `include_references` / … | — | 见 base.py 全文 |

LightRAG 构造器同名参数（lightrag.py:429-458）也读同一组 env：`top_k`、`chunk_top_k`、`max_*_tokens`、`kg_chunk_pick_method`（env `KG_CHUNK_PICK_METHOD`，默认 `VECTOR`）、`related_chunk_number`（常量 `DEFAULT_RELATED_CHUNK_NUMBER=5`，constants.py:63）。

**Top-level 语义（base.py docstring）**：local 模式 top_k 代表「实体数」，global 代表「关系数」；chunk 向量路（mix/naive）用 `chunk_top_k`。每个 VDB 还有 `cosine_better_than_threshold`（默认 0.2）作召回过滤阈值（实体/关系向量库,见 `cosine_better_than_threshold` 字段 base.py:256）。

### 4.2 返回结构

`aquery_llm` 返回 dict（lightrag.py:4230-4352）：

```python
{
  "status": "success" | "failure",
  "message": str,
  "data": {
    "entities":        [{"entity_name","entity_type","description","source_id","file_path","created_at"}],
    "relationships":   [{"src_id","tgt_id","description","keywords","weight","source_id","file_path","created_at"}],
    "chunks":          [{"reference_id","content","file_path","chunk_id"}],
    "references":      [{"reference_id","file_path"}],   # 按 file_path 出现频次排序编号
  },
  "metadata": {
    "query_mode": str,
    "keywords":   {"high_level":[], "low_level":[]},
    "processing_info": {"total_entities_found","total_relations_found",
                        "entities_after_truncation","relations_after_truncation",
                        "merged_chunks_count","final_chunks_count"},
  },
  "llm_response": {"content"|"response_iterator","is_streaming","llm_generated"},
}
```

各模式数据差异：local 只给图实体+关联 chunk；global 只给关系+关联 chunk；hybrid 两者都有；mix 多给向量 chunk；naive 的 entities/relationships 恒为 `[]`；bypass 全空。格式化实现在 `convert_to_user_format`（utils.py:6851）。`chunk` 的拼接顺序就是最终送入 LLM 的顺序（round-robin 后经重排/截断）。

---

## 5. 「内建 vs 自研」边界（关键核实）

### 5.1 **rerank 核实结论：`rerank.py` 确实进入 query 路径，但只是可选的 provider 层**

- **调用链证据**（全部是硬点、非推断）：
  1. `process_chunks_unified`（utils.py:6324-6334）在 `enable_rerank` 为真且有 query/chunks 时调用 `apply_rerank_if_enabled`。
  2. 该函数在 **utils.py:6187** 读取 `global_config["rerank_model_func"]`；未配置则 warning 并原样返回（utils.py:6188-6192）。
  3. `process_chunks_unified` 在两个 query 路径被调：KG 模式的 `_build_context_str`（**operate.py:5803**）和 naive 的 **operate.py:6722**。
  4. `rerank_model_func` 由 `LightRAG` 构造器字段 `rerank_model_func`（**lightrag.py:755**，`__post_init__` 在 1366-1372 包 rate-limit）经 `_build_global_config`（lightrag.py:1200, `asdict(self)`）进入 `global_config`。
  5. `rerank.py` 本身（Jina/Cohere/Aliyun 三家实现：`generic_rerank_api:240`、`cohere_rerank:448`、`jina_rerank:515`、`ali_rerank:555`）**只被 API 服务器 import**：`api/lightrag_server.py:2336-2348` 依 `--rerank-binding`（env `RERANK_BINDING`，默认 `"null"` → 关闭）包成 `server_rerank_func`，在 **lightrag_server.py:2427** 赋给 `rerank_model_func`。

- **评估**：我们此前判断「LightRAG 无内建 rerank」**不准确**。准确说法是：**LightRAG 内建了完整的 chunk 重排管道**（默认 `enable_rerank=True`），但从服务器侧默认关闭（`RERANK_BINDING=null` 无 provider），SDK 侧需自传 `rerank_model_func`。重排**只作用于 text chunks**（KG 模式三源 merge 之后、token 截断之前），不重排 entities/relations，也**不是多路融合**——它是单份候选列表的 cross-encoder 重排。min_rerank_score 过滤默认 0.0（`DEFAULT_MIN_RERANK_SCORE=0.0`，constants.py:67），故不配置时无副作用（但会在每个 query 打 warning）。

### 5.2 内建有什么、缺什么

| 能力 | 内建情况 |
|---|---|
| 实体向量检索（图路 local） | ✅ `_get_node_data:6020` |
| 关系向量检索（图路 global） | ✅ `_get_edge_data:6295` |
| chunk 纯向量检索 | ✅ `_get_vector_context:5092` |
| 多路融合 RRF | ❌ 无——round-robin 并集（operate.py:5301/5632） |
| 关键词稀疏检索（BM25/SPARSE） | ❌ 无——「关键词」只有 LLM 抽取的 hl/ll 两路 embedding 查询，**无词法打分**（全仓 grep `bm25|tfidf|sparse` 无命中） |
| 重排 | ✅ 有（chunk 级，可选 provider） |
| 三路原始排名暴露 | ❌ kg_query 内部过早融合，外部拿不到分路排名 |

这正好坐实 M5 缺口：**RRF(k=60) 融合、rerank、BM25/关键词稀疏路都必须自研**；LightRAG 只提供「三路召回 + chunk 重排」的原料。

---

## 6. 已知坑

1. **`enable_rerank=True` 但无 `rerank_model_func` 时每个 query 打一次 warning**（utils.py:6188-6192），且若配置 provider 出错会静默降级回原 chunks（utils.py:6258-6260）——线上别把 rerank 当硬依赖。
2. **mix/hybrid 的「融合」是 round-robin 位次合并**：列表顺序敏感、不做相似度归一。若两路召回同一 chunk_id 以先到者为准（vector > entity > relation），权重完全取决于路序。
3. **实体/关系 token 截断在 chunk 选取之前**（S2 在 S3 前，operate.py:5941/5948）：被截掉的实体不会贡献 chunk，可能丢掉图上关键 chunk（这是设计而非 bug，但自研外层时别直接复用 `_build_query_context`）。
4. **全空关键词兜底**：query ≥50 字符且 hl/ll 都空时直接返回 `fail_response`（operate.py:4659），短 query 则强行 `ll_keywords=[query]`——外部接 BM25 时注意这个分支的语义。
5. **kw 缓存**：`args_hash` 把 `enable_rerank`、`user_prompt`（text，含 prefix）等算进 key（operate.py:4738-4763 / 6795-6818），改动外层增强会命中不了缓存的旧条目；反之若复用缓存 key 模式要一并带上新参数。
6. **`only_need_context` 也会完整执行 4 阶段**（S1–S4 全跑），只是不发 LLM——`aquery_data` 并不省时。
7. `_build_context_str` 的 token 预算按「实际渲染的模板」计算（`system_prompt` 前向传递，operate.py:5739-5743），自定义 prompt 模板若改占位符会改变预算，需在 `kg_query` 层传入 `system_prompt`。
8. 返回给 LLM 的 chunk 顺序**就是**最终上下文顺序，重建输出时注意 M5 重排要在这之前完成，否则引用编号（reference_id 按 file_path 频次排）会错位。

---

## 7. 采用建议（M5：外层 RRF(k=60) + rerank + BM25 关键词路）

### 7.1 结论先行：**必须自己组装三路，不能只改返回值**

原因：RRF 需要三路各自独立的排名，而 kg_query 在 `_perform_kg_search`（operate.py:5301）/`_merge_all_chunks`（operate.py:5632）里已提前 round-robin 并集，`raw_data` 只有融合后结果，**拿不到分路排名**。改返回值无法恢复 RRF 所需信息。

### 7.2 推荐的注入架构（复用原语、绕开 kg_query）

LightRAG 的三路召回原语都是 `operate.py` 顶层 async 函数，可直接 import 复用：

| 复用对象 | 用途 |
|---|---|
| `_get_node_data`（operate.py:6020） | **图路 A**：实体检索（ll_keywords）→ 实体+关联边排名 |
| `_get_edge_data`（operate.py:6295） | **图路 B**：关系检索（hl_keywords）→ 关系+关联实体排名 |
| `_get_vector_context`（operate.py:5092） | **向量路 C**：chunk 纯向量检索（可直接传预计算 `query_embedding`） |
| BM25 自己实现/引库 | **稀疏路 D**：chunk 词法打分（LightRAG 无此原语） |
| `process_chunks_unified`（utils.py:6295） | **自家 rerank 队列**：D 路融入后对它一次性重排+min_score 过滤+截断（强复用，含 `apply_rerank_if_enabled` 接口） |
| `convert_to_user_format`（utils.py:6851）+ `PROMPTS["kg_query_context"]`（prompt.py:442） | **自家上下文组装**：仿 `_build_context_str`（operate.py:5693-5820）的 token 预算算法 |

**融合协议**：三路（+BM25）各自产出 `{(chunk_id/entity_name/rel_key): rank}` 列表 → RRF(k=60) 出最终 chunk 序（可对图路 A/B 与向量路在同一 chunk_id 空间上做 RRF，这正是要替换 round-robin 的位置）→ 送入 `process_chunks_unified` 重排 → 截断到 `max_total_tokens` 预算 → 组装 prompt 交 LLM。

### 7.3 代价较小的替代方案（不建议）

- 只想在 chunk 层面换排序：把外层包在 `kg_query(...)` 之外，用 `only_need_context=True` 拿 `raw_data.data.chunks`，再做一次重排/过滤再送 LLM——但**只能作用于融合后列表**，且 token 预算、关键字缓存都已按旧流程算过，改造胶水量不小，收益低。
- 直接在 `_merge_all_chunks` 之后 monkey-patch：侵入性强、随版本易破，不推荐。

### 7.4 落地注意

1. **关键词路不要复用 `get_keywords_from_query` 的 LLM 抽取做 BM25 词**：建议 BM25 分词直接用原 query（英文空格/中文字符切分），hl/ll 语义关键词仍走 LLM 抽取供图路 A/B 使用，两者解耦。
2. rerank 复用 `rerank_model_func` 约定（`query, documents, top_n` → `[{index,relevance_score}]`，见 `normalize_rerank_result` utils.py:6263），保证能接 Jina/Cohere/Aliyun 或自建服务，同时保留 `enable_rerank`/`min_rerank_score` 语义。
3. 模块 M5 落地时把上文 §5.2 的「缺什么」三项（RRF、BM25、自家 rerank 位置）作为验收清单；`aquery_data`（lightrag.py:3998）返回结构是我们的对外契约，自研组装后仍需产出同构的 `data.entities/relationships/chunks/references`。
4. 若坚持零改动内建路径，至少把 `enable_rerank` 显式设为 `False`，避免每个 query 的空 rerank warning 干扰日志。