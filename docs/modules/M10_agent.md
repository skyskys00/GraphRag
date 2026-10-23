> **状态行**：版本 v0.4（方案稿，P0 完成：`/agent` 骨架落地）｜ 状态：规划中（P0 完成，P1 待实施）｜ 更新：2026-10-23 ｜ 定位：**Agent 为完整系统主体，RAG 全链（M0–M7）作为其底座能力区**——不是「RAG + agent 附加」，而是「agent 系统内嵌 RAG 能力」 ｜ 契约：待定（见 §5） ｜ 上游（底座）：[M5 检索层](M5_retrieve.md) · [M6 生成层](M6_generate.md) · [M7 交互层](M7_interact.md)（完整 RAG 底座 M1–M4 见 [FRAMEWORK_NOTES §3](../../FRAMEWORK_NOTES.md) / [ARCHITECTURE §1](../../ARCHITECTURE.md)） ｜ 下游：[M8 前端](M8_frontend.md) ｜ 依据：用户拍板「agent 做完整系统、RAG 属于它的一部分」（2026-10-23 范式重启；同日选型定稿 LangGraph；P0 探底+骨架完成）｜ 运行：待定 ｜ 变更历史：见 `docs/CHANGELOG.md`

# M10 Agent 系统（方案稿 v0.4 —— Agent 为主体 / RAG 为底座）

## 0. 范式重启：从「RAG 附加」到「Agent 为主体」

v0.1 的方向是「在已评测稳定的 RAG 之上叠一层 agent 编排」。**2026-10-23 用户转向**：不做 RAG 的附加功能，而是把 **agent 做成完整系统，RAG 属于它的一部分**。这正对应业界 Agentic RAG（RAGFlow / Dify / LangChain agent 形态）。

| 维度 | v0.1（RAG 中心 + agent 附加） | **v0.2（Agent 中心 + RAG 底座）** |
|---|---|---|
| 系统主体 | RAG 全链 | **Agent**（对话状态 + 规划 + 工具注册表） |
| RAG 地位 | 主链路 | **底座能力区**（M5/M6/M7 被薄封装成工具） |
| 用户入口 | `/answer` 直连为主、agent 旁路 | **`/agent` 统一入口，一切对话先进 agent** |
| 意图路由 | M6 `route()` 常量 | **被 agent 规划吸收**（agent 决定要不要查、查什么） |
| 多知识库 | 问答时切库 | **agent 的多个数据源**，可跨库调用 |
| 前端叙事 | 问答背后是 RAG | 问答背后是 agent，决策链可视化 |

## 1. 为什么可行（核心约束）

**RAG 全链 M0–M7 已评测稳定、可展示**（三库基线在 `backend/tests/final_results/eval_results_summary.md`，器械场景 50 题 Recall 0.8833 / e2e Correctness 0.8733）。Agent 化成功的关键是：

1. **基底零改写**：M1–M7 本体不动，agent 通过「工具适配器（描述 + 入参 schema + 结果回填）」薄封装调用；已评测稳定的语义与引用契约不因收纳而降级。
2. **检索经验直接复用**：M5 的多路召回/RRF/rerank、M6 的组装/引用、M9 的评测纪律全是现成资产，agent 只是多了一个「决策层」决定怎么调度它们。
3. **诚实红线延续**：agent 答案 grounded 在工具 citations 里，工具结果不足如实说明——沿用 M9 诚实口径（v5.30 泄漏治理教训）。

## 2. 架构总览

```
                      ┌─────────────── Agent Core（M10，系统主体）───────────────┐
  用户 ⇄ FastAPI/SSE ⇄  对话状态 + 记忆 │  决策循环（LangGraph StateGraph）│  工具注册表  │
                      └───────────────┬───────────────────────┬───────────────┘
                                      │ 工具调用                │
          ┌────────────────┬──────────┴──────────┬─────────────┴───────┐
          ▼                ▼                     ▼                     ▼
   tool_retrieve    tool_rag_answer      tool_list_docs          tool_graph /
   （M5 检索）      （M6 综合应答）      （M7 文档清单）         tool_compare…
                                                                    （P2 起）
          └────────────────┬─────────────────────────────────────────┘
                           │ 全部复用已评测稳定底座
   RAG 底座能力区（M1–M7，本体零改动）：M5 检索 · M6 组装/生成/引用 · M7 图谱/对比/
   文档/多库 · M4 存储；M9 评测层持续服务全系统
```

- **松耦合**：工具注册表是 agent 与底座的唯一接缝；新能力=注册一个新工具，不改 agent 内核。
- **双注册原则**：图谱/文档/对比等**数据面板**接口（前端需交互渲染的）保留直连，同时同一能力注册为 agent 工具。agent 与前端面板各取所需，互不阻塞。

### 2.1 技术调研与选型依据（2026-10-23，选型定稿：LangGraph）

调研范围：LangChain / LangGraph / pi-agent / pydantic-ai / OpenAI Agents SDK / smolagents / AG2 / CrewAI / MCP。**结论：无现成「RAG+Agent」一体化框架，agent 编排层采用 LangGraph**（用户确认，动因见下文②）。

| 技术 | 角色定位 | 选型结论 |
|---|---|---|
| **LangGraph** | 低层状态图编排（StateGraph + checkpoint + 条件边） | **采用（agent 编排内核）**：P3 复杂 agentic 可持续编排；`bind_tools` 兼容决策协议探底；LangSmith 兼作可观测 |
| LangChain（standalone） | 全生态底座 | **不单独引**：langchain-core 随 langgraph 依赖树自带，消息/工具抽象就地复用，不装全量 package |
| pi-agent（pi.dev） | TypeScript **终端编码助手** harness | **排除**：面向写代码场景，与知识库问答 agent 错位 |
| pydantic-ai | 类型驱动 · 异步 · 轻量 agent 封装（Pydantic 团队） | 不引（曾列轻量备选；选 LangGraph 后弃） |
| OpenAI Agents SDK | OpenAI 官方单/多 agent + handoffs | 不引：OpenAI 生态绑定，托管能力国内不可用 |
| smolagents（HF） | 极简 loop（核心 ~1000 行） | 不引入（loop 设计思路可参考） |
| AG2 / CrewAI | 多 agent 协作框架 | 不引：多 agent 机制对单 loop 偏重 / 剧本化抽象控制力弱 |
| MCP | **协议/标准，非框架** | 本期不引：工具均为进程内 Python 函数，无需跨进程标准；留作将来「对外暴露 RAG 能力」的接口位 |

关键依据：①**无开箱即用**——调研范围内没有「自带 GraphRAG 检索底座 + agent loop + 中文图/向量」的一体化框架；LightRAG v1.5.7 自身仅带 AG2 multi-agent 示例（demo 级，非产品能力）。②**采用 LangGraph 而非自研**（2026-10-23 用户确认）——后续要做复杂 agentic-rag（多 agent / 持久化 / 中断恢复）时，在已知的 LangGraph 图上继续编排**可持续性更高**，比从自研 loop 届时再迁移的成本低；且蓝图本就预留此位（ARCHITECTURE §2.7「若后续要多智能体、复杂工具调用，再引入 LangGraph 状态机」）。③**红线不破**：工具注册表 = 唯一接缝不变，M5/M6/M7 零改写；LangGraph 仅替换原来「自研 loop」这一小块内核。④**MCP 属过度设计**：它是进程间工具调用标准（stdio/HTTP），我们的工具是进程内函数，不需要；只有将来把底座能力暴露给外部 agent 才需要 MCP-server 化。

### 2.2 LangGraph 初步编排方案（显式 StateGraph）

**不用 prebuilt `create_react_agent`**（需要：决策链事件透出 SSE、直答短路、citations 结构化收集），手写 3 节点状态图：

```
  START ─▶【route 决策】┬─ 有 tool_calls ─▶【execute_tools 执行】┬─ 余轮>0 ──▶（回 route）
                        │                      │                 └─ 余轮耗尽 ─┐
                        └─ 无 tool_calls ─▶【generate 生成】◀─────────────────┘
                                             │
                                            END
```

**State（`AgentState`，TypedDict）**：

| 字段 | 说明 |
|---|---|
| `query` | 本轮原始问题（冗余存，供组装/评测） |
| `collection_id` / `conversation_id` | 数据源与记忆单元（沿 M7 现有机制） |
| `history` | M7 conversations 拉取的多轮历史（只读注入，不回流） |
| `messages` | loop 内消息流，`add_messages` reducer（LangGraph 标准） |
| `remaining_rounds` | 最大决策轮 N=5，每轮递减，硬约束防失控 |
| `events` | 决策链事件（tool_decision / tool_result / done），SSE 透出 |
| `citations` | 引用收集，来自工具结果回填，最终答案组装 |

**节点 / 边**：

1. **`route`**（决策节点）：LLM `bind_tools`（原生 function calling，探底项 §3）判「调哪个工具」还是「直接答」；有 tool_calls → 条件边走 execute_tools，无 → 走 generate（直答短路，简单问题一轮应答）。
2. **`execute_tools`**（工具节点）：遍历本批 tool_calls，dispatch 到工具注册表（薄封装 M5/M6/M7），结果回填 `messages`(ToolMessage) + `events` + `citations`，`remaining_rounds - 1`；余轮>0 → 条件边回 route（可换问法再查 / 多步跨库），耗尽 → generate。
3. **`generate`**（生成节点)：LLM 基于最终 messages（含工具结果与引用）产出答案 + citations 汇总，写 `done` 事件 → END。

**与现栈的衔接**：
- **async**：用 langgraph async API（`AsyncStateGraph` / `.ainvoke`），与 FastAPI + 既有 async bootstrap / SSE 同构。
- **事件线**：节点内写 `events` → SSE 生产者逐条 emit（沿 M6 `answer_stream` 线：`tool_decision` → `tool_result` → `delta` → `citations` → `done`）。
- **工具注册表 = 唯一来源**：`register_tool(name, fn, description, args_schema)` 一处注册 → 驱动 ① langgraph tools ② system prompt ③ 前端可用工具展示。LangGraph 只是消费方。
- **决策协议兜底**：若 v4-flash 原生 `tools` 不可用/不稳 → route 换 `with_structured_output({action, args})` JSON 兜底；**graph 结构与节点不变**，只换这一层的模型输入/输出解析。
- **记忆**：首版 conversation 持久化仍走 M7 conversations；**不引 LangGraph checkpoint**（避免现有多轮记忆双轨）——P3 记忆增强时再评估，checkpoint / interrupt 恰是 LangGraph 相对自研 loop 的增值点。
- **可观测**：LangSmith trace 可选接入（LANGSMITH_API_KEY），失败不影响主链路，本地 dev 不强制。
- **依赖锁定**：`pip install langgraph`（自带 langchain-core），**不装完整 langchain 全量包**；版本锁定进 P0 探底项，与 lightrag v1.5.7 同纪律。
- **harness 分层**：LangGraph 本身就是 agent harness（决策循环 + 状态 + 工具执行 + 可选 checkpoint/interrupt）；项目外层的应用 harness（HTTP/SSE/会话/异常）已由 M7 + M10 事件线承接，不再造独立 harness 层。将来启用 checkpoint / interrupt 做记忆增强或人工介入时，持久化与暂停恢复也归 LangGraph，自定义 harness 工程面进一步收敛。

## 3. 决策协议（先探底原生 function calling，载体 = LangGraph `route` 节点）

- **决策节点 = §2.2 的 `route`**：模型经 `bind_tools` 暴露工具（原生 function calling）。先验证 DeepSeek v4-flash 原生 `tools` 可用性——可用则用原生调度（格式稳、少一层 JSON 解析漂移）；不可用或格式不稳再回退 `with_structured_output({action, args})` JSON 协议（graph 结构与节点不变，只换 route 的模型输入/输出解析）。结论回填本文档 §10。
- **P0 实测（2026-10-23）**：v4-flash 原生 `tools` **可用**（与思考不互斥，但生产保持 thinking=disabled 最稳；一次决策可返回多个 tool_call）——**无需 JSON 兜底**。明细见 §10-8。

## 4. 工具集定义（P1 三个，P2 扩展）

**工具空间边界（为何只封装 M5/M6/M7，没有 M1–M4）**：agent 是**在线查询期**系统——每次决策循环都发生在「文档已索引好」的世界，能调用的只有查询期能力（M5 检索 / M6 组装生成 / M7 文档·图谱·对比）。M1 解析 / M2 切分 / M3 建图 / M4 存储属**离线建设期**数据管线（文档 → 图+向量），一次或低频执行，agent 对话中无调用理由，故不进入工具空间。将来若要在对话中上传新文档使其立即可检索，仍走 M7 文档管理接口（内部编排 M1–M4），agent 只调管理类工具，不直接触碰 M1–M4。

| 工具（P1） | 复用底座 | 用途 |
|---|---|---|
| `tool_retrieve(query, collection?, allowed_docs?, top_k?)` | M5 `retrieve()` | 文档检索（三路+RRF+rerank 全复用），agent 主检索通道 |
| `tool_rag_answer(query, collection?, response_type?)` | M6 `answer()` | 综合应答（组装+生成+引用一条龙）；agent 判定「简单问题直接答」时的落点，**吸收原直连语义** |
| `tool_list_docs(collection?)` | M7 `documents.list_docs` | 文档清单 / 存在性确认 / 多库盘点 |

| 工具（P2） | 复用底座 | 用途 |
|---|---|---|
| `tool_graph(entity?, doc_id?, top_n?)` | M7 `collect_graph` | 图谱/邻域查询，实体扩展与关系探索 |
| `tool_compare(keys, doc_ids)` | M7 `compare.py` | 器械场景跨型号参数对比（检索式，零 LLM 成本） |

> P1 即体现「agent 是主体」：`tool_rag_answer` 让简单问题一轮「类直连」应答成立，`tool_retrieve` 让需要多步/跨库的问题可换问法再查，`tool_list_docs` 让 agent 有「对环境的基本感知」。三个工具从第一版就撑起「对话式系统」而非「单功能问答」。

## 5. 接口契约（草案）

- **后端（M7 新增，api 层）**：`POST /agent` + `GET /agent/stream`（SSE）——**统一对话入口**。参数：`query` / `collection_id` / `conversation_id` / `response_type`。复用 `bootstrap` 的 llm/rag/sparse/entities/sidecar 缓存。
- **RAG 直连降级**：`/answer`、`/answer/stream` **保留实现，不再作为前端对话入口**——降为 agent 内部的 `tool_rag_answer` 落点 + M9 评测/调试对照路径（评测对照价值仍高，见 §7）。
- **数据面板接口保留直连**：`/graph`、`/docs`、`/compare`、`/collections` 等维持原样（前端面板按需直调 + agent 工具双注册）。
- **事件线扩展**（沿用 M6 `answer_stream` 事件线，追加决策事件）：
  `tool_decision {action,args}` → `tool_result {tool, result_summary, citations_count}` → … → `delta` → `citations` → `done`。前端无需解析完整工具结果也能渲染决策链。

> **API 契约以 Swagger 为准**：FastAPI `docs_url="/swagger"`，代码即契约。

## 6. 前端呈现（M8）

- 问答视图背后**无感切换**到 agent（走 `/agent/*`），不做「agent 模式开关」——agent 是默认且唯一对话入口。
- **决策链折叠卡片**：每轮 `tool_decision → tool_result` 渲染成可折叠「🔧 调用工具箱检索：…（命中 k 条上下文）」小结，避免刷屏；简单问题一轮应答时卡片可收起为「已直接作答」。
- 图谱/文档/对比等面板视图维持现有直连渲染，不因 agent 化改变交互（同时后端已注册为 agent 工具）。
- 多会话/多知识库现有机制全部复用（conversation_id / collection_id 即 agent 的记忆与数据源隔离单元）。

## 7. 评测方案（M9 扩展）

- **新增 agent 端到端测试集**（复用 M9 runner / judge 基础设施，测试集 v0 设计走 `M9_testset.md` 规范）。
- **指标**：
  - `tool_accuracy`：每步工具选择是否合理（LLM 裁判）；
  - `multi_hop_recall`：多步检索后目标信息是否进入最终上下文；
  - e2e `Correctness / Faithfulness / Citation` 沿用现有四指标；
  - **RAG 对比回归**：同一题集，agent 首轮直答 vs 原 `/answer` 直连 vs 旧基线，量化「agent 引入不劣化底座」——这是「底座收纳」的回归门禁。
- **诚实红线**：agent 工具描述不得含 key_facts 措辞；评测口径 = 生产口径（沿 v5.30 泄漏治理）。

## 8. 分期落地

| Phase | 内容 | 完成标志（可验证） |
|---|---|---|
| **P0（探底）✔ 完成 2026-10-23** | v4-flash 原生 `tools` + 与思考/`extra_body` 互斥性；langgraph 依赖树最小安装（无全量 langchain）；`/agent` 路由骨架 + async StateGraph（注册 `tool_list_docs` 一个真实工具） | 探底结论回填 §10 ✓（§10-8）；`POST /agent` 健康应答可用 ✓（v5.42，实测工具决策→执行→生成→事件线闭环） |
| **P1 (MVP)** | **显式 StateGraph**（route / execute_tools / generate + 条件边）+ 三工具（retrieve / rag_answer / list_docs）+ 流式事件线 + 前端问答接 agent + agent 测试集 v0 + RAG 对比回归 | `/agent/stream` 端到端可用；agent 首轮直答 vs 直连 A/B **不劣化** correctness/faith/cite；无编造 |
| **P2** | + `tool_graph` / `tool_compare` + 多工具组合 + agent 评测全量 + A/B 报告 | 跨库多步问题可编排；A/B 报告归档；CHANGELOG 新版本条目 |
| **P3（可选）** | 意图路由显性化、agent 多轮记忆增强、复杂任务拆解（sub-agent） | 跨文档综述 / 参数对比类问题 by-tool 达标 |

> 每 Phase 结束按项目收尾章程：CHANGELOG + 受影响模块状态行同步；实测报告归档；能跑才算完成。A/B 报告路径沿用 `backend/tests/reports/` 分类结构。

## 9. 风险与取舍

- **底座回归风险**：任何 agent 改动不得回写 M5/M6 语义。破坏性变化只准发生在 `app/m10_agent/` 与 `m7_interact` 的 agent 路由/适配层；M5/M6 变更必须走既有评测门禁。
- **LLM 工具调用可靠性**：JSON 格式稳定性 P0 探底；兜底——解析失败重试 1 次、仍失败退化为 `tool_rag_answer` 单轮应答（诚实标注「退化为单次检索」）。
- **Token / 延迟成本**：多轮多次 LLM 调用。约束：N=5、工具结果入上下文前压缩（复用 M6 assemble token 预算）、流式不破坏 TTFT。
- **评测泄漏红线**：工具描述不得含 key_facts 措辞；同库同裁判同口径。

## 10. 已拍板决策（2026-10-23）

| # | 决策点 | 结论 |
|---|---|---|
| 1 | 系统范式 | **Agent 为主体，RAG 为底座**（范式重启自 v0.1） |
| 2 | 模块归属 | **独立 M10**（M6 保持纯净契约，仅被适配器薄封装） |
| 3 | 对话入口 | **agent 唯一对话入口**（`/agent`）；RAG 直连降为内部工具落点 + 评测对照 |
| 4 | 决策协议 | **先探底 DeepSeek v4-flash 原生 function calling**，JSON 协议兜底 |
| 5 | 数据面板 | **面板直连 + 同一能力注册为 agent 工具**（双注册） |
| 6 | P1 工具范围 | **三工具：retrieve / rag_answer / list_docs** |
| 7 | 框架选型（2026-10-23 调研 + 用户确认） | **agent 编排内核用 LangGraph**（显式 StateGraph：route / execute_tools / generate + 条件边，不用 prebuilt ReactAgent）；**不单独引 LangChain**（langchain-core 随 langgraph 自带）；pydantic-ai 弃选；**MCP 本期不引**（进程内工具）；明细见 §2.1/§2.2 |
| 8 | P0 探底结论（2026-10-23 实测） | ① v4-flash 原生 `tools` **可用**（finish_reason=tool_calls 合法返回；一次决策可**多个 tool_call** → execute_tools 须批量处理）；与 thinking **不互斥**，但生产保持 **thinking=disabled 最稳**（无 reasoning 烧 max_tokens、无偶发 length 截断）② **langgraph 1.2.16** 满足最小依赖（langchain-core 已有 1.6.2，**无全量 langchain**）③ async StateGraph 空跑通过——**LangGraph 1.x 纪律：节点函数必须返回 dict，路由选择用独立条件函数** |

> 下一步（P0）：探底 v4-flash 原生 `tools` 支持与互斥性（§3/§9），结果回填本文档并进 `docs/CHANGELOG.md`；随后 P1 落地 agent loop 骨架 + 三工具。