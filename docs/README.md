# 文档导航

> 从这里开始，按需求找到对应的文档。所有文档遵循「分层 + 单一事实源」原则，不复述内容，用链接交叉引用。

## 阅读路径

```
想了解项目是什么        →  ../README.md（项目概览 + 快速开始）
想了解整体架构和选型    →  ARCHITECTURE.md（蓝图）
想了解模块怎么分工      →  FRAMEWORK_NOTES.md §3（模块划分表）
想改某个模块            →  modules/Mx_*.md（接口契约 + 关键决策 + 已知坑）
想看版本历史            →  CHANGELOG.md（唯一权威时序）
想看 API 接口契约       →  启动后端后访问 /swagger
踩了坑想查解法          →  pitfalls/（集中记录，模块文档只留索引）
```

---

## 文档分层速览

### 🏛 蓝图层（方向与选型）

| 文档 | 内容 | 更新频率 |
|------|------|----------|
| **[ARCHITECTURE.md](ARCHITECTURE.md)** | 系统架构蓝图 v2：总体架构 / 选型决策 / 版本锁定 / 分阶段实施路线 | 架构级变化时 |

### 🎯 决策层（模块分工与概念决策）

| 文档 | 内容 | 更新频率 |
|------|------|----------|
| **[FRAMEWORK_NOTES.md](FRAMEWORK_NOTES.md)** | 模块划分（M0–M9）/ 关键技术决策（A1/A2/B3）/ 文档治理规则 | 模块划分或概念决策变化时 |
| **[`modules/RETRIEVAL_OPTIMIZATION.md`](modules/RETRIEVAL_OPTIMIZATION.md)** | 检索优化方法单一事实源（方法→阶段→状态判定总表；效果数据见检索对比总表） | 新方法落版 / 弃用时 |

### 📦 模块层（每个模块一份）

> 统一 4 节结构：**接口契约 / 关键决策 / 已知坑·待办 / 版本**。模板见 [`modules/_TEMPLATE.md`](modules/_TEMPLATE.md)。

| 模块 | 文档 | 一句话职责 |
|------|------|-----------|
| M0 共享层 | [`modules/M0_contracts/`](modules/M0_contracts/) | 数据契约（TextUnit v2 / parse 产物） |
| M1 解析层 | [`modules/M1_parse.md`](modules/M1_parse.md) | MinerU + Docling 双引擎解析 |
| M2 切分层 | [`modules/M2_chunk.md`](modules/M2_chunk.md) | 标题驱动切块 → TextUnit |
| M3 索引层 | [`modules/M3_index.md`](modules/M3_index.md) | LightRAG 建图 + bge-m3 向量编码 |
| M4 存储层 | [`modules/M4_storage.md`](modules/M4_storage.md) | Postgres + pgvector 持久化 |
| M5 检索层 | [`modules/M5_retrieve.md`](modules/M5_retrieve.md) | 三路召回 + RRF + rerank + query 预处理 |
| M6 生成层 | [`modules/M6_generate.md`](modules/M6_generate.md) | 上下文组装 + DeepSeek 生成 + 引用标注 |
| M7 交互层 | [`modules/M7_interact.md`](modules/M7_interact.md) | FastAPI + SSE + 文档管理 + 图谱 API |
| M8 前端 | [`modules/M8_frontend.md`](modules/M8_frontend.md) | React + TS + Vite + AntV G6 |
| M9 评测层 | [`modules/M9_evaluation.md`](modules/M9_evaluation.md) ｜ [`modules/M9_testset.md`](modules/M9_testset.md) | 中文测试集 + LLM 裁判 + ablation 回归 |

### 📋 需求与参考层

| 文档 | 内容 |
|------|------|
| [`modules/M8_frontend_req.md`](modules/M8_frontend_req.md) | 前端需求文档（FR-xx 条目） |
| [`modules/GRAPH_OPTIMIZATION_v4.md`](modules/GRAPH_OPTIMIZATION_v4.md) | 图谱优化 v4 方案（双层图谱 + 关系分类 + 实体筛选） |
| [`modules/MULTIMODAL.md`](modules/MULTIMODAL.md) | 多模态：图片→视觉描述→独立 TextUnit（M0–M8 **全链已实施**，M3/M5 零改动**已实测**） |
| [`PARSER_COMPARISON.md`](PARSER_COMPARISON.md) | MinerU vs Docling 选型复核 |

### 🐛 坑点层（集中存放，模块文档只留索引）

> 所有踩过的坑统一放在 `pitfalls/`，按主题分类。模块文档的「已知坑」小节只留一句话 + 链接，不重复记录。

| 文档 | 内容 | 涉及模块 |
|------|------|----------|
| **[pitfalls/README.md](pitfalls/README.md)** | 坑点索引 + 新增流程 | — |
| [`pitfalls/deepseek-thinking-mode.md`](pitfalls/deepseek-thinking-mode.md) | DeepSeek v4-flash 思考模式导致 content 为空 | M3 / M6 |
| [`pitfalls/postgres-storage-pitfalls.md`](pitfalls/postgres-storage-pitfalls.md) | PG 存储层 7 个坑（向量表后缀 / HNSW / 单写者 等） | M4 / M5 |
| [`pitfalls/bge-m3-xinference.md`](pitfalls/bge-m3-xinference.md) | bge-m3 + Xinference 配置坑（return_sparse / sparse 索引 / reranker） | M3 / M5 |
| [`pitfalls/understand-anything-dashboard.md`](pitfalls/understand-anything-dashboard.md) | UA 图谱 schema 错位 → dashboard 结构视图/导览空白（layers 缺 nodeIds / tour 字段漂移，数据可无损修） | —（第三方工具） |
| [`pitfalls/lightrag-chunk-id-dedup.md`](pitfalls/lightrag-chunk-id-dedup.md) | LightRAG 同文档 content 去重 → `chunk_order_index` 前移，按序号与 M2 对齐错位（改按 content 对齐） | M3 / M5 |

### ⏱ 时序层（变更唯一权威记录）

| 文档 | 内容 |
|------|------|
| **[CHANGELOG.md](CHANGELOG.md)** | 版本变更日志（semver）+ 各版本实测结论 |

---

## API 契约

后端 FastAPI 自动生成 Swagger UI，**代码即契约**，永远不会过时：

- **开发环境**：`http://localhost:8787/swagger`
- 启动命令：`cd backend && python -m app.m7_interact.runner --port 8787`

---

## 文档治理规则（摘要）

完整规则见 [FRAMEWORK_NOTES.md §0](FRAMEWORK_NOTES.md#0-文档治理分层与单一事实源v20-建立)。

- **版本变更只进 CHANGELOG**，同步更新受影响模块的**状态行**版本号；模块文件不维护历史流水。
- **实测记录只进 CHANGELOG**（结论 + 关键数据）；蓝图/决策/模块文档按结果导向瘦身。
- **不复制内容**：跨文档引用一律用链接（如「见 ARCHITECTURE §x」）。
- 一个功能落版 ≈ 改 2 处：CHANGELOG 登记 + 受影响模块状态行；接口/结构真变化才动正文。
