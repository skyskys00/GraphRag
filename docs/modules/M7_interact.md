# M7 模块记录：交互层（FastAPI + SSE + 上传/调度）

> **版本：** v10.2
> **状态：** 已落地
> **更新：** 2026-09-21
> **定位：** HTTP API + SSE 流式问答 + 文档上传调度 + 图谱查询 + 多知识库 + 多会话
> **契约：** REST/SSE → M5/M6 编排结果；完整接口以 **Swagger UI** 为准（代码即契约，永不过时）
> **上游：** [M5 检索层](M5_retrieve.md) / [M6 生成层](M6_generate.md) | **下游：** [M8 前端](M8_frontend.md)
> **依据：** [`ARCHITECTURE.md`](../ARCHITECTURE.md) §2.8 ｜ [`FRAMEWORK_NOTES.md`](../FRAMEWORK_NOTES.md) §3
> **运行：** `cd backend && python -m app.m7_interact.api` → Swagger: http://localhost:8787/swagger
> **变更历史：** 见 [`CHANGELOG.md`](../CHANGELOG.md)

> **关于 API 契约：** 本模块的完整接口规格（端点/请求体/响应模型/字段类型/必填项）以 **Swagger UI 为单一事实源**（FastAPI `docs_url="/swagger"` 自动生成）。启动后端后访问 `http://localhost:8787/swagger` 查看，随代码自动更新，不再另写静态 API 文档。

## 1. 定位

M7 = **把「带引用的答案」变成用户可交互的产品外观**，位于线 B 末端：接收 query → 调 M6 `answer()` → 以 HTTP/SSE 对外提供，另配一个轻量 WebUI 验证主链路。不新增检索、不新增生成逻辑；只做「编排结果 ↔ 浏览器」之间的适配壳。

模块表原文职责：**FastAPI + SSE + 多轮记忆 + WebUI**（FRAMEWORK_NOTES §3）；关键技术归属：FastAPI+SSE（后端壳）、AntV G6（前端渲染，后期）。

## 2. 需求来源（架构既定方向）

- **统一对外入口**：FastAPI 统一对外，承载 SSE 流式 + 引用标注 + 多轮会话、文档管理（ARCHITECTURE §2.8）。文档管理会牵动 M1–M3 重建，MVP 只做问答，不做文档管理（见 §6）。→ **v3 已落地**：`POST/GET /docs` + `DELETE /docs/{doc_id}`（§8）。
- **流式输出**：SSE + token 级流式（FastAPI `StreamingResponse`）；map-reduce 只在 reduce 阶段流式；把「检索完成」「生成第 N 段」做成事件上报（§2.7-4）。→ token 级流式依赖 M0 支持流式生成，MVP 取舍见 §6-2。
- **Web UI 起步**：LightRAG 自带 WebUI 仅调试用；重做阶段自写「轻量简约版」验证主链路（§2.8）。→ MVP 前端 = 零依赖轻量页。
- **专业前端（后期）**：图谱展示主轴 = 「问题 → 回答 + 引用高亮 → 点击引用定位原文 → 从答案节点在图上游走」，渲染层 AntV G6；页面只依赖稳定 API（SSE + 引用标注）、与后端解耦（§2.8 / §6 ⑥）。→ G6 动线排后期，不进 MVP。
- **联调基线**：FastAPI（StreamingResponse + SSE）作统一入口，产物契约校验衔接；跑真实文档，含前端走查（引用高亮 / 点击定位 / 图谱游走）；最后 Docker Compose 收敛（§6 ⑦）。→ 引用高亮 / 点击定位进 MVP 验收，图谱游走与 Compose 收敛排后。

## 3. MVP 范围

**做什么**：
1. **FastAPI 壳**：`POST /answer` 复用 M6 `orchestrator.answer()` → Answer dict 序列化为 JSON 契约；`GET /health` 健康检查。
2. **SSE 事件线**：`/answer/stream` 用 `StreamingResponse` 输出事件序列（`retrieved → delta → citations → done`），事件定义成契约喂给前端。MVP 的 `delta` 事件可退化为「整段回放」（生成非流式），事件协议先定型。
3. **轻量 WebUI（零依赖起步）**：提问框 → 答案渲染 + 引用数字标注 → 引用侧栏，点击引用定位到原文片段（file_path + page_range/anchor + snippet）。
4. **多轮 history 最小注入**：把历史消息拼接进 query / 生成 prompt，LightRAG 每次独立检索；不开会话级状态存储。

**暂不做**（列入二期 / 后期）：
- **token 级流式**：需 M0 providers 暴露流式生成接口（`text_delta` 回调）——M6 的 `query_func` 当前一次性返回整段，SSE 的 `delta` 先走结果回放；
- **AntV G6 图谱可视化动线**（「从答案节点在图上游走」）：npm 依赖未装，属独立前端工程步骤（ARCHITECTURE §6 ⑥ ②）；
- ~~**文档管理**（上传/删除再索引）~~：原列二期后台功能，**v3 已落地**（§8）；
- **char-level span_map**：scripts/ 时代 p2 的旧概念、已删；M6 的 textunit 级溯源 + page/anchor/snippet 已满足「定位原文片段」，不再回归字符区间；
- **鉴权 / 多用户 / Docker Compose 收敛部署**：M8 评测后的收尾。

## 4. 架构与实现

### 4.1 数据流

```
浏览器 WebUI（轻量页 / EventSource）
   │  POST /answer / SSE 订阅
   ▼
[ m7 ] FastAPI（app/m7_interact/）
   │  ├─ /answer        → 调 m6 answer() → Answer dict → JSON 契约
   │  └─ /answer/stream → StreamingResponse；事件序列编码
   ▼ 复用（不新写检索/生成/引用解析）
app/m6_generate/orchestrator.answer
   （query → M5 检索 → 组装 → 生成 → parse_citations → Answer）
```

### 4.2 组件（规划目录 `app/m7_interact/`）

| 文件 | 职责 |
|---|---|
| `api.py` | FastAPI 应用：`GET /health`、`POST /answer`（请求含 query/history/response_type/stream）、`POST /answer/stream`（SSE）；把 M6 Answer dict 做字段级序列化 |
| `events.py` | SSE 事件构造/编码：事件类型 + JSON payload；事件线 `retrieved → delta → citations → done`（对齐前端消费） |
| `respond.py` | 组装请求 → 调 `answer()` → 产出 JSON 或事件流；收 M6 meta 做成事件负载 |
| `history.py` | 多轮最小注入：`history: list[dict]` 拼进 query/生成 prompt（不建会话存储） |
| `web/` | 轻量 WebUI：静态 HTML/JS 单页（提问 → 答案 + 引用定位），无框架依赖，后续可换 G6 工程 |
| `documents.py` | 文档入库编排（v3）：`ingest`（M1 `process_one` → M2 `process_document` → M3 `ainsert_custom_chunks` 增量 → `build_workspace_deps` 重建 sparse/sidecar/entities）+ `documents.json` 注册表 + 软删过滤（`excluded_doc_ids`），模块级 `asyncio.Lock` 串行入库；**上传原件保留 `uploads/` 供追溯**（v9.3，不再清理） |

### 4.3 契约（HTTP / SSE）

```jsonc
// POST /answer
{ "query": "客户投诉的处理流程？", "history": [{"role":"user","content":"..."}],
  "response_type": "请分段回答，先总后分", "stream": false }
→ 200 { "query": "...", "text": "...",
        "citations": [ {marker, text_unit_id, full_doc_id, file_path, page_range, anchor, snippet, score} ],
        "retrieval": {...}, "meta": { mode, used_chunks, context_tokens, source_docs, ... } }

// /answer/stream（SSE，事件逐段上报）
event: retrieved  data: {"used_chunks":8,"source_docs":["..."]}
event: delta      data: {"text":"当前已生成的文本片段"}
event: citations  data: {"citations":[...],"meta":{...}}
event: done       data: {"text":"完整答案","query":"..."}
// DOCS：文档管理（v3）
POST   /docs                  → 202 {task_id, filename, status:"processing"}   // 落盘 uploads → 后台 ingest → 状态流转
GET    /docs                  → [{task_id, doc_id, filename, status, error, created_at}]  // 未删除，按创建倒序
DELETE /docs/{doc_id}         → {deleted: doc_id}   // 软删：注册表标记 + parse/chunks 文件清除，检索侧 excluded 过滤
```

> `retrieval` 字段体积可能很大（M5 三路原始结果），HTTP JSON v1 直接透传；若实测过大再切 `meta` 摘要（决策点，落地时定）。

### 4.5 多会话（v10）

- **作用域**：每个 Collection 独立一套会话（注册表 + 明细），删库自动连带清空。
- **数据布局**：注册表 `<working_dir>/conversations.json`（仅元数据：title/created_at/updated_at/message_count/preview）；明细 `<working_dir>/conversations/<conv_id>.json`（完整 messages，含 citations/meta）。
- **命名**：`conv_<uuid8>`（与 `col_<uuid8>` 风格一致）。
- **核心 API**（全部带 `collection_id` query，缺省 `default`）：
  - `GET /conversations` — 列表（按 updated_at 倒序）
  - `POST /conversations?title=` — 新建（缺省标题「新对话」）
  - `GET /conversations/{conv_id}` — 明细（完整 messages）
  - `PATCH /conversations/{conv_id}?title=` — 重命名
  - `DELETE /conversations/{conv_id}` — 删除
- **答问落库**：`POST /answer` 与 `GET /answer/stream` 新增 `conversation_id` 参数。带会话时：历史从后端会话读取（`build_query_with_history` 取最近 4 条注入 query），答完 `append_round` 原子落库（`asyncio.Lock` 防并发写）。
- **首问自动命名**：标题仍为占位「新对话」时，用第一条 user 消息前 30 字覆盖。
- **向后兼容**：`conversation_id` 缺省 = 无会话模式（前端透传 history），WebUI / 旧客户端行为不变。

## 5. 前置与缺口

| 项 | 现状 | 缺口 |
|---|---|---|
| M6 Answer 契约 | `answer()` 已返回 query/text/citations/retrieval/meta，citations 带 file_path/page_range/anchor/snippet | **query_func 非流式**（一次性整段）——token 级 SSE 需 M0 补流式接口，MVP `delta` 先回放 |
| 前端访问能力 | 本机未装 npm 前端工程 / AntV G6 | MVP 零依赖轻量页即可；G6 引入是独立部署步骤（§6 ⑥ ②） |
| 引用定位原文 | citations 含 file_path/page_range/anchor/snippet，可定位到「文件 + 页/章节 + 文本片段」 | 无字符级 span（旧 span_map 方案已删）；MVP 定位粒度够用 |
| 多轮会话 | M6 单轮（history 参数位已留） | history 注入语义要定：独立检索拼接 vs 真会话态——MVP 取前者 |
| 项目依赖 `FastAPI/uvicorn` | 环境未装 | `pip install -i https://pypi.tuna.tsinghua.edu.cn/simple fastapi "uvicorn[standard]"` |

## 6. 关键取舍

1. **MVP 非流式 JSON 先行，SSE 事件线随后**：M6 的 `query_func` 是一次性返回整段。先做 `POST /answer`（非流式）打通主链路与契约，再让 SSE 事件协议成型（含 `delta` 回放）；token 级流式列为二期（待 M0 补流式）。
2. **诚实化流式**：不伪造「真 token 流」。MVP 的 SSE `delta` 事件语义 = 「事件线已就位，负载当前是整段回放」；要真流式就必须是 M0 从 DeepSeek 流式拿增量，而不是前端打字机假装。落地时以此判断要不要引入。
3. **轻量前端零依赖起步**：单页 HTML/JS 验证主链路（提问 / 答案 / 引用定位），不引入前端构建链；AntV G6 图谱动线作为后期独立步骤（FRAMEWORK_NOTES §5.2 渲染库定位）。
4. **引用定位粒度 = 文件 + 页/章节 + 片段**：沿用 M6 citations 字段即可满足「点击引用定位原文」；不回归 char-level span_map（历史方案已删且收益存疑）。
5. **多轮最小注入不建会话态**：history 拼进 query/prompt，检索每次独立；不做状态存储、不做跨会话持久化（二期再做）。
6. **只调 M6 不改 M6**：M7 壳组合 `answer()` 与 M0 的 LLM client，不反向改 M5/M6 内部，遵守低耦合三原则。

## 7. 验收标准

- [x] `POST /answer` 返回与 CLI `answer()` 一致的 Answer dict —— 实测单题（投诉）29.6s：citations 8 条回连 textunit/PG，meta 五字段齐全，retrieval 完整序列化（M5 无 numpy 污染）。
- [x] `/answer/stream` 事件序列符合契约 —— 实测事件线 `retrieved → delta×544 → citations → done`（单题 11s）；delta 为 M0 `build_query_stream_func` 真 token 增量，不再是分块回放。
- [x] WebUI 提问 → 答案渲染 + 引用标注；点击引用定位原文 —— 静态页 GET / 200（10KB，box/id/cite 控件在），服务端就绪；**浏览器内交互走查未在本环境做**（待用户开浏览器验证）。
- [x] 多轮最小注入 —— `history.py` 离线用例通过（role 前缀拼接 + 当前问题），HTTP 层参数透传已实现。
- [x] 不劣化 —— `app/m6_generate/` 零改动；M6 离线单测未受影响（M7 只新增壳）。
- [x] **文档上传闭环（v3）** —— `POST /docs` → processing → ready（doc_id 回填）；对新文档提问命中（引用 file 指向新 doc）；`DELETE /docs/{doc_id}` → 列表消失、检索零召回；正式文档问答/SSE 回归绿。

## 8. 实施步骤（落地核对）

1. **装依赖 + FastAPI 壳**（`/health`、`POST /answer` 复用 `orchestrator.answer()`）→ ✅ `fastapi 0.136.3 / uvicorn 0.52.4` 已装；实测单题 29.6s，JSON 契约与 CLI 一致。
2. **SSE 事件线**（`events.py` 编码，`/answer/stream`）→ ✅ 实测 `retrieved→delta×544→citations→done`（单题 11s）；delta 为真 token 增量（v2 升级，见 §10）。
3. **轻量 WebUI**（零依赖单页 `web/index.html`）→ ✅ 静态挂载 GET / 200；浏览器走查待用户验证（§7）。
4. **多轮注入**（`history.py`）→ ✅ 离线用例通过；`POST /answer`/`/answer/stream` 均接受 `history:[]`。
5. **token 级流式** → ✅ v2（M0 `build_query_stream_func` → M6 `answer_stream` → SSE delta 真增量）；**AntV G6 图谱动线 + Docker Compose 收敛** → 未做，排 M8 前端工程后。
6. **文档上传/文档管理（v3）** → ✅ `documents.py`（ingest 编排 + 注册表 + asyncio.Lock 串行）+ 三路由（`POST/GET /docs`、`DELETE /docs/{doc_id}`，`docs_url` 让位 `/swagger`）+ 联动 M5/M6 透传 `excluded_docs` 软删过滤。实测：上传→就绪→提问命中新文档→删除→零召回（§7）。前端配套见 `M8_frontend.md` v2.1。

## 9. 风险与缓解

- **反复试 SSE 却卡在模型调用** → MVP 先非流式 JSON 打通链路，SSE 事件线先成型、token 级流式等 M0 有流式接口再上，避免在生成不稳时叠流式复杂度。
- **前端工程依赖（npm/G6）未装阻塞** → 轻量页零依赖，不阻塞 MVP；G6 引入是独立部署步骤。
- **多轮注入后检索质量下降** → v1 仅简单拼接，做不了会话态就明说；效果不达预期则演进为会话摘要注入（二期）。
- **引用定位粒度不足** → 当前「文件 + 页/章节 + 片段」已满足点击定位；真需要字符级高亮再评估补 span（不优先）。
- **与已有模块耦合** → M7 只组合 M6 Answer + M0 LLM client，不反向改 M5/M6 内部；契约只增不改。

## 10. 版本

- **v10.2**（2026-09-21）：检索/生成 query 分离（Bug4 根因修复，方案 D，详见 CHANGELOG v5.3）。
- **v9.3**（2026-09-20）：上传原件保留——`ingest_task` 不再 unlink 上传临时文件，原件留 `uploads/` 供追溯（CHANGELOG v4.0.4）。
- **v5**（2026-09-15）：文档预览 `GET /docs/{id}/preview` + 图谱按文档过滤 `GET /graph?doc_id=` + 引用置信度排序修复。
- 变更记录：**逐条版本历史见 `docs/CHANGELOG.md`**（v0 规划 → v1 壳 → v2 真流式 → v3 文档管理 → v4 图谱导出 → v5 预览/过滤/排序）。本文件不再维护历史流水。