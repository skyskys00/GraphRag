# 坑合集：bge-m3 + Xinference 配置注意事项

> **分类：** Embedding
> **严重度：** 中（配置错了直接报错或检索质量下降）
> **首次整理：** 2026-09-13（M5 检索层 keyword 路 + rerank 接入时）
> **涉及模块：** M3 索引层 / M5 检索层

---

## 坑 1：bge-m3 实例必须以 `return_sparse=true` 启动

**现象：** sparse 编码请求报错，或返回的 lexical_weights 为空。

**根因：** `return_sparse` 是 **flag 引擎的加载参数**（`FlagEmbeddingModel.__init__` → `BGEM3FlagModel(return_sparse=...)`），不是请求参数。启动时没开的话，运行时怎么传都没用。

**Xinference 启动命令：**

```bash
# 正确：启动模型时指定 return_sparse=true
xinference launch -n bge-m3 -t embedding --model-format flag -f pytorch -c 1 \
  --model-args '{"return_sparse": true}'

# 错误：只启动默认 dense 模式
# xinference launch -n bge-m3 -t embedding
```

**同一实例的两路输出：**
- 不传 `return_sparse` → dense 1024d（M3 dense 通路不变）
- 传 `return_sparse=true` → sparse `{token_id: score}`（M5 keyword 路用）

---

## 坑 2：sparse 索引是离线构建，不自动增量

**现象：** 新增文档后 keyword 路检索查不到新文档的内容。

**根因：** M5 的 sparse 索引（`m5_sparse.json`）是一次性全量构建后落盘的，存在就复用，不会自动增量。

**应对：** M7 上传管线的 `build_workspace_deps` 在文档入库/软删后**自动重建并重载** sparse 索引（v1.6 已解决）。手动跑 CLI 时如果改了文档，记得删 `m5_sparse.json` 让它重建。

---

## 坑 3：bge-reranker-v2-m3 走 `/v1/rerank` 接口

**现象：** 不知道 reranker 怎么调用，或以为要走 embedding 接口。

**根因：** Xinference 对 reranker 模型有独立的 `/v1/rerank` API，不是 embedding 接口的变种。

**调用方式：** `app/m5_retrieve/rerank.py` 封装了 Xinference rerank 调用，传入 query + candidate 列表，返回带 score 的排序结果。

---

## 坑 4：文件态库没有 keyword 路

**现象：** 用文件态 workspace（`data/lightrag_deepseek`）跑 `--formal` 报 PG 相关错误。

**根因：** keyword 路的数据源直接从 PG 的 `lightrag_doc_chunks` 表读全量构建。文件态库不走 PG，所以没有 keyword 路。

**应对：** 文件态库用默认四模式（local/global/mix/naive）即可，不走 `--formal`。PG 是正式库。

---

## 关联

- M5 检索层 §5 已知坑的第 1/2/3/6 条 = 对应坑 1/2/表后缀/坑 4
- M3 索引层的 embedding 配置 → 坑 1（同一模型实例）
