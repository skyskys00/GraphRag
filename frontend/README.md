# M8 前端：GraphRAG 知识问答

React 18 + TypeScript + Vite。消费 M7 后端 FastAPI/SSE 契约（`/answer`、`/answer/stream`、`/health`）。

## 启动

```bash
# 后端先启（backend/ 下）
cd ../backend && python -m app.m7_interact.runner --port 8787

# 前端（本目录）
npm install --registry=https://registry.npmmirror.com
npm run dev     # http://localhost:5173，已配 /api 代理到 127.0.0.1:8787
npm run build   # 产物 dist/ ，可拷到 backend/app/m7_interact/web/ 替换零依赖页
```

## 离线模式（不连后端）

```bash
VITE_USE_MOCK=true npm run dev
```

走 `src/mocks/events.ts` 的 mock SSE 流，用于无后端走查 UI。

## 目录

```
src/
  types.ts              # M7 契约镜像（Answer / Citation / SseEvent / ChatMessage）
  lib/
    api.ts              # POST /answer + /health
    sse.ts              # EventSource 订阅 /answer/stream → AsyncIterable<SseEvent>
    cite.ts             # 正文 [n] 引用拆分 + HTML 转义
  hooks/
    useChat.ts          # 会话状态机（pending/streaming/complete/error）
  mocks/
    events.ts           # 离线 mock 流
  components/
    TopBar.tsx          # 顶栏 + 状态灯
    ChatView.tsx        # 消息列表容器
    MessageBubble.tsx   # 用户/AI 气泡 + 引用角标 + 错误态
    CitationPanel.tsx   # 右侧引用面板
    InputBar.tsx        # 输入框 + 回答要求模板
    EmptyState.tsx      # 空态建议芯片
```

## 设计风格

浅暖色系（CSS 变量 `:root`，见 `index.css`）：米白底 `#FAF6F0` / 暖棕主色 `#B36B3B` / 琥珀强调 `#E59B3C` / 深咖啡灰正文 `#3D3229`。

## 已知边界（MVP）

- 多轮 history 由前端累计，后端无会话态。
- 引用定位粒度 = 文件 + 页/章节 + 原文片段（snippet），不直接打开原文件。
- 图谱可视化（AntV G6）排二期。
- 纯文本渲染安全红线：LLM 输出一律不经 innerHTML。
