# M3 模块记录：索引层（LightRAG 建图 + bge-m3 向量）

> **版本：** v0.3.1
> **状态：** 已落地
> **更新：** 2026-09-17
> **定位：** TextUnit → LightRAG 知识图谱 + bge-m3 向量索引
> **契约：** TextUnit → 图索引（entities/relations） + 向量索引（dense + sparse）
> **上游：** [M2 切分层](M2_chunk.md) | **下游：** [M5 检索层](M5_retrieve.md)
> **依据：** [`ARCHITECTURE.md`](../ARCHITECTURE.md) §2.4 / §3.1
> **运行：** `cd backend && python -m app.m3_index.runner -w data/lightrag_deepseek`
> **变更历史：** 见 [`CHANGELOG.md`](../CHANGELOG.md)

## 1. 定位与职责

把 M2 的 TextUnit（`data/chunks/<doc_id>.jsonl`）送入 **LightRAG**：抽取实体/关系建知识图谱 + bge-m3 dense 向量编码，产物写入工作目录（图 graphml + 实体/关系/文本块 KV + 向量库），供 M5 检索层读取。

- 图谱：LightRAG `ainsert_custom_chunks(full_text, text_chunks, doc_id=...)`，图抽取经 LLM；
- 向量：Xinference bge-m3（dense 1024 维，encoding_format=float）。

## 2. 代码结构（`app/m3_index/`）

| 文件 | 职责 |
|---|---|
| `runner.py` | CLI 入口：`-c` chunks 根、`-w` working_dir（默认 `data/lightrag`）、`--limit`、`--only <doc_id前缀>`；逐文档隔离失败；初始化/收尾 storages |
| `providers.py` | LLM（GLM/DeepSeek 可切换）+ embedding 构造器（见 §4 实测） |

**关键参数**：`addon_params={"language": "zh"}`（中文实体抽取）、`llm_model_kwargs={"temperature": 0.1, "max_tokens": 8000}`、`role_llm_configs={"extract": RoleLLMConfig(max_async=2)}`。

## 3. 复现命令

```bash
# 工作根 = backend/（2026-09-14 前后端重排：app/ data/ inputs/ lightrag/ 等移入 backend/，先 cd 再执行）
cd backend
# 默认工作目录（GLM 库，`backend/data/lightrag`）
/opt/anaconda3/envs/graphrag/bin/python -m app.m3_index.runner
# DeepSeek 隔离库（效果对比用，`backend/data/lightrag_deepseek`）
/opt/anaconda3/envs/graphrag/bin/python -m app.m3_index.runner -w data/lightrag_deepseek
# 单文档（调试）
/opt/anaconda3/envs/graphrag/bin/python -m app.m3_index.runner --only 7497ed75
```

## 4. DeepSeek v4-flash 思考模式修复（关键坑）

**症状**：DeepSeek 抽取反复失败/重试卡住，结果 `content` 为空。

**根因**：DeepSeek v4-flash 是**推理模型**，默认开启思考模式，`reasoning_tokens` 占预算 75–100%；极端情况（`response_format=json_object`）把 `max_tokens` 全部烧在思考上，`content` 为空 → 解析失败 → 重试 → 卡住。

**修复**：关闭思考 `{"thinking": {"type": "disabled"}}`。**OpenAI Python SDK 不接受 `thinking` 直接参数，必须经 `extra_body` 透传**（直接传会报 `AsyncCompletions.create() got an unexpected keyword argument 'thinking'`）：

```python
# providers.py DeepSeek 分支
extra_kwargs: dict = {"extra_body": {"thinking": {"type": "disabled"}}}
```

- `reasoning_effort: "low"` ❌ 无效（仍占 64% reasoning）；
- `reasoning: {effort: "none"}` ❌ 未真正关闭；
- `thinking: {type: "disabled"}` ✅ 彻底关闭（reasoning_tokens 字段消失，全部 token 归 content）。依据 DeepSeek 思考模式文档（api-docs.deepseek.com/zh-cn/guides/thinking_mode）。

## 5. GLM vs DeepSeek 实测对比（2026-09-13）

### 5.1 索引结果概览

| 文档（页/块） | GLM（data/lightrag） | DeepSeek（data/lightrag_deepseek） | 说明 |
|---|---|---|---|
| 会议纪要（1页/4块） | 实体23 / 关系23 | 实体16 / 关系10 | GLM 有噪音实体 |
| 办公用品（1页/1块） | 实体16 / 关系13 | 实体10 / 关系9 | GLM 有噪音实体 |
| 季度销售复盘（2页/5块） | 实体76 / 关系88 | 实体47 / 关系48 | 差异最大 |
| 投诉SOP（2页/15块） | —（曾失败） | 实体110 / 关系121 ✅ | DeepSeek 一次通过 |
| 产品需求（2页/5块） | —（未做） | 实体53 / 关系45 ✅ | DeepSeek 一次通过 |

> 备注：`data/lightrag` 内投诉SOP/产品需求实际是 DeepSeek 之后补跑进去的（此前 GLM 只成功 3 文档），故公平对比仅限前 3 行。效率：5 文档 30 块全量索引约几分钟，零失败。

### 5.2 质量采样结论：DeepSeek 数量少、质量更高

**例会纪要**（GLM 独有 vs DeepSeek 独有实体）：
- GLM 独有：`事项`/`状态`/`负责人`/`已排期`/`待办清单`/`会议概况`/`2026-09-08下午14:00`——**表格列头词 + 时间**，是噪音不是实体；
- DeepSeek 独有：`智能客服系统迭代排期会议`——**真实会议标题实体**。

**季度销售复盘**（差距最大 −29/−40）：
- GLM 独有绝大多数是**「指标名 + 数值」拼接的脏实体**：`ARPU值提升8%`、`收入1,200万元`、`整体毛利率38.5%`、`新增经销商网点42家`、`续费率92%`、`同比增长18.7%`；
- DeepSeek 独有是**干净的纯名称实体**：`毛利率`、`销售收入`、`经销商`、`渠道下沉拓展`、`北京`、`深圳`。

**信息是否丢失？不丢**。深查 graphml 边，DeepSeek 侧 `销售收入8,650万元`/`毛利率38.5%` 等数值**完整体现在关系描述文本（`<data key="d8">`）里**，只是实体名保持了纯名称——数值从「实体名」降级为「关系描述语义」：

```
GLM  ：实体「ARPU值提升8%」  ← 数值搅进实体名（脏）
DeepSeek：实体「ARPU」+ 关系描述写道「…提升8%」 ← 干净，信息仍在
```

### 5.3 结论（决策 2026-09-13）

**后续统一用 DeepSeek**（`.env` 已切）：
- DeepSeek v4-flash 抽取**克制、贴近业务对象**，图结构更规范，信息不丢；
- GLM 免费档抽取**颗粒度粗**——表头词/状态词当实体、指标数值拼实体名，图噪声大；
- 效率：DeepSeek 5 文档 30 块几分钟零失败；GLM ~14s/次且投诉SOP曾失败。
- 保留 `data/lightrag`（GLM 库）作对照样本，正式数据以 `data/lightrag_deepseek` 为准。

## 6. 验收清单（当前）

- [x] 5 文档全量 DeepSeek 索引成功（隔离库）；
- [x] 抽取中文实体正常（language=zh）、实体名贴近业务对象、数值入关系描述；
- [x] 思考模式修复对 DeepSeek v4-flash 生效（reasoning_tokens 消失）；
- [x] 逐文档隔离失败、返回码聚合；`--only`/`--limit` 调试可用。

## 7. 版本

- **v0.3**（2026-09-14）：增量建图 `ainsert_custom_chunks` 被 M7 上传管线复用。
- 变更记录：**逐条版本历史见 `docs/CHANGELOG.md`**（v0.1 GLM 冒烟 → v0.2 DeepSeek 定案+思考模式修复 → v0.3 联动复用）。本文件不再维护历史流水。