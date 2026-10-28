> **版本：** v0.5（方案稿；项目版本 **v6.x**——v6.0 追认定格历史 v5.43–v5.50，能力演进路线 P0–P3 见 §11）
> **状态：** P1 已验收（v5.47）｜ P2-cite 收口（v5.49）｜ 多轮直答修复 + 前端决策链改版（v5.50）｜ P0 可观测埋点落地（v6.2）；评测量化基线见 CHANGELOG
> **更新：** 2026-10-28
> **定位：** **Agent 为完整系统主体，RAG 全链（M0–M7）作为其底座能力区**——不是「RAG + agent 附加」，而是「agent 系统内嵌 RAG 能力」
> **契约：** 待定，见 §5（API 以 Swagger 为准）
> **上游：** [M5 检索层](M5_retrieve.md) · [M6 生成层](M6_generate.md) · [M7 交互层](M7_interact.md)（M1–M4 见 [FRAMEWORK_NOTES §3](../../FRAMEWORK_NOTES.md)）｜ **下游：** [M8 前端](M8_frontend.md)
> **依据：** 用户拍板「agent 做完整系统、RAG 属于它的一部分」（2026-10-23 范式重启，同日选型定稿 LangGraph）｜ **运行：** 待定
> **变更历史：** 见 [`CHANGELOG.md`](../CHANGELOG.md)

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
- **async / 事件线**：langgraph async API（`.ainvoke`）与 FastAPI + SSE 同构；节点内写 `events` → SSE 逐条 emit（`tool_decision` → `tool_result` → `delta` → `citations` → `done`）。
- **工具注册表 = 唯一来源**：`register_tool(name, fn, description, args_schema)` 一处注册，驱动 langgraph tools / system prompt / 前端工具展示，LangGraph 只是消费方。决策协议兜底：原生 `tools` 不稳则 route 换 `with_structured_output` JSON 解析，graph 结构与节点不变。
- **记忆**：首版仍走 M7 conversations，**不引 LangGraph checkpoint**（避免多轮记忆双轨）——P1 记忆增强时再评估（§11.3）。
- **可观测 / 依赖**：LangSmith trace 可选接入（失败不影响主链路，本地不强制）；`pip install langgraph` 自带 langchain-core，不装全量 langchain。
- **harness 分层**：LangGraph 即 agent harness，外层应用 harness（HTTP/SSE/会话/异常）由 M7 + M10 事件线承接；将来启用 checkpoint / interrupt 时持久化与暂停恢复归 LangGraph。

## 3. 决策协议（载体 = LangGraph `route` 节点）

route 经 `bind_tools` 暴露工具、走原生 function calling；若不稳则回退 `with_structured_output({action, args})` JSON（graph 结构与节点不变）。**P0 实测（2026-10-23）：v4-flash 原生 `tools` 可用、一次决策可返回多个 tool_call，无需 JSON 兜底**；生产保持 thinking=disabled 最稳。明细见 §10-8。

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

- **新增 agent 端到端评测**（复用 M9 runner / judge 基础设施，测试集 `testset_device_v2` 50 题，collection `col_b7b876b1`）。
- **指标**：
  - `tool_accuracy`：每步工具选择是否合理（LLM 裁判）；
  - `multi_hop_recall`：多步检索后目标信息是否进入最终上下文；
  - e2e `Correctness / Faithfulness / Answer Relevance / Citation Accuracy` 沿用现有四指标；
  - **RAG 对比回归**：同一题集，agent 首轮直答 vs 原 `/answer` 直连基线，量化「agent 引入不劣化底座」——这是「底座收纳」的回归门禁。
- **诚实红线**：agent 工具描述不得含 key_facts 措辞；评测口径 = 生产口径（沿 v5.30 泄漏治理）。
- **评测口径约定（2026-10-24 修正）**：agent 答案的 `[n]` 是**生成阶段编号**（非检索位次），judge 必须按 marker 精确对齐到对应 text_unit——`citation_accuracy` 以 `ctx_by_marker` 显式映射传入（runner 由 citations 反查 sidecar 构建），**禁用**直连的「marker n = contexts[n-1]」位次约定。基线同题 A/B 时基线也须按「citations → 完整内容」窄池重判（`scripts/recompute_baseline_strict.py`），避免 top5 宽池虚高。

### 7.1 A/B 结果与归因（v5.46）

同口径对比（agent `run_agent_device_50_20261023_fixed.json` vs 基线 strict `run_e2e_device_50_20261005_strict.json`，明细 `backend/tests/reports/agent/agent_vs_direct_ab.md`）：correctness **+0.038**、answer_relevance **+0.052** 不劣化；**faithfulness −0.213、citation_accuracy −0.170 劣化**（Δ≥−0.03 门禁未全绿）；multi_hop 0.908 / tool_acc 0.833 为 agent 独有增益。量化全表见 CHANGELOG v5.46。

**归因（已排除两种 artifact）**：marker 位次错配（修正后 0.612→0.7264）与 top5 宽/窄池不对等（基线同口径重判后差距依旧）均非原因；真实差异是**引用标注粒度**——agent 多轮检索答案更全面（correctness↑），但一个 `[n]` 只绑一个 text_unit，跨块聚合事实（如 DV-TN-010 六行规格表）被判无依据；直连「贴块写作」天然逐句对应。判别性验证见 §7.2，修复见 §9.4。

### 7.2 判别性验证：劣化确实是标注粒度（v5.46.1）

对 26 个 faithfulness ≤ 0.75 的劣化题，用同份 answer 重建完整 agent 上下文做宽松池重判：**回升 16 / 持平 2 / 回落 8，平均 Δ=+0.090**（DV-TN-010 0.300→0.875）；回落中 2 题为 rebuild 超时伪影、其余 ≤−0.167。**结论：信息全部在 agent 上下文，损失纯在标注粒度——无检索遗漏、无生成丢失**。明细见 CHANGELOG v5.46.1。

### 7.3 决策链体验增强（v5.46.1）

tool_result 携带命中预览 `snippets`；`_events_to_tool_steps` 分轮落库，决策链刷新可回放；修复 `/agent/stream` 装饰器错挂致 422 的路由 bug。明细见 CHANGELOG v5.46.1。

## 8. 分期落地

| Phase | 内容 | 完成标志（可验证） |
|---|---|---|
| **P0（探底）✔ 完成 2026-10-23** | v4-flash 原生 `tools` + 与思考/`extra_body` 互斥性；langgraph 依赖树最小安装（无全量 langchain）；`/agent` 路由骨架 + async StateGraph（注册 `tool_list_docs` 一个真实工具） | 探底结论回填 §10 ✓（§10-8）；`POST /agent` 健康应答可用 ✓（v5.42，实测工具决策→执行→生成→事件线闭环） |
| **P1 (MVP) ✔ 完成 2026-10-24** | **显式 StateGraph**（route / execute_tools / generate + 条件边）+ 三工具 + 流式事件线 + 前端接 agent + agent 测试集 v0 + RAG 对比回归 | 功能全部完成（v5.43 三工具+StateGraph+事件线+落库；v5.44 前端切 `/agent/stream`；v5.45 token 级流式 + `[n]` 引用；v5.46 评测回归 + 口径修正；v5.46.1 判别性验证）→ **v5.47 验收通过**（faithfulness/citation 劣化归因为标注粒度，见 §7.2） |
| **P2** | **P2-cite 已落地（v5.49，§9.4）**；其余 + `tool_graph` / `tool_compare` + 多工具组合 + agent 评测全量 + A/B 报告 | 跨库多步问题可编排；A/B 报告归档；CHANGELOG 新版本条目 |
| **P3（可选）** | 意图路由显性化、agent 多轮记忆增强、复杂任务拆解（sub-agent） | 跨文档综述 / 参数对比类问题 by-tool 达标 |

> 每 Phase 结束按项目收尾章程：CHANGELOG + 受影响模块状态行同步；实测报告归档；能跑才算完成。A/B 报告路径沿用 `backend/tests/reports/` 分类结构。

## 9. 风险与取舍

- **底座回归风险**：任何 agent 改动不得回写 M5/M6 语义。破坏性变化只准发生在 `app/m10_agent/` 与 `m7_interact` 的 agent 路由/适配层；M5/M6 变更必须走既有评测门禁。
- **LLM 工具调用可靠性**：JSON 格式稳定性 P0 探底；兜底——解析失败重试 1 次、仍失败退化为 `tool_rag_answer` 单轮应答（诚实标注「退化为单次检索」）。
- **Token / 延迟成本**：多轮多次 LLM 调用。约束：N=5、工具结果入上下文前压缩（复用 M6 assemble token 预算）、流式不破坏 TTFT。
- **评测泄漏红线**：工具描述不得含 key_facts 措辞；同库同裁判同口径。
- **引用标注粒度风险（v5.46 实测证实，v5.49 已修）**：agent 多轮检索后答案综合跨 text_unit 内容，但 `[n]` 一个编号只绑定一个块 → faithfulness/citation 较直连劣化。判别性验证（§7.2）确认信息均在上下文，**为纯标注粒度问题**；修复与结果见 §9.4。

### 9.1 P2 候选：P2-cite（已由 §9.4 收口）

背景：§7.2 坐实劣化为标注粒度——`[n]` 一编号绑一块，被引块承载不了整句跨块综合。原首选方案是 **text_unit_id 直引**（generate 直接指真实块、放弃编号清单），但探索证伪：LLM 从未见过 `{16hex}-chunk-{NNN}` 形态、提示后也不稳定产出，且评测 `[n]` 正则与前端 CitationPanel 全锚整数 marker，改动面横跨生成+评测+前端。最终改道 **A2/M6 式对齐**（多编号并列 + 评测宽池），落地与结果见 §9.4（v5.49）。

### 9.2 忠实度双轨裁判（v5.47，P1 收口依据）

8 落题深挖发现被罚项集中在型号推断/出处/概括/图块 OCR 等「元信息层」——是 agent 的合理增值而非编造。故 judge 对无依据陈述打 `kind`（fact/meta/enhance）：只罚 fact，meta 单报供人工核查，enhance 单列知识增强分；代码侧配 meta 指纹 + 数值词面核验兜底，并修复 `fact_total` 未透传致分母虚高的隐层 bug。**接受的现实：单次 judge 判定方差 ±0.3（同 8 题多轮重判 0→0.83 跳），结论一律下粗粒度定性、不以小数承重**。明细见 CHANGELOG v5.47。

### 9.3 答案分箱 + 忠实度朴素单层（v5.48）

`generate` 把建议/提醒/经验结论拆进末尾 `<advice>…</advice>`，主体只留可 `[n]` 对到资料的纯事实，`[n]` 只对主体还原；评测随之删 kind 三分类，回朴素单层 + 数值词面核验 + 来源引导语不判罚。**50 题新基线：faithfulness 0.7840 / citation 0.8652 / correctness 0.9283 / relevance 0.9770 / multi-hop 0.9247 / tool_acc 0.8580**（vs 直连：correctness/relevance 不劣化，faith/citation 仍红但较 v5.46 大幅收敛）。明细见 CHANGELOG v5.48。

### 9.4 P2-cite 落地（v5.49，§9.1 候选兑现→证伪改道）

双端修正：**生成端** SYSTEM_PROMPT 要求跨块事实并列标注 `[1][2]`（编号仍为整数 marker，`_build_citation_index` / `_resolve_citations` 零改动，前端/评测/回放生态零破坏）；**评测端** `runner._rebuild_retrieval_pool` 重放 `tool_retrieve` 重建决策链全命中池（完整 content + `【文档：…】` 头，上限 40 块），faithfulness / multi_hop 改吃重建池、citation 保持被引窄池 + `marker_ctx` 对齐（judge 视野 = 生产生成依据，非泄漏）。**50 题：faithfulness 0.7840→0.9368（Δvs直连 −0.029 过门禁）、citation 0.8648 贴线、correctness/relevance 不劣化、tool_acc 0.793**。

tool_acc 下降归因：DV-FS-017 为 judge JSON 解析失败伪分（实际 1.0），主降为 `list_docs` 调用时机随机 + judge「检索后补盘库冗余」偏好 + 重判窗口漂移，非质量退化（同批 correctness 反升）→ v5.49.1 补工具描述前置引导。明细见 CHANGELOG v5.49 / v5.49.1。

### 9.5 多轮历史绕检索直答缺陷与修复（v5.50，A+B）

**现象**：多轮会话中 route 层认定历史足够 → 绕开检索 → generate 无编号清单 → LLM 按历史风格编造 `[n]` → citations 被丢弃但正文残留假角标（点不动、引不出）；评测均单轮无历史，长期未暴露。**根因**：prompt 未禁「仅凭历史作答」+ 引用还原只清数组不清正文。**修复**：A 强制检索（SYSTEM_PROMPT 约束事实性问题必须先调工具）；B 诚实兜底（空清单注入「不要输出 [n]」+ `_strip_unbacked_markers` 剥离清单外角标，闲聊不误伤）。curl SSE 与浏览器双验证通过。明细见 CHANGELOG v5.50。

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

## 11. Agent 能力演进路线（P0–P3，2026-10-26 调研定稿）

### 11.0 总原则：按运行时接口分层，板块是能力目录

成熟 agent 系统不按「认知功能」（记忆/规划/反思…）切架构，而按**可独立替换的运行时接口**切（Anthropic Managed Agents：session / harness / sandbox 三层）。本项目映射：

| 运行时接口 | 本项目落点 |
|---|---|
| **session** | M7 会话层（conversations 表现有；P1 接入 LangGraph checkpointer，见 §11.3） |
| **harness** | M10 StateGraph（route / execute_tools / generate）+ 工具注册表 |
| **sandbox** | P3 新增执行接口（`execute(name, input) → output`，可替换实现） |

纪律：每个板块先落**可替换接口**再落能力；正式功能守 §2 简单优先；探索性能力单独 commit、必要时开关隔离（§10 探底纪律）。调研依据见 §11.9。

### 11.1 P0 可观测埋点 ✔ 落地（v6.2）

**目标**：每次 agent 运行可追溯，字段语义对齐 OTel GenAI，将来接 Langfuse/Phoenix 零改造。

**实现**（`telemetry.py` + `agent.py` 采集 + `api.py` 落库）：
- **两层分工**：内核只把节点级 span 写进 `AgentState.spans`（不碰 DB）；API 层 `start_run` 插 running 行、`finish_run` 更新终态并批量写 spans。M9 评测路径不写库（生产与评测共享行为、分离入口与落点）。
- **表**：`agent_runs`（run_id / conversation_id / question / status / model / duration_ms / tokens_in / tokens_out / tool_calls / error）+ `agent_spans`（run_id / span_id / node / tool / started_at / duration_ms / tokens / detail jsonb）。省略 `parent_span_id`（当前单层）与 ttft。
- **容错**：写入 best-effort（PG 故障只告警）；`finish_run` 同步调度写入，可在 CancelledError 中安全调用；`include_usage` 不被支持时降级、token 记空。
- **状态机**：running → completed / error / cancelled（SSE 断流）；进程崩溃停留 running，不自动清理。

**验证（v6.2 实测）**：闲聊 2.2s（route+generate 两段 span、tokens 1034/71）；真实检索题 223.6s（tokens 73034/1478、10 次工具调用）；SSE 3s 强断 → cancelled。SQL 一条 join 即可还原任意 run 的全链。

**已知限制**：cancelled/error 行 model 为空；跨运行聚合查询（p95、平均工具轮数）尚未做成报告，直接用 SQL。

**不做**：自建可观测 UI（交给 Langfuse/Phoenix）。

### 11.2 P0 上下文管理（token 预算 / 压缩 / 编号漂移）

**目标**：长会话不超窗、不稀释当轮上下文、不因历史编号错配引用。

- **预算**：`_conv_history` 改为「token 预算 + 保留最近 K 轮原文」，阈值（如模型窗口 70%）触发压缩。
- **压缩**：更早轮次经 LLM 摘要为一段「会话摘要」注入；优先舍弃 tool 原始输出、只留结论。
- **context editing（关键）**：历史里的旧 `[n]` 角标**注入前剥除或重写**——每轮编号按本轮 citations 重排，旧 `[6]`=A、新清单 `[6]`=B 会导致「在清单但语义错位」（v5.50 B 只能剥清单外编号，治不了这类）；建议历史注入时一律剥离角标，只保留文字。
- **结构化历史**：历史注入改为「轮次摘要 + 当轮检索块」，advice 区内容不进入事实上下文。
- **验证**：>20 轮会话不超窗、引用不跨轮错配；配「小规模多轮测试集」（10 题 × 追问两轮，检查每轮必检索、编号不跨轮错配、追问引用正确）。

### 11.3 P1 记忆（checkpointer 统一 + 小型长期记忆）

**目标**：会话状态可恢复、跨会话有轻量画像。

- **checkpointer**：`build_graph()` 挂 `PostgresSaver`；`conversation_id ↔ thread_id` 一一映射；conversations 表保留展示字段（citations/tool_steps/标题），消息正文以 checkpoint 为准（§10 关系说明的方案 A）；过渡期普通请求可先保持手动 history，仅 interrupt/恢复路径启用 checkpoint。
- **工作记忆**：上轮 citations 以结构化形式进入新轮 state（「已知上下文」），追问走增量检索而非重复全量检索。
- **长期记忆**：profile 小表（场景偏好、常用实体）+ pgvector 复用现有 embedding；后台写入、检索前注入（限 token）。
- **验证**：中断后 resume 成功；追问不重复全量检索；摘要生效。
- **不做**：mem0/Zep 式通用记忆平台（会变成第二套检索系统）。

### 11.4 P1 规划（受控 plan 分支）

- **设计**：route 条件边加「复杂度判定」——多跳/对比/多文档汇总 → plan 子图（任务分解 → 逐步执行 → 汇总）；普通问答保持现有三节点。state 增 `plan: [{step, status}]`，每次 generate 回注 plan（Manus todo 抗漂移）；设 `max_plan_steps` 上限。
- **验证**：多跳题工具步数合理、答案覆盖提升；普通问答不增加 LLM 调用。
- **不做**：多智能体角色编排（Cognition：上下文不共享导致隐含决策冲突）；最多保留只读并行检索子代理。

### 11.5 P2 反思（verifier，评测驱动）

- **设计**：generate 后加 verify 节点，只查**可判定项**（陈述引用覆盖、角标在清单内、被检索片段支持），最多重试 1 次；判据复用 M9 judge。
- **门槛**：先跑评测证明净增益，否则回滚（探底纪律）。
- **不做**：无判据的自动自我批评、LATS/树搜索（延迟翻倍、RAG 收益不成立）。

### 11.6 P2 人工介入（仅高危写操作）

- **设计**：LangGraph `interrupt()` + checkpoint；只在写库/覆盖上传/长任务前中断；SSE 增 `interrupt` 事件类型，前端确认卡片。
- **前置**：依赖 §11.3 checkpointer（无 checkpoint 无法 resume）。
- **验证**：中断 → 前端确认 → resume 全链路。
- **不做**：BPMN 式审批引擎；只读检索阶段不设审批。

### 11.7 P3 沙箱/计算（练手可做）

- **设计**：最小版 `subprocess` + 超时 + 禁网 + 只读临时目录；工具接口 `execute(code) → stdout`；白名单库（pandas/matplotlib）；凭证永不进沙箱；Docker/E2B 后置。
- **验证**：参数对比后计算差值/出图；越权访问与超时被拦截。
- **不做**：无明确产品场景的通用代码解释器（风险 > 收益）。

### 11.8 优先级与里程碑

| 期 | 内容 | 建议版本 | 验证方式 |
|---|---|---|---|
| P0 | 可观测埋点 ✔ v6.2 | v6.2 | SQL 可还原 run→spans（已实测） |
| P0 | 上下文管理（预算/压缩/编号剥离） | v6.3 | 长会话 + 多轮测试集 |
| P1 | checkpointer 统一 + 长期记忆 | v6.4 | resume 成功、追问增量检索 |
| P1 | 受控 plan 分支 | v6.5 | 多跳题步数/覆盖提升 |
| P2 | verifier 反思 | v6.6 | 评测净增益才保留 |
| P2 | 人工介入 interrupt | v6.7 | 中断→确认→resume |
| P3 | 沙箱最小版 | v6.8 | 计算/出图 + 越权拦截 |

> P0 两项优先：可观测是后续所有板块的验证底座，上下文管理直接根治 §9.5 遗留的多轮问题。版本号为建议位，按实际落地调整。

### 11.9 调研依据与过度设计红线

**依据**：Anthropic《Building Effective Agents》《Effective Context Engineering for AI Agents》《Managed Agents》《Context Management》；LangGraph memory / human-in-the-loop 文档；Manus《Context Engineering for AI Agents》；Cognition《Don't Build Multi-Agents》；Claude Code sandboxing；OpenAI Agents SDK。

**红线（明确不做）**：
- 多智能体角色编排（上下文不共享 → 隐含决策冲突）
- 通用记忆平台（mem0/Zep 全量）= 第二套检索系统
- 无判据自动反思、LATS/树搜索
- 工具 RAG / 动态工具加载（破坏 KV-cache；按状态 **mask** 而非 remove）
- 自建可观测 UI、BPMN 式审批引擎
- 无场景的通用代码解释器

> 后续路线：P0 可观测埋点 → P0 上下文管理 → P1 记忆/规划 → P2 反思/人工介入 → P3 沙箱，里程碑见 §11.8。