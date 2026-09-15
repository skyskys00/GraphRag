# LightRAG 拆解补充问答（框架实现细节）

> 位置：`lightrag/docs/` ｜ 定位：五份模块拆解（storage / retriever / graph / llm / interfaces）的**补充细节问答**，服务 M0/M2/M4/M5 落地，双向链回五份拆解。
> 日期：2026-09-12。本文不重复五份文档的机制描述，只收录「选型与落地层」的答疑。

---

## A. 存储层补充 —— 详见 [storage.md](storage.md)

### A1. Postgres 的图：AGE（PGGraphStorage）vs 纯表（PGTableGraphStorage）异同

两者实现同一个 `BaseGraphStorage` 接口、都能把图存进 Postgres、遍历语义（1-hop 邻居 / 度统计 / 子图 BFS）都要还原。区别在机制与依赖：

| | PGGraphStorage（AGE） | PGTableGraphStorage（纯表，**推荐**） |
|---|---|---|
| 机制 | Apache AGE 图扩展 + **Cypher** 查询 | 两张普通关系表 + **纯 SQL** 手写图遍历 |
| 依赖 | 额外装 C 扩展，版本须与 PG 匹配 | 只依赖 pgvector |
| 属性 | 顶点/边属性 JSON 存扩展里 | `properties JSONB` 列 |
| 可维护性 | 差（见坑） | 好，纯 SQL 可查可调 |

**AGE 的坑**（storage.md §6.8）：graphid 不能 cast bigint；Cypher 不能与 SQL join 混排；并发边写丢失/死锁需 `_is_transient_graph_write_error` 检测恢复；启动要写一堆 graph/label/索引防重入判断；作者自己遇图遍历退化都绕回 Python 侧 BFS。→ M4 选纯表 = 一次性规避全部。

### A2. 「少一个服务要运维」的含义

pgvector **不是独立服务**——它是 Postgres 的扩展（列类型 + HNSW 索引），向量就存在已在用的 Postgres 里。Milvus/Qdrant 则是**独立向量数据库进程**：单独部署/监控/升级/备份（Milvus 还带 etcd + MinIO）。「少一个服务」= 少维护一整条主从链。

### A3. 用 Milvus 做练习可行吗 / 占用风险

**可行**，且建议做成**与主架构解耦的独立实验**（standalone docker 单机即可）。注意三点：① standalone 实际起 3 个容器（milvus/etcd/minio），24GB Mac 上显眼但可承受；② Milvus 用 HNSW 也是近似检索，对比结论要类比正确；③ 它不进生产链路，练完即弃，别让实验依赖"半挂在"项目里。项目准生产只用 pgvector。

---

## B. 检索层补充 —— 详见 [retriever.md](retriever.md)

### B1. 五种模式异同

| 模式 | 锚点 | 检索路径 | 适合 |
|---|---|---|---|
| local | 实体 | 实体向量(低层关键词 ll) → 图取节点+边 | 实体级问答 |
| global | 关系 | 关系向量(高层关键词 hl) → 图取关系+端点 | 跨文档主题综述 |
| hybrid | 实体+关系 | local + global 两路并集 | 两者兼顾 |
| naive | 纯文本 | 只走 chunk 向量库，无图 | 简单语义检索 |
| mix | 全 | local + global + chunk 向量（三路）并集 | 默认、最全 |

共同点：先做关键词抽取（hl/ll）；返回结构一致（`data.entities/relationships/chunks/references`）。区别核心 = 锚点 + 是否带图 + 关键词分层。

### B2. 「三路召回 + chunk 重排的原料」是什么意思

**数据齐、方法要自己写**。原料 = 三路检索原语（`_get_node_data` / `_get_edge_data` / `_get_vector_context`）可直接 import 复用（各自返回**单路有序候选**）+ chunk 重排管道 `process_chunks_unified` 可接。缺 = **融合器（RRF）** 与 **关键词路（BM25/sparse）**，两样都无现成。硬约束：默认 query 路径内部已提前 round-robin 并集，返回结果**拿不到分路排名** → RRF 不能只改返回值，必须自己调原语、自己融合。

### B3. RRF 与 rerank 应在 mix「之前」还是「之后」

顺序：**三路召回 → RRF 融合 → rerank 精排 → 截断 → 组装**。
- RRF **在前**：便宜，把三路 top-k 合并成候选池（靠"多路共识"提分）；
- rerank **在后**：cross-encoder 逐个精排，贵且慢，只对融合后较小候选池做。
落到 LightRAG：RRF = **替换 round-robin 并集那一步**（`_perform_kg_search` / `_merge_all_chunks` 的 merge 点）；rerank 仍复用其 `process_chunks_unified`（`_build_context_str` 位置）。

---

## C. 图构建补充 —— 详见 [graph.md](graph.md)

### C1. `chunks: dict[chunk_id, TextChunkSchema{..., heading?, sidecar?}]` 的问号

问号 = **可选字段**（Optional），不是不确定——两字段在代码里确实只在特定条件出现：**仅 P 策略（paragraph_semantic）切分的 chunk 自带 `heading` 与 `sidecar(blockid)`**，F/R/V 没有（heading 依赖后置 backfill 才可能补上）。核心类型 `TextChunkSchema` 必填只有 `tokens/content/full_doc_id/chunk_order_index` 四项。

---

## D. LLM 接入补充 —— 详见 [llm.md](llm.md)

### D1. 「API server / .env（WebUI 部署才需要）」指什么

指 LightRAG 官方 **web 服务器路径**（`api/lightrag_server.py` + 配套 WebUI）——它从环境变量（`LLM_BINDING` / `EXTRACT_LLM_*` 等）解析模型配置。**本项目走「程序内嵌」**（闭包传 `llm_model_func` + `role_llm_configs`），不经它的 env 解析。两姿势二选一，我们用后者。

### D2. 前端展示「KG 关系 + 文件索引 + 原文片段排序」要关注什么

1. **数据三件套**：返回结构已具备——`entities/relationships`（KG 画板）、`chunks`（原文候选，`reference_id+content`）、`references`（文件索引，按 file_path 频次排）。透传即可。
2. **排序必须保留**：返回给 LLM 的 chunk 顺序 = 最终上下文顺序（retriever §6.8）；引用编号与展示顺序要与生成上下文一致，否则引用错位。
3. **原文定位**：展示文本片段（`chunk.content` + 高亮）最轻；跳 PDF 原页需要页码/块坐标（M1/M2 补的 `page_range` PDF / `paraid` docx）。MVP 先做"文本片段 + 页码标注"。
4. **联动**：检索结果一次报文带三块数据，前端图/引用/片段三视图联动；SSE 事件透传 `raw_data`。

---

## E. 接口 / TextUnit 补充 —— 详见 [interfaces.md](interfaces.md)

### E1. TextUnit 契约三字段「够用」吗

分用途。**核心功能（建图/检索/生成）够**：LightRAG 的 chunk 只需 content + doc_id + heading，页码/块类型它不消费。**展示功能不够**：引用溯源到 PDF 页、结构化展示、按块类型筛选需要页码/块类型/title_path——所以要补。补的成本已确认极低：M1 一处写点（blocks.jsonl）、M2 一处收口（`build_chunks_dict_from_chunking_result`），不碰建图/检索主链路。

### E2. PDF 有页码、Word 没有吗

对，**格式本质**。PDF 固定排版 → 物理页稳定 → 页码可用；docx 流式排版 → 无稳定"页"概念 → 只能靠段落稳定标识 `paraid`（`w:paraId`）。契约设计：`page_range` 对 PDF 真实，docx 落 `paraid` 兜底。

### E3. 标题切分很重要，为什么标题路径不完善

标题机制**已内置**：P 策略本身就是「标题驱动切分」（heading 分块 + LevelMerge）。缺的是**形态**：给出的是 `heading {level, heading, parent_headings}` 三级结构（面包屑 `h1→h2→h3`），无章节编号、无完整路径树；且 F/R/V 不自带 heading。M2 把它升级为 `title_path`（level+parent_headings 拼全路径）并在所有策略下都有——**不必重写标题解析**，标题就在 blocks.jsonl 里。

### E4. 「对齐建议」的大白话版（interfaces.md §6）

- **M1 在 `sidecar/writer.py` 写 blocks.jsonl 时补 page_label / block_type** = 解析器解析完文档那一刻知道"这段落属第几页、是文字还是表格"，趁信息最全在生成产物处一并记下。改一处写点。
- **M2 P 策略输出组装时聚合 block_type / title_path** = 切分时一个 chunk 是若干块拼的，把"跨哪几页、以何类型为主、标题链"汇总到 chunk。收口函数 `build_chunks_dict_from_chunking_result` = 所有切分结果统一过这一个口子加新字段。
- **entity_refs 沿 LightRAG 反查** = "chunk 提到哪些实体"不必在 chunk 里冗余写一份——LightRAG 已存反向索引（`entity_chunks[实体名] = 出现的 chunk 列表`），要用时按 chunk_key 反查即可，不改写路径且兼容删除/重建一致性。

---

## 与项目契约的关系（防混淆）

- 本文与五份拆解 = **读进来的（LightRAG 现状）**；
- `docs/contracts/`（计划于步骤 ④ M0 落地） = **写出去的（我们自己的 TextUnit 规约：JSON Schema + 说明）**，是联调时的校验蓝本；其字段设计直接引用 Interfaces.E1–E4 的对齐结论。