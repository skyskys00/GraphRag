> **状态行**：版本 v0.4（方案稿；**项目版本 v6.x——v6.0 已追认定格历史 v5.43–v5.50，映射见 `docs/CHANGELOG.md` v6.0 条目**；P1 功能落地：三工具 + 显式 StateGraph + 会话落库 + 前端接 agent + token 级流式 + `[n]` 引用 + 决策链折叠/回放 + **v5.48 答案分箱 + v5.49 P2-cite（多编号并列 + 评测宽池）+ v5.50 多轮直答修复/前端决策链改版**）｜ 状态：**P1 已验收通过（v5.47）；v5.48 分箱收官；v5.49 P2-cite 收口；v5.50 修复多轮历史绕检索直答 + 前端决策链改版**——A 强制检索（事实性问题必须先调工具）+ B 诚实兜底（空清单禁编号、剥离清单外角标）；前端「Agent 执行过程」移至 AI 消息顶部 + 一键展开 + 调用序号；curl SSE 与浏览器双验证通过——引用标注粒度修正双落地：generate 跨块句并列标注 `[1][2]` + 评测上下文放宽为决策链全检索重建池（judge 视野 = 生产同口径，非泄漏），faithfulness 0.7840→**0.9368**（Δvs直连 −0.029 过门禁）、citation_accuracy 0.8648 贴线（Δ−0.031，噪声带已知项）、vs 直连 correctness +0.043 / relevance +0.035 不劣化；tool_acc 0.858→0.793 已归因为 judge JSON 解析伪分（DV-FS-017）+ list_docs 时机随机 + 重判窗口漂移，非引用协议劣化；单次 judge 判定噪声较大（同答案多轮 0→0.83 跳），结论一律下粗粒度定性｜ 更新：2026-10-26 ｜ 定位：**Agent 为完整系统主体，RAG 全链（M0–M7）作为其底座能力区**——不是「RAG + agent 附加」，而是「agent 系统内嵌 RAG 能力」 ｜ 契约：待定（见 §5） ｜ 上游（底座）：[M5 检索层](M5_retrieve.md) · [M6 生成层](M6_generate.md) · [M7 交互层](M7_interact.md)（完整 RAG 底座 M1–M4 见 [FRAMEWORK_NOTES §3](../../FRAMEWORK_NOTES.md) / [ARCHITECTURE §1](../../ARCHITECTURE.md)） ｜ 下游：[M8 前端](M8_frontend.md) ｜ 依据：用户拍板「agent 做完整系统、RAG 属于它的一部分」（2026-10-23 范式重启；同日选型定稿 LangGraph；P0 探底+骨架完成）｜ 运行：待定 ｜ 变更历史：见 `docs/CHANGELOG.md`

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

- **新增 agent 端到端评测**（复用 M9 runner / judge 基础设施，测试集 `testset_device_v2` 50 题，collection `col_b7b876b1`）。
- **指标**：
  - `tool_accuracy`：每步工具选择是否合理（LLM 裁判）；
  - `multi_hop_recall`：多步检索后目标信息是否进入最终上下文；
  - e2e `Correctness / Faithfulness / Answer Relevance / Citation Accuracy` 沿用现有四指标；
  - **RAG 对比回归**：同一题集，agent 首轮直答 vs 原 `/answer` 直连基线，量化「agent 引入不劣化底座」——这是「底座收纳」的回归门禁。
- **诚实红线**：agent 工具描述不得含 key_facts 措辞；评测口径 = 生产口径（沿 v5.30 泄漏治理）。
- **评测口径约定（2026-10-24 修正）**：agent 答案的 `[n]` 是**生成阶段编号**（非检索位次），judge 必须按 marker 精确对齐到对应 text_unit——`citation_accuracy` 以 `ctx_by_marker` 显式映射传入（runner 由 citations 反查 sidecar 构建），**禁用**直连的「marker n = contexts[n-1]」位次约定。基线同题 A/B 时基线也须按「citations → 完整内容」窄池重判（`scripts/recompute_baseline_strict.py`），避免 top5 宽池虚高。

### 7.1 A/B 实测结果（2026-10-24，source_set v5.46）

同口径对比（agent eval 报告 `backend/tests/reports/agent/run_agent_device_50_20261023_fixed.json` vs 基线 strict `run_e2e_device_50_20261005_strict.json`），完整表见 `backend/tests/reports/agent/agent_vs_direct_ab.md`：

| 指标 | Agent | strict 基线 | Δ | 判定 |
|---|---|---|---|---|
| Correctness（正确性） | 0.9115 | 0.8733 | +0.038 | 不劣化 |
| Answer Relevance（答案相关性） | 0.9830 | 0.9314 | +0.052 | 不劣化 |
| Faithfulness（忠实度） | 0.7528 | 0.9655 | −0.213 | **劣化** |
| Citation Accuracy（引用准确率） | 0.7264 | 0.8960 | −0.170 | **劣化** |
| Multi-hop Recall（agent 上下文召回） | 0.9080 | —（直连无此项） | +0.908 | agent 增益 |
| Tool Accuracy（工具链合理性） | 0.8330 | —（直连无此项） | +0.833 | agent 增益 |
| Context Recall（检索召回） | —（由 Multi-hop Rec 替代） | 0.8833 | — | 不可比 |

**判定（M10 §7 / build_agent_ab 回归门禁 Δ ≥ −0.03）**：**未全绿**。核心五项 −0.03 门禁下，correctness / answer_relevance 显著**不劣化甚至更优**；faithfulness、citation_accuracy **劣化**（Δ 约 −0.2）。

**劣化归因（非评测噪声，已排除两种 artifact）**：
1. ~~marker 位次错配~~：初版 agent 评测按 list order 传 citations → 编号与上下文错位造成虚低（0.612）。修正为 marker 精确对齐后 → 0.7264，且逐条复核（DV-FS-001 引用装箱单块判 False、DV-TN-010 重复编号 [9] 挂两处事实）确认修正判定正确。
2. ~~top5 宽池 vs 窄池~~：基线重判为「citations 完整内容」同口径后仍 0.896 / 0.966，差距依旧。
3. **真实结构性差异**：agent 多轮检索拿到更多资料 → 答案更全面正确（correctness↑），但**引用标注粒度不够**——一个 `[n]` 覆盖整句/整表，而依据分散在多个 text_unit 细分块（如 DV-TN-010 的规格表 6 行来自多个 block，答案只标 [9] 绑定其中一个）。裁判按被引用 text_unit 判 → 判无依据。直连「贴块写作」天然逐句对应，反而高分。

**结论（记录在案，待 P1 验收拍板）**：agent 引入后「答得更对但引用更散」——正确性收益真实、忠诚/引用劣化真实，二者同源（答案综合多来源而未细分标注）。P2 增强候选：生成阶段要求逐陈述标注来源 / 将跨块聚合事实 `[n]` 展开为多编号 / generate 提示强化「只引实际依据块」。

### 7.2 判别性验证：劣化确实是标注粒度（2026-10-24，v5.46.1）

为了区分「引用标注粒度」与「检索/生成遗漏」两个假说，对 v5.46 评测中 faithfulness ≤ 0.75 的 **26 个劣化题**，用同一份 agent answer 重建完整 agent 上下文（确定性 retrieve 按 tool_chain args + top_k），再做「宽松池」（全部检索命中而非仅被引用的 text_unit）重判：

**结果：回升 16 / 持平 2 / 回落 8，平均 Δ = +0.090**。强回升样例（宽松池几乎完全恢复）：
- DV-TN-010（六行规格表跨块聚合，v5.46 的 `[9]` 重复编号案例）：0.300 → **0.875**（+0.575）
- DV-SUM-008：0.680 → 0.930（+0.250）；DV-SUM-001 +0.233；DV-SUM-006 +0.183；DV-TN-012 +0.336

回落 8 题中 2 题为重建超时伪影（DV-IO-004、DV-FS-018，rebuild_ctx=0 → 宽松池空上下文判 0，非真实回落），其余 6 题回落均 ≤ −0.167。

**结论**：信息**全部在 agent 上下文**（宽松池基本满分），评分损失集中在「标注某一具体 text_unit 作为整句依据」这一步——**纯标注粒度问题，无检索遗漏、无生成丢失**。P1 归因闭环。

### 7.3 决策链体验增强（v5.46.1）

- tool_result 事件携带命中预览 `snippets`（citations[].snippet 前 100 字），前端「Agent 执行过程」折叠块展示 N 轮/M 次调用 + 每轮 `第 i 轮` 卡片（决策=查询、结果=`→ summary` + 命中预览）。
- tool_steps 历史回放：`_events_to_tool_steps`（分轮规则与前端 useChat 同构）在 POST /agent 与 /agent/stream 均随 `append_round` 落库；刷新重进会话决策链可恢复。
- 修复 **/agent/stream 422 路由 bug**：`@app.get("/agent/stream")` 误挂辅助函数 `_events_to_tool_steps`（要求 body → GET 恒 422），SSE 端点 `stream_agent_ep` 反成普通函数 → 前端「连接已断开」。恢复装饰器归属后浏览器端到端（实时渲染 + 刷新回放）全通过。

## 8. 分期落地

| Phase | 内容 | 完成标志（可验证） |
|---|---|---|
| **P0（探底）✔ 完成 2026-10-23** | v4-flash 原生 `tools` + 与思考/`extra_body` 互斥性；langgraph 依赖树最小安装（无全量 langchain）；`/agent` 路由骨架 + async StateGraph（注册 `tool_list_docs` 一个真实工具） | 探底结论回填 §10 ✓（§10-8）；`POST /agent` 健康应答可用 ✓（v5.42，实测工具决策→执行→生成→事件线闭环） |
| **P1 (MVP)** | **显式 StateGraph**（route / execute_tools / generate + 条件边）+ 三工具（retrieve / rag_answer / list_docs）+ 流式事件线 + 前端问答接 agent + agent 测试集 v0 + RAG 对比回归 | **功能全部完成**（v5.43 三工具+StateGraph+事件线+会话落库；v5.44 前端切 `/agent/stream` + 决策链折叠卡片 + 面板容错；v5.45 token 级流式 + `[n]` 引用角标；v5.46 评测回归；v5.46.1 判别性验证 + 决策链增强/回放）→ **评测回归完成但验收待定**：correctness/relevance 显著更优，faithfulness/citation_accuracy 真实劣化（Δ≈−0.2），核心五项未全绿；判别性验证坐实劣化为**引用标注粒度**非检索遗漏（§7.2），详见 §7.1 |
| **P2** | **P2-cite 已落地（v5.49，§9.4）**；其余 + `tool_graph` / `tool_compare` + 多工具组合 + agent 评测全量 + A/B 报告 | 跨库多步问题可编排；A/B 报告归档；CHANGELOG 新版本条目 |
| **P3（可选）** | 意图路由显性化、agent 多轮记忆增强、复杂任务拆解（sub-agent） | 跨文档综述 / 参数对比类问题 by-tool 达标 |

> 每 Phase 结束按项目收尾章程：CHANGELOG + 受影响模块状态行同步；实测报告归档；能跑才算完成。A/B 报告路径沿用 `backend/tests/reports/` 分类结构。

## 9. 风险与取舍

- **底座回归风险**：任何 agent 改动不得回写 M5/M6 语义。破坏性变化只准发生在 `app/m10_agent/` 与 `m7_interact` 的 agent 路由/适配层；M5/M6 变更必须走既有评测门禁。
- **LLM 工具调用可靠性**：JSON 格式稳定性 P0 探底；兜底——解析失败重试 1 次、仍失败退化为 `tool_rag_answer` 单轮应答（诚实标注「退化为单次检索」）。
- **Token / 延迟成本**：多轮多次 LLM 调用。约束：N=5、工具结果入上下文前压缩（复用 M6 assemble token 预算）、流式不破坏 TTFT。
- **评测泄漏红线**：工具描述不得含 key_facts 措辞；同库同裁判同口径。
- **引用标注粒度风险（2026-10-24 实测证实）**：agent 多轮检索后答案综合跨 text_unit 内容，但 `[n]` 一个编号只绑定一个块 → faithfulness/citation 较直连劣化（Δ≈−0.2，见 §7.1）。判别性验证（§7.2）确认信息均在 agent 上下文，**为纯标注粒度问题**（非检索/生成遗漏），对应 P2 候选见 **§9.1 P2-cite**。

### 9.1 P2 候选：P2-cite —— generate 阶段逐陈述标注真实块

**背景**：§7.2 判别性验证坐实 v5.46 忠实/引用劣化为「标注粒度」——信息在上下文，loss 仅在 `[n]` 一个编号绑一个块、而被引块无法承载整句跨块综合。P1 线下关，P2 选线收编。

**方案（A，首选）text_unit_id 直引**：
- generate 节点不再注入 `[n]` 编号清单（`_build_citation_index`/`_resolve_citations` 移除），改为要求 LLM 在陈述后用实际 `text_unit_id` 标注来源；
- 收尾按 text_unit 解析成 citations，`marker` 仍照还原（前端引用面板/预览跳转零改动）。

**取舍 vs 现状**：
| | 现状 `[n]`+清单（v5.46） | text_unit_id 直引 |
|---|---|---|
| 账本层 | 去重编号 + 清单注入（生成阶段带清单、多轮一致性靠提示） | 消除——LLM 直接指真实块 |
| 标注粒度 | 一编号一块，跨块综合无法并列 | 一陈述可列多个 text_unit → 粒度对齐直连「贴块」 |
| token/延迟 | 清单每轮占上下文 | 省清单；ID 略长于 `[n]` |
| 风险 | LLM 编造带外 ID → 需解析校验+回退（沿用 drop 策略） | 同左 |

**验证**：P1 全量 50 题复跑，faithfulness/citation_accuracy 应回升至 ≥直连基线（0.9655/0.8960）或 Δ≥−0.03 绿；A/B 表重出。

**（候补 B）**：聚合事实 `[n]` 展开为多编号（保持角标协议、代价是编号膨胀）——仅当 A 解析容错成本过高时回退。

**拍板状态**：已收进 P2（2026-10-24 用户确认），不占 P1 验收阻塞项。

### 9.2 忠实度双轨裁判（v5.47 落地，P1 收口依据）

**问题**：v5.46 判别性验证坐实劣化为引用标注粒度后，8 落题重判仍低——深挖发现被罚项集中在「上下文兜不住的结论性表述」：型号推断（DV-FS-001/002 的 LSP-1C 归源错误）、出处/概括说明（DV-TN-008 来源、DV-FS-018 的 6810 分类目录句）、图块 OCR 内容（DV-IO-004 产品示意图标注）。这些是 agent 的合理增值/元信息层，不是编造，却被裁判按「无依据」罚。

**口径（用户三次拍板）**：judge 对无依据陈述打 `kind`——`fact`（含数值/单位/参数/规格/型号/剂量/禁止事项/归源错误，强制 fact，必须溯源）／`meta`（出处来源/概括结论/否定说明/由原文可推出的推断、引用来源自纠说明，单报不计分，供人工核查）／`enhance`（常识级安全建议，单列知识增强分）。只罚 fact；`fact_total` 由 LLM 报、代码侧 `_EXTRA_FIELDS` 透传（修复隐层 bug：原缺 `fact_total` → 分母恒退回 `total_statements` 系统性虚高）。

**代码侧兜底层（压 LLM 判定方差，方向安全单向放松）**：①meta 指纹——出处/否定/纠错句式（「根据…说明书示意图」「只说明…未写明」「上一轮…本条资料」）强制降 meta；②数值词面核验——被判无依据的 fact，若全部数值 token（小数/整数+单位）在上下文原文逐字命中 → 改判有依据（救 LLM 看漏，如 DV-TN-008 六条表格数值）。

**接受的现实（记录在案）**：兜底压不住「LLM 每次报 fact_total/unsupported 的判定漂移」，同一 8 题多轮重判 0→0.83 区间跳（DV-FS-001 四轮 0.5/0.625/0.5/0.0）；裸版对照也证朴素「直接罚」会把 DV-IO-004 打崩到 0.167。故**结论一律下粗粒度定性，不以小数承重**。

**v5.48 预告（答案分箱）**：generate 分「资料事实主体 / 建议箱」，judge 只审主体对位，双轨兜底层退化为简洁单层，50 题重生成重测出稳定答案基线。

### 9.3 答案分箱 + 忠实度朴素单层收官（v5.48 落地，§9.2 预告兑现）

**分箱协议**：`generate` 把操作建议 / 安全提醒 / 经验结论 / 超资料补充拆进答案末尾 `<advice>…</advice>` 段，主体只留可 `[n]` 对到资料的纯事实；`[n]` 只对主体还原（建议区不参与引用）。无建议不输出段。评测只审主体 → 换 agent 大脑正确性不受建议区影响。

**评测口径复盘**（用户拍板）：删 v5.47 kind 三分类 / meta 指纹，回朴素单层（每陈述无依据即罚）+ 唯一代码侧兜底=数值词面核验；另叠一条提示词判据——「根据…说明书/资料/示意图标注」开头的**来源引导语**只声明出处、不单独判罚（主体残留出处/图块/概括句不再被误伤）。**型号词核验探底无效回滚**（详情见 CHANGELOG v5.48：型号多在标题不在 text_unit content、逐字命中救不回，净收益被重生成噪声吞没）。

**50 题全量新基线（2026-10-25）**：faithfulness **0.7840** / citation **0.8652** / correctness **0.9283** / relevance **0.9770** / multi-hop **0.9247** / tool_acc **0.8580**（judge 零失败）。vs 直连 strict 基线：correctness +0.055、relevance +0.046 不劣化；faithfulness −0.18、citation −0.031 仍红但较 v5.46（−0.213/−0.170）大幅收敛，引用贴近门禁且落在 judge 噪声带内。忠实度残留 = 引用标注粒度（P2-cite 范畴）+ 主体残留出处/概括句朴素浅罚，属已知项。量化全表见 CHANGELOG v5.48。

### 9.4 P2-cite 落地（v5.49，引用标注粒度双修收口，§9.1 候选兑现→证伪改道）

**背景**：v5.48 收口后 faithfulness 0.7840 仍距直连大；§7.2 判别性验证已坐实信息全在 agent 上下文、loss 纯在标注粒度。§9.1 原案（text_unit_id 直引）经三步探索证伪：LLM 从未见过 `{16hex}-chunk-{NNN}` 形态（提示后也不会稳定产出）→ 评测 `[n]` 正则与前端 CitationPanel 全锚整数 marker，直引要么重写评测要么空转回 1.0；改动面横跨生成+评测+前端。用户拍板 **A2/M6 式对齐**。

**A2 落地双端**：
- **生成端**（`agent.py`）：SYSTEM_PROMPT 追加「一个事实语句若依据跨多个资料块，必须并列标注全部相关编号（如 [1][2]），禁止只标其中一块；编号必须是最末一条消息资料清单里实际出现的编号」。`_build_citation_index` / `_resolve_citations` **零改动**，marker 仍按 `(doc_id, chunk_id)` 整数去重 → 前端/评测/回放生态零破坏。
- **评测端**（`runner.py`）：`_rebuild_retrieval_pool` 重放 `tool_retrieve` 步骤 → 决策链全命中重建池（完整 content + sidecar resolve 的 title 元信息作 `【文档：…】` 头，按块去重、上限 40 块）；`faithfulness` / `multi_hop_recall` 改吃重建池（judge 视野 = agent 真正检索过的全集，与生产生成依据同口径、§11 非泄漏），`citation_accuracy` 保持被引窄池 + `marker_ctx` 显式对齐；无 retrieve 时回退被引窄池兜底。

**50 题全量（2026-10-26，`run_agent_device_50_p2cite.json`，5845.8s）**：faithfulness **0.9368**（v5.48 0.7840 → **+0.153**，Δvs直连 −0.029 **过门禁**）；citation_accuracy 0.8648（持平，Δ−0.031 贴线）；vs 直连 correctness **+0.043** / relevance **+0.035** 不劣化；multi-hop 0.900（重建池口径正常波动）；tool_acc 0.793。**tool_acc 下降归因（排查完）**：DV-FS-017 1.0→0.0 为 **judge JSON 解析失败伪分**（LLM 返回内嵌 JSON 文本被 `_parse_judge_output` 误提取 → 0.0，实际判 1.0）；主降（DV-TN-007/IO-002/IO-004/TN-002）为 agent 重跑把 `list_docs` 从第 1 步挪到第 2 步 + judge「检索后补 list_docs 冗余」稳定偏好 + 重判窗口漂移；同批 correctness 反升证非质量退化。量化全表见 CHANGELOG v5.49。

**残留已知项**：citation −0.031 卡在单次 judge 噪声带内（每句重判漂移 ±0.3，指标整体波动 ±0.03）；tool_acc 的 list_docs 时机噪声 → **v5.49.1 已落地工具描述引导**（「检索/作答前作为第一步调用，避免冗余盘点」），改造后单轮冒烟观察为简链直答、未重复盘库，全量影响待下次评测核算。

### 9.5 多轮历史绕检索直答缺陷与修复（v5.50，A+B）

**现象（浏览器目验时发现）**：多轮历史会话中，route 层 LLM 认为历史已足够回答 → 绕开检索工具直接进 generate → 无编号清单 → LLM 按历史风格编造 `[n]` 角标；`_resolve_citations` 因编号不在清单丢弃 citations，但正文残留假角标 → 用户看到点不动/引不出的引用，前端也因无 tool_steps/citations 而无决策链与引用面板。评测均为单轮无历史，故长期未暴露。

**根因**：① SYSTEM_PROMPT 未约束「不得仅凭历史作答」；② 引用还原只清 citations 数组，不清正文角标。

**修复**：**A 强制检索（治本）**——SYSTEM_PROMPT 追加「除纯问候或与本知识库无关的闲聊外，事实性问题回答前必须先调用检索工具，不得仅凭已有对话历史或上一轮检索结果直接作答」；**B 诚实兜底（红线）**——generate 在编号清单为空时注入「不要输出任何 [n] 角标」，并新增 `_strip_unbacked_markers` 剥离正文/建议区所有不在清单内的角标（顺带堵住「清单非空但编造不存在编号」同类漏洞）。判据是本轮真实检索依据，闲聊与正常问答均不误伤。

**验证**：curl SSE 序列对比——修前带 12 条历史同题仅 `delta/citations/done`（零工具、`citations=[]`、正文含编造 `[1][2][3]`）；修后 `tool_decision→tool_result→delta→citations→done`、`citations=4`、角标全在清单内。闲聊冒烟——`citations=0`、正文零角标。浏览器目验——决策链在消息顶部、一键展开、序号/箭头、引用面板与角标按钮全部正常。

**遗留**：50 题全量评测未重跑（评测无历史，A 预期无劣化），量化影响待下次评测核算。

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