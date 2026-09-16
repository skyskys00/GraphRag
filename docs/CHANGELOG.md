# CHANGELOG（项目级变更日志）

> 定位：**版本变更时序的唯一权威记录**。以后改任何模块/前端/后端，先在此登记版本与要点。
> 维护规则见 §「版本约定与更新规则」｜ 文档分层与单一事实源见 `docs/FRAMEWORK_NOTES.md` §0 ｜ 模块记录结构见 `docs/modules/_TEMPLATE.md`

## 版本约定与更新规则

**Semver 三级**（2026-09-16 起沿用的规则；此前各模块用各自编号，历史不改）：

| 级别 | 含义 | 例子 |
|---|---|---|
| `vX`（主） | **阶段级**变化：架构方向 / 模块体系调整 / 换代式能力切换 | M8 v2 = 从「问答工具」升为产品化界面 |
| `vX.Y`（次） | **功能迭代**：新增可感知能力 | M8 v2.1 文档管理 / v2.2 图谱 / v2.3 侧边栏+预览 |
| `vX.Y.Z`（补丁） | **修复 / 微小调整 / 文档勘误**，不改变能力形态 | 引用排序 bug 修复、样式微调 |

**更新规则（写给未来的自己）**：

1. **每次改动先在此登记**（哪怕一行），并顺带在受影响模块文件**状态行**同步版本号。
2. 模块文件（`docs/modules/Mx_*.md`）**不再维护「变更记录」章**——历史已并入本文 v1.0 条目；需回溯时以本文为准。
3. 纯文档勘误（错字/链接/表述）→ 补丁级，直接在 `vX.Y.Z` 条目追加一行即可，不必每次都升版本号。
4. 版本与 git 的关系：正式提交时才打 tag（阶段级 vX / 功能级 vX.Y 可 tag；补丁级可只记不 tag）。
5. **实测记录统一登记在本文对应版本条目**（结论 + 关键数据，不做过程叙述 / 表格全量）；模块记录只留一句话结论 + 指向本文。

---

## [v2.1] 2026-09-16 —— 功能修复：预置问题 / 输入框位置 / 预览 404 / 检索白名单

**影响模块**：M5 检索（v1.6.1）、M7 交互（v5.1）、M8 前端（v2.3.1）

- **M8 v2.3.1 · 前端**：
  - 移除空态页 4 个预置问题（旧版五文档语境已不适用），描述文案改为通用「支持 PDF/DOCX/MD/PPTX/TXT 等格式文档上传，答案带引用，可溯源到原文」。
  - 输入框视觉上移：`.app` 加 `padding-bottom: 28px` + `.input-area` 底部 padding 8→28px，解决「输入框贴视口底部、看着别扭」（此前两次调整只改了 input-area 内部 padding-top，未触发布局位置）。
- **M7 v5.1 · 后端**：
  - 修复文档预览 404：`GET /docs/{doc_id}/preview` 不再依赖 `documents.json` 注册表判断文档是否存在，改为直接以 `data/chunks/<doc_id>.jsonl` 存在为准；filename 优先取注册表（上传时的原始名），无记录则从第一个 chunk 的 `file_path` 推断。（根因：初始离线建库的 5 篇文档不在注册表中，旧实现直接返回 404。）
  - 检索从「黑名单（excluded_docs）」升级为「**白名单 + 黑名单**」双过滤：新增 `allowed_docs`（已上传且未删除的文档集合），M5 retrieve / M6 answer / M7 respond / graph 导出全链路透传。语义：`allowed_docs` 有值时只从白名单文档召回；`excluded_docs` 仍保留用于软删即时生效。注册表为空时 `allowed_docs = None`（不限定，兼容纯离线建库场景）。
- **M5 v1.6 · 检索**：`retrieve()` 新增 `allowed_docs` 参数，在 rerank 后结果过滤阶段与 `exclude_docs` 叠加生效（取「在白名单 ∩ 不在黑名单」的交集）。

## [v2.0] 2026-09-16 —— 工程维护：文档体系重做

**模块 `docs`（文档体系）**：

- 建立本文 `CHANGELOG.md`：版本变更统一在此登记，消除「一个改动要改 5 处文档」的维护痛点。
- 新建 `docs/modules/_TEMPLATE.md`：模块记录统一为 4 节结构（接口契约 / 关键决策 / 已知坑·待办 / 版本）。
- 分层与单一事实源（写入 FRAMEWORK_NOTES §0）：五种文档各管一件事，用链接交叉引用，不复制内容。
- 模块记录瘦身：各 Mx 文件「变更记录」章改为指向本文；M5 规划附记标注为已落地复盘；M7/M8 补齐 v2.3 / v5 的落地记录缺口（侧边栏 / 预览 / 引用排序 top5 / 图谱按文档过滤）。
- 大幅降低后续每版本维护成本：以后一个功能落版 = 只改受影响模块的**状态行** + 在此登记一行。
- 该轮同时做「过程/实测记录归档」：M3 DeepSeek 抽取对比、M5 检索四题与 A/B 实测、ARCHITECTURE §8 实测附录、FRAMEWORK_NOTES §5 概念澄清（过程性内容）按时间并入本文 v1.0 各模块条目；蓝图（ARCHITECTURE）/ 决策（FRAMEWORK_NOTES）/ 模块（`modules/Mx_*.md`）按**结果导向**瘦身，删过程推演、留结论与指向本文的指引。

## [v1.0] 2026-09-15 —— M0–M8 全链路重构（历史聚合）

> v1.0 之前为架构调研与规划（2026-09-03 ~ 09-12，git log `db65a6a` ~ 前序提交），未在此逐条重列。
> 子条目按时间整理，**版本结论与实测数据以本文为准**；实现细节与接口契约见对应模块记录。

- **M1 解析层**（v0.1→v0.3）：骨架首跑 8/8 → A 项（docx 表格渲染 HTML、docx anchor 降级定案）→ M7 上传管线库函数 `process_one` 复用。
- **M2 切分层**（v1）：标题层级切分 + 表格整块 + 聚合字段，5 文档 30 TextUnit 闭环（与索引库对账一致）。
- **M3 索引层**（v0.2→v0.3，2026-09-13）：**DeepSeek v4-flash 统一定案** + 思考模式修复 + v0.3 `ainsert_custom_chunks` 增量建图被 M7 复用。
  - **关键坑（A 级）**：DeepSeek v4-flash 是**推理模型**，默认思考模式占 `reasoning_tokens` 75–100%，极端时烧光 `max_tokens` → `content=""`、抽取失败重试卡死。修复：`extra_body={"thinking":{"type":"disabled"}}` —— **OpenAI SDK 不接受 `thinking` 直接参数**，只能走 `extra_body`；`reasoning_effort=low` / `reasoning={effort:"none"}` 均无效。
  - **GLM vs DeepSeek 抽取实测**（公平 3 文档，实体/关系）：会议纪要 23/23 vs 16/10、办公用品 16/13 vs 10/9、季度复盘 76/88 vs 47/48。GLM 把表头词（`事项`/`负责人`）和「指标+数值」拼接（`ARPU值提升8%`/`收入1,200万元`）当实体、图噪声大；DeepSeek 实体净（贴近业务对象）、数值沉淀入关系描述**信息不丢**；投诉SOP/产品需求仅 DeepSeek 一次通过 → **定 DeepSeek 唯一后端**（`data/lightrag_deepseek` 正式库；`data/lightrag` GLM 留对照）。
- **M4 存储层**（v1，2026-09-13）：Postgres+pgvector 一库通吃（PGTableGraphStorage 纯表，非 AGE），`.env` 配置切换、业务代码零改动；数据对账（chunks 30 / docs 5）+ 四模式检索回归 + 持久化验证。
- **M5 检索层**（v0.1→v1→v1.5→v1.6，2026-09-13~14）：三路召回（图 + 向量 + bge-m3 sparse 关键词）→ RRF(k=60) → bge-reranker-v2-m3 精排 → PG `full_doc_id` 溯源 → query 预处理 A 同义词扩展 + B 专名加权 → 软删过滤 + sparse 联动重建。
  - **v0.1 冒烟**（09-13）：四题 × 四模式（local/global/mix/naive）纯召回秒级跑通；发现 chunk `file_path` 恒 `unknown_source`，真实来源在 `full_doc_id`；LightRAG 默认 rerank 空转。
  - **v1 实测四题**（智能客服/季度销售/投诉流程/迭代会议决定）：高相关片段全进 top8，正相关 score 0.6~0.9、负相关长尾 0.001~0.05，**rerank 区分度显著**；keyword 路 30 chunks 全参与打分，对图/向量双路都漏掉的术语型查询兜底。
  - **v1.5 A/B 实测**（6 道缩写/口语 query）：缩写**有效**——「PRD V2.1」经词典扩展为「产品需求文档」，挤出投诉 SOP 噪音、更聚焦 PRD；标准缩写**中性**——「客服」「华东」「一级投诉」bge-m3 本身已召回，预处理不劣化（仅顺序微调）；纯口语**无提升**——「定了啥事儿」「管啥的」词典/模糊匹配不到，属 **C 查询改写（LLM）** 适用域，暂不做；延迟 +<200ms。
- **M6 生成层**（v1→v1.1）：上下文组装（token 预算）+ DeepSeek flash 生成（关思考）+ TextUnit 级引用标注 + sidecar 溯源 → 软删 `exclude_docs` 透传。
- **M7 交互层**（v1→v5）：FastAPI 壳 + SSE 事件线 → v2 真 token 流式（`build_query_stream_func`）→ v3 文档管理（上传 / 软删 / `POST GET DELETE /docs`）→ v4 图谱导出 `GET /graph`（软删幽灵实体过滤）→ v5 文档预览 `GET /docs/{id}/preview` + 图谱按文档过滤 `?doc_id=` + 引用置信度排序修复。
  - **v3 上传闭环实测**：上传 → processing→ready → 对新文档提问命中 → 删除 → 列表移除 + 检索零召回。
- **M8 前端**（v1→v2.1→v2.2→v2.3）：SSE 问答主链路（逐字流式 + `[n]` 溯源 + 多轮）→ v2.1 文档上传/文档管理 → v2.2 AntV G6 知识图谱 + 引用→图谱单向下钻联动（实测聚焦「已定位引用相关实体 51 个，含 1 跳邻域共 52 个节点」）→ v2.3 左侧可折叠侧边栏 + 右栏 tab（引用/预览）+ 文档预览（最高置信度柔和高亮）+ 引用排序 top5 + 图谱按文档过滤下拉。lint / tsc / build 全绿。
  - **实现期坑（其余见 M8_frontend.md §4）**：引用 `text_unit_id`（`{doc_id}-chunk-{i}`）≠ 图谱节点 `chunks[]`（PG `chunk-{md5}`），反查须同时试 chunk 与 fullDocId（后者可靠）；dev StrictMode 双挂载会废掉一次性 pending ref → 用「build effect 存初始 render promise + `graphReady` 门控 focus effect」，`applyFocus` 里**不要二次 `await graph.render()`**（二次 render 长时间不 resolve、聚焦卡死）；headless Chrome 走查前须 `Network.setCacheDisabled`，防旧 vite 模块缓存伪证「代码没变」。
- **M0 共享层 / 契约**：textunit schema v2 + parse 契约；LLM/Embedding client（Xinference 网关）；键坑归档（DeepSeek 思考模式、bge-m3 return_sparse、Xinference 认证、HF 镜像）。