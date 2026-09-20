# GraphRAG

面向中文场景的**图增强检索（GraphRAG）**系统 —— 基于 LightRAG 内核，MinerU + Docling 解析，AntV G6 图谱可视化。

前后端分离：FastAPI 后端 + React + TypeScript 前端。

## 快速开始

```bash
# 1. 后端（conda env: graphrag）
cd backend
conda activate graphrag
python -m app.m7_interact.runner --port 8787
# API 文档: http://localhost:8787/swagger

# 2. 前端
cd frontend
npm install
npm run dev
# 访问: http://localhost:5173
```

## 功能

- **文档问答**：SSE 流式输出，答案带 `[n]` 引用标注，可溯源到原文片段
- **知识图谱**：双层图谱（文档级 + 实体级），力导向布局，支持话题聚类 / 关系分类 / 实体筛选
- **文档管理**：上传自动入库（解析→切块→建图），软删，多知识库隔离
- **仪表盘**：库概览统计 + 最近问答 + 文档概览

## 架构

```
原始文档 → [M1 解析] → [M2 切块] → [M3 索引] → [M4 存储]
                                            ↓
用户问题 → [M5 检索] → [M6 生成] → [M7 交互层] → [M8 前端]
```

- 索引管线（离线）：MinerU/Docling → TextUnit → LightRAG 图索引 + bge-m3 向量
- 查询管线（在线）：三路召回（图 + 向量 + sparse）→ RRF → rerank → DeepSeek 生成 + 引用标注

详细架构见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)；模块划分见 [`docs/FRAMEWORK_NOTES.md`](docs/FRAMEWORK_NOTES.md)。

## 项目结构

```
GraphRAG/
├── backend/            # Python 后端（FastAPI + LightRAG）
│   ├── app/            # M1–M7 业务代码
│   └── environment.yml # conda 环境
├── frontend/           # React + TypeScript + Vite 前端
│   └── src/components/ # 11 个组件 + hooks + lib
├── docs/               # 架构 / 模块 / 需求 / 变更日志
└── dev.sh              # 一体化开发启动脚本
```

## 文档导航

| 你想找什么 | 看这里 |
|-----------|--------|
| 架构总览 / 选型决策 | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) |
| 模块划分 / 文档治理规则 | [`docs/FRAMEWORK_NOTES.md`](docs/FRAMEWORK_NOTES.md) |
| 各模块接口契约 / 关键决策 / 已知坑 | [`docs/modules/`](docs/modules/) |
| 版本历史 / 变更记录 | [`docs/CHANGELOG.md`](docs/CHANGELOG.md) |
| 前端需求文档 | [`docs/modules/M8_frontend_req.md`](docs/modules/M8_frontend_req.md) |
| 图谱优化方案 | [`docs/modules/GRAPH_OPTIMIZATION_v4.md`](docs/modules/GRAPH_OPTIMIZATION_v4.md) |
| API 契约 | 启动后端后访问 `http://localhost:8787/swagger` |

## 技术栈

| 层 | 技术 |
|----|------|
| 解析 | MinerU 3.x（PDF 主力）+ Docling（多格式补充） |
| 索引 | LightRAG + bge-m3（dense + sparse，经 Xinference） |
| 存储 | Postgres + pgvector |
| 生成 | DeepSeek V4 Flash（关思考模式） |
| 后端 | FastAPI + SSE |
| 前端 | React 19 + TypeScript + Vite + AntV G6 5 |
