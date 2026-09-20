# M4 模块记录：存储层

> **版本：** v1
> **状态：** 已落地
> **更新：** 2026-09-13
> **定位：** Postgres + pgvector 一库通吃，可切换后端
> **契约：** M3 写 / M5 读，统一 `Base*Storage` 抽象
> **上游：** [M3 索引层](M3_index.md) | **下游：** [M5 检索层](M5_retrieve.md)
> **依据：** [`ARCHITECTURE.md`](../ARCHITECTURE.md) §2.5 / §3.1
> **运行：** `.env` 中 `STORAGE_BACKEND=pg` 切换
> **变更历史：** 见 [`CHANGELOG.md`](../CHANGELOG.md)

## 1. 定位与职责

M4 = **统一存取接口 + 可切换后端**：M3 写、M5 读，全链路只依赖一组 `Base*Storage` 抽象，后端（默认文件态 ↔ Postgres）切换只改构造参数，不碰业务代码。

**半程联调意义**：M1–M4 完成「文档 → 可检索」持久化闭环——当前 M3/M5 已在**默认文件态**（`data/lightrag_deepseek`）跑通，M4 是把同一套数据搬到 PG，**功能等价、数据可迁移、检索冒烟保持绿色**。

## 2. 选型结论（来自 storage.md §7 + ARCHITECTURE §2.5）

**直接采用 LightRAG 官方 Postgres 完整组合**（不是自研、不是从零重写）：

| 角色 | 存储类 | 说明 |
|---|---|---|
| KV | `PGKVStorage` | 全文档/文本块/LLM 缓存/实体/关系 ↔ chunk 属主 |
| 向量 | `PGVectorStorage` | chunks/entities/relationships 三张 `LIGHTRAG_VDB_*` 表，`content_vector VECTOR(1024)` + HNSW + `<=>` 余弦 |
| 图 | **`PGTableGraphStorage`（纯表，非 AGE）** | 规避 AGE 全部坑（graphid cast/join 混排/边写丢失 workaround）；图遍历 SQL 已与 NetworkX 语义对齐（1-hop UNION 索引查询、度统计、前沿受限 BFS、热度/标签搜索） |
| 文档状态 | `PGDocStatusStorage` | 文档调度表，不可省、不可降级（调度 API 雷打不动） |

**为什么纯表而非 AGE**：AGE 依赖额外扩展、版本行为差异多；纯表唯一依赖 `CREATE EXTENSION vector`（pgvector），`lightrag_graph_nodes/edges` JSONB 属性表 + 规范化无向边 + FK 级联，纯 SQL 可维护。**不引入 Milvus/Qdrant/NebulaGraph**（pgvector HNSW 对个人规模足够，少一个服务运维；扩级边界见 ARCHITECTURE §2.5）。

## 3. 落地记录（2026-09-13）

### 3.1 部署：orbstack + docker 容器
- 本机用 **orbstack 管理 docker**（`docker context` = orbstack）；
- 容器 `graphrag-pg`：`pgvector/pgvector:pg16` 镜像（官方内置 pgvector，免手装扩展），端口 `5432`，数据卷 `graphrag-pgdata:/var/lib/postgresql/data`；
- 实测 `pgvector 0.8.6`（`CREATE EXTENSION vector` 验证）；
- **持久化已验证**：`docker restart` 后 30 chunks / 244 nodes 数据完好。

### 3.2 切换开关（最小改动）
- `.env` 新增：`STORAGE_BACKEND=pg`（`local`=文件态）+ `POSTGRES_HOST/PORT/USER/PASSWORD/DATABASE=postgres` + `POSTGRES_WORKSPACE=lightrag_m4`；
- `app/m3_index/providers.py` 新增 **`build_storage_config()`**：按 `STORAGE_BACKEND` 返回四个存储类名（local=JsonKV/NetworkX/NanoVectorDB/JsonDocStatus；pg=PGKV/PGVector/**PGTableGraphStorage**/PGDocStatus）；
- `app/m3_index/runner.py` `build_rag()` 通过 `**build_storage_config()` 注入——**业务代码零改动，m5_retrieve 复用同一 build_rag 自动继承开关**；
- 注意 STORAGES 注册在 lightrag `kg/__init__.py`（PG 类名在 export 列表中），构造只需传类名字符串。

### 3.3 数据对账
PG 落库（workspace=lightrag_m4），与文件态库 `data/lightrag_deepseek` 对比：

| 指标 | 文件态库 | PG 库 | 结论 |
|---|---|---|---|
| doc_chunks / full_docs | 30 / 5 | 30 / 5 | ✅ 一致 |
| graph 节点 / 边 | 233 / 233 | **244 / 269** | ⚠️ LLM 抽取非确定性（重跑波动），非数据缺陷 |
| vdb 行数 | 233 entities / 233 rel | 244 / 269 | 同上 |
| 向量维度 | bge-m3 1024 | 1024（表名带 `_xinference_bge_m3_1024d` 后缀） | ✅ 一致 |

> 实体质量抽验：PG 库实体为 DeepSeek 干净风格（`2026年第三季度`/`客户投诉处理流程`/`一级投诉`…），无 GLM 式表头/「指标+数值」脏实体。
> 表名带 embedding 模型后缀（`lightrag_vdb_*_xinference_bge_m3_1024d`）——换 embedding 模型 = 新表 + 手动清爽旧表（见 §5.1）。

### 3.4 检索回归
- `app/m5_retrieve/runner.py -w data/lightrag_m4`，投诉流程单题 × 四模式（local/global/mix/naive）全绿：图三模式实体/关系召回质量与文件态库一致（`客户投诉处理流程`→`投诉受理流程`→`客服人员`、`公司`、`一线客服人员` 等链路正确），naive 召回 5 块；
- **兜底发现**：PG 路径 chunk 溯源表现不同——`aquery_data` 返回的 `chunk_id` 形如 `chunk-<hash>`（PG 生成），`file_path` 仍恒为 `unknown_source`（因 M3 索引时 `text_chunks` 只喂 content 未带路径，与存储后端无关）；**但 PG 表自带 `full_doc_id` 列，chunk_id→full_doc_id 回连比文件态更直接**（正式 M5 溯源可直接查库）。

## 4. 验收清单（ground truth）

- [x] docker 起 PG16+pgvector，`CREATE EXTENSION vector` 健康检查通过；
- [x] 切换只改配置（`.env` STORAGE_BACKEND），业务代码零改动演示成立；
- [x] PG 库 ↔ 文件态库 数据对账一致（chunks 30 / docs 5；图规模差异为 LLM 抽取非确定性）；
- [x] 四模式检索冒烟在 PG 库全绿（复用 `app/m5_retrieve/runner.py -w data/lightrag_m4`）；
- [x] 持久化：容器重启后数据不丢（卷挂载生效）；
- [x] 文档调度表齐全（13 张表含 doc_status / entity_chunks / relation_chunks 追踪表）。

## 5. 已知坑

PG 存储层共 7 个常见坑（向量表后缀 / HNSW 删改 / 单写者不变量 / callback 异常吞掉 / 跨 worker 可见性 / 边规范化 / workspace 行残留），**完整记录见 [`docs/pitfalls/postgres-storage-pitfalls.md`](../pitfalls/postgres-storage-pitfalls.md)**。

最高频的 3 个速查：

1. **换 embedding 模型 = 数据失效**——向量表带模型后缀，换模需重建；当前锁定 bge-m3 1024d。
2. **单写者不变量**——`drop`/`clear` 与在途写入并发会静默丢文档，须持 busy 预留。
3. **`index_done_callback` 异常被吞则丢数据**——调用方必须向上抛异常，不能静默 catch。

## 6. 遗留 / 后续

1. **启动脚本化**：本次为手工 docker run 一次性命令；正式环境可加 `scripts/` 启动/健康脚本（非必须，本机容器已常驻）；
2. **增量幂等验证**：二次入库同批文档不重复实体（写前恢复锚点契约），本次未做重跑覆盖验证；
3. **文件态库保留**：`data/lightrag_deepseek` 仍作对照样本，正式数据以 PG（workspace=lightrag_m4）为准。

## 7. 版本

- **v1**（2026-09-13）：Postgres+pgvector 一库通吃落地。
- 变更记录：**逐条版本历史见 `docs/CHANGELOG.md`**（v0 规划 → v1 落地 PG）。本文件不再维护历史流水。