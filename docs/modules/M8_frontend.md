# M8 模块记录：正式问答前端（React + SSE 流式 + 文档管理 + 知识图谱）—— 已落地

> 状态：**v1 问答主链路 + v2.1 文档上传/文档管理 + v2.2 知识图谱/问答联动 均已落地**（2026-09-15）。本文为模块落地记录（结构/契约/实测/验收/变更）；产品侧需求稿见 `docs/modules/M8_frontend_req.md`（v2.2）。
> 契约：消费 M7 交互层 API（`POST /answer`、`GET /answer/stream`(SSE)、`POST/GET /docs`、`DELETE /docs/{doc_id}`、`GET /graph`、`GET /health`）。
> 依据：`docs/ARCHITECTURE.md` §2.8 交互层 / §6 ⑥ 前端设计与联调 ｜ `docs/modules/M7_interact.md`（后端契约）

## 1. 定位

打开浏览器即用的正式 Web 问答前端（React 18 + TypeScript + Vite，工程 `frontend/`）。
- **v1**：打通「提问 → 答案逐 token 流式 → 引用 `[n]` 溯源 → 多轮追问」主链路；
- **v2.1**：业务人员**自助上传文档**并管理（上传 → 自动入库 → 对它提问，删除后不再召回）；
- **v2.2**：知识图谱展示（AntV G6，全图按实体类型着色）+ 问答→图谱单向下钻联动（引用「在图谱中查看」高亮引用相关实体及其 1 跳邻域）。

设计约定：浅暖色系（米白 #FAF6F0 / 暖棕 #B36B3B / 琥珀 #E59B3C）；**纯文本渲染 LLM 输出**（`textContent`，禁止 innerHTML 注入）。

## 2. 代码结构（frontend/src/）

| 文件 | 职责 |
|---|---|
| `App.tsx` | 视图切换（问答/文档管理/知识图谱）+ 健康探测 + 文档列表状态/上传/删除/2s 处理中轮询 + toast + 引用→图谱焦点透传（`graphFocus`） |
| `components/TopBar.tsx` | 顶栏：后端状态灯 + 问答/知识图谱/文档管理导航 + 清空会话 |
| `components/ChatView.tsx` / `MessageBubble.tsx` | 消息流：user/assistant 气泡、流式增量渲染、引用角标 `[n]`、错误态 |
| `components/CitationPanel.tsx` | 侧栏引用列表（文件/页/snippet/score）+「在图谱中查看」联动按钮（v2.2） |
| `components/InputBar.tsx` | 输入区：多行 + Enter 提交 + 回答要求模板 + 左侧附件上传按钮（v2.1） |
| `components/DocumentManager.tsx` | 文档管理视图：列表 / 状态 badge（处理中·已入库·失败）/ 软删 / 失败原因 / 返回问答（v2.1） |
| `components/GraphView.tsx` | 知识图谱视图：G6 v5 力导向全图 + 类型着色 + 缩放/拖拽 + 节点详情侧栏 + 引用聚焦（含 StrictMode 安全的 `graphReady` 门控）（v2.2） |
| `components/EmptyState.tsx` | 空态建议芯片 |
| `hooks/useChat.ts` | 会话状态机（pending/streaming/complete/error）+ SSE 订阅 + 多轮 history 自动累积 |
| `lib/sse.ts` | SSE 事件线解析（retrieved→delta→citations→done/error）；连接断开兜底文案 |
| `lib/api.ts` | 后端 API 封装：postAnswer / checkHealth + v2.1 uploadDoc / listDocs / deleteDoc + v2.2 fetchGraph |
| `lib/cite.ts` | 正文 `[n]` 角标正则拆分 |
| `mocks/events.ts` | `VITE_USE_MOCK=true` 离线 mock 流（不依赖后端走查） |

## 3. 与 M7 契约对接

- `GET /health`：启动探测 + 30s 轮询；离线常显「后端离线」且禁发新请求（FR-16）。
- `POST /answer`：非流式 JSON（备用）。
- `GET /answer/stream?q=&history=&response=`：SSE，浏览器原生 `EventSource` 订阅（GET，自动重连）；`history` 由前端多轮累计（后端无会话态）。
- v2.1：`POST /docs`（FormData 文件）、`GET /docs`（列表，含状态）、`DELETE /docs/{doc_id}`（软删）。
- v2.2：`GET /graph` → `{nodes:[{id, entity_type, description, docs[], chunks[]}], edges:[{source, target, relation, weight}], meta}`；软删文档独有实体已被服务端过滤。
- 同源策略：dev 走 Vite `/api` 代理到 8787；生产以 M7 静态挂载同源直连，无 CORS。

## 4. 实测

| 项 | 结果 |
|---|---|
| 构建 | `npm run build`（tsc + vite）**0 错**；v1 dist JS ≈236KB（gzip ≈75KB），v2.2 引入 G6 后 ≈1.65MB（gzip ≈481KB，有 chunk 体积告警 / 后续可代码分割） |
| 提问主链路 | `retrieved → delta×N → citations → done`，delta **真 token 增量**，逐字上屏；`[n]` 与 citations 严格对齐 |
| 多轮 | 追问带历史正常回答；`response_type` 中文模板生效 |
| 健康/断流 | `/health` 离线 → 顶栏「后端离线」；SSE 断开给出可读提示，已生成内容保留可复制 |
| **上传闭环（v2.1）** | `POST /docs` → processing → ready（`doc_id` 回填）；对**新文档**提问命中（引用 file 指向新 doc）；`DELETE /docs/{doc_id}` → 列表消失 + 提问 **0 召回** |
| **图谱渲染（v2.2）** | `GET /graph` ≈244 节点/269 边全图；G6 力导向布局渲染、类型着色、缩放/拖拽/节点点击可交互；点节点出详情侧栏（类型 tag / 描述 / 来源文档 / 关联关系），关联对端可再点跳转 |
| **引用→图谱联动（v2.2）** | 答完点引用卡「在图谱中查看」→ 切换图谱视图并聚焦该引用 chunk/文档相关实体（`035cfc6c09de40ac` 全 doc 命中 51 实体），聚焦栏渲染「已定位引用相关实体 51 个，含 1 跳邻域共 52 个节点」，命中实体高亮（focused 态）+ 视口落位 |
| 回归 | 文档管理落地后，正式文档 JSON 问答 / SSE 流式仍正常（`retrieved`→`delta` 首帧实测通过） |

> v2.2 交互走查经 headless Chrome（CDP）完成：渲染冒烟 / 联动聚焦 / 节点详情均断言通过；截图见走查产物。浏览器内视觉效果（配色/缩放手感）待用户浏览器确认。

## 5. 验收（需求稿对应项）

- [x] 契约冒烟：上传→就绪→提问命中→删除零召回，经 dev `/api` 代理全链路通过（§4）。
- [x] 图谱渲染：`GET /graph` 全图 / G6 力导向 / 类型着色 / 缩放拖拽 / 点节点详情（headless 断言）。
- [x] 问答→图谱联动：引用「在图谱中查看」→ 聚焦引用相关实体子图 + 高亮 + 聚焦栏（headless 断言）。
- [x] `npm run lint` + `npx tsc --noEmit` + `npm run build` 全绿。
- [ ] 浏览器走查（用户侧）：附件上传按钮、文档管理视图切换、处理中/成功 toast、错误态、图谱视觉效果。

## 6. 变更记录

- **2026-09-14 · v1**：问答主链路落地（TopBar / ChatView / MessageBubble / CitationPanel / InputBar / EmptyState / useChat 状态机 / SSE 订阅 / 离线 mock / /api 代理）。
- **2026-09-14 · v2.1**：文档上传 + 文档管理。前端：types `UploadDoc`、api `uploadDoc/listDocs/deleteDoc`、InputBar 附件按钮、DocumentManager 视图、App `activeView` 切换 + 2s 轮询 + toast、TopBar 导航。后端配套在 M7（见 `M7_interact.md` v3），文档需求见 `M8_frontend_req.md` v2.0 §4.7。
- **2026-09-15 · v2.2**：知识图谱。后端 `M7 graph.py`（`GET /graph`，软删过滤 + chunks 输出）；前端：安装 `@antv/g6@5.1`、types `GraphNode/GraphEdge/GraphData`、api `fetchGraph`、`GraphView.tsx`（力导向全图 + 类型着色 + 节点详情侧栏 + 引用聚焦）、TopBar「知识图谱」入口、App 视图切换 `type View='graph'` + `graphFocus`、CitationPanel「在图谱中查看」。需求见 `M8_frontend_req.md` v2.2 §4.8。