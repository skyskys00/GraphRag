# GraphRAG 重做：框架实现补充笔记（对话整理 v1）

> 整理日期：2026-09-11 ｜ 修订：2026-09-16（新增 §0 文档治理；按「结果导向」删除 §5 概念澄清与技术决策，实测记录并入 `docs/CHANGELOG.md`）｜ 2026-10-30（M10 agent 落地、M11 MCP 层立项，§3 模块图与关键技术清单对齐生产） ｜ 定位：**`docs/ARCHITECTURE.md` 的补充细节与待办清单**，不替代架构文档。
> 来源：重做规划讨论（旧代码审查结论、模块划分、宏观选型与技术决策）。
> 范围：本文件不含分阶段实施路线（见 `docs/ARCHITECTURE.md` §6）与版本时序（见 `docs/CHANGELOG.md`）。
> 约定：文中「内建」指 LightRAG 开箱具备的能力，「需自研/外包」指要在外层我们自己实现。

---

## 0. 文档治理（分层与单一事实源，v2.0 建立）

**五种文档各管一件事，用链接交叉引用、不复述内容**：

| 层 | 文档 | 权威内容 | 何时更新 |
|---|---|---|---|
| 蓝图 | `docs/ARCHITECTURE.md` | 方向 / 选型 / 版本锁定 / 路线 | 架构级决策变化时 |
| 决策 | `docs/FRAMEWORK_NOTES.md`（本文） | 模块分工表 / A1·A2·B3 概念决策 | 模块划分或概念决策变化时 |
| 模块 | `docs/modules/Mx_*.md` | 接口契约 / 关键决策 / 已知坑（结构见 `docs/modules/_TEMPLATE.md`） | 模块接口或实现结构变化时 |
| 需求·参考 | `docs/modules/M8_frontend_req.md` ｜ `docs/PARSER_COMPARISON.md` ｜ `docs/modules/M0_contracts/` | 需求稿 / 选型复核 / 契约定义 | 需求或契约版本变化时 |
| 时序 | `docs/CHANGELOG.md` | **版本变更的唯一权威时序 + 各版本实测结论** | **每次功能/修复落版都改** |

**关键规则（对症「每改一处要动 N 个文档」）**：

- **版本变更只进 CHANGELOG**，并连带更新受影响模块文件的**状态行**版本号；模块文件不维护历史流水。
- **实测记录只进 CHANGELOG**（结论 + 关键数据）；蓝图/决策/模块文档按「结果导向」保留结论，不写过程叙述与测试明细。
- **不复制内容**：正文引用其他文档一律用链接（如「见 ARCHITECTURE §x」「见 CHANGELOG」）。
- 一个功能落版 ≈ 只需动 2 处：CHANGELOG 登记一行 + 受影响模块状态行；接口/结构真变化才动正文。

---

## 1. 重做定位（2026-09-10 决策）

- **旧代码已删除**（原 `scripts/` 全部），从零重建。
- **三样资产复用**：`inputs/raw/`（原始文档）、`storage/` 与 `storage_backup/`（旧 LightRAG 索引库，作对照/参考）、`docs/ARCHITECTURE.md`（框架选型蓝图，结论不变）。
- 重做原因：第一版方向正确，但**模块内部细节失控，越到后期问题越大**——重做时按架构文档的结论 + 本文件的审查清单逐条规避。
- 遗留待清理：`inputs/parsed/`（旧 MinerU 中间产物），新解析层建议换目录输出后清理。

---

## 2. 架构审查结论（概要）

仅保留被本文件其他章节引用的三个编号决策（其余审查修正已并入 `docs/ARCHITECTURE.md` v2）：

- **A1｜内建边界**（按 `lightrag/docs/retriever.md` 实测修正）：LightRAG `mix` 无关键词路（sparse/BM25）、无多路融合 RRF；内建 **chunk 重排管道默认关闭、仅作用 chunk**（可复用管道）→ 关键词路与 RRF 融合放外层（M5），对应 ARCHITECTURE §2.4/§2.6 与本文件 §4。
- **A2｜embedding 载体**：Ollama 无 sparse → 定 Xinference（dense+sparse + `/v1/rerank` 一站），对应 ARCHITECTURE §3/§3.1。
- **B3｜语言设置**：实体抽取需另配 `language=zh`，仅 `SUMMARY_LANGUAGE=zh` 不够（见 ARCHITECTURE §3）。

---

## 3. 模块划分（M0–M11，高内聚 / 低耦合）

```
                    ┌───────────────── 数据线 A（离线）──────────────────┐
原始文档 inputs/raw │  M1 解析层       M2 切分层      M3 索引层         │
  │                │  MinerU/Docling → 统一MD → TextUnit → 图+向量写入  │
  ▼                └─────────────────────────────────────────────────┘
                                                    │ 经 M4 存储层
                    ┌──────────────── 解答线 B（在线）─────────────────┐
用户问题             │  M10 Agent 编排（唯一对话入口）                  │
  │  HTTP/SSE       │   LangGraph: route ⇄ execute_tools → generate     │
  │                 │     计划分支（非工具）：复杂题 → state.plan 三步    │
  │                 │     └─ 工具：M5 检索 / M5+M6 综合应答 / M7 文档   │
  │                 │  M7 交互层：FastAPI + SSE + 多会话 + 文档管理     │
  └────────────────┼───────────────────────────────────────────────────┘
        M0 数据契约层（parse 产物契约 + TextUnit 检索管线数据规范）──所有模块以契约衔接
        M6 生成层（直连：检索 → 组装 → 生成 + 引用）——降为 agent 的工具落点与评测对照
        M8 前端实现（React，消费 M7 契约）
        M9 评测层（三库 130 题 + 八维指标，贯穿全程）
        M11 MCP 接入层（规划中：把检索/问答能力暴露为 MCP 工具，旁路接入面）
```

| 模块 | 职责边界 | 输入 → 输出契约 |
|---|---|---|
| **M0 数据契约层** | 契约文档：parse 产物契约 + TextUnit v2（全系统唯一"块"标准）；**config / LLM / Embedding client 无独立 m0 包**，落实于 `m3_index/providers.py`（见 M3） | 定义 parse / TextUnit 契约 |
| **M1 解析层** | MinerU 主力 / Docling 多格式 / 扫描件路由；解析状态与重试 | 原文件 → 统一 Markdown |
| **M2 切分层** | 标题层级切分 / TokenChunker / 表格整块 / 元数据抽取 | 统一 Markdown → TextUnit(JSONL) |
| **M3 索引层** | LightRAG 建图（中文实体抽取、增量/选择性删除）+ bge-m3 编码；**兼任全项目共享 config / LLM / Embedding client（`providers.py`）** | TextUnit → 图+向量 |
| **M4 存储层** | 统一存取接口，后端可切换（默认 → Postgres+pgvector） | 供 M3 写、M5 读 |
| **M5 检索层** | 内建三路 + sparse/BM25（bge-m3 sparse 关键词路）+ RRF(k=60) + rerank（经 **Xinference** bge-reranker）+ 五特征融合排序 + query 预处理 | query+模式 → 精排 context 带来源 |
| **M6 生成层** | 意图路由 / 上下文组装（token 预算）/ 引用标注 / 流式事件；**agent 落地后降为 `tool_rag_answer` 的内部实现与评测对照组**，不再是独立入口 | query+context → 答案+引用 |
| **M7 交互层** | FastAPI + SSE + 多轮记忆 + WebUI + **文档管理**（`POST/GET /docs`、`DELETE /docs/{doc_id}` 软删，见 v3）+ **图谱导出**（`GET /graph`，软删过滤，见 v4）+ **文档预览**（`GET /docs/{doc_id}/preview`，见 v5）+ **按文档过滤图谱**（`GET /graph?doc_id=`，见 v5）+ agent SSE 端点（`/agent/stream`，断流后后台落库） | HTTP → 流式响应 / 文档列表 / 图谱 JSON / 预览 JSON |
| **M8 前端实现** | 正式 Web 前端（消费 M7 契约）：**v1** 答案 + 引用定位原文（SSE 逐字）→ **v2.1** 文档上传/文档管理视图 → **v2.2** 知识图谱（AntV G6 全图 + 引用「在图谱中查看」联动聚焦） → **v2.3** 侧边栏 + 右栏 tab + 文档全文预览 → **v5.7** agent 决策链卡片 → **v6.5.2** 回答区 Markdown 渲染 + `[n]` 角标插件 | HTTP/SSE → 用户界面 |
| **M9 评测层** | 三库 130 题测试集 + 检索四维 / 生成四维 + agent 专项（工具准确率、多跳召回）+ 裁判校准 | 测试集 → 指标报告 |
| **M10 Agent 系统** | **Agent 为主体、RAG 为底座**：唯一对话入口（`/agent`、`/agent/stream`）+ 工具注册表（`tool_retrieve` / `tool_rag_answer` / `tool_list_docs`，薄封装 M5/M6/M7）+ **LangGraph StateGraph**（route → execute_tools ⇄ route → generate，最多 5 轮，v4-flash 原生 function calling）+ PG checkpointer 会话快照 + 跨轮引用复用 + **规则触发的计划分支**（非工具：复杂题写 `state.plan` 三步计划，route 每轮回注 + generate 前核对覆盖） + `agent_runs`/`agent_spans` 可观测；**已落地（2026-10-30）** | query+history → 多轮工具决策 → 答案+引用 |
| **M11 MCP 接入层** | 把检索/问答能力暴露为 MCP 工具（`stdio` / `streamable-http` 两种传输），供 Claude Desktop / Cursor 等外部 AI 客户端调用；**旁路接入面，不进问答主链路**；**方案稿 v0.1，未实施** | MCP `tools/call` → 检索片段 / 带引用答案 |

> **关键技术排查清单**（拆模块时容易漏的技术支撑，与模块的归属对应）：

| 关键技术 | 归属模块 | 在本项目的角色 |
|---|---|---|
| **Xinference（模型服务网关）** | M3（`providers.py` client）＋ M5（rerank） | 统一承载 bge-m3（dense+sparse）与 bge-reranker-v2-m3，全项目经 HTTP 共用；**不属于任一个模块的内建部分** |
| **LangGraph** | **M10（agent 编排内核，已落地）** | 蓝图曾规划 M6 薄包装 LangChain 做工具调用协议；**实际以 LangGraph 为编排内核**——工具注册表自研（agent 与底座的唯一接缝），StateGraph 承载决策环（route / execute_tools / generate），决策协议用 v4-flash 原生 function calling，会话状态用 PG checkpointer（`thread_id = conversation_id`）；**不单独引 LangChain**（langchain-core 随 langgraph 依赖树自带），不深度用 LCEL |
| **Postgres + pgvector** | M4（存储实现） | 准生产地基：图 / 向量 / 原文块 / 状态一库 |
| **FastAPI + SSE** | M7（后端壳） | 对外 HTTP + 流式响应 |
| **AntV G6** | M8（前端渲染） | **已落地 v2.2**：知识图谱渲染 + 引用→图谱联动（答案→引用→原文→图上游走），v5.1.1 |
| **react-markdown / remark** | M8（回答渲染） | **已落地 v6.5.2**：回答区 Markdown 渲染；引用角标由自写 remark 插件在语法树上把 `[n]` 拆成链接节点，不注入 innerHTML |
| **RAGAS** | M9（评测） | LLM 裁判打分 |
| **DeepSeek**（deepseek-v4-flash） | M3（`providers.py` LLM client） | 抽取/生成统一 flash（2026-09-13 实测定案） |
| **MinerU / Docling** | M1（解析层） | PDF 主力 / 多格式兜底 |

**低耦合三原则**
1. 共享资源只从契约与 `m3_index/providers.py` 取（config / LLM / Embedding client）；模块间走「写文件 / 写库」衔接，不互调业务函数。
2. TextUnit 契约先行（M0 里定死 Schema），模块用样例数据 mock 上下游。
3. 每模块交付：独立 CLI 入口 + 契约校验 + 冒烟样例；联调先过契约再过行为。

**实施与联调顺序**
1. M0 + M4 先行（定契约与存储底座——两条线的汇合点）。
2. 线 A：M1→M2→M3；线 B：M5→M6→M7（用固定样例 context 并行走，不等数据线）。
3. 半程联调：M1–M4 完成后「文档 → 可检索」闭环先通；M5–M6 接上全链路。
4. M7 + M8 收尾，M9 评测回归。

**记录方式**：每模块 `docs/modules/Mx_*.md`（模板见 `_TEMPLATE.md`：接口契约 → 关键决策 → 已知坑·待办 → 版本）；版本逐条与实测见 `docs/CHANGELOG.md`。

---

## 4. LightRAG 源码拆解：四个「撕开点」（决策）

| 撕开点 | 处理 | 说明 |
|---|---|---|
| **存储层 storage** | **替换** | 默认 JsonKV+NetworkX+numpy 是内存态；换官方 Postgres+pgvector（准生产地基层，见 ARCHITECTURE §2.5） |
| **检索外层 retriever** | **外包扩展** | 内建 local/global/naive 保留；RRF + rerank + query 预处理全在 M5 外层（对应 A1） |
| **图构建 graph** | **微调复用** | 主流程复用；改造点仅为中文实体抽取的 prompt / `language=zh`（对应 B3） |
| **LLM 接入 llm** | **改配复用** | 接 DeepSeek；抽取/生成分模型、开 LLM 缓存、增量更新与选择性删除 |

---

## 5. 待办与下一步

- 实施进度**不在本文维护**，逐版本见 `docs/CHANGELOG.md`。里程碑：M0–M8 落地 2026-09-15｜M9 评测层落地 2026-10-06（三库 130 题）｜M10 agent 成为唯一对话入口 2026-10-30。
- ✅ **M9 评测层**（已落地）：三库 130 题 + 检索四维 / 生成四维 / agent 专项，裁判温度固定 0 + 判定缓存；版本间 Δ ≤ 0.03 视为噪声。
- ⏳ **M10 verifier**（方案稿，未实施，见 `docs/modules/M10_agent.md` §11.5）：生成后校验引用支撑与计划覆盖，最多重试一次；验证门槛 30 题 A/B、Δ > 0.03 才算有效。
- ⏳ **M11 MCP 接入层**（方案稿 v0.1，未实施，见 `docs/modules/M11_mcp.md`）：stdio + streamable-http 两套传输，把检索/问答暴露为 MCP 工具。
- ⏳ **Docker Compose 收敛部署**（长期未做）：当前靠 `dev.sh` 分组件拉起。