# M6 模块记录：生成层（编排 + 引用标注）—— 已落地

> 状态：**已落地**（2026-09-14 v1，MVP：single-window 组装 + 生成 + TextUnit 级引用）。下文为落地前的规划方向，实施结果见 §8 / §10 与代码 `app/m6_generate/`。
> 契约：query + M5 精排 context → 答案（text）+ 结构化引用（citations）。
> 依据：`docs/FRAMEWORK_NOTES.md` §3 模块划分 ｜ `docs/ARCHITECTURE.md` §2.7 Agent 编排与上下文组装 ｜ `docs/modules/M0_contracts/textunit.md` §1（M6 引用标注消费 text_unit_id/file_path/page_range/anchor）
> 前置：M5 检索 v1/v1.5 已落地（三路 RRF + rerank + full_doc_id 溯源）、DeepSeek v4-flash 统一链路、M2 chunk 产物含 page_range/anchor

## 1. 定位

M6 = **从「精排 chunk 列表」到「带引用的最终答案」** 这一段的 LLM 编排，位于线 B 检索〔M5〕与交互〔M7〕之间。输出是结构化 `Answer`（正文 + citations），不直接对接 HTTP/SSE（流式、多轮 history 归 M7）。

模块表原文职责：**意图路由 / map-reduce / 上下文组装（token 预算）/ 引用标注 / 流式事件**（FRAMEWORK_NOTES §3）。

## 2. 需求来源（架构既定方向）

- **上下文组装（5 轨道）**：把 原文块/实体/关系/图谱路径 各路召回经排名+过滤后压缩进 token 预算窗口（`max_data_tokens`），独立成函数便于复用测试（ARCHITECTURE §2.7-2）。
- **引用标注（grounded citations）**：让「来源 doc_id / TextUnit ID / 节点名」整链路流转，答案外层附 `[引用列表]`；local 层用 TextUnit ID 细引用，全局层用主题/报告名粗引用（§2.7-3）。
- **意图路由**：跨文档综述 → map-reduce（分块要点→合并→生成）；实体级/局部 → single-window（§2.7-1）。
- **流式**：SSE + token 级；map-reduce 只在 reduce 阶段流式（§2.7-4）→ 归 M7，M6 留事件位不实现。
- **LangChain 薄包装**：仅用于 history 管理与工具调用协议；组装/生成自研，不深度用 LCEL（§2.7 开头）→ **MVP 决策见 §6**。
- 检索策略（`global`/`local`/`mix`，M5）与生成策略（`single-window`/`map-reduce`，M6）是**分层、不互斥**的关系（ARCHITECTURE §5.6 附近）。

## 3. MVP 范围

**做什么**：
1. **上下文组装器**：M5 精排 top-N chunk → 按 token 预算压缩成生成用 context（带 marker）。
2. **生成**：中文 prompt + DeepSeek v4-flash（关思考模式），要求按 marker 输出 grounded 引用标注。
3. **引用解析**：把正文 `[n]` 映射回 TextUnit 级引用（text_unit_id / full_doc_id / file_path / page_range / anchor / snippet），输出结构化 citations。
4. **M6 溯源 sidecar**：chunk_id → {file_path, title_path, page_range, anchor, block_type}，从 M2 产物 `data/chunks/*.jsonl` 构建并启动加载（**不改 M5 返回结构**，解耦）。

**暂不做**（列入二期 / 他模块）：
- map-reduce 跨文档综述（意图路由 v1 固定 single-window，函数位先留）；
- SSE 流式、多轮 history、工具调用（M7）；
- LangGraph 状态机（仅当多智能体/复杂工具调用需要，架构 §2.7）；
- 语义缓存（ARCHITECTURE §2.7-5 二期）；
- 全局综述 / 社区摘要（入口在 M5，非 M6）。

## 4. 架构与实现

### 4.1 数据流

```
query ─┬─► M5 retrieve() ─► results[chunk_id/content/full_doc_id/score]
       │                        │
       └─► route()              ▼
             （v1=常量 single）  assemble(query, results, max_tokens)  → context[marker→chunk]
                                  │
                 generate(context, response_type)  → 带 [n] 标注的正文
                                  │
                 parse_citations(text, context)    → Answer{citations}
```

### 4.2 组件（规划目录 `app/m6_generate/`）

| 文件 | 职责 |
|---|---|
| `sidecar.py` | 构建/加载 chunk 溯源映射（读 `data/chunks/*.jsonl` → `{text_unit_id: {file_path,title_path,page_range,anchor,block_type}}`） |
| `assemble.py` | `assemble(query, results, max_tokens) -> (context, marker_map)`：按 score 顺序拼原文块，超预算截断；marker 与 chunk_id 一一对应 |
| `generate.py` | 中文 RAG prompt（含 `response_type`）+ DeepSeek flash 调用（复用 M0/M3 providers 的 `extra_body=thinking disabled`），要求 grounded 标注 |
| `cite.py` | `parse_citations(text, marker_map, sidecar)`：`[n]` → Citation 列表；越界/缺失回退提示 |
| `orchestrator.py` | `async answer(rag, query, ...) -> Answer`：编排 A→B→C→D；意图路由函数位（v1 常量 single-window） |

### 4.3 输出契约（dataclass）

```python
@dataclass
class Citation:
    marker: int                # 正文中的 [n]
    text_unit_id: str          # = LightRAG chunk_key（textunit.md 对齐）
    full_doc_id: str
    file_path: str
    title_path: str | None
    page_range: list[int] | None
    anchor: str | None
    snippet: str
    score: float

@dataclass
class Answer:
    text: str
    citations: list[Citation]
    retrieval: dict            # 透传 M5 的 query/preprocess/routes/fusion
    meta: dict                 # model / 耗时 / 生成 tokens
```

## 5. 前置与缺口

| 项 | 现状 | 缺口 |
|---|---|---|
| M5 精排结果 | `retrieve()` 带 chunk_id/content/full_doc_id/score | **无 file_path/page_range/anchor**（M5_retrieve §8 遗留 #4 已点出）→ M6 用 sidecar 补齐（§4.2），不动 M5 返回 |
| chunk 级溯源数据 | M2 产物 `data/chunks/<doc_id>.jsonl` 含 page_range/anchor/title_path（textunit 契约 v2 §4） | 需从 JSONL 构建 sidecar（1 次性离线，启动加载） |
| 生成模型 | DeepSeek v4-flash 全链路已定（不用 pro，thinking 已 disable） | 复用 M0/M3 providers，不新增 |
| 中文 prompt | LightRAG 自带 prompt 基于英文场景 | 需中文化 response_type / 引用指令 |
| 依赖 | conda env 目前仅 lightrag-hku / langchain-text-splitters / 解析三件套 | **MVP 决定不引入 langchain/langgraph**（§6） |

## 6. 关键取舍

1. **不引 LangChain，MVP 自研极简编排**：架构 §2.7 说 LangChain 薄包装仅用于 history 管理 + 工具调用协议——而 MVP 是单轮、无外部工具调用，这两样都用不上。自管一个极简 `history: list[dict]` 即可；等 M7 多轮/工具化再评估引入。**每行逻辑可测，无框架抽象包袱**（符合"简单优先"）。
2. **意图路由 v1 = single-window 常量**：当前 5 文档、query 以单点知识为主；`route()` 留函数位和说明（full_doc_id 跨 doc ≥2 + 综述触发词 → 提示 map-reduce），map-reduce 作为后续第二步迭代点。
3. **引用粒度：细引用（TextUnit）为主**：local 检索场景答案逐句标注 `[n]` → 映射到 chunk（=TextUnit）；全局/综述场景的"报告名粗引用"暂不覆盖（未做 map-reduce）。
4. **组装预算兜底**：rerank top-8 若估算超 `max_tokens`（默认 ≈4000），按 score 顺序截断并保留 marker 映射，宁少勿乱。
5. **引用真实性兜底**：生成阶段要求"无依据不标注、引用必须来自 context"；`parse_citations` 对越界 `[n]` 或空 text 的回退为 `not_found` 提示，不打补丁编造引用。

## 7. 验收标准

- [x] **端到端**：5 题（M5 四题 + 1 缩写/口语）`answer()` 跑通，返回 Answer；耗时可接受（9.1s / 6.9s / 7.2s / 26.0s / 7.5s）。
- [x] **引用可回连**：每条 Citation 的 text_unit_id 存在于 PG `lightrag_doc_chunks`；file_path/page_range 与 M2 sidecar 一致。
- [x] **引用可定位**：PDF 块带 page_range（季度复盘 `[0,0]/[1,1]`）；docx/md 块无页则退化"文件 + 文本片段"（textunit 契约 v2 §4.2）。
- [x] **忠实度抽查**：5 题每个论断都能在对应引用的 content 里找到依据；材料缺口如实说明不编造（投诉受理步骤、会议决议文本均明确标注缺失）。
- [x] **组装函数单测**：给定固定 retrieval fixture，输出在 token 预算内、marker 与 chunk 一致。
- [x] **不劣化**：M6 只读 `retrieve()` 结果与 M2 sidecar，不改 M5 内部。

## 8. 实施步骤（落地核对，每步验证方式）

1. **sidecar 构建**（读 data/chunks JSONL 全量 30 条）→ ✅ 每条有 file_path；PDF 块 page_range 正常，docx/md 无页走降级。
2. **assemble** → ✅ fixture 单测通过（预算内 + marker 映射正确）。
3. **generate**（复用 providers flash + thinking disabled，中文 prompt + response_type）→ ✅ 5 条 query 出文且含 `[n]`。
4. **parse_citations** → ✅ `[n]` 全部命中 context；构造越界/缺失用例走回退分支（unmatched 计数）。
5. **orchestrator.answer 端到端** → ✅ 答案 + citations + 引用可回连 PG；5 题人工忠实度抽查通过（§7）。
6. **（后续）意图路由 map-reduce**：跨文档综述 query 试点，v1 恒走 single-window（`route()` 函数位已留、defect 提示位 `map-reduce(pending)`）。

## 9. 风险与缓解

- **flash 思考模式忘关** → 复现 M3 坑（content 空、重试卡死）；直接复用 providers 的 `extra_kwargs`，不新写 LLM client。
- **模型自标 `[n]` 与 context 错位 / 漏标** → prompt 强约束 + parse 兜底回退；若频繁则升级为"按段落强制引用"指令。
- **中文生成格式不稳定** → `response_type` 中文模板（分点/先总后分）先零样本试，效果差再 few-shot。
- **预算超窗** → assemble 截断 + 记录截断日志，供 M8 评估召回是否够。
- **与已有模块耦合** → M6 只入 M0/读 M5 结果与 M2 产物，不反向改 M3/M4/M5 内部，遵守"低耦合三原则"。

## 10. 变更记录

- **2026-09-14 · v0 规划**：规划初稿（不执行）；对齐 FRAMEWORK_NOTES §3 / ARCHITECTURE §2.7 / textunit 契约 v2；明确 MVP 范围、缺依赖（溯源 sidecar）、LangChain 不引入决策、验收与实施步骤。
- **2026-09-14 · v1 落地**（MVP 全量实现于 `app/m6_generate/`）：sidecar（30 textunit 溯源）/ assemble / generate / cite / orchestrator / runner（CLI，`-w/-q/--response`）。运行 `python -m app.m6_generate.runner -q "问题"`。单测（无 LLM）通过；内置 5 题端到端通过（8 chunks each，citations 9/3/8/4/8，耗时 7–26s）。已知边界：`[n]` 与检索上下文错位时按真实材料诚实说明（e.g. 投诉受理步骤正文仅标题），不编造。
- **2026-09-14 · v1.1（软删过滤透传）**：`answer()` / `answer_stream()` 增 `exclude_docs` 参数透传 M5 `retrieve(exclude_docs=)`（M7 传入 `deps.excluded_docs`）——软删文档不再进入组装/引用。生成/组装逻辑未改动。