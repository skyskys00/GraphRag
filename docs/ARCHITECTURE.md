# GraphRAG 系统架构与选型方案（v1）

> 调研日期：2026-09-03 ｜ 状态：规划基准稿，待确认项见 §7
> 本节结论由 4 路并行调研交叉验证（框架层 / 解析层 / 存储检索层 / Agent 与平台层），
> 信息源自 GitHub 一手仓库与官方文档，个别观点标注了来源与风险。

---

## 0. 决策基准（用户已确认）

| 项 | 决定 | 对架构的影响 |
|---|---|---|
| 规模 | 个人/小团队知识库（数十万文档以内） | 单机可承载，优先「可嵌入、零运维」，避开集群 |
| LLM | 国内模型为主 | 全链路走 OpenAI 兼容接口接 DeepSeek / Qwen / GLM；控制 token 成本 |
| 图谱 | 轻量图（LightRAG 式） | 图作「关系索引」辅助跨文档查询，不做全量社区检测 |
| 部署 | 本地/私有化单机 | Docker Compose 编排，数据不出内网 |
| 硬件 | 本机 **Mac M3 / 24GB 统一内存 / Metal 3**（arm64，已核实） | Apple Silicon MPS 可加速 MinerU 与本地模型；Python 需 conda 建 3.10+ 环境（系统 3.9 太老） |
| LLM 接入 | **DeepSeek**（OpenAI 兼容 API） | 抽取/生成统一走 DeepSeek，单 key 跑通全链路 |
| 内容形态 | **PDF 为主** + doc/txt/md 等 | MinerU 主力 + Docling 补多格式 |
| 综述问答 | **需要全局综述** | LightRAG `global`/`mix` 起步，按效果决定是否补社区摘要 |
| 目标 | **开发练手、测通即可**；暂不优化长期运维成本 | 存储优先「跑通」，MVP 用 LightRAG 默认存储 |

**核心选型一句话**：
**LightRAG（框架内核）+ MinerU 3.x（文档解析）+ bge-m3（中文 embedding）+ Postgres/pgvector（持久化）+ LangGraph/轻量 Agent 层（编排与组装）**，全部本地私有化，抽取/建图用国产便宜模型。

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
│  [5 检索召回] mix/混合：local(图遍历)+global(关系链)+naive(向量)+BM25       │
│              → RRF(k=60) 融合 → bge-reranker 精排                          │
│    │                                                                      │
│    ▼                                                                      │
│  [6 Agent 编排] LangGraph（或轻量路由）：                                  │
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

1. **统一文档格式 = Markdown**（解析层出口）。MinerU 输出 Markdown、Docling 输出 Markdown、RAGFlow DeepDoc 也归一为带版面的排版结构后进 Markdown。**理由**：Markdown 是 LightRAG/LangChain/LlamaIndex 全部原生输入的通用语言；保留标题层级、表格（HTML）、公式（LaTeX）信息，是后续「标题层级切分」和图表问答的前提。原始 PDF 仅存档，不参与索引。
2. **统一数据模型 = Document ─ TextUnit**（参考微软 GraphRAG 的数据模型）。每个块（TextUnit）必须携带：`text / 来源 doc_id / 页码 / 标题路径（hierarchy） / 块类型（text|table|title|...） / embedding / 关系出的实体引用`。所有下游（向量、图、引用标注）都只认这套模型。

> 反面教材：直接把 pdfplumber 逐页吐的裸文本喂给检索，会丢掉结构，导致中文表格/条款块切开、引用无法溯源。

### 2.2 文档解析层（选型结论）

| 工具 | 定位 | 结论 |
|---|---|---|
| **MinerU 3.x**（opendatalab） | PDF/扫描件 → Markdown/JSON，中文解析开源第一梯队；自动去页眉页脚、阅读顺序、公式→LaTeX、表格→HTML、跨页表格合并；离线私有部署 + Apple MPS/GPU | **主力解析器（强推）**。低资源用 `pipeline`（纯 CPU 可跑，4GB 显存），精度优先用 `vlm/hybrid`（8GB 显存）。需注意 license 为「MinerU Open Source License」（基于 Apache 2.0 的定制版，**非纯 Apache**，商用前读条款） |
| **Docling**（IBM，MIT） | DOCX/PPTX/XLSX/HTML/EPUB/邮件等**多格式**统一解析 | **多格式补充件**。中文版式弱于 MinerU，用作非 PDF 格式的兜底 |
| **PaddleOCR / PP-StructureV3**（Apache-2.0） | 中文 OCR 底座、表格单元格坐标 | **OCR 增强件**。MinerU pipeline 已内置 PP-OCRv6，一般不用重复部署；仅在需要印章/古籍/生僻字或表格坐标做二次结构化时单独用 |
| LlamaParse | 闭源 SaaS，绑定 LlamaCloud | **不推荐**（离线约束 + 付费 + 闭源） |

**MinerU 引擎选择**（pipeline / vlm / hybrid 三选一）：

| 维度 | `pipeline` | `vlm` | `hybrid`（有 GPU 时推荐） |
|---|---|---|---|
| 原理 | 传统 OCR（内置 PP-OCRv6）+ 版面分析 | 视觉语言大模型（MinerU2.5-Pro） | 两者融合，`effort=medium/high` |
| 硬件 | 纯 CPU 可跑；最低 4GB 显存（含 Apple MPS） | 必须 GPU ≥8GB 显存 | 同 vlm（≥8GB 显存） |
| 准确率(OmniDocBench v1.6) | 86.47 | 95.30 | 95.26(medium)/95.39(high) |
| 特点 | 快、稳、无 VLM 幻觉；复杂版面弱 | 最高精度、吃显存 | 精度高+低幻觉，medium 比 high 快 35–220% 且只降 0.13 分 |

**选择建议（本机 = Mac M3 / 24GB / MPS）**：练手阶段**首选 `pipeline`**（MPS/CPU 都能跑、最快最稳，中文常规文档 86 分够用）；遇到扫描件/复杂多栏再对该类文档单独开 `hybrid medium`（M3 统一内存可跑、会慢）；不主动上 `vlm`。

### 2.2.1 结构化抽取（可选增强）：google/langextract

> 用户原指的「longextract」实为 **google/langextract**（Apache-2.0，非 Google 官方支持产品），与同名评测基准 longextract-bench 无关。

- **定位**：「**文本 → 结构化知识**」的语义字段抽取库，**不是文档解析器**——输入是纯文本/URL（不处理 PDF 版面、无 OCR），工作中位于 MinerU 之后。
- **核心能力**：LLM 少样本驱动抽取（默认 Gemini，支持 OpenAI / Ollama 本地模型）；**schema 约束**（`output_schema` 强制结构）；**每个抽取值映射回源文本字符区间**（grounded，无法定位自动标 null）；长文档分块并行多轮抽取（可处理 14 万+ 字符）；输出 JSONL + 可交互 HTML 高亮可视化。
- **在 GraphRAG 里的用途**（与 LightRAG 内置实体抽取互补，非常规依赖）：
  1. 需**定向提取固定字段**的文档（合同要素、报告指标、临床记录）→ 按自定义 schema 抽成结构化记录，可作高置信度图节点/属性来源或结构化查询；
  2. **grounded 溯源**天然契合引用标注需求，可复用其「取值 ↔ 原文位置」思想强化答案溯源；
  3. 批量任务可用 Vertex/OpenAI Batch 降本。
- **注意**：默认走 Gemini——能否接国内模型取决于第三方 provider 插件，实现时需核实。

### 2.3 切分层

- **结构化文档（合同/手册/论文/法规）→ 优先「标题层级切分」**：按 MinerU 还原出的标题结构聚合，每个 chunk 是完整结构单元（RAGFlow 的 Title/Hierarchy Chunker 即此思路）。**不建议对结构化文档用纯语义切分**（慢、对表格/条款不稳）。
- **通用/长文本 → TokenChunker**：`chunk_size ≈ 512 tokens（中文约 500–1000 字）`，`overlap 10–15%`，**先按自然段落边界切再按 token**（避免从句子/表格中间切断）。
- **上限**：单 chunk 不超过 `1200–1500 tokens`（超出显著降低检索精度并超 reranker 预算）。
- **表格整块为一个 chunk**，标题/章节路径作为 metadata 前置。

### 2.4 索引层（双索引）

- **图索引（LightRAG 建图）**：LLM 抽取 实体/关系 构建轻量图。**每个环节可独立配模型**——抽取/建图用便宜模型（DeepSeek-lite/Kimi/Qwen），回答用强模型；开 `ENABLE_LLM_CACHE`（LLM 缓存）与 `MAX_ENTITY_TOKENS`（上下文截断）控成本。支持**增量更新与选择性删除**（复用索引期缓存重建受影响实体）——这是知识库持续扩写的关键。
- **向量索引**：TextUnit 用 **bge-m3** 编码（dense+sparse 一次拿到，8192 token，1024 维，中文强）。**无需另建 BM25 索引**——bge-m3 的 sparse 即稠密+稀疏混合检索的基础。精排用 **bge-reranker**。
- 五查询模式：`local / global / hybrid / naive / mix`，默认 `mix`（三者合并）——详见 §2.6。

### 2.5 持久化层

**注意（按你的练手目标）**：MVP 直接用 LightRAG 默认存储跑通功能即可，下表「准生产/扩展」两行只在需要时再启用——不要为了"生产就绪"提前引入 Postgres。

**分阶段演进，避免一开始就上重系统（个人单机从简）：**

| 阶段 | 存储 | 说明 |
|---|---|---|
| MVP（先跑通） | LightRAG 默认存储（JsonKV + NetworkX + 轻量向量库） | 零运维；**缺点：内存态，不适合长期保存，索引重建即费 token** |
| **准生产（推荐目标）** | **Postgres + pgvector 一库通吃**：图结构、向量、原文块、索引状态同一库 | LightRAG 官方推荐路径；pgvector 支撑「百万–数千万」级向量，个人/小团队完全够；**省一个独立向量库服务的运维** |
| 扩展（数据量再上万级 +） | 图 → **NebulaGraph**（Apache-2.0 可商用、中文原生、分布式）｜向量 → **Milvus**（亿级主战场、中文生态最强）或 **Qdrant**（部署轻、内置 RRF/DBSF） | 迁移成本高，非必须不要提前做 |

**许可证风险提醒（重点，避免踩坑）：**
- **Neo4j Community = GPLv3**（可商用内用，但**修改版对外分发须开源**，且 Community 单实例无高可用）；官网 license 页面有过 Commons Clause 历史——**下载发行版时务必核对 LICENSE 文件是否是纯 GPLv3**。真要开图库，**NebulaGraph（Apache 2.0）更省心**。
- **Elasticsearch**：默认 AGPL/SSPL/EL-2.0 三选一（SSPL/EL-2.0 非 OSI 开源，SSPL 网络服务触发源码公开）；**商用省心用 OpenSearch（Apache 2.0）**。
- **Chroma**：HNSW 必须全量驻内存，64GB 约放 1500 万条 1024 维向量——**适合起步，天花板低**。

### 2.6 检索召回层（hybrid 混合检索）

**结论：做「图谱 + 向量 + 关键词」三路混合，融合器用 RRF（k=60），精排用 bge-reranker。**

- `local`（图遍历）：query → 实体匹配 → 沿图扩张（实体↔原文/↔关系/↔邻实体）——实体级问答主力；
- `global`（关系链/跨文档主题）：轻量图下即关系链聚合，替代微软 GraphRAG 昂贵的社区报告；
- `naive`（纯向量）：语义召回，覆盖种子实体未命中的情况；
- `BM25/sparse`（bge-m3 自带）：精确匹配人名/机构名/中文专名/数字兜底。
- LightRAG 的 `mix` 模式天然把三路 RRF 融合，LLM 调用量比微软 GraphRAG 少一个数量级，**成本与延迟都适配个人单机**。
- 上线初期建议就用 **RAGAS + 自有中文测试集**（含人名/专名 query）定 baseline（faithfulness / context recall 等），再调轮重排与融合权重。
- **全局综述类问题**（已确认需要）：用 LightRAG `global`（关系链跨文档聚合）起步；若主题综述效果不足，再按 §5-5 补社区摘要（HiRAG 分层树思路）。

### 2.7 Agent 编排与上下文组装（「传给 agent → 返回用户」这一段的落地）

**推荐：LightRAG 内核 + 一层可控的 Agent 编排**（个人单机不必上重 LangGraph；若后续要多智能体、复杂工具调用，再引入 LangGraph 状态机）。

组装规范（抄微软 GraphRAG 的最佳实践、做中文化）：

1. **意图路由**：问题是否需要「全库级跨文档综述」→ 走 **map-reduce**（分块出带重要性评分要点 → 合并排序 → 生成）；实体级/局部问题 → 走 **single-window**（单上下文一次生成）。`response_type` 中文描述（如「请分段落、先总后分地回答」）。
2. **上下文组装（Local 风格，5 轨道）**：把 原文块 / 实体 / 关系 / 图谱路径 各路召回结果经过排名+过滤后，**压缩进一个预设 token 预算（`max_data_tokens`）的上下文窗口**，而非把各路原始结果整块塞进 prompt。窗口组装独立成函数，便于复用与测试。
3. **引用标注（citation）**：让「来源 doc_id / TextUnit ID / 节点名」在检索→重排→组装→生成的整条链路里流转，最终答案外层附 `[引用列表]`（参考 RAGFlow grounded citations 的思路）。local 层用 TextUnit ID 细引用，全局层用主题/报告名粗引用。
4. **流式输出**：SSE + token 级流式（FastAPI `StreamingResponse`）；map-reduce 场景只在 reduce 阶段流式。把「检索完成」「生成第 N 段」做成事件上报。
5. **语义缓存**（可选，二期）：高频相似问题按 embedding 相似度命中直接返回，缓存里同时保留命中问题的引用列表。

### 2.8 交互层

- **Web UI**：LightRAG 官方 server 自带 WebUI（文档管理 / 图探索 / 查询调试），个人单机够用；对外给 API 用 FastAPI 包一层（SSE + 引用标注 + 多轮会话）。
- 多轮会话记忆（可选用 RAGFlow「AI Memory」/Dify 会话变量思路；或自管 history 注入）。

---

## 3. 模型配置策略（国内模型）

| 环节 | 建议 | 说明 |
|---|---|---|
| 实体抽取/建图 | 便宜模型：DeepSeek-lite / Kimi / Qwen（可再开 `ENABLE_LLM_CACHE`） | 抽取是调用最密集的环节，用便宜模型省大头 |
| 回答生成 | 强模型：DeepSeek / GLM | 输出质量优先 |
| Embedding | **bge-m3 本地部署**（Ollama / Xinference / TEI） | 私有化场景首选自部署；也可 DashScope text-embedding-v3 API（`dense&sparse`，有免费额度）|
| Rerank | **bge-reranker-v2-m3**（本地） | 中文专名精确性提升明显 |
| 语言设置 | `SUMMARY_LANGUAGE=zh`（LightRAG） | 让实体/关系/报告直接输出中文，避免英文图污染 |

> 中文 token 膨胀约 2 倍：`max_tokens` 与 `max_data_tokens` 都要给足。

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
2. **维护状态**：微软 GraphRAG 已维护模式（仅修 bug）→ 只当检索/组装思路参考；**LightRAG 2026-09 仍日均提交**，但 v1.5 迭代快，需锁定版本并在升级前回归测试。
3. **成本**：抽取环节最贵 → 便宜模型 + LLM 缓存 + 增量更新（不要全量重建）。
4. **中文抽取噪声**：轻量图不加 schema 约束时实体质量不稳 → 可选 `SchemaLLMPathExtractor`（LlamaIndex）思路给抽取加 schema；或用 RAGFlow Graph 编译的受约束抽取作对比。
5. **全局问答能力**：放弃社区报告后，纯 LightRAG `global` 的主题综述能力弱于微软 GraphRAG。若后续发现「全库综述」需求频繁，再评估在 light 图基础上补社区摘要（参照 HiRAG 分层树思路）。
6. **LangExtract**：默认 Gemini 且非 Google 官方支持产品（医疗场景受 Health AI Developer Foundations 条款约束）；作为可选增强层，接国内模型需验证第三方 provider 插件。

---

## 6. 分阶段实施路线

> 阶段任务按「做得到、可验收」的边界划分；「完成形态」列是可感知的验收效果。交互层（UI/API）的分层定位见 §2.8，组装层规范见 §2.7。

| 阶段 | 内容（任务边界） | 完成形态（可验收） | 状态（2026-09） |
|---|---|---|---|
| **P0 跑通** | conda 建 3.11 环境 → `lightrag-hku` + `mineru` → 测试文档建图 → query 跑通 local/hybrid/global 综述 | 3 个模式的中文问答可用 | ✅ 完成 |
| **P1 多格式** | `scripts/parse.py`（MinerU/Docling 路由、幂等/容错/元数据）+ index 固定 `ids=doc_id`（见 p1.md） | 混合文档库（pdf/docx/md）一次性入库，3 问验收过 | ✅ 完成 |
| P2 引用溯源 + 交互层 | ① parse.py 扩展：填 `span_map`（md 偏移 ↔ PDF 页码，读 mineru `middle.json` / docling provenance）；② 切分层落地（§2.3）：结构化文档标题层级切分，TextUnit 携带 doc_id / 页码 / 标题路径；③ FastAPI 壳：`POST /query` 返回结构化回答 + `references[]`（含 doc_id+页码），SSE 流式（「检索完成 / 生成第 N 段」事件）；④ 交互：FastAPI 自带 `/docs`（Swagger）即可用，可选加 Streamlit 单页（上传 / 提问 / 引用列表） | ① parsed json 的 `span_map` 非空且指向真实页码；② 回答引用到页级，如「张伟 · 03_会议纪要.md · 第 1 页」；③ 浏览器经 `/docs` 或 SSE 能看到流式事件 | 待做 |
| P3 Agent 编排（对应 §2.7） | 自研组装层：意图路由（全局 → map-reduce；局部 → single-window）→ 检索多路结果压缩进 `max_data_tokens`（组装前过滤+排名）→ **带引用**生成；多轮会话（history 注入，记忆归 Agent 层管）；可选：rerank（bge-reranker，§2.6）与语义缓存（二期，§2.7-5） | 多轮追问能衔接上文；token 预算受控（组装前压缩）；答案引用列表随链路流转、可回到 TextUnit/页码 | 待做 |
| P4 扩展（按需） | Postgres+pgvector 一库通吃 / 图迁 NebulaGraph / 社区摘要补全局综述（§5-5） | 触发条件：数据量上万级，或全局综述不达标 | 按需 |

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