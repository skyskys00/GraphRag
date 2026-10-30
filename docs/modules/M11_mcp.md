# M11 模块规划：MCP 接入层（外部 AI 客户端调用本系统）

> **版本：** v0.1（方案稿）
> **状态：** 规划中 —— 两套传输方案已定，**代码未实施**
> **更新：** 2026-10-30
> **定位：** 把本系统的检索/问答能力暴露成 MCP 工具，供 Claude Desktop / Claude Code / Cursor 等 AI 客户端调用（**不是**面向真人的 Web 产品，那条路是 M7+M8）
> **契约：** MCP 客户端 `tools/call` → `{query, collection_id}` → 检索片段 / 带引用答案
> **上游：** [M5 检索层](M5_retrieve.md) / [M6 生成层](M6_generate.md) / [M7 交互层](M7_interact.md)（复用 `bootstrap.get_deps` 与现成工具函数） | **下游：** 无（旁路接入面，不进入问答主链路）
> **依据：** MCP 规范（`stdio` / `streamable-http` 两种传输）｜ [python-sdk 文档](https://github.com/modelcontextprotocol/python-sdk)
> **运行：** 待实施
> **变更历史：** 见 [`CHANGELOG.md`](../CHANGELOG.md)

## 1. 接口契约

### 1.1 架构与复用点

```
MCP 客户端（Claude / Cursor）
  └── stdio 子进程 或 HTTP 连接 ──→ app/m11_mcp/server.py
                                     └── bootstrap.get_deps(collection_id)
                                        └── M5 retrieve / M6 rag_answer / documents.list_docs
```

**业务逻辑零新增。** `m10_agent/agent.py` 里已有两个可直接复用的工厂函数：

| 现有函数 | 复用于 | 返回结构 |
|---|---|---|
| `_retrieve_tool(deps)` | `retrieve` 工具 | `{query, n_results, results[{rank, doc_id, content, score}], citations[{file_path, title_path, page_range, text_unit_id}]}` |
| `_rag_answer_tool(deps)` | `answer` 工具 | `{text, citations, status}` |
| `documents.list_docs(deps)` | `list_docs` 工具 | 文档清单 + 状态 |
| `collections.list_collections(proj)` | `list_collections` 工具 | `[{collection_id, name, created_at}]` |

M11 只做三件事：**参数签名包装、deps 生命周期、传输适配**。

### 1.2 工具清单（两方案共用）

| MCP 工具 | 参数 | 返回 | 说明 |
|---|---|---|---|
| `list_collections` | — | 库列表 | 让客户端知道有哪些知识库 |
| `list_docs` | `collection_id="default"` | `[{doc_id, file_path, status, chunks}]` | 库内文档清单 |
| `retrieve` | `query`, `top_k=5`, `collection_id="default"` | 片段 + 来源（文件/章节/页码） | 只读检索，不带生成 |
| `answer` | `query`, `collection_id="default"` | 答案 + `[n]` 引用 | M6 直连一条龙 |

**不做 `agent` 工具**（M10 完整决策链）：它内部会自己多轮选工具，与外部客户端的工具编排职责重叠，且慢、贵。需要时再单独讨论。

**只读。** 上传 / 删除 / 建库等写操作不暴露，走 M8 界面。

---

## 2. 方案 A：stdio（本地进程）

### 2.1 进程模型

MCP 客户端按配置启动一个 Python 子进程，双方通过 stdin/stdout 传 JSON-RPC 行。**不占端口、不需要鉴权**——进程由本地用户自己拉起，操作系统权限就是安全边界。

生命周期：客户端打开会话时启动一次，会话期间常驻，关客户端即退出。

### 2.2 实现要点

```
backend/app/m11_mcp/
├── __init__.py
└── server.py          # ~120 行
```

- SDK：`mcp` 包（v2 时代入口是 `from mcp.server import MCPServer`；v1 的 `FastMCP` 是旧名）。
- deps 初始化放在 `MCPServer` 的 `lifespan` 里：`await get_deps()` 预热默认库，退出时 `await dispose()`。
- 每次工具调用按 `collection_id` 取 deps——`bootstrap.get_deps()` 内部有 `_deps_map` 按库缓存，切库不重复初始化。
- 入口：`mcp.run(transport="stdio")`。

### 2.3 客户端配置

```json
{
  "mcpServers": {
    "graphrag": {
      "command": "/opt/anaconda3/envs/graphrag/bin/python",
      "args": ["-m", "app.m11_mcp.server"],
      "cwd": "<项目>/backend"
    }
  }
}
```

### 2.4 取舍

| 项 | 情况 |
|---|---|
| 启动成本 | 每次拉起要初始化 LightRAG + PG + embedding，**约 5~10 秒**、内存数百 MB。会话内复用，可接受 |
| 并发 | 单进程串行处理请求；MCP 客户端本身也很少并发调用 |
| 部署 | 零部署，客户端配置即安装 |
| 适用 | 个人本机用、给同事的本机配一份 |

---

## 3. 方案 B：HTTP 传输

### 3.1 先纠正一个常见误解：SSE 是旧传输

MCP 有两代 HTTP 传输，**MCP 协议维护方（modelcontextprotocol）在协议规范与官方 Python SDK 文档中一致建议：新实现不要再基于 SSE**。

| 传输 | 规范版本 | 形态 | 官方态度 |
|---|---|---|---|
| `sse` | 2024-11-05 | 两个端点：`GET /sse` 建长连接收消息，`POST /messages?session_id=…` 发消息 | **旧传输**。SDK 传输选择表里「何时用」一列写的是 "You don't."；警告块原文：SSE was superseded by Streamable HTTP in the 2025-03-26 protocol revision … **Don't build anything new on it.** |
| `streamable-http` | 2025-03-26 | 单端点 `/mcp`：POST 发 JSON-RPC，响应按需返回 JSON 或 SSE 流 | **推荐**，SDK 文档的「何时用」一列写的是 "Anything you deploy." |

> 出处：[`modelcontextprotocol/python-sdk`](https://github.com/modelcontextprotocol/python-sdk) `docs/run/index.md`（传输选择表 + 警告块）；协议规范见 modelcontextprotocol.io/specification 的 2025-03-26 修订版。SDK 仍保留 `mcp.run(transport="sse")`，仅为兼容未迁移的客户端。

差别在长连接管理：SSE 传输要求连接全程不断，断线即会话失效，反向代理和负载均衡都容易出问题；Streamable HTTP 允许每次请求独立，服务端可在同一端点上按需流式返回。**本方案主推 `streamable-http`，`sse` 只作为兼容旧客户端的备选**（配置项切换，代码近乎相同）。

### 3.2 部署形态：挂载 vs 独立进程

| | 挂载到现有 FastAPI（8787，路径 `/mcp`） | 独立进程（8788） |
|---|---|---|
| deps | **与主 API 共享** `_deps_map`，内存只一份 | 再初始化一份 LightRAG + reranker，**内存约翻倍** |
| 进程管理 | 复用 `dev.sh` / 现有健康检查 | 需新增启停脚本与日志 |
| 隔离性 | MCP 异常可能影响主 API（只读工具，风险可控） | 崩溃互不影响 |
| 部署改动 | 需要在 M7 `lifespan` 里多 enter 一个 session manager | 新增一个入口文件 |

**推荐挂载**：deps 初始化太重（embedding + cross-encoder 模型），独立进程省下的隔离性不值那份内存。

挂载的技术形态（SDK v2）：

```python
# 模块级构建（session_manager 只在 app 工厂调用后才存在）
mcp_app = server.streamable_http_app()

# 宿主 FastAPI 的 lifespan 里进入 session manager
async with server.session_manager.run():
    ...
# 路由：app.mount("/mcp", mcp_app)

```

**坑（必读）**：被 mount 的子应用自己 `lifespan` **不会执行**，session manager 必须由宿主 lifespan 进入；漏掉时启动正常但每个请求报 `RuntimeError: Task group is not initialized`。

### 3.3 安全设计

**现状：M7 API 完全没有鉴权**（本地单用户项目）。一旦开 HTTP 就是新的暴露面，必须补：

1. **默认只绑 `127.0.0.1`** —— 与本机客户端可用，外网不可达。
2. **Bearer token 校验中间件**，只作用于 `/mcp` 路径，不影响现有接口。token 走环境变量 `MCP_AUTH_TOKEN`，未配置则拒绝启动 HTTP 模式（避免「忘了设」变成裸奔）。
3. **对外暴露必须经反向代理加 TLS**，且 token 不能出现在 URL 里。
4. **请求体积上限**（SDK 有 `max_request_body_size` 参数）+ 单次 `top_k` 上限，防止大查询拖垮进程。

### 3.4 实现要点

在方案 A 的 `server.py` 基础上增加：

| 改动 | 内容 |
|---|---|
| `run()` 分支 | `--http` 或环境变量切换：`mcp.run(transport="streamable-http", port=8788)` |
| 挂载模式 | 导出 `build_asgi_app()` 给 `app/m7_interact/api.py` 挂到 `/mcp` |
| 鉴权 | 包一层 ASGI middleware 校验 `Authorization: Bearer <token>` |
| 超时 | `answer` 工具走 LLM 生成，单个请求可能 20~60 秒，需放宽网关/反代超时 |
| 会话 | MCP 的 session 与 `collection_id` 解耦——session 只保连接，库由每次调用参数决定 |

### 3.5 客户端配置

```json
{
  "mcpServers": {
    "graphrag": {
      "url": "http://127.0.0.1:8787/mcp",
      "headers": { "Authorization": "Bearer <MCP_AUTH_TOKEN>" }
    }
  }
}
```

### 3.6 取舍

| 项 | 情况 |
|---|---|
| 启动成本 | 不需要新进程（挂载模式）；客户端连接即用 |
| 多客户端 | 天然支持多人/多设备，但**当前系统是单用户假设**（会话/集合无租户隔离），多人用要另做权限模型 |
| 安全成本 | token 管理、TLS、反代都要自己加 |
| 适用 | 想给局域网内几台设备用、或接远程 AI 客户端 |

---

## 4. 关键决策

- **只读、四个工具、不做 agent 工具**：MCP 是「给 AI 客户端一个知识库」，不是把整个系统搬出去；write 操作和完整 agent 编排留在 M7/M8。
- **业务逻辑零新增**：复用 `_retrieve_tool` / `_rag_answer_tool`，M11 只做协议适配——避免出现两套检索口径。
- **HTTP 主推 `streamable-http` 而非 `sse`**：SSE 是 2024-11 的旧传输，长连接断线即会话失效、反代难做；Streamable HTTP 是同端点的继任者，SDK 文档明确推荐。
- **HTTP 挂载到主 API 而非独立进程**：deps 初始化包含 embedding 与 cross-encoder 模型，两份进程内存翻倍，隔离收益不划算。
- **stdio 优先实施**：零部署、零鉴权、个人场景足够；HTTP 等有跨设备需求再做。

## 5. 已知坑与待办

- **mount 的子应用 lifespan 不执行** → session manager 必须由宿主 lifespan 进入，否则启动正常但请求全报 `Task group is not initialized`。
- **SDK v1/v2 命名差异**：v1 是 `from mcp.server.fastmcp import FastMCP`，v2 是 `from mcp.server import MCPServer`；传输参数从构造函数挪到了 `run()` / `app` 工厂，`mount_path` 已移除。按 POC 时实际装的版本写。
- **stdio 日志污染**：stdio 传输下 stdout 是协议通道，任何 `print()` 都会破坏 JSON-RPC。日志必须走 stderr 或文件（本项目现有代码有大量 `print(flush=True)`，M11 需统一改）。
- **待办**：deps 初始化 slow-start 预热（可选）；多租户权限模型（当前不做）；`resources` 暴露文档原文（当前不做）。

## 6. 验证方法

**方案 A（stdio）**：
1. `python -m app.m11_mcp.server` 手动启动，确认不报错且 stderr 有就绪日志。
2. 客户端连上后问「列出有哪些知识库」「行政库里办公用品分几类」。
3. 换 `collection_id` 再问一次，验证多库切换不重复初始化。

**方案 B（HTTP）**：
1. 手动发 JSON-RPC：`curl -s -X POST http://127.0.0.1:8787/mcp -H 'Authorization: Bearer <token>' -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"initialize",...}'`。
2. 不带 token 应返回 401；带错误 token 同样 401。
3. 连续两次 `answer` 调用，确认 deps 复用（第二次无初始化日志）。

## 7. 里程碑

| 期 | 内容 | 验证 |
|---|---|---|
| M11.1 | stdio server + 四个工具 | 客户端连上可问答，切库正常 |
| M11.2 | `streamable-http` 独立进程模式 + token 鉴权 | curl 走通 initialize / tools/call，无 token 401 |
| M11.3（可选） | 挂载到 M7 `/mcp`，共享 deps | 与主 API 同进程，内存不翻倍 |

> 先做 M11.1，M11.2/11.3 按是否有跨设备需求再启动。
