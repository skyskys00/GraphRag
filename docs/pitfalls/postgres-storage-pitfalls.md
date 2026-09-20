# 坑合集：Postgres 存储层 7 个常见坑

> **分类：** 存储
> **严重度：** 中–高（从数据丢失到性能退化不等）
> **首次整理：** 2026-09-13（M4 存储层落地时）
> **涉及模块：** M4 存储层 / M5 检索层 / M7 交互层

---

## 坑 1：换 embedding 模型 = 数据失效

**现象：** 切换 embedding 模型后查询报 `DataMigrationError`，或向量表乱掉。

**根因：** LightRAG 的 PG 向量表名带模型名+维度后缀（如 `LIGHTRAG_VDB_CHUNKS_xinference_bge_m3_1024d`），换模型 = 新表 = 旧数据不可用。

**注意：** 不是"迁移一下就行"，维度和向量空间完全不同，只能重建索引。

**应对：** M4 锁定 bge-m3 1024d，不混模型。真要换模型需手动清理旧向量表 + 全量重索引。

---

## 坑 2：HNSW 不适合频繁删改

**现象：** 大量删除+写入后向量查询变慢，或索引膨胀。

**根因：** HNSW 索引是为读多写少设计的，频繁删改会导致索引结构退化。

**应对：** 删改重场景可临时切 IVFFLAT（`POSTGRES_VECTOR_INDEX_TYPE` 环境变量）。当前项目文档量小，HNSW 没问题。

---

## 坑 3：单写者不变量

**现象：** 并发写入后部分文档"消失"，或图数据不一致。

**根因：** LightRAG 的 pipeline 写入假设单写者。pipeline 外的 admin 写路径（`drop`/`clear`）与在途写入并发会静默丢文档。

**应对：** `drop`/`clear` 须持 `busy`/`destructive_busy` 预留。M7 上传管线已经按串行调度设计。

---

## 坑 4：index_done_callback 异常被吞则丢数据

**现象：** 文档状态标为 PROCESSED，但图/向量里没有对应数据。

**根因：** 索引完成回调 `index_done_callback` 如果抛出异常且被调用方吞掉，文档状态仍会标记为 PROCESSED，但实际存储层写入失败。

**应对：** 调用方必须把存储层异常向上抛，不能静默 catch。M3 runner 已正确处理。

---

## 坑 5：跨 worker 读可见性

**现象：** worker A 写入的文档，worker B 立刻查询查不到。

**根因：** 缓冲写只在写入进程内可见，其他 worker 要等 `index_done_callback` 完成后才读到。

**应对：** 单进程场景没问题；多 worker 部署时注意写入后的查询延迟。当前项目单进程，无影响。

---

## 坑 6：纯表边规范化用 Python min/max，而非 SQL LEAST/GREATEST

**现象：** 同一条无向边被存了两次（A→B 和 B→A 各一条），导致重复。

**根因：** PGTableGraphStorage 用 Python 的 `min/max` 规范化无向边（保证 A<B 统一存为 source=A target=B）。如果某处用了 SQL 的 `LEAST/GREATEST`，在非 C 排序规则下对非 ASCII 字符的排序结果可能和 Python 不一致，产生重复边。

**应对：** 保持两种实现不混用。本项目纯表图存储全部走 Python 规范化路径。

---

## 坑 7：表名带 workspace 维度，清库注意

**现象：** 删除一个 collection 的数据后，PG 里还残留该 workspace 的行。

**根因：** 所有 PG 表都带 `workspace` 列，行级隔离。删除 collection 时如果只删了文件没清 PG 行，数据残留。

**应对：** 删除 collection 时 best-effort 执行 `DELETE FROM lightrag_* WHERE workspace=$1`。残留不报错（不读就不影响），但要知道有这件事。

---

## 关联

- M5 检索层 §5.3 的「PG 表名带 embedding 后缀」= 坑 1，是同一个问题
- M7 交互层 collection 删除逻辑 → 坑 7
