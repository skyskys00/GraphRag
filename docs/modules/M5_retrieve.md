# M5 模块记录：检索层 v1（三路 RRF + rerank + 溯源）

> 状态：**正式落地**（2026-09-13，PG 库 lightrag_m4，四题全绿。v0.1 冒烟基线保留作对比）。
> 契约：query 输入 → 精排后 chunk 列表（带 full_doc_id / score / 来源）。
> 依据：`docs/FRAMEWORK_NOTES.md` §5.6/§5.11 ｜ `docs/ARCHITECTURE.md` §3.2
> 前置：M3 索引（PG，bge-m3 dense 1024d）+ M4 存储切换 + Xinference bge-m3/bge-reranker-v2-m3

## 1. 定位

M5 = **多源召回 + 融合 + 精排 + 溯源**，位于 LightRAG 外层自研（框架本身只有 local/global/mix/naive 四模式，无 RRF、无关键词路、无内置 rerank 模型接入）。输出是结构化 chunk 列表，供 M6 生成层/引用标注层消费。

## 2. 架构与实现

### 2.1 三路召回（按 FRAMEWORK_NOTES §5.6）

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

`_rrf(ranked_lists, k=60)` —— 三路位次独立，按 `1/(k+rank)` 累加后降序，top 40 进精排。

参数选择：k=60 是 GraphRAG/RAG 社区常用值，兼顾长尾排名贡献与头部压制。三路权重等同（个人规模不用加权）。

### 2.3 精排（bge-reranker-v2-m3）

走 Xinference `/v1/rerank`，候选 = RRF top 40 的 content，返回 top 8（`app/m5_retrieve/rerank.py`）。

内部 LightRAG 自带 `enable_rerank=True` 空转（因为没配模型），三路召回调用显式传 `enable_rerank=False` 关掉，避免日志噪音。

### 2.4 引用溯源（chunk → full_doc_id）

PG 路径最直接：`lightrag_doc_chunks` 自带 `full_doc_id` 列，sparse 索引构建时一并带出，结果里每个 chunk 都有 `full_doc_id`。**比文件态 unknown_source 干净得多**，文件态需要从 vdb payload 取，本次 PG 正式库不绕那个弯路。

### 2.5 代码结构

| 文件 | 职责 |
|---|---|
| `app/m5_retrieve/sparse_index.py` | 关键词路：读 PG doc_chunks → sparse 编码 → 落盘 `m5_sparse.json`；score() 做内积排序 |
| `app/m5_retrieve/rerank.py` | Xinference /v1/rerank 封装（bge-reranker-v2-m3） |
| `app/m5_retrieve/retriever.py` | 编排：三路召回 → RRF → rerank → 溯源元数据，返回结构化结果 |
| `app/m5_retrieve/runner.py` | CLI：`--formal` 走正式链路（`-w data/lightrag_m4 --formal`）；默认仍保留 v0.1 四模式冒烟 |

## 3. 复现命令

```bash
# 工作根 = backend/（2026-09-14 前后端重排：app/ data/ inputs/ 等移入 backend/，先 cd 再执行）
cd backend
# 正式链路（PG 库，自动构建稀疏索引）
python -m app.m5_retrieve.runner -w data/lightrag_m4 --formal
# 单题
python -m app.m5_retrieve.runner -w data/lightrag_m4 --formal -q "客户投诉的处理流程"

# 仍可回退到 v0.1 冒烟（验证 LightRAG 内部召回）
python -m app.m5_retrieve.runner -w data/lightrag_m4 -m mix
```

## 4. 实测结果（PG 库，四题）

三路召回规模均正常（graph 20 / vector 20 / keyword 30），RRF+精排 Top-8 质量：

| 题目 | 关键命中 | 评估 |
|---|---|---|
| 智能客服核心功能 | #1 PRD 项目背景、#3 核心功能模块表、#4 PRD 首页；#2 迭代会议 | ✅ 高相关 PRD 全部前排，会议也在 |
| 季度销售业绩 | #1~#5 全部是销售业绩报告 5 段（概览/完成情况/产品线/策略/区域），score 0.21~0.92 | ✅ 全部命中，rerank 分数差分明 |
| 投诉处理流程 | #1~#8 全部是投诉 SOP（受理流程/分级/派单/时效/注意事项/封面），score 0.22~0.92 | ✅ 全部命中，主题相关性强 |
| 迭代会议决定 | #1 迭代会议纪要；后排有 PRD 背景/投诉升级等长尾 | ✅ 核心命中，长尾合理（query 中「智能客服」扩散到 PRD） |

**rerank 判别力**：正相关片段 score 普遍 0.6~0.9，负相关长尾多在 0.001~0.05，区分度显著（bge-reranker-v2-m3 生效）。

**keyword 路补充**：30 chunks 全参与打分，对图/向量双路都漏掉的术语型查询（如精确产品名）提供兜底。

## 5. 与 v0.1 的对比

| 维度 | v0.1 冒烟 | v1 正式 |
|---|---|---|
| 召回源 | 仅 LightRAG 内部（图 / 向量） | 图 + 向量 + 关键词三路 |
| 融合 | LightRAG 自带 round-robin | RRF(k=60) 三路融合 |
| 精排 | 空转（enable_rerank 默认 True 无模型） | bge-reranker-v2-m3（Xinference） |
| 溯源 | chunk_id 前缀猜 | full_doc_id 直接从 PG 表拿 |
| query 预处理 | LightRAG keyword LLM 抽种子 | 同左（中文分词在 keyword 路由 bge-m3 自处理） |

## 6. 已知坑 / 注意事项

1. **bge-m3 实例必须带 return_sparse 启动**：否则 sparse 请求会报错（BGEM3FlagModel 没算 lexical_weights）。当前已重载好；若重启 Xinference 重载入模型别忘了 `return_sparse=true`。
2. **sparse 索引是离线构建**：`m5_sparse.json` 存在则复用；文档入库/软删后由 M7 上传管线 `build_workspace_deps` **自动重建并重载**（v1.6，见 §9），不再需要手动。
3. **PG 表名带 embedding 后缀**：`LIGHTRAG_VDB_CHUNKS_xinference_bge_m3_1024d`——换模型=新表，和 M4 §5.1 是同一个坑。
4. **keyword 路没走 jieba/BM25**：bge-m3 sparse 一个模型解决 dense+sparse，少一个依赖；如果以后要中文分词定制（如领域词典），可以加 BM25 备用路。
5. **query 预处理（v1.5 已落地，见 §10）**：A 同义词扩展 + B 专名识别已接入，LightRAG keyword LLM 仍负责高层语义；未覆盖的仅剩口语类 query（需 C 查询改写 LLM，暂不做）。
6. **文件态库没有 keyword 路**：keyword 路数据源直接读 PG；文件态库走 `--formal` 会报错（找不到 PG workspace），用默认四模式即可。

## 7. 验收清单（v1）

- [x] 关键词路（bge-m3 sparse）：30 chunks 索引构建成功，单 query 打分可用；
- [x] RRF(k=60) 三路融合：graph + vector + keyword → top 40；
- [x] bge-reranker-v2-m3 精排：Xinference /v1/rerank 接入，top 8 输出；
- [x] 引用溯源：chunk → full_doc_id 直接从 PG 表带出；
- [x] 四题端到端跑通，rerank 分数区分度显著（0.9+ vs 0.0x）；
- [x] 关掉 LightRAG 内部空转 rerank 日志噪音。

## 8. 遗留 / 后续

1. **query 预处理 C：查询改写（LLM）**：A 同义词扩展 + B 专名识别已落地（§10），口语类 query（「定了啥事儿」「管啥的」）实测预处理无提升，需 LLM 改写为检索友好表述；暂不做。底层可再补 jieba 分词 + 同义词词林；
2. **增量索引** → 已解决（v1.6）：sparse 全量重建挪到 M7 `documents.build_workspace_deps`，上传/删除后自动执行（当前文档量下全量成本可接受，不引入增量维护）；
3. **文件态 keyword 路**：如需在本地文件态库也跑正式链路，要额外读 kv_store_text_chunks 建 sparse 索引（非必须，PG 是正式库）；
4. **溯源到页/段落级**：目前只到 full_doc_id，M6 引用标注需要更细粒度（M2 块有 anchor/page_range，M4 里 chunk 表暂未带出，后续可接入）。

## 9. 变更记录

- **2026-09-13 · v0.1**：首版冒烟 CLI，四模式召回验证。
- **2026-09-13 · v1 落地**：三路 RRF + rerank + 溯源全链路；bge-m3 重载启用 sparse；四题在 PG 库端到端通过。
- **2026-09-14 · v1.5 落地**：A 同义词扩展（领域词典 + 实体名反向模糊匹配）+ B 专名识别加权（正则 + 实体列表，注入 ll_keywords）实现并接入三路召回；6 道缩写/口语 query AB 对比验证（见 §10.8）；C 查询改写（LLM）暂不做。
- **2026-09-14 · v1.6（软删过滤 + 增量 sparse 联动）**：`retriever.retrieve()` 增 `exclude_docs` 参数——精排后按 `full_doc_id` 过滤软删文档（M7 传入 `deps.excluded_docs`，M6 透传）；`m7_interact/documents.py` 上传/删除后自动重建 sparse 索引（`build_workspace_deps`）。实测：软删文档从候选消失、提问 0 召回；正式文档回归正常。

---

## 10. v1.5 规划：query 预处理（中文增强）

> 状态：**v1.5 已落地**（2026-09-14）。A 同义词扩展 + B 专名识别加权 两件已实现并验证。C 查询改写（LLM）暂不做。
> 目标：提升图检索（local/global/mix）的实体命中率，解决中文专名/缩写/同义导致的种子漏匹配。

### 10.1 为什么要做

当前 LightRAG 内部已有 **keyword LLM** 从 query 抽 `high_level/low_level` keywords（见 `operate.py:get_keywords_from_query`），再用这些关键词去向量库查实体/关系。对英文和标准术语效果好，但中文场景有几个痛点：

1. **专名缩写 / 口语化**：用户说「客服」→ 库里是「客户服务」/「一线客服人员」；说「华东」→ 库里是「华东大区」；说「PRD」→ 库里是「产品需求文档」。keyword LLM 抽的是用户原文，直接去向量库搜可能偏。
2. **实体名称精确匹配差**：图检索的第一步是 keyword → 实体向量 top_k（cosine > 0.2）—— 同义/缩写差得远的话进不了候选集，后面图遍历再强也没用。
3. **数字 / 编号 / 条款**：dense 向量对精确数字不敏感（如「2026年第三季度」「CS-SOP-2026-014」「一级投诉」），sparse 关键词路部分兜底，但 query 侧如果能把专名显式提出来更好。

### 10.2 做什么（范围）

**三小件，不引入新模型**：

| 模块 | 说明 | 输入 | 输出 |
|---|---|---|---|
| **A. 同义词扩展** | 领域词典 + 反向查图库实体名，把 query 中的口语词/缩写扩展为标准术语 | query + 实体名列表（从图存储拿） | 扩展后的 query（给 keyword 路 / vector 路） |
| **B. 专名识别与权重倾斜** | 从 query 中识别出「人名 / 机构名 / 产品名 / 编号 / 日期」类专名，在 keyword 路和实体检索中加权 | query | 专名词表 + 权重标记 |
| **C. 查询改写（可选）** | 用 LLM 把口语化 query 改写为检索友好的标准表述，作为辅助 query 参与召回 | query | 改写后的 query（1~2 条） |

**不做什么**：
- 不引入 NER 模型（太重，个人项目用规则+词典足够）
- 不做 query 意图分类（MVP 只有信息检索一类）
- 不做多轮对话上下文理解（单轮，后面 M7 应用层再考虑）

### 10.3 详细设计

#### 10.3.1 A. 同义词扩展（领域词典 + 实体名反向匹配）

**思路**：维护一份轻量同义词词典（yaml 或 json），同时从 PG 图存储中拿到全量实体名列表做反向模糊匹配。

步骤：
1. 从 PG `lightrag_graph_nodes` 取全部 `node_name`（~244 个，量极小，启动时缓存）；
2. 词典匹配：query 中出现的词 → 扩展为标准词（如「客服」→「客户服务、一线客服人员」）；
3. 反向模糊匹配：对 query 中的每个名词片段，在实体名列表里找 top-N 近似匹配（简单子串 + 编辑距离），作为候选扩展；
4. 扩展词**追加到 query 末尾**（或注入 hl/ll_keywords），给 LightRAG keyword 路和 vector 路使用。

数据来源：
- **手动领域词典**：`app/m5_retrieve/synonyms.yaml`，几十条即可（从当前 5 个文档的实体中归纳）。MVP 阶段先手工维护，不自动生成。
- **实体名列表**：运行时从图存储读，缓存到内存。

#### 10.3.2 B. 专名识别（规则 + 正则）

**思路**：用正则和简单规则把 query 里的专名挑出来，给它们更高权重。

识别类型：
- **日期**：`2026年第三季度`、`9月8日`、`Q3` → 归一为标准格式
- **编号**：`CS-SOP-2026-014`、`V2.3` 等文档/版本编号
- **等级/级别**：`一级`、`紧急`、`P1`
- **组织机构**：`研发组`、`产品组`、`华东大区`（从实体名列表里匹配）
- **产品名**：`智能客服系统`、`智能硬件产品线`（同上）

实现方式：
- 纯 Python 正则 + 实体名列表匹配，零额外依赖
- 输出一个 `weighted_terms: {term: weight}` 字典，专名权重 2.0~3.0，普通词 1.0

作用点：
- **keyword 路**：sparse 检索时把专名对应的 token 权重乘以系数（或在 RRF 前重排）
- **图检索**：把专名注入 `ll_keywords`（通过 QueryParam），让 LightRAG 多拿几个种子实体

#### 10.3.3 C. 查询改写（可选，看 A/B 效果）

**思路**：用 DeepSeek flash 把用户口语化 query 改写成 1~2 条「检索友好」的标准表述。

比如：
- 输入：「客服咋处理投诉啊」
- 改写1：「客户投诉处理流程是怎样的」
- 改写2：「客户服务投诉处理标准作业流程」

然后把改写后的 query 也跑一遍检索，结果合并到 RRF。

**为什么放可选**：
- 当前只有 5 个文档、query 也偏正式，提升可能有限
- 增加 LLM 调用成本和延迟
- 等 A/B 测完发现命中率确实不够再上

### 10.4 接入方式

在 `retriever.py` 的 `retrieve()` 之前加一步 `preprocess_query(query, rag) -> PreprocessedQuery`：

```python
@dataclass
class PreprocessedQuery:
    original: str              # 用户原 query
    expanded: str              # 同义词扩展后的 query（给 vector/keyword 路）
    hl_keywords: list[str]     # 注入给 LightRAG 的 high-level keywords（可选）
    ll_keywords: list[str]     # 注入给 LightRAG 的 low-level keywords（专名加权）
    weighted_terms: dict       # {term: weight} 给 keyword 路加权
    rewritten: list[str]       # 改写后的 query（可选）
```

三路召回时：
- graph 路：用原 query + 注入 ll_keywords（专名）
- vector 路：用 expanded query
- keyword 路：用 expanded query + weighted_terms 加权

### 10.5 验收标准

用当前 4 道题 + 补充 3~5 道「带缩写/口语化」的题做对比：

| 指标 | 预期提升 |
|---|---|
| 图检索种子实体命中率 | 专名 query 提升 20%+（拿更多相关实体进候选） |
| 最终 rerank top-3 相关率 | 口语化/缩写 query 提升明显 |
| 延迟 | 增加 < 200ms（纯规则，不调 LLM） |

**怎么测**：把预处理前后的 graph 路召回实体列出来对比，看扩展前没命中的关键实体（如「华东大区」对应「华东」query）扩展后能不能进 top_k。**已实测**：6 道缩写/口语 query 全量 AB 对比，见 §10.8。

### 10.6 实施顺序

1. **第一步（必做）**：A 同义词扩展 + 实体名反向匹配 —— 改动最小、效果最直接
2. **第二步（必做）**：B 专名识别（正则 + 实体列表匹配）—— 给专名加权
3. **第三步（可选）**：C 查询改写 —— 看前两步效果决定是否做

### 10.7 风险与取舍

- **词典维护成本**：MVP 阶段手工，量小（几十个词对），可接受。文档多了再考虑自动从实体描述中归纳。
- **实体名列表来源**：从图存储读，依赖存储后端——PG 直接 SQL 拿，文件态走 NetworkX —— 要做适配层。MVP 先支持 PG。
- **过扩展引入噪音**：扩展太多会把不相关的也带进来。解决：只扩展高度匹配的（编辑距离 ≤ 2 或子串匹配），且扩展词数上限 5 个。
- **与 LightRAG keyword LLM 的关系**：不是替代，是补充——keyword LLM 负责高层语义（「核心功能有哪些」→「智能问答、工单分配」），预处理负责中文表层的专名/缩写对齐。

### 10.8 A+B 落地实测（2026-09-14，6 道缩写/口语 query）

对比方法：同一 query 跑两遍正式链路（三路 RRF + rerank top-8），一遍关闭预处理（baseline）、一遍开启（PREPROC），对比 rerank 结果与来源 doc / ll_keywords 注入情况。

| 题目 | 类型 | baseline 表现 | 预处理后 | 结论 |
|---|---|---|---|---|
| 客服咋处理投诉 | 缩写 | top5 全对（受理流程/注意事项/派单/登记/目的） | 同 top5，顺序微调 | 中性（bge-m3 对常见口语已鲁棒） |
| 华东的Q3销售怎么样 | 缩写 | top5 全对（目标分析/季度概览/策略/区域/产品线） | 同 top5 | 中性 |
| 一级投诉怎么升级 | 缩写 | top5 全对（升级机制 #1/#2） | 同 top5，顺序微调 | 中性 |
| PRD V2.1写了啥 | 缩写 | #1 命中，但 #3/#4 混入投诉 SOP 噪音（评分 0.003/0.003） | #3 换为 PRD 核心功能模块、#5 换功能模块，噪音消除 | **改善**：缩写成「产品需求文档」后定位到 PRD 文档 |
| 那次迭代会定了啥事儿 | 口语 | 会议纪要 #1，长尾噪音大（0.004/0.0005） | 完全相同，ll_kw=[] | 无提升：口语词未覆盖，需 C 查询改写 |
| 研发组管啥的 | 口语 | 会议纪要 #1，长尾噪音大 | 完全相同（ll_kw=['研发组']） | 无提升：同上 |

**结论**：

1. **缩写/专名型 query 有效**：「PRD V2.1」经词典扩展为「产品需求文档」，rerank 结果挤出投诉 SOP 噪音、更聚焦 PRD 文档——A（词典扩展）+ B（版本号正则加权）生效。
2. **已标准化的缩写中性**：「客服」「华东」「一级投诉」bge-m3 dense 本身已能召回，预处理不劣化（顺序微调未影响正确性）。
3. **纯口语 query 无提升**：「定了啥事儿」「管啥的」——词典无覆盖、实体名模糊匹配（子串 + 编辑距离）匹配不到，ll_kw 掉空或只带原始词。这正是 **C 查询改写（LLM）** 的适用域，本次按计划暂不做（§10.6 第三步可选）。
4. **延迟**：纯规则（dict 查表 + 正则 + ~244 实体编辑距离匹配），每 query 增加 <200ms，符合 §10.5 预期。