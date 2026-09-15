# LightRAG v1.5.7 存储层拆解（模块 M4 参考）

> 本文档是 GraphRAG 重做项目（M0–M8）对 LightRAG v1.5.7 存储层的拆解，服务于模块 M4：「抽象复用 + 换官方 Postgres+pgvector 实现」（架构对照 `docs/ARCHITECTURE.md` §2.4/§2.5）。
> 源码根：`lightrag/source/`。下文路径如 `lightrag/base.py` 均相对该根。行号为 v1.5.7（`lightrag/_version.py:3`）快照值。
> 补充问答（选型落地细节，e.g. AGE vs 纯表、Milvus 练手）：见 [qa-notes.md](qa-notes.md #A)。

---

## 1. 定位与职责

存储层是 LightRAG 的**持久化 + 检索底座**，由四层组成：

| 层 | 职责 | 关键文件 |
|---|---|---|
| **存储抽象** | 4 类抽象基类（KV / Vector / Graph / DocStatus）定义后端必须实现的接口契约 | `lightrag/base.py` |
| **后端注册** | 名称 → 实现类的解析、兼容性校验、环境变量检查 | `lightrag/kg/__init__.py`、`lightrag/kg/factory.py` |
| **后端实现** | 每个后端一份实现（文件态 / 内存态 / 各种服务器后端） | `lightrag/kg/*_impl.py` |
| **跨进程协调** | 锁、更新标志、共享数据、pipeline 唤醒通道——多 worker（gunicorn）时让读写进程对同一份数据达成一致 | `lightrag/kg/shared_storage.py`、`lightrag/kg/pipeline_ingress.py` |

一句话职责描述：**以「每个工作区一个写者 + `index_done_callback` 提交点」为一致性模型，支撑文档入库（全量/增量）与查询（local / global / naive / hybrid / mix），并向前端提供图 / 文档 / 向量三路数据**。查询语义最终落在 `operate.py` 对四类存储的调用上，二节会给出精确调用链。

存储类型的命名空间（`lightrag/namespace.py:7`）是贯穿全局的身份键：
`full_docs` / `text_chunks` / `llm_response_cache` / `full_entities` / `full_relations` / `entity_chunks` / `relation_chunks`（KV），`entities` / `relationships` / `chunks`（向量），`chunk_entity_relation`（图），`doc_status`（文档状态）。

---

## 2. 关键文件 / 类

### 2.1 抽象层 `lightrag/base.py`（1863 行）

| 类 | 行 | 说明 |
|---|---|---|
| `StorageNameSpace` | 192 | 四类存储的共同基类：`namespace` / `workspace` / `global_config`，以及 `initialize` / `finalize` / `index_done_callback` / `drop_pending_index_ops` / `drop` 五个生命周期方法 |
| `BaseVectorStorage` | 251 | 向量存储抽象。`requires_embedding_func` 类标志 + `_generate_collection_suffix()`（按 embedding 模型名生成集合后缀，如 `text_embedding_3_large_3072d`，base.py:275） |
| `BaseKVStorage` | 418 | 键值存储抽象。`supports_strict_point_reads` 可选能力标志 + `get_by_id_strict` 完整或抛错语义（base.py:439） |
| `BaseGraphStorage` | 519 | 图存储抽象。顶点 / 边增删查、度、批量变体、`get_knowledge_graph` 子图、标签检索。**属性契约**：值必须可标量化（str/int/有限 float/bool），禁嵌套容器与 None（base.py:696 起的长注释） |
| `DocStatus` 枚举 | 1028 | 文档状态：PENDING→PARSING→ANALYZING→PROCESSING→PROCESSED \| FAILED（PREPROCESSED 已废弃） |
| `DocProcessingStatus` | 1044 | 文档状态行结构，`from_stored`（1103）容忍外来字段 |
| 调度分页结构 | 1123–1267 | `CursorPosition` / `CURSOR_START` / `CURSOR_END` / `CursorAfter`（键集扫描三态游标）、`DocSchedulingRecord` / `DocStatusPage`、`SourceAbsent` / `SourceUnique` / `SourceConflict`（源冲突三态） |
| `DocStatusStorage` | 1269 | **第 4 类存储抽象**，继承 `BaseKVStorage`。bounded 调度 API 全部 `@abstractmethod`：`get_docs_by_statuses_page` / `get_docs_by_ids` / `get_full_docs_by_ids` / `resolve_doc_source_strict` 等（见 §4） |

其余：`QueryParam`（查询参数，base.py:90）、`TextChunkSchema`（79）、`StoragesStatus`（1771）、`DeletionResult`（1781）、`QueryResult` / `QueryContextResult`（1796 / 1848）。

> **问题 1 的直接答案**：四个存储抽象全部在 `base.py`：`BaseVectorStorage`、`BaseKVStorage`、`BaseGraphStorage`，以及第 4 类 `DocStatusStorage`（它不是独立文件，而是 base.py 内继承 `BaseKVStorage` 的子抽象）。接口签名清单见 §4。

### 2.2 注册与工厂 `lightrag/kg/__init__.py` + `lightrag/kg/factory.py`

- `STORAGE_IMPLEMENTATIONS`（__init__.py:1）：四类存储各自允许的后端名 + `required_methods` 最小方法集（如 KV 要求 `get_by_id`/`upsert`）。
- `STORAGE_ENV_REQUIREMENTS`（:51）：每个后端要求的 env 变量（如 `PGKVStorage` 要 `POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_DATABASE`）。
- `STORAGES`（:122）：后端名 → 模块相对导入路径。
- `verify_storage_implementation`（:153）：初始化时校验后端是否属于对应类型。
- `get_storage_class`（factory.py:17）：前四个默认后端硬编码直接导入，其余按 `STORAGES` 动态 importlib 解析。

### 2.3 默认实现（文件态，内存驻留）

| 文件 | 类（行） | 存储形态 |
|---|---|---|
| `lightrag/kg/json_kv_impl.py` | `JsonKVStorage`（32） | **跨进程共享内存**（`multiprocessing.Manager().dict()`，见 `get_namespace_data`）+ 文件 `kv_store_<ns>.json` 仅作持久化 |
| `lightrag/kg/json_doc_status_impl.py` | `JsonDocStatusStorage` | 同类，`doc_status` 专用，实现调度 API |
| `lightrag/kg/networkx_impl.py` | `NetworkXStorage`（34） | 进程内单个 `networkx.Graph` + 整图 GraphML 文件 `graph_<ns>.graphml`；**无增量同步，靠 update flag 触发整图 reload** |
| `lightrag/kg/nano_vector_db_impl.py` | `NanoVectorDBStorage`（53） | 进程内 `NanoVectorDB` + JSON 文件 `vdb_<ns>.json`；延迟 embedding + redo log |

单独成文的配套机制：
- `lightrag/kg/shared_storage.py`：跨进程共享 dict / update flag / 两级锁（`UnifiedLock` 246、`KeyedUnifiedLock` 935）/ pipeline_status / 扫描任务存储。
- `lightrag/kg/write_seq.py`：`__write_seq__` 有序写入令牌（69），解决文件态后端崩溃重放时同秒写入的先后判定。
- `lightrag/kg/pipeline_ingress.py`：workspace 级 pipeline 唤醒邮箱（doc 通知 / auto-rescan 脏标志 / manual retry 粘性队列三通道）。

### 2.4 Postgres 后端（问题 3 主战场）

同一份 `postgres_impl.py` 里装了四类实现 + 连接池 + 迁移：

| 类 | 行 | 说明 |
|---|---|---|
| `PostgreSQLDB` | 419 | asyncpg 连接池封装：`initdb`（559，建表 + 迁移）、`_run_with_retry`（781，连接级重试）、`configure_vector_extension`（888，**pgvector**）、`configure_age_extension`（930，**Apache AGE**）、`configure_vchordrq`（1317，pgvecto.rs）、`_create_vector_index`（2635，HNSW/HNSW_HALFVEC/IVFFLAT/VCHORDRQ） |
| `ClientManager` | 2868 | 进程级单例连接池：`get_config`（2884，env 优先 + config.ini 兜底）、`get_client`（3080）、`release_client`（3102）。**首次初始化即锁定配置** |
| `PGKVStorage` | 3120 | KV → 9 张关系表（见 §3.5） |
| `PGVectorStorage` | 3866 | 向量 → 3 张含 `content_vector VECTOR(dim)` 列的表；`setup_table`（4112，**按 embedding 维度建表 + 旧表迁移 + 维度不符抛错**）、`upsert`（4547，缓冲式延迟 embedding）、`_flush_pending_vector_ops`（4603，单事务整体落库） |
| `PGDocStatusStorage` | 5457 | doc_status → `LIGHTRAG_DOC_STATUS` 表 + 键集分页调度 API（`get_docs_by_statuses_page` 5991） |
| `PGGraphStorage` | 7209 | **图用 Apache AGE 图扩展**（Cypher 查询，per-workspace 一个 AGE graph）；`_bfs_subgraph`（8791）是 Python 侧 BFS 兜底 |

`lightrag/kg/pgtable_impl.py` 是**纯关系表、无任何扩展依赖**的图实现：

| 类 | 行 | 说明 |
|---|---|---|
| `PGTableGraphStorage` | 267 | `lightrag_graph_nodes` / `lightrag_graph_edges` 两张表（JSONB 属性列）；`_bfs_frontier`（1078）是前沿受限迭代式 BFS | 

---

## 3. 核心机制与数据流

### 3.1 一致性模型（整个存储层的设计公理）

所有文件态 / 内存态后端的正确性依赖三个并发不变量（docstring 反复声明，networkx_impl.py:48、nano_vector_db_impl.py:67）：

1. **每个 workspace 恰好一个写者**：由 pipeline 的 `busy` / `destructive_busy` 标志保证（AGENTS.md《Pipeline concurrency contract》），其他进程只读。
2. **最终一致即可**：读进程只需在写者 `index_done_callback` 完成后看到新数据；间隙内读到旧快照是合法的。
3. **底库同步操作不可被抢占**：`graph.add_node` / `client.upsert` 在一个单线程 asyncio 事件循环内天然互斥，所以方法不需要每次调用都加锁。

对 PG 后端，不变量 3 不成立（无本地内存态，操作直接异步落库），因此其并发模型退化为「缓冲 + 单事务 + `_flush_lock`」，见 3.4。

### 3.2 两种跨进程同步协议（文件态后端）

**协议 A：共享内存（JsonKV / JsonDocStatus）**
- `self._data` 是 `shared_storage._shared_dicts` 里 `Manager().dict()` 的代理，各进程看到同一份内存。
- 写：锁内 mutate `_data` → `set_all_update_flags`（把别的进程的 `storage_updated` 置 True，语义="有脏数据待落盘"）。
- 提交：锁内若 flag 为 True → 快照 `_data.copy()` → 线程池 `write_json` → `clear_all_update_flags`。
- 特点：**读即一致，无 reload**；文件只是持久化镜像（json_kv_impl.py:30 起 docstring）。
- 关键实现点：`get_by_id` 返回 **deep copy**（json_kv_impl.py:296），防调用方改嵌套结构污染存储行。

**协议 B：文件态 + update flag reload（NetworkX / NanoVectorDB / Faiss）**
- 进程内持有一份图/索引；跨进程只靠「原子文件写」+「flag 通知」。
- 写者提交（`index_done_callback`）：锁内 `atomic_write`（临时文件 + rename，读方永远看到完整文件）→ `set_all_update_flags` → **立刻复位自己的 flag**（防自我 reload）。
- 读者：任何操作先经单一瓶颈 `_get_graph()`（networkx_impl.py:326）/ `_get_client()`（nano_vector_db_impl.py:386）：锁内查 flag，为 True 则**整文件重载**。
- **没有增量同步 API，reload 必是全量**：整图重解析 GraphML、整库重读 JSON —— 这是大图场景的首要痛区。

`upload`-time 额外坑：NetworkX 的提交把 `self._graph` 交给 worker 线程序列化，事件循环上的协程可能并发改图 → 引入 `_commit_gate`（`asyncio.Event`，networkx_impl.py:297）在 `_get_graph` 返回前把关；gate 泄漏一次即 workspace 死锁（networkx_impl.py:999 的 finally）。

### 3.3 延迟 embedding + redo log（NanoVectorDB 的守护协议，write_seq 配套）

nano_vector_db_impl.py 的前置缓冲协议（docstring 69 起）：
- `upsert` **不调 embedding 模型**，只把带 `content` 的 `_PendingNanoDoc` 放进 `_pending_upserts`（按 id 覆盖旧的）；模型在 `_flush_pending_locked`（489）时**每个 id 只 embed 一次**。
- `delete` 同理进 `_pending_deletes`，flush 时一次 `client.delete` 合并——因为 `NanoVectorDB.delete` 每次全矩阵重建（`np.delete`），实体/关系合并阶段每关系一删会 O(边数) 全矩阵拷贝。
- 失败重放：flush 后先搬进 `self._unsaved_upserts` / `_unsaved_deletes`（redo log，未落盘），save 失败时保留；下次 commit/finalize 先 reload 别人提交的快照，再把 log 覆写回去（issue #3688）。
- **写序判定**：redo 重放时若同 id 已被别的写者提交过更行版本，不能覆盖。`__created_at__` 是整秒粒度会平票，故 `upsert` 用 `next_write_seq()`（write_seq.py:75）打 `__write_seq__` 纳米秒单调令牌，`row_is_strictly_newer`（write_seq.py:89）定胜负。
- `drop_pending_index_ops`（1015）在批次中止时只清 pending 缓冲，不清已落 client 的行（幂等重放兜底，见 AGENTS.md 失败重试语义）。

### 3.4 PG 后端的落库模型（全异步、无 flag）

PG 存储共享一个 `ClientManager` 连接池（单例，首 init 锁定配置，二次不同配置直接报错，postgres_impl.py:3072）。与文件态后端的关键差异：

- **无 update flag 协议**：写即 `INSERT ... ON CONFLICT (workspace, id) DO UPDATE`，其他 worker 立即可见（跨 worker 读可见性 > 文件态）。只有 `PGVectorStorage` 的 **pending 缓冲**是进程本地的。
- `PGVectorStorage.upsert`（4547）也走延迟 embedding：缓冲 `_pending_vector_docs` / `_pending_vector_deletes`；`_flush_pending_vector_ops`（4603）在 `_flush_lock` 下 embed + 单事务 flush，**成功则清缓冲，失败则缓冲原样保留下次重试**，缓存向量复用不再 embed。
- `index_done_callback` 对 PGKV/PGDocStatus 是空操作（<span>PG 即时持久化</span>，postgres_impl.py:3749）；对 PGVectorStorage 是「flush 缓冲 + 落库」。**注意 `index_done_callback` 返回值在各后端不一致**（NetworkX 返回 bool，PG 返回 None），调用方以异常为失败信号。

### 3.5 Postgres 表结构（问题 3 详细答案）

**KV / 向量 / 文档状态**：在 `postgres_impl.py` `TABLES`（9469）与 `SQL_TEMPLATES`（9639）集中定义，共 12 张表，名 → 用途：

| 表 | 作用 | 关键列 |
|---|---|---|
| `LIGHTRAG_DOC_FULL` | 全文文档 KV | `content TEXT`, `meta JSONB`, `content_hash TEXT`, `process_options` |
| `LIGHTRAG_DOC_CHUNKS` | 文本块 KV | `content`, `llm_cache_list JSONB`, `heading JSONB`, `sidecar JSONB` |
| `LIGHTRAG_LLM_CACHE` | LLM 应答缓存 KV | `original_prompt`, `return_value`, `cache_type` |
| `LIGHTRAG_FULL_ENTITIES`/`LIGHTRAG_FULL_RELATIONS` | 文档级写前恢复锚点 | `entity_names JSONB` / `relation_pairs JSONB`, `count` |
| `LIGHTRAG_ENTITY_CHUNKS`/`LIGHTRAG_RELATION_CHUNKS` | 实体/关系↔chunk 属主追踪 | `chunk_ids JSONB` |
| `LIGHTRAG_DOC_STATUS` | 文档状态调度表 | `status`, `chunks_list JSONB`, `metadata JSONB`, `content_hash TEXT`, `created_at`/`updated_at`（键集排序列） |
| `LIGHTRAG_VDB_CHUNKS`/`LIGHTRAG_VDB_ENTITY`/`LIGHTRAG_VDB_RELATION` | **向量表**：chunks/entities/relationships | `content_vector VECTOR(dimension)`、`full_doc_id` / `entity_name` / `source_id,target_id`、`chunk_ids VARCHAR(255)[]` |

要点：
- 向量**就在关系表里**，一列 `content_vector VECTOR(dim)`（pgvector 类型）；按（workspace, id）复合主键，`workspace` 承担数据隔离（因每实例一个 workspace 可传参）。
- **表名带 embedding 模型后缀**：`setup_table`（4112）+ `BaseVectorStorage._generate_collection_suffix`（base.py:275）生成 `LIGHTRAG_VDB_ENTITY_text_embedding_3_large_3072d` 之类；换 embedding 模型 = 新表 + 迁移，维度不一致抛 `DataMigrationError`（postgres_impl.py:4215）——**不自动清旧数据**。
- 向量检索 SQL：`chunks` / `entities` / `relationships` 模板（SQL_TEMPLATES:9869/9879/9888）用 `content_vector <=> $4`（余弦距离）过滤 + 排序 + LIMIT；`<=>` 运算符由 `configure_vector_extension`（888）注册的 pgvector codec 提供。
- HNSW 索引：`_create_vector_index`（2635）`USING hnsw (content_vector vector_cosine_ops)`，维度在 `ALTER TABLE ... TYPE VECTOR(dim)` 时固化；可配 HNSW_HALFVEC / IVFFLAT / VCHORDRQ（vchordrq 为 pgvecto.rs 需另装扩展）。
- KV upsert 模板（9740 起）用 `ON CONFLICT DO UPDATE SET ... COALESCE(NULLIF(EXCLUDED.x,''), 原值)`：**空串不覆盖已有元数据**，防止部分写把 `sidecar_location`/`parse_format` 等推进列抹掉。

**图**：`postgres_impl.py` 的 `PGGraphStorage`（AGE）与 `pgtable_impl.py` 的 `PGTableGraphStorage`（纯表）两条路线并存。

*路线 1 —— PGGraphStorage（Apache AGE）*：
- per-workspace 一个 AGE graph，label `base`（顶点）+ `DIRECTED`（边）；顶点属性 JSON 里存 `entity_id`，边属性存描述/weight/source_id 等。
- Cypher 通过 `SELECT * FROM cypher(graph, '...') AS (...)` 执行（postgres_impl.py:7708 等）；`initialize`（7276）建 label + 一堆索引（顶点实体 id 表达式索引、边 start/end 复合索引、GIN props 索引，7356–7367）。
- 顶点属性按 `entity_id` 表达式索引存取；`get_node_edges`（7695）用 `MATCH (n:base {entity_id:...})-[r]-()` 出/入向两次 UNWIND 查询。
- **缺点**：依赖 AGE 版本行为（graphid 无法 bigint、Cypher 无法与 join 混排、边写丢失检测 `_is_transient_graph_write_error` 7140 等一长串 workaround）；社区报告与图遍历退化时作者自己都绕到 Python 侧 BFS（`_bfs_subgraph` 8791）。

*路线 2 —— PGTableGraphStorage（纯关系表，推荐参考）*：
- 建表 `_DDL`（pgtable_impl.py:77）：
  - `lightrag_graph_nodes(workspace, namespace, id, properties JSONB, updated_at)`，`PK(workspace, namespace, id)`；
  - `lightrag_graph_edges(workspace, namespace, src_id, tgt_id, properties JSONB, updated_at)`，`PK(workspace, namespace, src_id, tgt_id)`；
  - **边存规范化顺序**：`src_id = min(a,b)`、`tgt_id = max(a,b)`（Python min/max，非 SQL LEAST/GREATEST——两种混用会在非 C 排序规则下对非 ASCII 偏离产生重复边）→ 无向边天然单行；
  - `idx_..._namespace_tgt`（174）补 tgt 侧索引；两个 FK → nodes（ON DELETE CASCADE，198–258）；孤儿边启动清理 DO 块；非规范顺序上的部分索引（190）。启动有 `pg_advisory_xact_lock` 串行建表迁移。
- **图遍历语义在 SQL 层的落点**（这才是 M4 最该抄的部分）：
  1. **1-hop 邻居**：`get_node_edges`（865）/ `get_nodes_edges_batch`（1430）用 `WHERE (src_id = $x OR tgt_id = $x)`；batch 时拆成 **UNION 两条分别走索引的臂**：`src_id = ANY($3)` 走主键前缀，`tgt_id = ANY($3)` 走 `_namespace_tgt` 索引——单一 `OR` 谓词无法用任一索引会退化为整表 seq scan（`_bfs_frontier` 内注释 1139–1144 的教训）。
  2. **度**：`node_degrees_batch`（1368）`SELECT id, COUNT(*) FROM (SELECT src_id AS id ... UNION ALL SELECT tgt_id AS id ...) GROUP BY id`；`node_degree`（891）同构；自环计两次。`edge_degree`（907）= 两端度数之和。
  3. **子图/`get_knowledge_graph`**：`*` 通配（1231）`degree DESC, id COLLATE "C" ASC` 排名取前 N（`COLLATE "C"` = Python 码点序）；精确种子走 `_bfs_frontier`（1078）**前沿受限迭代 BFS**：每层一个 SQL，含 UNWIND 邻居 UNION、`NOT EXISTS` 反连接去重（而非 `<> ALL` 标量，避免 O(邻居×已访问)）、`candidate_degrees` CTE 求度、`ORDER BY degree DESC, id COLLATE "C" ASC LIMIT 预算`——结果受 max_nodes 约束且每层按度名次截断，与 NetworkX 的度优先 BFS 语义对齐。
  4. **标签检索**：`get_popular_labels`（923）同上排名，但补足孤立点（无边的度 0 实体也进结果）；`search_labels`（959）+ `_search_score`（496）Python 侧打分（精确=1000 / 前缀=500 / 包含=100-len / 词界+50）。

> **问题 3 完整答案**：官方 Postgres 的图有两条实现——AGE（把图当图模型，Cypher）与纯表（JSONB 属性列的关系表，SQL）。对 M4「官方 Postgres+pgvector 一库通吃」，**PGTableGraphStorage 是唯一无扩展依赖、纯 SQL 可维护、且遍历语义与 NetworkX 对齐的模板**；向量在 `LIGHTRAG_VDB_*` 三张表的 `content_vector VECTOR` 列并配 HNSW 索引；local/global 所需的图访问模式（1-hop 邻居、度、批量属性取回、子图 BFS）在 SQL 层的实现见上。

### 3.6 查询数据流（local / global 各自需要存储提供什么）

`operate.py` 的查询主流程 `kg_query`（operate.py:4588）→ `_build_query_context`（5895）：

- **local**（`_get_node_data` 6024）：`entities_vdb.query(keywords)` 向量搜实体 → `get_nodes_batch` + `node_degrees_batch` 拿节点属性与**图度（rank）** → `get_nodes_edges_batch` 取**1-hop 邻边** → `get_edges_batch` + `edge_degrees_batch` 拿边属性与端度 → 按 `(rank=…, weight)` 排序。→ 你的 PG 适配器至少要在这两个 batch + 1-hop + 度上不出 1 对 N 轮询。
- **global**（`_get_edge_data` 6295）：`relationships_vdb.query(keywords)` 向量搜关系 → `get_edges_batch(pairs)` 按点对取边属性 → `get_nodes_batch` 取端点节点装饰。**不锚定实体、以关系为锚**，机制远轻于微软 GraphRAG 的社区报告。
- **naive**：纯 `chunks_vdb.query`；**mix**：local + global + `chunks_vdb.query` 合并（无 RRF，见 ARCHITECTURE.md §2.4 边界）。

→ 存储层对查询的供给契约 = 向量 Top-K 相似检索（`content_vector <=>`）+ 图的 1-hop 展开、度排名、批量点边取回。**图存储的 `get_knowledge_graph` 只服务图可视化接口，不参与检索热路径。**

### 3.7 写入数据流（入库）

`ainsert` → pipeline（`lightrag/pipeline.py` 的 `_PipelineMixin`）批量处理 → `operate.py` 的抽取/合并写三类存储（实体/关系 upsert 图+向量、chunk 进 KV+向量）→ 每批末尾 `index_done_callback` 提交 → `set_all_update_flags`。跨 worker 唤醒靠 `pipeline_ingress.py` 邮箱（doc 通道有界，溢出 coalesce 成 auto-rescan 脏标志，miss 由下次运行的初始扫描从 `doc_status` 重建）。

---

## 4. 输入输出接口（签名清单）

### 4.1 共同基类 `StorageNameSpace`（base.py:192）

```python
async def initialize(self)              # 初始化存储
async def finalize(self)                 # 释放资源（进程退出兜底 flush）
async def index_done_callback(self)      # 批处理提交点：把缓冲/内存态落到持久层
async def drop_pending_index_ops(self)   # 批次中止时丢弃未 flush 缓冲（默认 no-op）
async def drop(self) -> dict[str, str]   # 清空存储, 返回 {"status","message"}
```

### 4.2 `BaseVectorStorage`（base.py:302 起抽象方法）

| 方法 | 签名 | 语义 |
|---|---|---|
| `query` | `(query: str, top_k: int, query_embedding: list[float]=None) -> list[dict]` | 向量检索，返回带 `id`/`distance`/`created_at` 的记录 |
| `upsert` | `(data: dict[str, dict[str, Any]]) -> None` | 批量插/改（**文件态：内存缓冲，落盘在 index_done_callback**） |
| `delete_entity` | `(entity_name: str) -> None` | 删实体向量 |
| `delete_entity_relation` | `(entity_name: str) -> None` | 删入射/出射某实体的关系向量 |
| `get_by_id` | `(id: str) -> dict \| None` | 读你自己的写（缓冲优先） |
| `get_by_ids` | `(ids: list[str]) -> list[dict \| None]` | 批读，保序 |
| `delete` | `(ids: list[str]) -> None` | 批量删 |
| `get_vectors_by_ids` | `(ids: list[str]) -> dict[str, list[float]]` | 只取 id→向量（供重建/缓存） |

### 4.3 `BaseKVStorage`（base.py:435 起）

```python
get_by_id(id: str) -> dict | None
get_by_id_strict(id: str) -> dict | None   # 可选能力：出错抛、miss=确认不存在（gate 在 supports_strict_point_reads）
get_by_ids(ids: list[str]) -> list[dict | None]
filter_keys(keys: set[str]) -> set[str]    # 返回 keys 中【不存在】的子集（去重判定）
upsert(data: dict[str, dict]) -> None
delete(ids: list[str]) -> None
is_empty() -> bool
```

### 4.4 `BaseGraphStorage`（base.py:524 起）

单点：
```python
has_node(node_id) -> bool
has_edge(src, tgt) -> bool
node_degree(node_id) -> int
edge_degree(src_id, tgt_id) -> int        # = 两端度之和
get_node(node_id) -> dict | None          # 只回属性；不存在/出错需区分
get_edge(src_id, tgt_id) -> dict | None
get_node_edges(src) -> list[tuple[str,str]] | None  # []节点在无关系; None=确认不存在; raise=后端无法回答
upsert_node(node_id, node_data) -> None
upsert_edge(src_id, tgt_id, edge_data) -> None
delete_node(node_id) / remove_nodes(nodes) / remove_edges(edges) 
get_all_labels() -> list[str]
get_knowledge_graph(node_label, max_depth=3, max_nodes=1000) -> KnowledgeGraph
get_all_nodes() / get_all_edges() -> list[dict]
get_popular_labels(limit=300) -> list[str]   # 全节点按度排名，平局按标签升序，需含孤立点
search_labels(query, limit=50) -> list[str]
```

批量变体（基类默认逐个串行，后端可覆盖；**PG/AGE 全覆盖，是性能关键**）：
```python
get_nodes_batch(node_ids) -> dict[str, dict]
node_degrees_batch(node_ids) -> dict[str, int]
edge_degrees_batch(edge_pairs) -> dict[tuple, int]
get_edges_batch(pairs) -> dict[tuple, dict]
get_nodes_edges_batch(node_ids) -> dict[str, list[tuple]]
has_nodes_batch / upsert_nodes_batch / upsert_edges_batch
```

### 4.5 `DocStatusStorage`（base.py:1291 起，调度控制平面）

```python
get_status_counts() -> dict[str, int]
get_docs_by_statuses(statuses, strict=False) -> dict[str, DocProcessingStatus]
get_docs_by_track_id(track_id) -> dict[str, DocProcessingStatus]
get_docs_paginated(status_filter, status_filters, page, page_size, sort_field, sort_direction) -> (list, total)
get_all_status_counts() -> dict[str, int]
get_doc_by_file_path(file_path) -> dict | None
get_doc_by_file_basename(basename) -> (doc_id, doc_data) | None        # 按文件名去重
get_doc_by_content_hash(content_hash, *, exclude_doc_id=None) -> ...   # 内容哈希去重，必失败关闭+确定性最早行
get_docs_by_statuses_page(statuses, *, limit, position=CURSOR_START, strict=False) -> DocStatusPage  # 键集扫描关键路径
get_docs_by_ids(doc_ids, *, strict=False) -> dict[str, DocSchedulingRecord]
get_full_docs_by_ids(doc_ids, *, strict=False) -> dict[str, DocProcessingStatus]
count_docs_by_statuses(statuses, *, strict=True) -> int   # 准入控制用，base 默认抛 StorageCapabilityError
update_doc_status_fields(doc_id, fields, *, missing_ok=False) -> None  # 定向更新，created_at 不可改
resolve_doc_source_strict(canonical_source_key) -> SourceResolution       # Absent/Unique/Conflict
list_source_conflicts_page(*, limit, position) -> SourceConflictPage      # base 默认不支持
repair_source_conflict(key, *, primary_doc_id, expected_count, expected_fp, dry_run) -> Result  # CAS 修复
```

> 后端只需实现**抽象方法**；`required_methods`（`lightrag/kg/__init__.py:10/22/36/46`）只抽检最少的（KV 查 `get_by_id/upsert`，VECTOR 查 `query/upsert`，GRAPH 查 `upsert_node/upsert_edge`，DOC_STATUS 查 `get_docs_by_statuses`）。**自研 adapter 若想走 pipeline 完整路径，DocStatusStorage 的调度方法一个都不能省**（instantiable 即能力保证，无降级，base.py:1275 起）。

---

## 5. 「内建 vs 自研」边界（问题 4）

### 5.1 默认组合（问题 2）

`lightrag/lightrag.py:403/406/409/412` 硬编码默认值：

```
kv_storage          = "JsonKVStorage"
vector_storage      = "NanoVectorDBStorage"
graph_storage       = "NetworkXStorage"
doc_status_storage  = "JsonDocStatusStorage"
```

即 **JsonKV + NetworkX + NanoVectorDB（向量）+ JsonDocStatus**，四者全部零依赖内置文件态实现。与旧版的主要结构性变化（v1.5.7 相比早期三存储时代）：

1. **doc_status 从「一个特殊 KV namespace」独立成第四类抽象存储**（`DocStatusStorage`，base.py:1269），并新增一整套 bounded 调度 API（键集分页、严格读、源冲突三态解析）——支撑 pipeline 的受限内存扫描与 FAILED 恢复语义，这是旧版 JsonKV 一桶装的 doc_status 做不到的。
2. 向量写入从「即时 embed」改为**延迟 embed + redo log**（issue #2785 / #3688，见 3.3）——合并阶段的每关系删除不再全矩阵拷贝。
3. `index_done_callback` 从「内部触发」改为 pipeline 显式批提交点 + `drop_pending_index_ops` 中止钩子（base.py:210）。

### 5.2 切换入口（问题 4 答案）

- **构造时传参**（推荐、可编程切换）：`LightRAG(kv_storage="PGKVStorage", vector_storage="PGVectorStorage", graph_storage="PGTableGraphStorage", doc_status_storage="PGDocStatusStorage", workspace=...)`。
- **env**：各后端要求的环境变量见 `STORAGE_ENV_REQUIREMENTS`（kg/__init__.py:51）；初始化时 `check_storage_env_vars`（utils.py:5862）缺失即 `ValueError`。
- 解析链：`LightRAG.__post_init__`（lightrag.py:1321 起）先 `verify_storage_implementation` + `check_storage_env_vars`，再 `get_storage_class`（factory.py:17）→ 实例化 8 个 KV + 1 图 + 3 向量 + 1 doc_status 实例（lightrag.py:1430–1504）。
- PG 内核连接配置：`ClientManager.get_config`（postgres_impl.py:2884）**env 覆盖 config.ini**（`POSTGRES_HOST/PORT/USER/PASSWORD/DATABASE/WORKSPACE/MAX_CONNECTIONS/SSL_*`、向量索引 `POSTGRES_VECTOR_INDEX_TYPE` 默认 HNSW 等）。
- **「内建 vs 自研」的边界一句话**：检索模式（local/global/naive/hybrid/mix）与四类存储抽象**内建**；RRF 融合、reranker 精排、关键词路（sparse/BM25）、NebulaGraph/新后端 adapter**自研**（对照 ARCHITECTURE.md §2.4/§2.5）。存储侧「自研」= 实现四类抽象的一个新后端类并在 `STORAGES` 注册，或直接复用官方 PG 三件套 + PGTableGraphStorage。

---

## 6. 已知坑（选型与运行时必读）

1. **默认组合全程内存态**：Nano/NetworkX 进程内驻留 + 全量文件 reload；进程崩溃丢未落盘数据；大图 GraphML 整文件序列化耗时且 `NanoVectorDB.save` 的 base64 编码占 GIL（nano_vector_db_impl.py:730 注释明言 400MB 矩阵会让事件循环阻塞）。MVP 跑通可以，**准生产必须换 PG**（ARCHITECTURE.md §2.5 已注明）。
2. **写放大**：文件态后端每次 `index_done_callback` 重写整个文件/图（无增量同步）。批量小、批次多时 IO 成本线性放大。
3. **单写者不变量**：pipeline 外的 admin 写入路径（实体/关系编辑、`delete_entity*`、`drop`）**不在 pipeline gate 内**，调用方自己担单写者序列化（networkx_impl.py:206 起「Non-pipeline write paths」）；`drop` 必须持有 pipeline `busy` 预留（API 层 `/documents/clear` 已如此）。协议 A 的 `Manager().dict()` 跨进程 mutate 需全程持锁，读也要锁（json_kv_impl.py:88 起）。
4. **`index_done_callback` 失败被吞则数据丢失**：NetworkX/Nano/Faiss 的异常必须向上抛（`_insert_done` 只认异常），调用方吞掉会让文档标 PROCESSED 而图未落盘（networkx_impl.py:991 注释）。
5. **跨 worker 读可见性**：PGVector/OpenSearch 等缓冲写只在写入进程内可见，其他 worker 要 `index_done_callback` 后才读得到（base.py:326 的 multi-worker note）。
6. **换 embedding 模型 = 数据失效**：向量存放在「模型名+维度」后缀的表/集合里；Nano 换模型必须清目录，PG 有维度检查（`DataMigrationError`）但**不自动清老数据**，需手动清理或迁移。
7. **cosine_better_than_threshold 是必填**：NanoVectorDB `__post_init__` 要求 `vector_db_storage_cls_kwargs` 里显式给（nano_vector_db_impl.py:289 会 `ValueError`）；`LightRAG` 已默认注入该阈值（lightrag.py:1335）。
8. **AGE 的坑**（若选 PGGraphStorage）：版本驱动行为差异（graphid 不可 cast bigint、Cypher 构造函数不能参与 SQL join、并发边写丢失与死锁重试、边写丢失需要 `_is_transient_graph_write_error` 检测恢复）；启动要为 graph/label/索引写一堆防重入判断。**纯表的 PGTableGraphStorage 完全规避这些**。
9. **PG 向量索引选型**：HNSW（默认）不适合随后大量删改的库（需重建）；IVFFLAT 建库后第一二次查询差、需先灌数据再加索引；真实维度与 `VECTOR(dim)` 不匹配的 INSERT 直接报错。索引想在 `create index concurrently` 下建注意服务端空闲连接配置。
10. **批量阈值**：`_chunk_by_budget`（postgres_impl.py:236）按字节预算切批；PG 的连接级重试 `_run_with_retry` 只解决连接层，查询层死锁/序列化冲突需背靠 `_is_transient_error` 类自兜（pgtable_impl.py:44）。
11. **增量写入并发**：pipeline 并发契约（见 AGENTS.md《Pipeline concurrency contract》）——`busy`（处理中，不挡 enqueue）、`destructive_busy`（clear/delete 独占，挡一切写）、`scanning`/`scanning_exclusive`、`pending_enqueues`；`clear_documents` 同时删表 + input 文件，若与正在入库的写并发会静默丢文档。**M4 换 PG 后这些锁语义不变，仍由 `shared_storage` 提供**。
12. **文档级清理的追踪表不能省**：`LIGHTRAG_ENTITY_CHUNKS` / `LIGHTRAG_RELATION_CHUNKS`（实体/关系→chunk 属主）与 `LIGHTRAG_FULL_ENTITIES`/`FULL_RELATIONS`（写前恢复锚点）是 purge/fail-closed 契约的地基；自研 adapter 若跳过它们，文档级删除会留下孤儿实体（AGENTS.md §Purge recovery contract）。

---

## 7. 我们项目的采用建议（M4 落地）

针对 ARCHITECTURE.md §2.5「Postgres + pgvector 一库通吃」目标，结论如下：

1. **组合**（一旦切准生产，推荐）：
   ```
   kv_storage="PGKVStorage"  vector_storage="PGVectorStorage"
   graph_storage="PGTableGraphStorage"  doc_status_storage="PGDocStatusStorage"
   ```
   这正好是官方 PG 后端的完整组合，**图选纯表（PGTableGraphStorage）而非 AGE（PGGraphStorage）**：规避 AGE 全部坑，图遍历 SQL 已与 NetworkX 语义对齐（度优先 BFS、1-hop UNION 索引查询、`COLLATE "C"` 平局序），并且不需要额外安装扩展。唯一依赖是 pgvector（普通 `CREATE EXTENSION vector`）。
2. **MVP 阶段**：先用默认四件套跑通（零依赖、便于对比调试），M4 落地时**只改一行构造参数**即可切换——这正是「存储做成可切换接口」的收益。两套可以同时练，`workspace` 做数据隔离。
3. **抽象的复用边界**：保留 `Base*Storage` 抽象与 `namespace.py` 命名空间不变；自研（NebulaGraph adapter 等）只需实现抽象方法 + 注册 `STORAGES`。**凡走 pipeline 完整路径，DocStatusStorage 的纯 SQL 版雷打不动**（调度 API 不可省、不可降级）。批量变体务必实现（over 默认串行接口），查询热路径就是 `get_nodes_batch` / `node_degrees_batch` / `get_nodes_edges_batch` / `get_edges_batch`/`edge_degrees_batch`。
4. **直接可抄的 SQL 资产**（pgtable_impl.py 内）：`lightrag_graph_nodes/edges` 建表 DDL（77）+ 规范化边 + FK 级联 + `_namespace_tgt` 索引；1-hop UNION 邻居（1153）；度统计（1368）；前沿受限 BFS（1078）；热度/搜索标签 SQL（923/959）。向量侧抄 `postgres_impl.py` 的 `TABLES`/`SQL_TEMPLATES`/`_create_vector_index`（HNSW + `<=>` 余弦）。
5. **运维配套**：embedding 模型选 `bge-m3`（1024 维）后锁定，换模型清 `LIGHTRAG_VDB_*_<suffix>` 表；开 `ENABLE_LLM_CACHE` 减抽取成本；增删改索引前确认 `POSTGRES_VECTOR_INDEX_TYPE`（默认 HNSW，删改重可临时改 IVFFLAT）。`storage_state` 类调度问题（FAILED 重试、purge 恢复锚点）在 M4 换库后**契约不变**，回归跑 `tests/pipeline/`。
6. **不建议**在 M4 引入 Milvus/Qdrant 做向量：pgvector HNSW 对「百万–数千万」级个人规模足够，且少一个服务要运维；数据量真上万级再评估（ARCHITECTURE.md §2.5 已给边界）。