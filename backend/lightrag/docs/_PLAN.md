# LightRAG 源码拆解规划（步骤①）

> 状态：执行中 ｜ 本文件是 5 个并行子代理的执行蓝图，各代理产出一份拆解 md，统一放 `lightrag/docs/`。
> 代码位置：`lightrag/source/`（从 github.com/HKUDS/LightRAG 克隆，锁定 tag v1.5.7）。

## 结构约定

```
lightrag/
├── README.md          # 这层文件夹说明：clone 版本、拆解开销
├── source/            # LightRAG 源码（git clone，不随主仓库提交）
└── docs/              # 五方向源码拆解
    ├── _PLAN.md       # 本蓝图
    ├── storage.md     # ① 存储层
    ├── retriever.md   # ② 检索层
    ├── graph.md       # ③ 图构建与实体抽取
    ├── llm.md         # ④ LLM 接入
    └── interfaces.md  # ⑤ 模块间输入输出接口规范
```

## 五个拆解方向（各一个并行子代理）

每个文档统一骨架：**定位与职责 → 关键文件/类（含路径）→ 核心机制与数据流 → 输入输出接口 → 「内建 vs 自研」边界 → 实现时的已知坑**。

### ① storage.md —— 存储层

- 覆盖：Storage 抽象（KVStorage / VectorStorage / GraphStorage / DocStatusStorage）与全部实现（Json / NetworkX / Numpy / Postgres+pgvector / Neo4j / Nebula / Milvus / Qdrant 等）。
- 要回答：默认（JsonKV+NetworkX+Numpy）与各数据库实现的接口签名；图存储如何组织节点/边（属性、embedding 存哪）；「Read-Only Job Cache」；谁能替换、替换成本。
- 与我们项目：M4 存储层要「复用 LightRAG 抽象 + 换 Postgres 实现」，本文档要给出**接口清单**和**官方 Postgres 实现的落点**，供 M4 直接对齐。

### ② retriever.md —— 检索层

- 覆盖：query/aquery 主流程里 local/global/hybrid/naive/mix 各自怎么实现（代码位置）、内部如何调用向量检索与图遍历、返回结构（带证的 chunks/entities/relations 组织）。
- 要回答：`mix` 到底合并了什么、有无任何 RRF/rerank/关键词路（应无——确认 §2.4「内建 vs 自研」边界为真）；参数（top_k、max_token_for_text_unit 等）。
- 与我们项目：M5 外层要包 RRF(k=60)+rerank+关键词路，本文档要标出**需要外层接管的位置**（改返回值 or 自管三路）与**调用入口签名**。

### ③ graph.md —— 图构建与实体抽取

- 覆盖：insert 时文档如何被 LLM 抽取实体/关系（prompt.py 的 entity extraction prompt）、`language=zh` 开关确切位置、`MAX_ENTITY_TOKENS`/`ENABLE_LLM_CACHE` 作用点；增量更新与「选择性删除」的现成能力。
- 要回答：抽取调用走向（每个 chunk 抽完 → 合并 → 写图）；结果存哪；删除了某文档如何从图里剔除（或是否只能重建）。
- 与我们项目：M3 图构建要「微调复用 + 中文配置」，本文档给出**需要改的 prompt/参数精确入口**。

### ④ llm.md —— LLM 接入

- 覆盖：`create_llm_client` / OpenAI 兼容函数类型（FunctionType）、`llm_model_func`/`embedding_func` 注入方式、LLM 缓存（`ENABLE_LLM_CACHE` 的缓存键与命中）、每环节模型分离（extract/响应共用同一 func 还是可分离）。
- 要回答：接 DeepSeek 的确切写法（base_url/model 传参）；缓存持久化位置与失效；多模态/语音等我们不需要的扩展点。
- 与我们项目：M0 llm_client 要对齐这套注入接口，本文档给出**接 DeepSeek 的最小配置**与**缓存开关的坑**。

### ⑤ interfaces.md —— 模块间输入输出接口规范

- 覆盖：**Document / Chunk 数据模型**（来源、字段、chunk 怎么切、有无页码/标题元数据）；insert/query 全流程各环节的输入输出形状（file→documents→chunks→entities/relations→存储；query→检索→`llm.complete`）。
- 要回答：LightRAG 自己的中间产物 schema 与我们的 TextUnit 契约的差距（要补页码/标题路径/块类型等）；哪一层补最合理。
- 与我们项目：M0 契约设计 + M1/M2 适配，本文档给出**对齐/换算点清单**。

## 执行备注

- 子代理只读 `lightrag/source/`，产出的 md 用 Write 写到 `lightrag/docs/` 各自文件。
- 版本锁定：优先 `v1.5.7` tag；若无则记录 checkout 到的 commit。
- 所有文档以「我们的项目姿态」收尾：哪些**直接复用**、哪些**外层自研**、哪些**替换**。