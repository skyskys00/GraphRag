# GraphRAG 练手项目

本地私有化的轻量 GraphRAG（知识图谱 + 向量 + RAG）系统。
核心选型见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。
技术栈：LightRAG（图/向量双索引）+ DeepSeek（OpenAI 兼容，建图+生成）+ 本地 bge embedding + **MinerU / Docling（多格式解析）**。

- [p0.md](p0.md) —— P0 极简原型说明（md 直录，验证 DeepSeek+bge+LightRAG 链路）。
- [p1.md](p1.md) —— P1 多格式解析层规划（PDF/docx/md 混合一次入库，已落地）。

## 快速开始

```bash
# 1. 建环境
conda env create -f environment.yml   # 或 conda create -n graphrag python=3.11 -y && pip install lightrag-hku mineru docling

# 2. 配置 DeepSeek key
cp .env.example .env

# 3. 放文档到 inputs/raw/（pdf / docx / pptx / xlsx / html / md / txt 混合均可）

# 4. 解析 → 建索引 → 问答
conda run -n graphrag python scripts/parse.py     # raw/ → parsed/（统一 markdown + 元数据）
conda run -n graphrag python scripts/index.py     # parsed/ → storage/（建图 + 向量化）
conda run -n graphrag python scripts/query.py "FP16 模型量化有什么好处?" --mode mix
```

解析路由：`pdf → MinerU pipeline`，`docx/pptx/xlsx/html/epub → Docling`，`md/txt → 直通拷贝`（详情见 p1.md §3）。

## 目录

```
docs/ARCHITECTURE.md   架构与选型文档（定稿）
p0.md / p1.md          P0 原型说明 / P1 多格式解析规划与落地
inputs/
  raw/                 原始上传物（唯一输入：pdf/docx/md…混合）
  parsed/              解析产物（唯一中间件：<doc_id>.md + .md.json；可整目录删掉重解析）
scripts/
  parse.py             解析调度器（路由/幂等/容错/元数据）
  index.py             建图索引入口（读 parsed/，ids 固定为 doc_id）
  query.py             问答入口（--mode local/global/hybrid/mix）
storage/               LightRAG 运行时存储（生成，不入库）
.env                   DeepSeek API 配置（本地，不入库）
```

## 说明

- 建图/抽取、回答都走 DeepSeek（OpenAI 兼容，默认 `deepseek-v4-flash`，可换 `deepseek-v4-pro`）。
- Embedding 用本地 `sentence-transformers`（默认 `BAAI/bge-small-zh-v1.5` 快速验证，可换 `BAAI/bge-m3`）。
- 环境注意：MinerU 依赖 `transformers<5.0`，当前已锁 `transformers==4.57.6` + `sentence-transformers==4.1.0`；换装注意版本配对。