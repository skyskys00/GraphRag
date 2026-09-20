# GraphRAG 系统架构与选型方案（v2）

> 调研日期：2026-09-03 ｜ 修订：2026-09-11（依据拆解调研与 **docs/FRAMEWORK_NOTES.md** 修正事实、明确「内建 vs 自研」边界、补版本锁定与全部实施路线）
>
> **本文件层定位（蓝图层）**：方向 / 选型 / 版本锁定 / 路线。不承载模块实现细节（见 `docs/modules/Mx_*.md`）、不承载版本时序（见 `docs/CHANGELOG.md`）、概念决策与文档治理见 `docs/FRAMEWORK_NOTES.md` §0/§5。
>
> **本版修订摘要**：
> - **A 档修正**：明确 LightRAG `mix` 不含稀疏/BM25、且无内建 RRF 与 reranker（§2.4/2.6，检索增强在外层）；embedding 载体定 **Xinference**（Ollama 无 sparse，§3）；chunk 上限理由更正（§2.3）；DeepSeek 模型名更正为 `deepseek-v4-flash / v4-pro`（§3/§7）；NebulaGraph 非 LightRAG 官方支持、需自写 adapter（§2.5）。
> - **补充**：版本锁定总表（§3.1）、query 预处理（§2.6）、目录与产物规范（§6-阶段0）、图片索引策略（§6-阶段1）、评测基线贯穿（§6）、模型下载镜像（§6-阶段0）。
> - **§6 分阶段实施路线已完整撰写**（按模块顺序、逐阶段给验收）。
> - **2026-09-16 瘦身**：§8 实测附录并入 `docs/CHANGELOG.md`（时序内容移出蓝图，本节留指引）；§2 压缩引擎/选型过程细节，保留选型结论与「内建 vs 自研」边界。

本节结论由 4 路并行调研交叉验证（框架层 / 解析层 / 存储检索层 / Agent 与平台层），信息源自 GitHub 一手仓库与官方文档，个别观点标注了来源与风险。

---

## 0. 决策基准（用户已确认）

| 项 | 决定 | 对架构的影响 |
|---|---|---|
| 规模 | 个人/小团队知识库（数十万文档以内） | 单机可承载，优先「可嵌入、零运维」，避开集群 |
| LLM | 国内模型为主 | 全链路走 OpenAI 兼容接口接 DeepSeek / Qwen / GLM；控制 token 成本 |
| 图谱 | 轻量图（LightRAG 式） | 图作「关系索引」辅助跨文档查询，不做全量社区检测 |
| 部署 | 本地/私有化单机 | Docker Compose 编排，数据不出内网 |
| 硬件 | 本机 **Mac M3 / 24GB 统一内存 / Metal 3**（arm64，已核实） | Apple Silicon MPS 可加速 MinerU 与本地模型；Python 需 conda 建 3.10+ 环境（系统 3.9 太老） |
| LLM 接入 | **DeepSeek**（OpenAI 兼容 API） | 抽取/生成统一走 DeepSeek，单 key 跑通全链路；**实测定 `deepseek-v4-flash` 全模型（抽取+生成），不用 pro**（见 §3.1 与 CHANGELOG v1.0 M3） |
| 内容形态 | **PDF 为主** + doc/txt/md 等 | MinerU 主力 + Docling 补多格式 |
| 综述问答 | **需要全局综述** | LightRAG `global`/`mix` 起步，按效果决定是否补社区摘要 |
| 目标 | **开发练手、测通即可**；暂不优化长期运维成本 | 存储做成**可切换接口**：MVP 用 LightRAG 默认存储跑通，Postgres 适配器随时切换；代码按 M0–M9 模块化重建（见 **docs/FRAMEWORK_NOTES.md**） |

**核心选型一句话**：
**LightRAG（框架内核）+ MinerU 3.x（文档解析）+ bge-m3（本地 embedding，经 Xinference 供 dense+sparse）+ bge-reranker-v2-m3（精排）+ Postgres/pgvector（持久化）+ LangChain 薄包装（消息/工具层）**，全部本地私有化，抽取/建图用国产便宜模型（deepseek-v4-flash），**全链路统一 `deepseek-v4-flash`（2026-09-13 实测定案，见 CHANGELOG v1.0；不用 pro）**。

---

## 1. 总体架构

两条主线：**索引管线**（离线，文档 → 可检索结构）与**查询管线**（在线，问题 → 答案）。

```
┌──────────────────────────── 索引管线（离线） ────────────────────────────┐
│                                                                           │
│  数据源 PDF/DOCX/PPTX/HTML/MD/扫描件                                       │
│    │                                                                      │
│    ▼                                                                      │
│  [1 解析层]  MinerU 3.x（主力，离线）──┐                                   │
│              Docling（多格式补充）      │  统一中间格式：Markdown            │
│              PaddleOCR（OCR 增强）    ┘                                   │
│    │                                                                      │
│    ▼                                                                      │
│  [2 切分层]  结构化文档→标题层级切分；通用→TokenChunker(512/overlap10-15%)  │
│              ──► 产出统一数据模型：Document ─ TextUnit                      │
│    │                                                                      │
│    ▼                                                                      │
│  [3 索引层]  双索引并行                                                    │
│    ├─ 图索引：LLM 抽取 实体/关系 建图（LightRAG）                           │
│    └─ 向量索引：bge-m3(dense+sparse) 对 TextUnit 编码                       │
│    │                                                                      │
│    ▼                                                                      │
│  [4 持久化]  图结构 + 向量 + 原文 + 状态                                  │
│              起步：LightRAG 默认存储│准生产：Postgres+pgvector 一库通吃      │
│              │扩展：图→NebulaGraph / 向量→Milvus/Qdrant                    │
└──────────────────────────────────────────────────────────┬───────────────┘
                                                           │ 写入
┌──────────────────────────── 查询管线（在线） ─────────────▼───────────────┐
│                                                                           │
│  用户问题                                                                 │
│    │                                                                      │
│    ▼                                                                      │
│  [5 检索召回] 内建 mix(图+向量local/global/naive) + 关键词sparse/BM25        │
│              → RRF(k=60) 融合 → bge-reranker 精排（外层增强，M5）            │
│    │                                                                      │
│    ▼                                                                      │
│  [6 Agent 编排] LangChain 薄包装（history+工具协议）：                      │
│    意图识别 → 多路召回 → context 组装（带引用/来源 ID）→ 生成(流式)         │
│    全局性/跨文档问题 → map-reduce 分块合并                                  │
│    │                                                                      │
│    ▼                                                                      │
│  [7 交互层]  WebUI / FastAPI（SSE 流式 + 引用标注）                        │
└───────────────────────────────────────────────────────────────────────────┘
```

---

## 2. 逐模块设计与选型

### 2.1 文本数据格式（统一中间格式）

**原则：所有输入统一收敛为两个内部标准，避免下游各管各的。**

1. **统一文档格式 = Markdown**（解析层出口）。MinerU 输出 Markdown、Docling 输出 Markdown；RAGFlow DeepDoc 输出的本质是**纯文本 chunk（并非 Markdown）**，可借鉴其版面聚类思路，归一处理仍需自建（对照基准，不作为依赖）。**理由**：Markdown 是 LightRAG/LangChain/LlamaIndex 全部原生输入的通用语言；保留标题层级、表格（HTML）、公式（LaTeX）信息，是后续「标题层级切分」和图表问答的前提。原始 PDF 仅存档，不参与索引。
2. **统一数据模型 = Document ─ TextUnit**（参考微软 GraphRAG 的数据模型）。每个块（TextUnit）必须携带：`text / 来源 doc_id / 页码 / 标题路径（hierarchy） / 块类型（text|table|title|...） / embedding / 关系出的实体引用`。所有下游（向量、图、引用标注）都只认这套模型。

> 反面教材：直接把 pdfplumber 逐页吐的裸文本喂给检索，会丢掉结构，导致中文表格/条款块切开、引用无法溯源。

### 2.2 文档解析层（选型结论）

| 工具 | 定位 | 结论 |
|---|---|---|
| **MinerU 3.x**（opendatalab） | PDF/扫描件 → Markdown/JSON，中文解析开源第一梯队；自动去页眉页脚、阅读顺序、公式→LaTeX、表格→HTML、跨页表格合并；离线私有部署 + Apple MPS/GPU | **主力解析器（强推）**。低资源用 `pipeline`（纯 CPU 可跑，4GB 显存），精度优先用 `vlm/hybrid`（8GB 显存）。需注意 license 为「MinerU Open Source License」（基于 Apache 2.0 的定制版，**非纯 Apache**，商用前读条款） |
| **Docling**（IBM，MIT） | DOCX/PPTX/XLSX/HTML/EPUB/邮件等**多格式**统一解析 | **多格式补充件**。中文版式弱于 MinerU，用作非 PDF 格式的兜底 |
| **PaddleOCR / PP-StructureV3**（Apache-2.0） | 中文 OCR 底座、表格单元格坐标 | **OCR 增强件**。实测 MinerU v3.4.5 OCR = **pytorchocr**（PyTorch 复刻推理 **PP-OCRv6** 权重，PDF-Extract-Kit-1.0 打包，pip 主链不依赖 paddlepaddle）；Docling 扫描件默认 **RapidOCR**（**PP-OCRv4/v5**，多语言）。一般无需重复部署；仅在需要印章/古籍/生僻字或表格坐标做二次结构化时单独用 |
| LlamaParse | 闭源 SaaS，绑定 LlamaCloud | **不推荐**（离线约束 + 付费 + 闭源） |

**MinerU 引擎选择（本机 = Mac M3 / 24GB / MPS）**：练手阶段**首选 `pipeline`**（MPS/CPU 都能跑、最快最稳，中文常规文档够用）；扫描件/复杂多栏对该类文档单独开 `hybrid medium`；不主动上 `vlm`。引擎原理/硬件/自评分数对比见 `docs/PARSER_COMPARISON.md`。

**源码实测补充（2026-09-12，详见 docs/PARSER_COMPARISON.md）**：
1. MinerU 引擎用 **`-b pipeline`**（3.4.5 默认 hybrid-engine 依赖大型 VLM，本机不可行）；
2. **LightRAG 官方 parser 已内置 mineru 与 docling 两套消费链**（`parser/external/`，ir_builder 产 blocks.jsonl）——M1 沿用官方链、只补扩展字段，不重写解析器；
3. **结构对接一律走 JSON / content_list**（Markdown 导出会丢 label/prov 等元数据，仅作轻量视图）；
4. docx 无页码且 Docling 不读 `w14:paraId`——原文定位需**自研补丁**或降级「文件+文本片段」（见 docs/modules/M0_contracts/textunit.md §4.2）。

> 口径提醒：引擎自评分数为 **MinerU 官方口径**、以英文/通用版式榜单为主，中文常规文档仅供参考（具体 OCR 版本随 MinerU 迭代，安装后以实际为准）——详见 `docs/PARSER_COMPARISON.md`。

### 2.2.1 结构化抽取（可选增强）：google/langextract

- 定位：「**文本 → 结构化知识**」的语义字段抽取库，**不是文档解析器**（输入纯文本/URL、无 OCR/版面），工作在 MinerU 之后。
- 与 `longextract-bench` 评测基准无关（Apache-2.0，**非 Google 官方支持产品**）；默认走 Gemini，能否接国内模型需核实 provider 插件。
- 用途（与 LightRAG 实体抽取互补，非常规依赖）：定向抽固定字段（合同要素/报告指标）作高置信度图节点来源；`grounded` 溯源（取值↔原文字符区间）思想可强化答案溯源。
- **决策**：MVP 不纳入，作后续可选项。

### 2.3 切分层

- **结构化文档（合同/手册/论文/法规）→ 优先「标题层级切分」**：按 MinerU 还原出的标题结构聚合，每个 chunk 是完整结构单元（RAGFlow 的 Title/Hierarchy Chunker 即此思路）。**不建议对结构化文档用纯语义切分**（慢、对表格/条款不稳）。
- **通用/长文本 → TokenChunker**：`chunk_size ≈ 512 tokens（中文约 500–1000 字）`，`overlap 10–15%`，**先按自然段落边界切再按 token**（避免从句子/表格中间切断）。
- **上限**：单 chunk 不超过 `1200–1500 tokens`（bge-reranker-v2-m3 上下文上限 8192 tokens，设上限主要控**定位粒度与时延**——过长块中 query 相关性会被稀释）。
- **表格整块为一个 chunk**，标题/章节路径作为 metadata 前置。

### 2.4 索引层（双索引）

- **图索引（LightRAG 建图）**：LLM 抽取 实体/关系 构建轻量图。**每个环节可独立配模型**——抽取/建图与回答**统一 `deepseek-v4-flash`**（2026-09-13 实测定案，不用 pro，见 CHANGELOG v1.0）；开 `ENABLE_LLM_CACHE`（LLM 缓存）与 `MAX_ENTITY_TOKENS`（上下文截断）控成本。支持**增量更新与选择性删除**（复用索引期缓存重建受影响实体）——这是知识库持续扩写的关键。
- **向量索引**：TextUnit 用 **bge-m3** 编码（dense+sparse 一次拿到，8192 token，1024 维，中文强）。**无需另建 BM25 索引**——前提是 embedding 载体支持 sparse 输出（选 **Xinference**；**Ollama 只吐 dense、不满足**）。精排用 **bge-reranker-v2-m3**。

**内建 vs 自研（重做关键边界，必读）**：
- **LightRAG 内建**：`local / global / naive / hybrid / mix` 五种检索模式（图 + 向量），其中 `mix = local + global + naive`；
- **外层自研（检索增强层，模块 M5）**：关键词路（sparse/BM25 可切换）、**RRF(k=60) 融合**、**bge-reranker 精排**、query 预处理——LightRAG **无多路融合 RRF**；chunk 重排管道实测存在但默认 off（`rerank.py`，`RERANK_BINDING="null"`），仅作用于 chunks 且需 provider，「三路融合 + 精排」仍是目标态、需外层实现。M5 可**复用其 `process_chunks_unified` 精排管道**（详见 `lightrag/docs/retriever.md`）。

### 2.5 持久化层

**注意（按你的练手目标）**：存储做成**可切换接口**（模块 M4）。MVP 用 LightRAG 默认存储跑通功能；**Postgres 适配器在 M4 阶段就实现，切换只改配置**——不在 MVP 阶段提前引入 Postgres 服务，也不锁死在默认存储。

**分阶段演进，避免一开始就上重系统（个人单机从简）：**

| 阶段 | 存储 | 说明 |
|---|---|---|
| MVP（先跑通） | LightRAG 默认存储（JsonKV + NetworkX + 轻量向量库） | 零运维；**缺点：内存态，不适合长期保存，索引重建即费 token** |
| **准生产（推荐目标）** | **Postgres + pgvector 一库通吃**：图结构、向量、原文块、索引状态同一库 | LightRAG 官方推荐路径；pgvector 支撑「百万–数千万」级向量，个人/小团队完全够；**省一个独立向量库服务的运维** |
| 扩展（数据量再上万级 +） | 图 → **NebulaGraph**（Apache-2.0 可商用、中文原生、分布式）｜向量 → **Milvus**（亿级主战场、中文生态最强）、**Qdrant**（部署轻、内置 RRF/DBSF）或 **Chroma**（最轻，HNSW 须全量驻内存，64GB 约 1500 万条 1024 维——**适合起步、天花板低**） | 迁移成本高，非必须不要提前做。⚠️ **LightRAG 官方无 NebulaGraph 适配器**（官方支持 pgvector/Neo4j/Milvus/Qdrant/MongoDB 等），需自写 storage adapter，成本高于表格所示 |

**许可证风险提醒（重点）**：Neo4j Community = **GPLv3**（修改版对外分发须开源、官网 license 页面有过 Commons Clause 历史，下载前务必核对纯 GPLv3）；Elasticsearch = **ELv2 / SSPL 双许可**（均非 OSI 开源）。真要开图库选 **NebulaGraph（Apache 2.0）**、搜索选 **OpenSearch**——轻量图方案下用 Postgres 内联基本绕开这些（汇总见 §5 第 1 条）。

### 2.6 检索召回层（三路混合：内建 + 外层增强）

**目标态：做「图谱 + 向量 + 关键词」三路混合，融合用 RRF（k=60），精排用 bge-reranker-v2-m3。** 按能力归属拆两层（对应 §2.4 的「内建 vs 自研」边界）：

**LightRAG 内建三路（M5 直接封装）：**
- `local`（图遍历）：query → 实体匹配 → 沿图扩张（实体↔原文/↔关系/↔邻实体）——实体级问答主力；
- `global`（跨文档主题 / 关系链）：**不锚定单一实体**，以关系为锚，把散布多篇文档、围绕同一主题的片段捞回聚合——直接支撑综述类问题；机制远轻于微软 GraphRAG 的社区报告；
- `naive`（纯向量）：语义召回，覆盖种子实体未命中的情况；
- `mix` = local + global + naive 合并检索（LightRAG 内建的合并，无 RRF）。

**外层增强（自研实现，模块 M5）：**
- **关键词路**：bge-m3 `sparse`（经 Xinference）或 BM25——**接口做成可切换**。精确匹配人名/机构名/中文专名/数字兜底；
- **RRF(k=60) 融合**：三路（图 + 向量 + 关键词）按排名倒数融合，产出统一列表；
- **rerank 精排**：bge-reranker-v2-m3（经 Xinference `/v1/rerank`）对 RRF 结果 top-K 精排；
- **query 预处理**：意图/实体识别 + 中文专名改写，提升 `local` 种子实体命中率（中文缩写/同义易漏）。

> ⚠️ 边界提醒：LightRAG 的 `mix` **不含关键词路，也没有内建 RRF 与 reranker**（v1 稿把目标态写成内建能力，重做后已更正）。

**检索策略 vs 生成策略（分层，勿混淆）**：`global` 是**检索策略**（M5 决定从哪召回）；`map-reduce` 是**生成/组装策略**（M6 决定如何组织上下文与合并）。跨文档综述通常是「global 检索 + map-reduce 组装」**组合使用**，二者不互斥（见 §2.7 意图路由）。

**评测与迭代（贯穿）**：建 **RAGAS + 自有中文测试集**（含人名/专名 query）定 baseline（faithfulness / context recall 等），再调融合权重与 rerank 阈值；每次改动回归对比（模块 M9）。
**全局综述类问题**：用 LightRAG `global`（关系链跨文档聚合）起步；若主题综述效果不足，再按 §5-5 补社区摘要（HiRAG 分层树思路）。

### 2.7 Agent 编排与上下文组装（「传给 agent → 返回用户」这一段的落地）

**推荐：LightRAG 内核 + 一层可控的 Agent 编排**（对应模块 M6）。用 **LangChain 薄包装**：仅用于**对话 history 管理**与**工具调用协议**（agent 可调图谱检索 / 文档检索两个工具）；模板化的 prompt/上下文组装**自研，不深度用 LCEL 抽象**。若后续要多智能体、复杂工具调用，再引入 **LangGraph 状态机**。

组装规范（抄微软 GraphRAG 的最佳实践、做中文化）：

1. **意图路由**：问题是否需要「全库级跨文档综述」→ 走 **map-reduce**（分块出带重要性评分要点 → 合并排序 → 生成）；实体级/局部问题 → 走 **single-window**（单上下文一次生成）。`response_type` 中文描述（如「请分段落、先总后分地回答」）。
2. **上下文组装（Local 风格，5 轨道）**：把 原文块 / 实体 / 关系 / 图谱路径 各路召回结果经过排名+过滤后，**压缩进一个预设 token 预算（`max_data_tokens`）的上下文窗口**，而非把各路原始结果整块塞进 prompt。窗口组装独立成函数，便于复用与测试。
3. **引用标注（citation）**：让「来源 doc_id / TextUnit ID / 节点名」在检索→重排→组装→生成的整条链路里流转，最终答案外层附 `[引用列表]`（参考 RAGFlow grounded citations 的思路）。local 层用 TextUnit ID 细引用，全局层用主题/报告名粗引用。
4. **流式输出**：SSE + token 级流式（FastAPI `StreamingResponse`）；map-reduce 场景只在 reduce 阶段流式。把「检索完成」「生成第 N 段」做成事件上报。
5. **语义缓存**（可选，二期）：高频相似问题按 embedding 相似度命中直接返回，缓存里同时保留命中问题的引用列表。

### 2.8 交互层

- **后端（M7，已落地）**：FastAPI 统一对外——在线线：SSE 流式 + 引用标注 + 多轮会话；**文档管理**（`POST/GET/DELETE /docs`，软删）、**图谱导出** `GET /graph`（软删过滤，节点带 `docs[]`/`chunks[]`）、**文档预览** `GET /docs/{doc_id}/preview`、**按文档过滤** `GET /graph?doc_id=`。各版本演进与实测见 `docs/CHANGELOG.md` v1.0 M7 条目，接口细节见 `docs/modules/M7_interact.md`。
- **Web UI**：M7 自带零依赖 WebUI（答案 + 引用定位原文）作轻量兜底；正式前端见 **M8**（React，`frontend/`）。
- **专业前端（M8，已落地）**：图谱展示主轴 = 「问题 → 回答 + 引用高亮 → 点击引用定位原文 → 引用「在图谱中查看」跳图谱并聚焦相关实体子图」。渲染层 **AntV G6 v5.1.1**；布局「左侧可折叠侧边栏 + 主区 + 右栏 tab」，问答为主视图，导航项数组模式便于扩展新模块，右栏 tab 切换「引用来源 / 预览」（为未来「关联」模块留位）。版本演进（含 v2.3 侧边栏/预览/排序/过滤）与实测见 `docs/CHANGELOG.md` v1.0 M8 条目，细节见 `docs/modules/M8_frontend.md` + 需求稿 `M8_frontend_req.md`。
- 多轮会话记忆：当前自管 history 注入（可进阶 RAGFlow「AI Memory」/Dify 会话变量思路）。
- **模型下载**：本机直连 HuggingFace 不通，统一 `HF_ENDPOINT=https://hf-mirror.com`。

---

## 3. 模型配置策略（国内模型）

| 环节 | 建议 | 说明 |
|---|---|---|
| 实体抽取/建图 | 便宜模型：**deepseek-v4-flash** / Kimi / Qwen（可再开 `ENABLE_LLM_CACHE`） | 抽取是调用最密集的环节，用便宜模型省大头 |
| 回答生成 | **deepseek-v4-flash**（**统一全链路 flash**，不用 pro——2026-09-13 实测定案） | 输出质量优先；flash 实测抽取已优于 GLM（见 CHANGELOG v1.0） |
| Embedding | **bge-m3 经 Xinference 本地部署**（dense+sparse 双输出） | **不选 Ollama**（只吐 dense、无 sparse，破坏「免建 BM25」前提）；也可 DashScope text-embedding-v3 API（`dense&sparse`，有免费额度）|
| Rerank | **bge-reranker-v2-m3**（经 Xinference `/v1/rerank` 标准接口） | 中文专名精确性提升明显 |
| 语言设置 | 生成/报告 `SUMMARY_LANGUAGE=zh`；**实体抽取需另行配 `language=zh`**（entity_extraction 参数或自定义 prompt） | 只设 SUMMARY_LANGUAGE 时实体仍可能英文，两个都要配，避免英文图污染 |
| 测试/低成本 | **GLM 免费档备选**：`glm-4.5-flash` / `glm-4-flash-250414`（bigmodel `https://open.bigmodel.cn/api/paas/v4`） | 主链仍 DeepSeek；**测试/评测/练手**用 GLM 免费档控成本或作降级；`glm-4.7-flash` 访问量大、大概率不可用，勿作第一选择（接入示例见 `lightrag/docs/llm.md` §3.2.1） |

> 中文 token 膨胀约 2 倍：`max_tokens` 与 `max_data_tokens` 都要给足。

### 3.1 组件版本锁定总表（初始基线，实施时以最新稳定版为准并回归）

| 组件 | 版本/型号（初始基线） | 许可证 | 部署形态 | 备注 |
|---|---|---|---|---|
| LightRAG（pip：lightrag-hku） | v1.5.7 起锁定，升级前回归 | MIT | Python 包 | 迭代快，版本是易碎点（§5.2） |
| MinerU | 3.x（装后核实内置 OCR 内核） | 定制 Apache（§2.2） | Python / MPS | pipeline 起步，扫描件 hybrid |
| Docling | 当前稳定版 | MIT | Python | 多格式兜底 |
| bge-m3 | BAAI/bge-m3 | MIT | **Xinference（FlagEmbedding 后端）** | dense+sparse，8192/1024 维 |
| bge-reranker-v2-m3 | BAAI/bge-reranker-v2-m3 | MIT | **Xinference** | `/v1/rerank` |
| **Xinference（模型网关）** | 当前稳定版 | Apache-2.0 | 本地进程/容器 | 统一承载 bge 两模型，单地址三端点；先于一切模块部署 |
| DeepSeek API | `deepseek-v4-flash`（**全链路统一，不用 pro**） | — | 云端（OpenAI 兼容） | 抽取 / 生成，单 key 全链路；**注意 v4-flash 是推理模型，须 `extra_body` 传 `thinking={"type":"disabled"}` 关思考模式**（见 CHANGELOG v1.0） |
| GLM（bigmodel，测试/低成本备选） | `glm-4.5-flash` / `glm-4-flash-250414`（免费档） | 商用授权视邀请（个人测试 OK） | 云端 `https://open.bigmodel.cn/api/paas/v4` | 测试 / 评测 / 降级用；`glm-4.7-flash` 流量大不稳定，不作主选 |
| Postgres + pgvector | Postgres 16 + pgvector 0.8+ | PostgreSQL / Apache | 本地或容器 | 准生产，一库通吃 |
| FastAPI | 当前稳定版 | MIT | Python | SSE 流式 |
| AntV G6 | v5.1.1（**已落地** 2026-09-15） | MIT | npm 前端（frontend/） | 知识图谱渲染（M8 v2.2）；引入后 dist JS ≈1.65MB（gzip ≈481KB，后续可代码分割） |

---

## 4. 两条落地路线（决策点）

| | **A：LightRAG 内核 + 自研 Agent 层（推荐）** | **B：直接上 RAGFlow 平台** |
|---|---|---|
| 本质 | 用 LightRAG（MIT）做数据导入/建图/检索，自己写组装层 | RAGFlow v0.27 的 Knowledge Compilation（Graph 模式）+ Agentic RAG + DeepDoc |
| 优点 | 图/检索/prompt 全可控；MIT 无平台锁定；成本只随 token；**贴合「开发一个 GraphRAG 项目」的目标** | 开箱即用：文档理解、图编译、引用溯源、可视化编排、中文支持最好、私有化完整 |
| 代价 | 文档切分、引用、流式、UI、评测都要自己写或接 | 定制受平台边界限制；组件重（ES/Infinity）；**v0.27 已废弃旧 GraphRAG**，跟随官方演进节奏 |
| 适用 | 要深度定制、学习/研发导向 | 优先交付业务价值、研发投入少 |

**推荐 A**（与你「开发 GraphRAG 项目」的目标一致）。**建议借用 RAGFlow 的两个成熟能力**：DeepDoc 的文档切分思路（照搬到自建切分层），以及 MinerU/Docling 作为解析器插件的接入方式。既有平台（RAGFlow/Dify）当参考与对照基准，不作为运行依赖。

---

## 5. 风险与注意事项清单

1. **License**：MinerU 定制 Apache 条款（§2.2）；Neo4j Community GPLv3（§2.5）；ES 双许可坑。轻量图方案下图库选 Postgres 内联即可绕开大部分。
2. **维护状态**：微软 GraphRAG 已维护模式（仅修 bug）→ 只当检索/组装思路参考；**LightRAG 2026-09 仍日均提交**，但 v1.5 迭代快，需锁定版本并在升级前回归测试（版本锁定表见 §3.1）。
3. **成本**：抽取环节最贵 → 便宜模型 + LLM 缓存 + 增量更新（不要全量重建）。
4. **中文抽取噪声**：轻量图不加 schema 约束时实体质量不稳 → 可选 `SchemaLLMPathExtractor`（LlamaIndex）思路给抽取加 schema；或用 RAGFlow Graph 编译的受约束抽取作对比。
5. **全局问答能力**：放弃社区报告后，纯 LightRAG `global` 的主题综述能力弱于微软 GraphRAG。若后续发现「全库综述」需求频繁，再评估在 light 图基础上补社区摘要（参照 HiRAG 分层树思路）。
6. **LangExtract**：默认 Gemini 且非 Google 官方支持产品（医疗场景受 Health AI Developer Foundations 条款约束）；作为可选增强层，接国内模型需验证第三方 provider 插件。
7. **模型网关单点（Xinference）**：embedding/rerank 都经它，服务不可用则检索不可用 → 纳入健康检查与启动脚本；其余模块禁止绕过它直连本地模型。

---

## 6. 分阶段实施路线（方向级）

> 原则：本步只定**顺序、每步做什么、每步的重点是什么**；具体任务清单在进入该步时再展开（记录到 `docs/modules/Mx_xxx.md`），方向未定前不写细。

| # | 操作（做什么） | 重点（关注什么） |
|---|---|---|
| **① 拆解 LightRAG 源码** | 拉取并锁定版本（v1.5.7 起），安装跑通最小样例，通读目录结构 | 源码拆解四大块：**存储 / 检索(retriever) / 图构建与实体抽取 / LLM 接入 / 模块间接口规范**；验证 §2.4「内建 vs 自研」边界为真，明确 RRF / rerank / 关键词路哪些需外层实现 |
| **② 拆解 MinerU / Docling 源码** | 分别拉取安装，用样例 PDF / docx 跑通，读源码 | **数据流**（输入 → 版面/文字 → 中间表示 → 输出；页码 / 表格 / 公式 / 标题路径如何携带）与**输入输出接口规范**；确认可归一为统一 Markdown（M1 adapter 的对接依据） |
| **③ 部署 Xinference 模型网关** | 启动 Xinference，加载 bge-m3 与 bge-reranker-v2-m3，写启动 + 健康检查脚本 | 验证 embedding / rerank 端点可用；**实测 bge-m3 dense+sparse 双输出**（「免建 BM25」的前提，拿不到早失败）；可与 ①② 并行 |
| **④ 定契约地基（共享层 M0）** | 定义 Document / TextUnit Schema、config（模型映射 / 缓存 / 语言）、llm_client / embed_client | **契约先行**：TextUnit 字段（text / doc_id / 页码 / 标题路径 / 块类型 / embedding / 实体引用）与 doc_id 规则定死，所有模块据此开发、用样例数据 mock 上下游；**契约以 JSON Schema 机器校验落地，说明见 `docs/modules/M0_contracts/`** |
| **⑤ Agent 编排（M6）** | 按 LangChain 薄包装接入（history + 工具协议）；定意图路由与上下文组装的方向 | **编排边界**：组装 / 引用自研，LangChain 不做重抽象；single-window 与 map-reduce 各治哪类问题 |
| **⑥ 前端设计与展示** | ① （可选）Figma 画稿，对齐「问题 → 回答 + 引用 → 定位原文 → 图谱游走」动线；② **搭前端工程并引入 AntV G6**（本机未装：npm 初始化 + 安装 `@antv/g6`，属部署步骤）；③ **与 ⑤ 编排对齐，定前端所需的最小 API 契约**（SSE 事件定义 / 引用标注字段形状）；④ 按主轴线实现页面：回答 + 引用高亮 → 点击定位原文 → 答案节点图上游走 | 页面**只依赖稳定 API**（SSE + 引用标注）、与后端解耦；时序是**先部署依赖（②）→ 画稿对齐（①）→ 定 API 契约（③）→ 渲染实现（④）**；G6 只做渲染层，布局/交互体验在稿上先定 |
| **⑦ 联调与评测基线** | 后端各段（解析 → 切分 → 索引 → 检索 → 编排）+ **前端页面端到端**汇合：FastAPI（StreamingResponse + SSE）作统一入口，产物经「契约校验（④ 的 Schema）/ 写库」衔接；跑真实文档，建 RAGAS 种子测试集；**含前端走查**（引用高亮 / 点击定位 / 图谱游走）；最后 **Docker Compose** 收敛部署 | 联调**先过契约再过行为**；靠**统一日志 / 观测**定位断点（契约错 vs 行为错）；评测基线守住每次改动；前端当「眼睛」，端到端问题在真实路径上暴露 |

> **当前推进状态（2026-09-16）**：①–⑦ 路线已全部落地（模块记录在 `docs/modules/`），**M9 评测层待启动**；Docker Compose 收敛部署未做（后续项）。各模块版本与实测见 `docs/CHANGELOG.md`。

---

## 7. 决策已确认（本稿定稿基准）

| # | 项 | 结论 |
|---|---|---|
| 1 | 硬件 | 本机 Mac M3 / 24GB / Metal 3（已核实） |
| 2 | LLM | DeepSeek（OpenAI 兼容 API） |
| 3 | 内容形态 | PDF 为主 + doc/txt/md |
| 4 | 全局综述 | 需要 |
| 5 | LangExtract 结构化抽取 | 暂不纳入 MVP，作后续可选项 |
| 6 | 目标 | 练手、测通即可；不预优化运维成本 |
| 7 | embedding/rerank 载体 | **Xinference**（本地模型网关，支持 dense+sparse 与 `/v1/rerank`；不选 Ollama） |
| 8 | DeepSeek 模型 | **统一 `deepseek-v4-flash`**（抽取+生成；不用 pro；2026-09-13 实测定案，见 CHANGELOG v1.0） |
| 9 | 检索增强 | 三路 RRF(k=60) + rerank **外层自研**（模块 M5） |
| 10 | 开发形态 | 按 **M0–M9** 模块化重做；契约先行、线 A/B 并行走；记录按 docs/modules/ |

---

## 8. 实测附录（历史，已归档）

> 2026-09-16 起：实测记录（M3 DeepSeek 抽取对比、M5 检索四题与 A/B 预处理对比、M7/M8 落地闭环与前端走查、实现期踩坑）**按时间并入 `docs/CHANGELOG.md` v1.0 各模块条目**，本节不再留存正文。
> 数据现状：`inputs/raw` 5 文档（会议纪要.docx / 办公用品.pdf / 季度复盘.pdf / 投诉SOP.md / 产品需求.docx）已全量过 M1→M2→M3；正式库 `data/lightrag_deepseek`（DeepSeek）与对照库 `data/lightrag`（GLM）已于 2026-09-20 归档 `data/archive/`，当前正式数据以 PG（workspace=`default_ws`）为准。