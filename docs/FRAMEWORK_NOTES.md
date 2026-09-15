# GraphRAG 重做：框架实现补充笔记（对话整理 v1）

> 整理日期：2026-09-11 ｜ 定位：**`docs/ARCHITECTURE.md` 的补充细节与待办清单**，不替代架构文档。
> 来源：重做规划讨论（旧代码审查结论、模块划分、宏观选型、概念澄清与技术决策）。
> 范围：本文件不含 §6 分阶段实施路线（另行撰写）。
> 约定：文中「内建」指 LightRAG 开箱具备的能力，「需自研/外包」指要在外层我们自己实现。

---

## 1. 重做定位（2026-09-10 决策）

- **旧代码已删除**（原 `scripts/` 全部），从零重建。
- **三样资产复用**：`inputs/raw/`（原始文档）、`storage/` 与 `storage_backup/`（旧 LightRAG 索引库，作对照/参考）、`docs/ARCHITECTURE.md`（框架选型蓝图，结论不变）。
- 重做原因：第一版方向正确，但**模块内部细节失控，越到后期问题越大**——重做时按架构文档的结论 + 本文件的审查清单逐条规避。
- 遗留待清理：`inputs/parsed/`（旧 MinerU 中间产物），新解析层建议换目录输出后清理。

---

## 2. 架构审查结论（概要）

> 初步调研版 ARCHITECTURE 存在若干明显的事实/表述错误（模型名、LightRAG 能力边界、embedding 载体、许可证表述等），**已全部在 ARCHITECTURE.md v2 修订中按该稿顶部摘要校正，此处不再逐条记录**。仅保留被本笔记其他章节引用的三个编号：

- **A1｜内建边界**（按 `lightrag/docs/retriever.md` 实测修正）：LightRAG `mix` 无关键词路（sparse/BM25）、无多路融合 RRF；内建 **chunk 重排管道默认关闭、仅作用 chunk**（可复用管道）→ 关键词路与 RRF 融合放外层（M5），对应 §4、§5.5。
- **A2｜embedding 载体**：Ollama 无 sparse → 定 Xinference，对应 §5.3、§5.6。
- **B3｜语言设置**：实体抽取需另配 `language=zh`，仅 `SUMMARY_LANGUAGE=zh` 不够（见 ARCHITECTURE §3）。

---

## 3. 模块划分（M0–M9，高内聚 / 低耦合）

```
                    ┌───────────────── 数据线 A（离线）──────────────────┐
原始文档 inputs/raw │  M1 解析层       M2 切分层      M3 索引层         │
  │                │  MinerU/Docling → 统一MD → TextUnit → 图+向量写入  │
  ▼                └─────────────────────────────────────────────────┘
                                                    │ 经 M4 存储层
                    ┌──────────────── 解答线 B（在线）─────────────────┐
用户问题             │  M5 检索层   M6 编排层        M7 交互层          │
  │  HTTP/SSE       │  LightRAG召回 → RRF+rerank → 组装+引用 → FastAPI/UI│
  └────────────────┼───────────────────────────────────────────────────┘
        M0 共享层（数据契约 / 配置 / LLM 与 Embedding client）──所有模块只依赖它
        M9 评测层（中文测试集 + RAGAS 指标，贯穿全程）
```

| 模块 | 职责边界 | 输入 → 输出契约 |
|---|---|---|
| **M0 共享层** | 数据模型、config、LLM/Embedding client（经 **Xinference**）、日志（唯一对外依赖） | 定义 TextUnit 契约 |
| **M1 解析层** | MinerU 主力 / Docling 多格式 / 扫描件路由；解析状态与重试 | 原文件 → 统一 Markdown |
| **M2 切分层** | 标题层级切分 / TokenChunker / 表格整块 / 元数据抽取 | 统一 Markdown → TextUnit(JSONL) |
| **M3 索引层** | LightRAG 建图（中文实体抽取、增量/选择性删除）+ bge-m3 编码 | TextUnit → 图+向量 |
| **M4 存储层** | 统一存取接口，后端可切换（默认 → Postgres+pgvector） | 供 M3 写、M5 读 |
| **M5 检索层** | 内建三路 + sparse/BM25 关键词路 + RRF(k=60) + rerank（经 **Xinference** bge-reranker）+ query 预处理 | query+模式 → 精排 context 带来源 |
| **M6 编排层** | 意图路由（**LangGraph**）/ map-reduce / 上下文组装（token 预算）/ 引用标注 / 流式事件（**LangChain** 薄包装） | query+context → 答案+引用 |
| **M7 交互层** | FastAPI + SSE + 多轮记忆 + WebUI + **文档管理**（`POST/GET /docs`、`DELETE /docs/{doc_id}` 软删，见 v3）+ **图谱导出**（`GET /graph`，软删过滤，见 v4）+ **文档预览**（`GET /docs/{doc_id}/preview`，见 v5）+ **按文档过滤图谱**（`GET /graph?doc_id=`，见 v5） + 引用排序修复 | HTTP → 流式响应 / 文档列表 / 图谱 JSON / 预览 JSON |
| **M8 前端实现** | 正式 Web 前端（消费 M7 契约）：**v1** 答案 + 引用定位原文（SSE 逐字）→ **v2.1** 文档上传/文档管理视图 → **v2.2** 知识图谱（AntV G6 全图 + 引用「在图谱中查看」联动聚焦） → **v2.3** 左侧可折叠侧边栏 + 右栏 tab（引用/预览） + 文档全文预览（置信度第一片段高亮） + 引用按置信度排序并限前 5 条 + 图谱按文档过滤 | HTTP/SSE → 用户界面 |
| **M9 评测层** | 中文测试集 + RAGAS 指标 + 回归对比 | 测试集 → 指标报告 |

> **关键技术排查清单**（拆模块时容易漏的技术支撑，与模块的归属对应）：

| 关键技术 | 归属模块 | 在本项目的角色 |
|---|---|---|
| **Xinference（模型服务网关）** | M0（client）＋ M5（rerank） | 统一承载 bge-m3（dense+sparse）与 bge-reranker-v2-m3，全项目经 HTTP 共用；**不属于任一个模块的内建部分** |
| **LangChain / LangGraph** | M6（薄包装） | 仅用于对话 history 管理与工具调用协议（agent 可调图谱/文档检索工具）；组装逻辑自研，不深度用 LCEL |
| **Postgres + pgvector** | M4（存储实现） | 准生产地基：图 / 向量 / 原文块 / 状态一库 |
| **FastAPI + SSE** | M7（后端壳） | 对外 HTTP + 流式响应 |
| **AntV G6** | M8（前端渲染） | **已落地 v2.2**：知识图谱渲染 + 引用→图谱联动（答案→引用→原文→图上游走），v5.1.1 |
| **RAGAS** | M9（评测） | LLM 裁判打分 |
| **DeepSeek**（deepseek-v4-flash / pro） | M0（LLM client） | 抽取用便宜模型、生成用强模型 |
| **MinerU / Docling** | M1（解析层） | PDF 主力 / 多格式兜底 |

**低耦合三原则**
1. 只 import M0；模块间走「写文件 / 写库」衔接，不互调业务函数。
2. TextUnit 契约先行（M0 里定死 Schema），模块用样例数据 mock 上下游。
3. 每模块交付：独立 CLI 入口 + 契约校验 + 冒烟样例；联调先过契约再过行为。

**实施与联调顺序**
1. M0 + M4 先行（定契约与存储底座——两条线的汇合点）。
2. 线 A：M1→M2→M3；线 B：M5→M6→M7（用固定样例 context 并行走，不等数据线）。
3. 半程联调：M1–M4 完成后「文档 → 可检索」闭环先通；M5–M6 接上全链路。
4. M7 + M8 收尾，M9 评测回归。

**记录方式**：每模块 `docs/modules/Mx_xxx.md`（职责 → 契约 → 任务清单 → 验收 → 已知坑）；联调进度 `docs/integration/`。

---

## 4. LightRAG 源码拆解：四个「撕开点」

| 撕开点 | 处理 | 说明 |
|---|---|---|
| **存储层 storage** | **替换** | 默认 JsonKV+NetworkX+numpy 是内存态；换官方 Postgres+pgvector（准生产地基层，见 §5.10） |
| **检索外层 retriever** | **外包扩展** | 内建 local/global/naive 保留；RRF + rerank + query 预处理全在 M5 外层（对应 A1） |
| **图构建 graph** | **微调复用** | 主流程复用；改造点仅为中文实体抽取的 prompt / `language=zh`（对应 B3） |
| **LLM 接入 llm** | **改配复用** | 接 DeepSeek；抽取/生成分模型、开 LLM 缓存、增量更新与选择性删除 |

---

## 5. 概念澄清与技术决策

### 5.1 LangChain vs Harness（做 agent 时的分工）

- **它是两层，常一起用，不是二选一**：harness = agent 主循环外壳（谁调度「输入→选工具→执行→看结果→继续/终止」、权限、停止条件、上下文管理、暂停/恢复）；LangChain/LangGraph = 工具定义与状态编排辅助。
- 类比：harness 是底盘+驾驶舱，LangGraph 是可插拔自动驾驶套件。**车能开不靠套件，但套件让定制舒服。**
- 做 OpenClaw 式 agent：**必须自己写/引入一个 agent loop（harness）**，但 **LangChain 不是必须**——OpenClaw 类平台核心自有一套 agent loop，不依赖 LangChain 厚重抽象。
- **本项目定位**：两端都不用深做；只封一层很薄的「工具调用协议」（agent 能调图谱/文档检索两个工具）+ 对话 history 管理，其余交 LightRAG。Agent 编排是 P3 阶段的薄包装。

**记忆主体澄清**：GraphRAG 的长期知识在**图谱/向量库本身**；LangChain 只承载「多轮对话 messages 历史」这一层，两者不抢活。

### 5.2 专业前端：Figma 与渲染库的关系（易混淆点）

- **Figma 是设计工具**，产出静态设计稿/视觉规范，不参与运行时渲染。
- 运行时真正干活的是**图可视化渲染库**：本项目重点在图谱展示，图可视化推荐 **AntV G6**（蚂蚁，中文生态、力图布局、交互文档最友好）或 react-force-graph。
- **结论**：图可视化场景的「本体选择」在渲染库；Figma 用不用取决于是否要先画稿对齐——图谱展示这条「问题→回答+引用高亮→点击引用定位原文→从答案节点在图上游走」动线值得先画稿。Figma 是可选项，不是依赖。

### 5.3 模型服务网关（Xinference）与「三模型统一」

三个模型（生成 / embedding / reranker）各自独立部署的问题：各起各服务、显存不统筹、各自配版本与健康检查、出问题要查多套日志。
本地推理服务（Xinference 与 Ollama / vLLM / LMDeploy 同类）把它们收进**一个进程**：
- 一个 HTTP 地址，三种接口（生成 / embedding / rerank）；一份日志、一次健康检查、一个启动脚本；显存/批处理统一调度；换模型只改一处配置。
- 逻辑等价：**像 DeepSeek API 把服务托管好一样，只是它在本地**。

**为什么选 Xinference 而非 Ollama**：Xinference 的 FlagEmbedding 后端能同时输出 **dense + sparse**，且提供标准 **rerank 接口**；Ollama 只吐 dense、也**没有标准 rerank 端点**。这一条直接决定 A2 的解法。

### 5.4 模型网关 / 数据库：各自应重点盯什么

| 组件 | 态度 | 关注点 |
|---|---|---|
| LightRAG | 源码拆解重建 | 四个撕开点（§4） |
| MinerU / Docling | 重度使用 | 数据流、输入输出接口规范 |
| 模型网关 | 拿来即用 | 接口契约（HTTP + OpenAI 兼容）、版本锁定、服务可用性/健康检查 |
| 数据库 | 自建 adapter | 表结构设计 + **存储语义还原**（NetworkX 图遍历语义换成 SQL 后不能变） |

### 5.5 检索原理：dense / sparse / 图 三路与 RRF

三种向量的分工：

| | dense（稠密） | sparse（稀疏） | 图遍历 |
|---|---|---|---|
| 形态 | 1024 维浮点（语义坐标） | 大多为 0、仅出现过的词有权重的长向量 | 实体-关系图 |
| 擅长 | 语义相似（同义改写） | 精确匹配（专名/编号/条款） | 跨文档关联、多跳 |
| 例子 | 「合同终止的补偿金」命中「届满不予续签的赔偿」 | 「OpenClaw」「阿尔茨海默病」 | A 文档实体 ↔ B 文档关系 |

三路合流示意：

    bge-m3（一个模型）
       ├── dense  ──► 语义路（同义改写）
       └── sparse ──► 关键词路（专名 / 编号）
    LightRAG ───► 图遍历 ──► 图路（跨文档关联）
                      ↓
        三路各自召回 → RRF(k=60) 融合 → rerank 精排（Xinference 承载）

**bge-m3 的价值**：一个模型同时输出 dense 与 sparse 两路 → 一套管线内「语义路 + 关键词路」齐备，**免建独立 BM25**。第三路图遍历由 LightRAG 提供。三路各自召回后用 **RRF（倒数排名融合，k=60）** 合并，再经 **rerank 精排**。

> ⚠️ A1 提醒：这套「三路 + RRF + rerank」**不是 LightRAG 开箱能力**，融合与精排要在外层（M5）实现。

### 5.6 sparse 与 BM25 的优劣 / 替代边界（本版新增）

共同点：都是把文档表示为「每个词一个权重、多数为 0」的稀疏向量，打分基于「query 词在文档中的出现情况」——都对专名/编码/条款这类精确信息高命中，这是 dense 做不到的。

**本质差异在「权重怎么来的」**：

| 维度 | BM25 | bge-m3 sparse |
|---|---|---|
| 原理 | 纯统计公式（词频 × 逆文档频率 × 长度归一 + k1/b 平滑） | 神经网络**训练得到**的词权重 |
| 需要训练 | 无 | 有（bge-m3 自带） |
| 可解释 / 可调 | 强（k1/b、字段加权、词典扩展） | 弱（黑盒权重） |
| 分词 | 可挂任意分词器 / 领域词典（法律、医、代码） | 固定 BPE 词表，不可定制 |
| 中文适配 | 需分词器 | bge-m3 多语言词典内置 |
| 部署开销 | 需单独索引 + 服务 | 与 dense 同一模型，**零额外服务** |
| 与 dense 联动 | 独立组件 | 同模型同管线，天然配合 |

**BM25 不可替代的时机**：
1. 需要 k1/b 调参、标题/关键字段加权、query 扩展等**精细控制**；
2. **领域分词器**（法律、医疗、代码）效果优于固定词表；
3. 已有 BM25 基础设施，不值得重搭；
4. 需要**可解释命中词**给前端做「搜索依据」高亮。

**sparse 可以替代 BM25 的时机**：
1. **已部署 bge-m3 需要 dense**——sparse 顺带取得、零额外服务与维护（最常见的替代理由，恰好是本项目）；
2. 不想维护中文分词词典 / 停用词表（内置多语言词表）；
3. 通用场景下训练权重与 BM25 相当或略优（bge-m3 在 BEIR 等基准上 sparse 路不输 BM25）。

**本项目决策**：MVP 用 bge-m3 dense+sparse 作为「向量+关键词」两路，**不另建 BM25**；若 M9 评测发现中文专名/精确查询召回不足，再补 BM25 或替换 sparse——**M5 关键词路做成可切换接口**（`sparse` / `bm25` 两种实现），避免路径写死。

### 5.7 rerank：现成接口 vs 自建

- **rerank（cross-encoder）原理**：query 与每个候选文档拼成一句整体过模型打分——比向量检索精确得多、也慢得多，因此只用于「粗召回后精排 top 几十条」。
- **接口是行业标准**：`/v1/rerank`（源自 Cohere，被 Xinference / TEI / Jina / SiliconFlow 实现）。用 Xinference：模型变稳定 HTTP 端点，**按接口规范 POST 即用**（传 query + 候选列表 → 返回每个候选分数）。
- **自建才需要写**：加载模型 → 手写推理循环 → 自己起 HTTP 服务。这正是「拿来即用 vs 自己搭」的差距。

### 5.8 NetworkX 与图存储

- NetworkX 是 Python **内存态图算法库**；LightRAG 默认用它存图（`storage/graph_chunk_entity_relation.graphml` 即其序列化产物）。
- **不适合作生产持久化**：数据全在内存、落盘靠序列化文件，文档一大图就重。
- 准生产把「图」换成数据库内可查询的形态（Postgres 关系表 / JSONB，见 5.10）。

### 5.9 评测体系（RAGAS）与旧「自发自测」的差异

**RAGAS 核心**：不逐条标注标准答案，用 **LLM 当裁判**按维度打分：
- **Faithfulness（忠实度）**：答案每个论断能否在召回 context 里找到依据（防乱编）；
- **Context Precision（相关性）**：召回的片段里多少是答案实际用上的（治检乱召回）；
- **Context Recall（召回率）**：正确事实所在片段是否真被召回（治该召回丢了）；
- **Answer Relevance**：答案是否切题。

**闭环**：固定测试集（50–100 条，分类覆盖实体级/多跳/全局综述/中文专名；每条只标「应命中哪些文档」）→ 整套跑一遍 → LLM 判分（可用 DeepSeek 便宜模型）→ 回归对比（改代码/提示词后重跑比总分、比哪类 query 变坏）。

**与旧自测的本质区别**：测试集固定可版本化（非随手提问）→ 量化指标（非主观观感）→ 可回归比对 → 失败可下钻（定位是检索没召回还是组装编造）。局限：LLM 裁判本身有偏差，但我们要的是**相对比较**而非绝对分数。

### 5.10 框架选型确认：仍用 LightRAG + 官方 Postgres（工程量修正）

**修正一个关键认知**：`把 LightRAG 存储语义还原成 SQL` 官方**已做过**——PostgreSQL storage（pgvector + 图）是其**首推生产路径**（README 支持列表之首）。故 M4 是「拉官方实现 → 核对表结构与图遍历语义 → 按需改造」的**适配工程**，不是从零重写。唯一要核实的点：官方近期版本图用 JSONB 存储时，local/global 图遍历语义是否保持——属拆分源码时的核实项，不是否决项。

**候选对比**（约束：可拆源码 / 中文 / Postgres 官方支持 / 维护状态）：

| 框架 | 可拆源码 | 中文 | Postgres+pgvector | 维护 | 结论 |
|---|---|---|---|---|---|
| **LightRAG** | ✅ MIT、分层清晰 | ✅ 中文场最热 | ✅ 官方首推 | ✅ 活跃 | **保持，最优** |
| 微软 GraphRAG | ⚠️ 重、贵 | ⚠️ 弱 | ⚠️ 第三方 | ❌ 维护模式 | 仅参考 |
| RAGFlow | ✅ 平台级 | ✅ 最好 | ✅ | ✅ | 对照基准，不做内核 |
| nano-graphrag | ✅ 更轻 | ⚠️ 一般 | ⚠️ 未完整 | ⚠️ 更新慢 | 备用，不优先 |
| HiRAG | 仅借「分层树」思路 | ⚠️ 中文好 | ⚠️ 未完整 | ⚠️ 不稳 | 只借全局综述思路（§5 / ARCHITECTURE §5-5） |

**结论**：不换。要的图谱可视化/引用溯源在自研层（Agent 编排 + 前端），与底层框架解耦；且不成熟开源 GraphRAG 后期暴雷概率高。

---

## 5.11 实测记录：M3 索引层 DeepSeek 后端定案（2026-09-13）

**决策**：LLM 后端**统一 DeepSeek v4-flash**（抽取+生成全 flash，不用 pro），GLM 降为临时备选。正式索引目录 `data/lightrag_deepseek/`（全 5 文档）；`data/lightrag` 保留为 GLM 对照库。详细对比与复现见 `docs/modules/M3_index.md`。

**DeepSeek v4-flash 思考模式修复**（A 级坑，涉及 DeepSeek 系列推理模型）：
- 症状：抽取失败/重试卡死，`content` 为空；根因是推理模型默认思考模式占 `reasoning_tokens` 75–100%，极端时烧光 `max_tokens`。
- 修复：`extra_body={"thinking": {"type": "disabled"}}`。**OpenAI SDK 不接受 `thinking` 直接参数**（报 `AsyncCompletions.create() got an unexpected keyword argument`），必须走 `extra_body` 透传；`reasoning_effort=low` 与 `reasoning={effort:"none"}` 均无效。
- 依据：DeepSeek 思考模式文档（api-docs.deepseek.com/zh-cn/guides/thinking_mode）。

**GLM 免费档抽取质量差（对比数据）**：

| 文档（页/块） | GLM 实体/关系 | DeepSeek 实体/关系 |
|---|---|---|
| 会议纪要（1/4） | 23/23（含表头词噪音） | 16/10 |
| 办公用品（1/1） | 16/13 | 10/9 |
| 季度复盘（2/5） | 76/88（大量「指标+数值」脏实体） | 47/48 |
| 投诉SOP（2/15） | 曾失败 | 110/121 ✅ |
| 产品需求（2/5） | 未做 | 53/45 ✅ |

结论：DeepSeek 数量少但更净——实体名贴近业务对象（如 `智能客服系统迭代排期会议`），数值沉淀进关系描述（`<data key="d8">`）；GLM 把表头词（`事项`/`负责人`）、「指标+数值」拼接（`ARPU值提升8%`/`收入1,200万元`）当实体，图噪声大。信息不丢，效率高（30 块几分钟零失败）。

---

## 6. 待办与下一步

1. ✅ 已完成：`docs/ARCHITECTURE.md` 修订至 **v2**（A/B 档修正、§3.1 版本锁定总表、§6 方向级实施路线）。
2. ✅ 已完成：M0 契约拆解实际落为 `docs/modules/M0_contracts/`（textunit schema v2 + parse 产物契约），TextUnit 契约、config、llm/embed client。
3. ✅ 已完成：**LightRAG 源码拆解**按 §4 四个撕开点解剖（storage 替换 / retriever 外包 / graph 微调 / llm 改配），记录在 `lightrag/docs/` + `docs/modules/Mx_*`。
4. ✅ 已完成：**M0–M8 全线落地**（2026-09-15）——M1 解析 → M2 切分 → M3 索引(DeepSeek) → M4 存储(PG) → M5 检索 → M6 生成 → M7 交互层(v3 文档管理 / v4 图谱导出 / v5 预览+按文档过滤+引用排序) → M8 前端(v1 问答 / v2.1 文档上传管理 / v2.2 知识图谱 G6 / v2.3 侧边栏+预览+引用排序+图谱过滤)，模块记录在 `docs/modules/`，联调验证在各自记录 §7/§8。
5. ⏳ **M9 评测层**（不着急）：中文测试集 + RAGAS 指标 + 回归对比。快速证伪点 = LLM 裁判（DeepSeek 便宜模型判分）能否稳定区分「检索召回不足 vs 组装编造」。**投产评测暂不启动**。