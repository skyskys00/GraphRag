# M8 模块记录：正式问答前端（React + TypeScript + Vite）

> **版本：** v5.3
> **状态：** 已落地
> **更新：** 2026-09-22
> **定位：** 四视图（问答 / 文档管理 / 知识图谱 / 仪表盘）+ 多知识库切换 + 多会话
> **契约：** 消费 M7 REST + SSE 接口（见 Swagger UI: http://localhost:8787/swagger）
> **上游：** [M7 交互层](M7_interact.md) | **下游：** 浏览器用户
> **依据：** [`M8_frontend_req.md`](M8_frontend_req.md) ｜ [`ARCHITECTURE.md`](../ARCHITECTURE.md) §2.9
> **运行：** `cd frontend && npm run dev` → http://localhost:5173
> **变更历史：** 见 [`CHANGELOG.md`](../CHANGELOG.md)

## 1. 定位

打开浏览器即用的正式 Web 问答前端（React 18 + TypeScript + Vite，工程 `frontend/`）。
- **v1**：打通「提问 → 答案逐 token 流式 → 引用 `[n]` 溯源 → 多轮追问」主链路；
- **v2.1**：业务人员**自助上传文档**并管理（上传 → 自动入库 → 对它提问，删除后不再召回）；
- **v2.2**：知识图谱展示（AntV G6，全图按实体类型着色）+ 问答→图谱单向下钻联动（引用「在图谱中查看」高亮引用相关实体及其 1 跳邻域）；
- **v5.0**：多会话（后端持久化，不再 localStorage 存消息）——侧边栏会话区（列表 / 新对话 / 重命名 / 删除）+ TopBar「新对话」按钮 + useChat 多会话重写 + 懒创建（首问自动建会话）+ 首问自动命名（后端负责）。
- **v5.1**：批量上传（文件选择器 `multiple`，多选后前端循环调 `POST /docs`）+ 会话流式锁定（回答生成中真实 `streaming` 锁，禁会话切换 / 新建 / 删除）。
- **v5.2**：生成期间可切换/新建会话（Bug2 方向反向，`streamingMap` 每会话独立状态 + 侧边栏呼吸小圆点指示 + 输入框当前会话仍禁用）+ 引用面板去掉固定 5 条上限（后端相对阈值过滤后动态展示全部）。
- **v5.3**：文档预览表格渲染——`PreviewUnit.html` 随上游 M7 v10.3 透传，表格单元（`block_type=table`）以真实 HTML 表格展示（`dangerouslySetInnerHTML`，来源为自身解析器结构化输出），无 html 时回退纯文本。

设计约定：浅暖色系（米白 #FAF6F0 / 暖棕 #B36B3B / 琥珀 #E59B3C）；**纯文本渲染 LLM 输出**（`textContent`，禁止 innerHTML 注入）。

## 2. 代码结构（frontend/src/）

| 文件 | 职责 |
|---|---|
| `App.tsx` | 视图切换（问答/文档管理/知识图谱）+ 健康探测 + 文档列表状态/上传/删除/2s 处理中轮询 + toast + 右栏 tab（引用/预览）+ 引用→图谱焦点透传（`graphFocus`） |
| `components/TopBar.tsx` | 顶栏：后端状态灯 + 新对话（v5.0 改「清空会话」→「新对话」） |
| `components/Sidebar.tsx` | 左侧可折叠导航：知识库切换器（v3.0）+ 会话列表区（v5.0，新对话/重命名/删除）+ 导航项数组（仪表盘/问答/文档管理/图谱，扩展只需 push 一项） |
| `components/ChatView.tsx` / `MessageBubble.tsx` | 消息流：user/assistant 气泡、流式增量渲染、引用角标 `[n]`、错误态 |
| `components/CitationPanel.tsx` | 右栏引用列表：按置信度排序限前 5 条（score 降序）+ 每条「在图谱中查看」「原文档 #」跳预览（v2.2+v2.3） |
| `components/DocumentPreview.tsx` | 文档预览：读 `GET /docs/{id}/preview`，按 `text_unit_id` 定位，最高置信度片段柔和高亮（v2.3） |
| `components/InputBar.tsx` | 输入区：多行 + Enter 提交 + 回答要求模板 + 左侧附件上传按钮（v2.1；v2.3 输入区向上 5px） |
| `components/DocumentManager.tsx` | 文档管理视图：列表 / 状态 badge（处理中·已入库·失败）/ 预览 / 软删 / 失败原因 / 返回问答（v2.1+v2.3 预览入口） |
| `components/GraphView.tsx` | 知识图谱视图：G6 v5 力导向全图 + 类型着色 + 缩放/拖拽 + 节点详情侧栏 + 引用聚焦 + 按文档过滤下拉（StrictMode 安全 `graphReady` 门控；v2.2+v2.3） |
| `components/EmptyState.tsx` | 空态建议芯片 |
| `hooks/useChat.ts` | 会话状态机（pending/streaming/complete/error）+ SSE 订阅 + 多会话管理（v5.0 重写：会话列表/选中/新建/重命名/删除，懒创建，后端持久化） |
| `lib/sse.ts` | SSE 事件线解析（retrieved→delta→citations→done/error）；连接断开兜底文案 |
| `lib/api.ts` | 后端 API 封装：postAnswer / checkHealth + v2.1 uploadDoc / listDocs / deleteDoc + v2.2 fetchGraph + v2.3 fetchDocPreview / fetchGraph(docId) + v3.0 collections/stats + v5.0 conversations |
| `lib/cite.ts` | 正文 `[n]` 角标正则拆分 |
| `mocks/events.ts` | `VITE_USE_MOCK=true` 离线 mock 流（不依赖后端走查） |

## 3. 与 M7 契约对接

- `GET /health`：启动探测 + 30s 轮询；离线常显「后端离线」且禁发新请求（FR-16）。
- `POST /answer`：非流式 JSON（备用）。
- `GET /answer/stream?q=&history=&response=`：SSE，浏览器原生 `EventSource` 订阅（GET，自动重连）；`history` 由前端多轮累计（后端无会话态）。
- v2.1：`POST /docs`（FormData 文件）、`GET /docs`（列表，含状态）、`DELETE /docs/{doc_id}`（软删）。
- v2.2：`GET /graph` → `{nodes:[{id, entity_type, description, docs[], chunks[]}], edges:[{source, target, relation, weight}], meta}`；软删文档独有实体已被服务端过滤。
- v2.3：`GET /docs/{doc_id}/preview`（该文档 M2 TextUnit 全量，供预览定位）；`GET /graph?doc_id=`（按文档过滤子图）；引用置信度排序修复（后端 `cite.py parse_citations` 末尾排序全部生效，前端再 sort+slice 双保险）。
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
| **侧边栏（v2.3）** | 左侧可折叠导航（问答/文档管理/知识图谱），折叠态仅图标；导航项数组模式 |
| **文档预览（v2.3）** | `GET /docs/{id}/preview` → 右栏 tab「预览」：按 `text_unit_id` 定位片段，最高置信度片段柔和浅杏高亮（非亮非黄）；引用「原文档 #」一键跳转 |
| **引用排序 top5（v2.3）** | 引用来源按置信度降序仅显示前 5 条（后端 `parse_citations` 排序 + 前端 `sort().slice(0,5)` 双保险） |
| **图谱按文档过滤（v2.3）** | 图谱顶部下拉筛选文档 → `GET /graph?doc_id=` 子图重新渲染；切换文档维度后 focus 状态清理 |
| 回归 | 文档管理落地后，正式文档 JSON 问答 / SSE 流式仍正常（`retrieved`→`delta` 首帧实测通过） |

> v2.2 交互走查经 headless Chrome（CDP）完成：渲染冒烟 / 联动聚焦 / 节点详情均断言通过；截图见走查产物。浏览器内视觉效果（配色/缩放手感）待用户浏览器确认。

## 5. 验收（需求稿对应项）

- [x] 契约冒烟：上传→就绪→提问命中→删除零召回，经 dev `/api` 代理全链路通过（§4）。
- [x] 图谱渲染：`GET /graph` 全图 / G6 力导向 / 类型着色 / 缩放拖拽 / 点节点详情（headless 断言）。
- [x] 问答→图谱联动：引用「在图谱中查看」→ 聚焦引用相关实体子图 + 高亮 + 聚焦栏（headless 断言）。
- [x] v2.3：`npm run lint` + `npx tsc --noEmit` + `npm run build` 全绿；侧边栏折叠/预览定位/引用排序 top5/图谱文档过滤走查通过。
- [ ] 浏览器走查（用户侧）：侧边栏折叠手感、预览高亮视觉、图谱渲染视觉效果。

## 6. 版本

- **v2.3**（2026-09-15）：侧边栏 + 右栏 tab + 文档预览 + 引用排序 top5 + 图谱文档过滤。
- 变更记录：**逐条版本历史见 `docs/CHANGELOG.md`**（v1 主链路 → v2.1 文档管理 → v2.2 图谱 → v2.3 侧边栏/预览/排序/过滤）。本文件不再维护历史流水。