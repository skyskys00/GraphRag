# M5 模块记录：检索层

> **版本：** v1.13
> **状态：** 已落地
> **更新：** 2026-09-24
> **定位：** 三路召回（graph/vector/keyword）+ RRF(k=60) + bge-reranker + 五特征融合排序 + 表格 NL 摘要 + query 预处理 + 白名单过滤
> **契约：** query + 模式 → 精排后 chunk 列表（full_doc_id / score / snippet）
> **上游：** [M3 索引层](M3_index.md) / [M4 存储层](M4_storage.md) | **下游：** [M6 生成层](M6_generate.md)
> **依据：** [`ARCHITECTURE.md`](../ARCHITECTURE.md) §2.6 ｜ [`FRAMEWORK_NOTES.md`](../FRAMEWORK_NOTES.md) §3
> **运行：** `cd backend && python -m app.m5_retrieve.runner -q "问题" -m mix`
> **变更历史：** 见 [`CHANGELOG.md`](../CHANGELOG.md)

## 1. 定位

M5 = **多源召回 + 融合 + 精排 + 溯源**，位于 LightRAG 外层自研（框架本身只有 local/global/mix/naive 四模式，无 RRF、无关键词路、无内置 rerank 模型接入）。输出是结构化 chunk 列表，供 M6 生成层/引用标注层消费。

## 2. 架构与实现

### 2.1 三路召回

| 路 | 实现 | 来源 |
|---|---|---|
| **graph**（图召回） | `aquery_data(mode='mix', enable_rerank=False)` → 取 chunks 列表（20 块），位次为 LightRAG round-robin 排序 | LightRAG 图检索（PGTableGraphStorage 1-hop + 度+前沿） |
| **vector**（密集向量） | `aquery_data(mode='naive', enable_rerank=False)` → 取 chunks 列表（20 块），位次为 bge-m3 余弦相似度 | LightRAG PGVectorStorage（HNSW + `<=>`） |
| **keyword**（稀疏） | bge-m3 sparse 内积排序，top 40。索引从 PG `lightrag_doc_chunks` 直接读全量，分批 sparse 编码，落盘 `m5_sparse.json` | Xinference bge-m3 flag 引擎（**return_sparse=true 重载**，同实例 dense 不受影响） |

**bge-m3 sparse 重载要点**：
- `return_sparse` 是 **flag 引擎加载参数**（`FlagEmbeddingModel.__init__` → `BGEM3FlagModel(return_sparse=...)`），不是请求参数；请求层只是二选一地返回 dense 或 sparse。
- 2026-09-13 已将 Xinference 中 bge-m3 以 `return_sparse=true` 重启，CPU flag 引擎。同一实例：不传 return_sparse → dense 1024d（M3 dense 通路不变）；传 `return_sparse=true` → sparse `{token_id:score}`。
- 降级方案（keyword 路不可用时）：走 jieba+BM25；本次未引入 jieba，bge-m3 sparse 已够用。

### 2.2 RRF 融合

`_rrf(ranked_lists, k=60)` —— 三路位次独立，按 `1/(k+rank)` 累加后降序，top 40 进精排。k=60 是社区常用值；三路权重等同（个人规模不用加权）。

### 2.3 精排（bge-reranker-v2-m3）

走 Xinference `/v1/rerank`，候选 = RRF top 40 的 content，返回 top 8（`app/m5_retrieve/rerank.py`）。内部 LightRAG 自带 `enable_rerank=True` 空转（因为没配模型），三路召回调用显式传 `enable_rerank=False` 关掉，避免日志噪音。

### 2.4 引用溯源（chunk → full_doc_id）

PG 路径最直接：`lightrag_doc_chunks` 自带 `full_doc_id` 列，sparse 索引构建时一并带出，结果里每个 chunk 都有 `full_doc_id`。**比文件态 unknown_source 干净得多**（文件态需从 vdb payload 取，正式库不绕那个弯路）。

### 2.5 代码结构

| 文件 | 职责 |
|---|---|
| `app/m5_retrieve/sparse_index.py` | 关键词路：读 PG doc_chunks → sparse 编码 → 落盘 `m5_sparse.json`；score() 做内积排序 |
| `app/m5_retrieve/rerank.py` | Xinference /v1/rerank 封装（bge-reranker-v2-m3） |
| `app/m5_retrieve/retriever.py` | 编排：三路召回 → RRF → rerank → 溯源元数据，返回结构化结果 |
| `app/m5_retrieve/query_preprocess.py` | query 预处理（v1.5）：A 同义词扩展 + B 专名识别加权（见 §7） |
| `app/m5_retrieve/runner.py` | CLI：`--formal` 走正式链路（`-w data/default_ws --formal`）；默认仍保留 v0.1 四模式冒烟 |

## 3. 复现命令

```bash
# 工作根 = backend/（2026-09-14 前后端重排：app/ data/ inputs/ 等移入 backend/，先 cd 再执行）
cd backend
# 正式链路（PG 库，自动构建稀疏索引）
python -m app.m5_retrieve.runner -w data/default_ws --formal
# 单题
python -m app.m5_retrieve.runner -w data/default_ws --formal -q "客户投诉的处理流程"

# 仍可回退到 v0.1 冒烟（验证 LightRAG 内部召回）
python -m app.m5_retrieve.runner -w data/default_ws -m mix
```

## 4. 实测结论

- **结果导向结论**：v1 四题实测（智能客服 / 季度销售 / 投诉流程 / 迭代会议决定）高相关片段全进 top8，**正相关 score 0.6~0.9、负相关长尾 0.001~0.05，rerank 区分度显著**；keyword 路 30 chunks 全参与打分，对图/向量双路都漏掉的术语型查询兜底。
- **v1.5 A/B 实测**（6 道缩写/口语 query）：缩写**有效**（「PRD V2.1」→扩展为「产品需求文档」挤出投诉 SOP 噪音）、标准缩写**中性**（bge-m3 本身已召回，不劣化）、纯口语**无提升**（需 C 查询改写，暂不做）；预处理纯规则、延迟 +<200ms。
- 三路召回规模正常（graph 20 / vector 20 / keyword 30）。**实测明细表与溯源见 `docs/CHANGELOG.md` v1.0 M5 条目，本文不复述。**

## 5. 已知坑 / 注意事项

bge-m3 + Xinference 相关配置坑（return_sparse 启动参数 / sparse 索引离线构建 / reranker 接口 / 文件态库限制），**完整记录见 [`docs/pitfalls/bge-m3-xinference.md`](../pitfalls/bge-m3-xinference.md)**。
PG 存储相关坑（向量表名后缀等）**见 [`docs/pitfalls/postgres-storage-pitfalls.md`](../pitfalls/postgres-storage-pitfalls.md)**。

速查清单：

1. **bge-m3 实例必须 `return_sparse=true` 启动**——否则 sparse 请求报错；当前已重载好。
2. **sparse 索引随上传/删除自动重建**（M7 `build_workspace_deps`），无需手动。
3. **PG 向量表名带模型后缀**——换模=新表，同 M4 坑 1。
4. **纯口语 query 预处理无提升**——需 C 查询改写（LLM），暂不做。
5. **keyword 路用 bge-m3 sparse**——没走 jieba/BM25；需领域分词定制再加。
6. **文件态库无 keyword 路**——用默认四模式，不走 `--formal`。

## 6. 验收清单（v1）

- [x] 关键词路（bge-m3 sparse）：30 chunks 索引构建成功，单 query 打分可用；
- [x] RRF(k=60) 三路融合：graph + vector + keyword → top 40；
- [x] bge-reranker-v2-m3 精排：Xinference /v1/rerank 接入，top 8 输出；
- [x] 引用溯源：chunk → full_doc_id 直接从 PG 表带出；
- [x] 四题端到端跑通，rerank 分数区分度显著（0.9+ vs 0.0x）；
- [x] 关掉 LightRAG 内部空转 rerank 日志噪音；
- [x] query 预处理 A/B 接入三路召回（v1.5，见 §7），C 查询改写暂不做。

## 7. v1.5 附记：query 预处理（落地摘要）

**动机**：中文场景用户口语/缩写（「客服」「PRD」「华东」）与库内标准实体名（「客户服务」「产品需求文档」「华东大区」）对不齐，而检索第一步是 keyword → 实体向量 top_k，同义/缩写差得远进不了候选集，图遍历再强也没用。

**落地两小件（纯规则，不引入新模型）**：
- **A. 同义词扩展**：领域词典（`synonyms.yaml`）+ 从 PG 图存储拿全量实体名反向模糊匹配（子串 + 编辑距离），扩展词追加进 query；控过扩展：编辑距离 ≤2 或子串匹配、扩展词上限 5 个。
- **B. 专名识别加权**：正则 + 实体名列表（日期 / 编号 / 等级 / 机构 / 产品名），输出 `weighted_terms` 注入 keyword 路加权、`ll_keywords` 注入图检索。

**接口**：`preprocess_query(query, rag) -> PreprocessedQuery{original, expanded, hl_keywords, ll_keywords, weighted_terms, rewritten}`，接入点在 `retriever.py retrieve()` 前。graph 路用原 query + ll_keywords，vector 路用 expanded，keyword 路用 expanded + weighted_terms。

**取舍**：不上 NER 模型（个人项目规则+词典够用）；词典量小（几十条，从现有文档实体归纳）。**AB 实测结论汇总见 §4 与 CHANGELOG。**

## 8. 遗留 / 后续

1. **query 预处理 C：查询改写（LLM）**：口语类 query 实测无提升（见 §7），需 LLM 改写为检索友好表述；暂不做。底层可再补 jieba 分词 + 同义词词林。
2. **增量索引** → 已解决（v1.6）：sparse 全量重建挪到 M7 `documents.build_workspace_deps`，上传/删除后自动执行（当前文档量下全量成本可接受，不引入增量维护）；
3. **文件态 keyword 路**：如需在本地文件态库也跑正式链路，要额外读 kv_store_text_chunks 建 sparse 索引（非必须，PG 是正式库）；
4. **溯源到页/段落级**：目前只到 full_doc_id，M6 引用标注需要更细粒度（M2 块有 anchor/page_range，M4 里 chunk 表暂未带出，后续可接入）。

## 9. 版本

- **v1.10**（2026-09-21）：rerank 同步阻塞事件循环修复——`rerank()` 调用包 `asyncio.to_thread`（单 worker 下同步 urllib 占死事件循环，并发切会话请求被排队），详见 CHANGELOG v5.4。
- **v1.6**（2026-09-14）：软删过滤 + sparse 联动重建。
- **v1.9**（2026-09-21）：rerank 排序 bug 修复（按 relevance_score 降序，而非输入 index），详见 CHANGELOG v5.3。
- **v1.8**（2026-09-21）：复合专名整体加权 + 泛化子串抑制（Bug4 专名检索污染，详见 CHANGELOG v5.1）。
- 变更记录：**逐条版本历史与实测见 `docs/CHANGELOG.md`**（v0.1 冒烟 → v1 三路 RRF+rerank+溯源 → v1.5 query 预处理 A/B → v1.6 软删过滤）。本文件不再维护历史流水与实测明细。