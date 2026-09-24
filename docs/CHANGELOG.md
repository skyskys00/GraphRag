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

## [v5.10] 2026-09-24 —— 表格行列名上下文增强（M2 v1.3）

**影响模块**：M2（v1.3，表格块 content 前置列名摘要行）。

**动机**：table_numeric 类目的检索 Precision 卡在 0.40，方向 1（numeric_match 提权）已验证无效且有害。换思路从表示端入手——给表格行块加自然语言列名前缀，让 dense embedding 和 sparse 索引同时受益（Recall 维度）。

**改动**：
- M2 `chunker.py` `split_table_block()`：在表格 Markdown content 最前面插入列名增强行 `【表格：caption | 列：列名1 | 列名2 | ...】`，caption 有则加、列名必加。
- HTML 预览字段（`html`）不受影响，不改 `_group_table_html`。
- 行级切分后每个子块都带列名前缀（每组重复表头的同时，额外用自然语言列名摘要做语义前缀）。

**全量评测（35 题 eval_cservice，报告 `tests/reports/run_retrieval_v5.10_col_prefix.json`）**：

| 指标 | v5.9 基线 | v5.10 列名前缀 | Δ |
|---|---|---|---|
| Context Precision | 0.5657 | 0.5543 | -0.011 |
| CP（加权） | 0.6817 | 0.6753 | -0.006 |
| Context Recall | 0.9202 | 0.9323 | **+0.012** |

**按类目 Recall 变化**：
- table_numeric: 0.792 → 0.917（**+0.125** ⬆）
- comparison: 0.738 → 0.900（**+0.163** ⬆）
- fact_single: 0.975 → 0.925（-0.050）
- fact_cross_doc: 0.929 → 0.893（-0.036）

**结论**：
- ✅ 列名前缀显著提升 Recall（table_numeric +0.125），相关表格行更容易被召回。
- ⚠️ Precision 持平或微降——排序端（rerank + 五特征融合）没有能力把新增召回的正确答案排进 top-5。
- 方向正确，后续收益在排序端：Recall 天花板被抬高了，下一步应优化数字型问题的排序策略（如表格块 numeric_match 增益、block_type 偏置等）。

**文档更新**：[`M2_chunk.md`](modules/M2_chunk.md) v1.3。

## [v5.7] 2026-09-22 —— 表格双表示全链路打通 + M9 裁判稳定性改进（M2 v1.2 / M9 v1.1）

**影响模块**：M2（v1.2 表格行级切分+双表示）、M7（v10.3 预览透传 html）、M8（v5.3 PreviewUnit.html + 表格渲染）、M9（v1.1 裁判稳定性 + 测试集修正 + 新基线）。M1 不动。

**1. M9 裁判稳定性（对应 Q1 优化项 3/4/1）**：
- `judge.py`：全局并发信号量 `CONCURRENCY=5`（每题 20+ chunk 并发直连不再互相踩）；非重试错误名单（4xx 类名 + 状态码 400/401/403/404/409/413/422）立即失败不空耗退避；指数退避 + 抖动（2→4→8s，上限 20s ±0.5s）；重试循环 `await asyncio.sleep`。
- 失败识别：`context_recall` / `context_precision` 中裁判失败的 fact/chunk 标记 `error` 并 **排除出均分**（不按 0 分计污染 precision/recall），单独计数 `failed_facts` / `failed_chunks`；`report.py` 汇总 `judge_failed_questions`（Markdown 摘要含失败数）。
- **验证**：全量 35 题 judge 失败 = 0；总耗时 1379s（v5.6）→ **580s**（v5.7），主要收益来自并发限流 + 退避重试（不再整批同步失败）。

**2. 表格双表示全链路（对应 Q2 层级 0 + 层级 2）**：
- M2 `chunker.py`：表格 TextUnit **content 从 HTML 转为 Markdown**（embedding/生成用干净文本），新增 `html` 字段存重建的 HTML（预览用）。HTML 解析用标准库 `html.parser`（不新增依赖），兼容 MinerU（`table_body` + caption/footnote）与 Docling（`export_to_html`）产物。
- 行级切分：大表按 `TABLE_SPLIT_ROWS=5` 拆成多组，每组**重复表头**；标题紧接表格时「标题 + 首个切分组」并入同一 TextUnit（保上下文），其余独立。
- M7 `api.py` 预览接口 `units` 追加 `html` 字段；M8 `types.ts` `PreviewUnit.html`、`DocumentPreview.tsx` 表格分支改渲染 `u.html ?? u.content`（旧库无 html 时回退，兼容）。
- 重建 `eval_cservice_ws`：全文重切（M2 94 units，含 20 个表格单元）+ 清 13 表 PG 行 + M3 重索引 + M5 稀疏重建。

**3. 测试集 GT 修正**：`testset_cservice_35.json` 的 CS-TN-001/002/003 标准答案数字与文档冲突（文档自洽正确，GT 系构造时编造/张冠李戴）——如 CS-TN-001 文档 Q3 工单 46,820 件 vs GT 128,450；CS-TN-003 文档 2026E 市场 425 亿 vs GT 186 亿（186 实为 2023 值）；CS-TN-002 人均最高是华南 168 件/人而非华东 845。已按文档修正。

**新基线（v5.7，35 题 retrieval，报告 `tests/reports/run_retrieval_v5.7_table_dual.json`）**：
- 总体：Context Recall **0.9035** / Context Precision **0.4143** / Precision 加权 **0.5872**（v5.6 baseline：0.9111 / 0.3714 / 0.5304；recall 微降系 GT 修正后裁判判定更严，weighted 净升 +0.057）。
- **table_numeric precision 0.0625 → 0.2188（3.5×）**——表格 Markdown 化后数据被稳定检索（top1-3 即含全部所需数字的表格单元），不再受 HTML 标签噪音干扰；GT 修正后裁判不再「与数字矛盾」误判。CS-TN-001 达 0.5（相关 ranks [1,2,3,5]）。
- 其余题型：fact_cross_doc precision 0.61→0.70 / recall 0.89→0.93；comparison precision 0.41→0.44；fact_single precision 0.35→0.41；unanswerable precision 0（该类语义上应低，检索出的弱相关干扰项被判不相关属正确行为）。

**遗留**：table_numeric 绝对精度仍低（4 题里 3 题仅 1 个相关 chunk 进 top8）——下一步优化方向为 Q2 层级 3（针对数字型问题的检索权重/排序），Phase 2 再推进。

**文档更新**：[`M2_chunk.md`](modules/M2_chunk.md) v1.2 ｜ [`M7_interact.md`](modules/M7_interact.md) v10.3 ｜ [`M8_frontend.md`](modules/M8_frontend.md) v5.3 ｜ [`M9_evaluation.md`](modules/M9_evaluation.md) v1.1。

## [v5.6] 2026-09-22 —— M9 评测层 Phase 1 落地：骨架 + 检索指标（M9 v1.0）

**影响模块**：M9 评测（v1.0，Phase 1 完成；从「规划中」转「可用」）。

**定位**：M9 是 bypass/离线评测层——量化 RAG 检索/生成质量，为 Phase 3 回归门禁打基础。三阶段路线：Phase 1（骨架 + 检索指标）→ Phase 2（生成指标 + 完整测试集）→ Phase 3（ablation + 回归门禁）。

**新增代码**（`backend/app/m9_eval/`，8 个文件）：
- `runner.py`：CLI 入口，`--testset / --mode / --collection / --report / --limit`。retrieval 模式逐题跑 `m5_retrieve.retrieve()`（模块直调，无 HTTP 开销）+ 双指标计算，输出 JSON + Markdown 报告。workspace 映射：default→default_ws / eval_cservice→eval_cservice_ws / eval_admin→eval_admin_ws。
- `testset.py`：测试集加载 + 校验（必填字段 / 唯一 ID / 合法题型与难度）+ 统计。
- `judge.py`：LLM 裁判封装——DeepSeek v4-flash 当裁判，提示词中文化，0-1 连续打分，内存 + 磁盘双层缓存（SHA256 key），失败重试 2 次。
- `metrics/context_recall.py`：每个 key_fact 独立让裁判判断能否在检索上下文中找到依据 → 命中数/总数 = recall（细粒度事实点计数）。unanswerable 类题跳过。
- `metrics/context_precision.py`：裁判逐 chunk 判断相关性 → 相关数/总数 = precision；另算排名加权 precision（1/rank 权重）。
- `report.py`：逐题结果 → 总报告（overall + by_category + by_difficulty），JSON 落盘 + Markdown 摘要。

**建库**：`eval_cservice_ws` 独立 workspace（M3 LightRAG 图+向量索引 + M5 稀疏索引，81 chunks/5 文档），与 default_ws 互不干扰。

**首次全量评测（35 题，2026-09-22）**：
- 总体：Context Recall **0.9111** / Context Precision **0.3714** / Precision 加权 **0.5304**。
- 按题型：fact_cross_doc recall 0.89·precision 0.61（图检索跨文档最有效）；fact_single recall 0.95；summary precision 0.63；proper_noun recall 0.92·precision 0.33；comparison recall 0.90·precision 0.41；**table_numeric 最弱（precision 0.06）**——表格类问题检索噪音大（表格 HTML 标签影响 embedding），列为后续优化项；unanswerable recall 0 符合预期（该跳过）。
- 总耗时 1379s（23 分钟）——主要消耗在裁判 LLM（DeepSeek API）连接不稳定导致的失败重试，非检索本身瓶颈。
- 已知待改进：`judge.py` 重试用同步 `time.sleep`（async 中阻塞事件循环），Phase 2 顺手改 `asyncio.sleep`。

**文档更新**：[`M9_evaluation.md`](modules/M9_evaluation.md) v1.0（Phase 1 完成）；[`M9_testset.md`](modules/M9_testset.md) 不变。

## [v5.5] 2026-09-21 —— 解析器双引擎实测复核 + 分工修订（docx 改走 MinerU，PARSER_COMPARISON v1.3 / M1 v1.3）

**影响模块**：M1 解析（v1.3）。

**做法**：raw 样本（PDF×2、DOCX×2、HTML×2）→ `data/parse_cmp/{mineru,docling}/` 产物（已保留供溯源），对比 `blocks.jsonl`。

**核心发现（修订选型结论）**：
- **MinerU v3.4.5 原生支持 docx/pptx/xlsx**（office 后端，纯解析零模型），之前选型漏看了。
- **DOCX 改走 MinerU**：表格结构识别更准（Docling 会把表头单元格拆成独立 paragraph 块，PRD 23 块里 8 块是拆出来的表头 + 3 个空段，内容重复且块数虚高）；heading 数量一致；纯文本内容一致；同样零模型、耗时接近。
- **PDF 维持 MinerU**：正文段落 Docling 覆盖 ~95%、表格 100% 一致，但 Docling 整段丢 bullet 列表项（季度复盘「五、下季度策略建议」5 条全丢）；MinerU 图形化大标题会整丢（当图吞）。表格内容等价，Docling HTML 更紧凑语义化、MinerU 带冗余属性。
- **HTML / EPUB / MD / TXT 仍走 Docling**：MinerU 不支持。
- anchor：仅 MinerU PDF 有 `page:bbox`；Docling PDF 的 prov 未接（实现留白）；docx 两家都不给 paraId。

**文档更新（v1.3）**：PARSER_COMPARISON §10 实测复核重构——删 10.3.1/10.3.2/10.3.3 分节大段分析，综合两轮结果为 **10 维度横向对比总表**（格式覆盖 / PDF文本保真 / DOCX文本保真 / 表格结构-PDF / 表格结构-DOCX / 标题heading / anchor / 模型依赖 / 性能 / 适用场景）。

**代码变更**：
- `m1_parse/config.py`：MINERU_EXTS 加 `.docx/.pptx/.xlsx`；DOCLING_EXTS 去掉这三个。
- `m1_parse/mineru_adapter.py`：`_locate_auto` → `_locate_output`，同时找 `auto/`（PDF）与 `office/`（docx/pptx/xlsx）目录。

**时间参考**：MinerU 45.6s / 2 PDF（pipeline，CPU）+ 13.1s / 2 DOCX（office 后端，含 API 启动开销）。

**文档更新（规划中，P0）**：M9 评测层新增 [`M9_testset.md`](modules/M9_testset.md) v0.1——测试语料与测试集设计规范落地。2 个知识库（客服业务库 5 篇最小集 + 办公行政库 3 篇最小集），35+15 题，含 7 类题型，内容设计原则（共享实体 / 易混淆点 / 跨文档引用 / 版本痕迹 / 干扰项），文档构造流程与质检清单。方案 A（客服中心场景），最小集先行。**办公行政库最小集已构造完成**：3 篇文档（A1 办公用品领用管理办法.pdf / A2 员工差旅报销管理制度.docx / A3 IT设备管理与领用规范.md），覆盖 PDF+DOCX+MD 三种格式，设计了 12 个跨文档共享实体、3 处交叉引用、2 组易混淆概念、版本迭代痕迹；配套测试集 15 题（fact_single×6 / fact_cross_doc×2 / proper_noun×2 / comparison×2 / table_numeric×2 / unanswerable×1），存放 `backend/tests/testsets/testset_admin_15.json`。语料存放 `backend/inputs/testset_corpus/eval_admin/`。**客服业务库最小集已构造完成**：5 篇文档（D1 投诉SOP.md / D2 智能客服PRD.docx / D3 Q3运营数据报表.pdf / D4 话术规范+FAQ.md / D5 行业趋势报告.pdf），覆盖 MD+DOCX+PDF 三种格式，设计了三级投诉/智能问答引擎/ART/FCR/工单系统v3.0/王小燕等跨文档共享实体、3 组易混淆概念（响应时间/升级流程/解决率）、多处交叉引用、版本迭代痕迹（SOP v1.0→v2.3 / PRD v1.0→v2.1）、D5 作为弱相关干扰项文档。配套测试集 35 题（fact_single×10 / fact_cross_doc×7 / proper_noun×6 / comparison×4 / table_numeric×4 / summary×2 / unanswerable×2），每题含 ground_truth / key_facts / must_have_docs / must_not_have_docs / difficulty / source_docs / tags，存放 `backend/tests/testsets/testset_cservice_35.json`。语料存放 `backend/inputs/testset_corpus/eval_cservice/`。

## [v5.4] 2026-09-21 —— rerank 事件循环阻塞修复（切会话卡死，M5 v1.10）

**影响模块**：M5 检索（v1.10）。

**Bug**：单 worker uvicorn 下，流式问答的检索阶段 `rerank()` 是同步阻塞 HTTP（`urllib.request.urlopen` → Xinference bge-reranker，timeout=180），在 async `retrieve()` 中直接调用会占死整个事件循环 —— 期间所有并发请求（含前端切换会话的 `GET /conversations/{id}` `fetchConversation`）全部排队，表现为「流式输出中切到其它对话卡住，等当前输出结束才跳转」。

- **M5 · rerank 调用包 `asyncio.to_thread`**：`retriever.py` 中 `rerank(query, candidates, top_n=RERANK_TOP)` → `await asyncio.to_thread(rerank, query, candidates, top_n=RERANK_TOP)`，rerank 在线程池执行，事件循环保持响应。生成阶段（AsyncOpenAI 流）与 PG（asyncpg）本为真异步无障碍，检索路径唯一同步阻塞点即此番修复。
- **实测（探针法）**：流式进行中每秒打 `GET /conversations/{id}`，修复前 1 次 probe 恰好撞上 rerank 窗口耗时 **14.2s**（该切换请求全程在事件循环排队）；修复后连续 18 次 probe 全部 <2ms 零排队，SSE 事件线完整（128 条 data），多轮历史引用结果不变（PRD 0.976、SOP 被过滤，与 v5.3 一致）。

**说明**：rerank 自身 ~14s（CPU 推理 top-8）耗时不做优化，本次只解并发阻塞；「切走后当前会话继续输出、可切换查看/提问」的前端链路（patchIfActive 停止渲染 + 后端照常落库）v5.2 已就位，本修复打通了最后的后端并发瓶颈。

## [v5.3] 2026-09-21 —— 检索/生成 query 分离 + rerank 排序修复（修 Bug4 根因，M5 v1.9 / M6 v1.4 / M7 v2.1）

**影响模块**：M5 检索（v1.9）、M6 生成（v1.4）、M7 应答（v2.1）。

**Bug4 根因定位**：v5.2 的相对阈值过滤只是下游补救，真正根因是**多轮会话历史污染检索 query**——`build_query_with_history` 把最近 4 条历史拼进 query 后，文本量增大、引入通用术语，导致 bge-reranker 对所有候选 chunk 的分数全面虚高（SOP 从 0.03 → 0.94），相对阈值过滤的分母（max_score）被拉高，弱引用全部过关。

- **M6/M7 · 检索/生成 query 分离（方案 D）**：
  - `orchestrator.py`：`answer()` 和 `answer_stream()` 均新增 `retrieval_query: str | None = None` 参数；检索阶段用 `retrieval_query or query`（纯用户问题，不带历史），生成阶段仍用 `query`（带历史，保持多轮连贯性）。
  - `respond.py`：`make_answer()` 和 `stream_answer()` 均改为 `q_gen = build_query_with_history(query, history)` 传给生成，`retrieval_query=query` 传给检索。
  - **效果**：query「智能客服系统项目设定了哪些核心业务目标？」（带 2 轮历史）修复前 6 条引用、SOP 排第二 → 修复后仅 1 条引用（PRD 0.976），SOP 被相对阈值正确过滤。
- **M5 · rerank 排序 bug 修复**：
  - `rerank.py`：`sorted(resp["results"], key=lambda x: x["index"])` → `key=lambda x: x["relevance_score"], reverse=True`。原代码按输入时的原始 index 排序而非按相关性分数降序，导致上下文组装顺序错误（高分 chunk 可能排在低分之后）。
  - 影响范围：M5 精排结果顺序、M6 assemble 上下文拼接顺序、引用面板排序（parse_citations 会重新按 score 排序，前端展示不受此 bug 影响，但上下文质量受影响）。

## [v5.2] 2026-09-21 —— 生成期间可切会话 + 引用置信度相对阈值过滤（M6 v1.3 / M8 v5.2）

**影响模块**：M6 生成（v1.3）、M8 前端（v5.2）。

**规划中（P0）**：M9 评测层 v0.1 规划文档已出（`docs/modules/M9_evaluation.md`）——中文测试集 50 题 + LLM 裁判（DeepSeek flash）+ 6 项核心指标 + 7 组 ablation study，三阶段落地约 7 天。待执行。

- **M8 · 生成期间可切换/新建会话（修 Bug2，方向反向）**：
  - 原 v5.1 「流式锁定」方向做反了——用户原意不是"生成期间锁死会话"，而是正相反：**生成期间可以切到其他会话或新建会话去查看/使用，不必一直等输出**。
  - `useChat.ts`：单 `streaming` 状态 → `streamingMap: Record<convId, boolean>` 每会话独立生成状态；`send` 按会话管理 streaming/abort；`selectConversation / newConversation` 移除 guard（解锁切换/新建）；`deleteConversation` 改为先 abort 该会话流式连接再删。
  - 切走会话后，原会话的流式事件继续在后台跑（后端照常落库），只是不更新当前视图；切回来时看不到中间过程但最终结果已落库（刷新会话即可见完整答案——当前实现切走时停止 patch messages，最终 done 事件也不 patch，下次切回通过 `selectConversation → fetchConversation` 从后端读完整历史）。
  - 侧边栏：移除 `streamingDisabled`，生成中的会话名前加呼吸小圆点（`.conv-streaming-dot`，脉冲动画）提示"正在生成"；新对话/重命名/删除按钮均不再禁用。
  - 当前会话生成中时，输入框仍禁用（避免同一会话并发提问）。
- **M6 · 引用置信度相对阈值过滤（Bug4 方案 B）**：
  - `cite.py parse_citations` 在 sort 后、return 前新增过滤：`score < max_score × CITE_SCORE_RATIO` 的弱引用移除，避免低置信度噪音文档（如语义重叠但不相关的 SOP）凑数展示；至少保留 `CITE_MIN_KEEP=2` 条（极端情况不致空引用）。
  - 参数：`CITE_SCORE_RATIO = 0.1`、`CITE_MIN_KEEP = 2`。
  - **实测**：query「智能客服系统项目设定了哪些核心业务目标？」过滤前引用 1 条（PRD 0.976，LLM 本次只引了 1 条）→ 过滤后 1 条；对于 LLM 引用了 5+ 条的场景，第二梯队（0.01–0.05）的弱相关 chunk 会被过滤掉，引用面板只保留高置信度来源。
- **M8 · 引用面板去掉固定 5 条上限**：`CitationPanel.tsx` 移除 `MAX_CITATIONS = 5` 截断，动态展示后端返回的全部引用（后端已按相对阈值过滤，条数可控）；移除 "共 N 条仅展示前 5 条" 提示。

## [v5.1] 2026-09-21 —— 批量上传 + 会话流式锁定 + 引用/专名检索修复（M5 v1.8 / M6 v1.2 / M8 v5.1）

**影响模块**：M5 检索（v1.8）、M6 生成（v1.2）、M8 前端（v5.1）。后端接口零改动（批量上传前端循环复用 `POST /docs`）。

- **M5 · 复合专名整体加权 + 泛化子串抑制**（修 Bug4 检索污染）：
  - `query_preprocess.py` 新增 `_COMPOUND_SUFFIX_RE`（`X系统/平台/产品/项目/方案/引擎/中心/部门/工作组/大区/模块`，整词 ≥4 字加权 3.0 > 实体子串 2.0）；`ll_keywords` 构建加子串归并：已选更具体的专名后，其泛化子串（如「客服」「客服系统」「系统」）不再进入 graph seed。
  - **实测**：query「智能客服系统项目设定了哪些核心业务目标？」修复前 `ll_keywords=['客服','客服系统','系统']`（共享泛化词把客户服务投诉处理SOP 拉进引用位 2/3/4）→ 修复后 `['智能客服系统项目']`；端到端回答只引用 PRD DOCX 一条（score 0.976），SOP 不再出现在引用中。弱覆盖 query 不误伤：「智能硬件」→`['智能硬件']`、「三级投诉的处理时限」→`['三级','投诉']`。
- **M6 · 引用 snippet 表格 HTML 转纯文本**（修 Bug3 html 标签裸露）：
  - `cite.py` 新增 `_html_to_text()`：表格/富文本 chunk 先还原标签边界为空格（`</td|th|tr|p|div|li|br>`），再剥剩余标签、折叠空白 → 引用面板 snippet 展示纯文本。
  - **实测**：PDF 表格 snippet 从 `<table><tr><td rowspan=1 colspan=1>销售区域…` → `销售区域 季度目标(万元) 实际完成(万元) … 华东大区 2,800 3,120 111.4%`。
- **M8 · 批量上传 + 会话流式锁定**（修 Bug1/2）：
  - **批量上传**：InputBar（pdf/docx/md）与 Dashboard（pdf/docx/md/pptx/txt）文件选择器加 `multiple`，`onUpload` 签名 `(file: File)` → `(files: File[])`，前端循环调 `POST /docs`（后端复用单文件接口零改动），toast 汇总上传数量。
  - **会话流式锁定**：`useChat` 新增真实 `streaming` ref+state（原 UI 仅靠 message state 推断，回答完成前不可靠）；`send` 开始时置锁、`finally` 解锁；锁定期内 `selectConversation/newConversation/deleteConversation` 直接 return，且侧边栏会话切换/重命名/删除、TopBar「新对话」按钮同步 `disabled`（title 提示「回答生成中」）。
  - **验证**：`tsc --noEmit` 零错误；Bug3/4 经 `POST /answer` 端到端实测（见上）；Bug1/2 为 UI 行为，未做浏览器走查（tsc + 逻辑审查覆盖）。

## [v5.0.1] 2026-09-21 —— 表格数据全链路修复（M1 v1.1 / M2 v1.1 / M7 v10.1 / M8 v5.0.1）

**影响模块**：M1 解析（v1.1）、M2 切分（v1.1）、M7 交互（v10.1）、M8 前端（v5.0.1）

- **M1 · MinerU 表格内容修复**：`blocks_builder.py` 原只从 `text` 字段读内容，但 MinerU 表格数据存在 `table_body`（HTML 字符串）和 `table_footnote` 里，`text` 为 None → 表格 content 为空 → M2 过滤 → 索引/问答/预览全链路丢失。修复：`typ == "table"` 时从 `table_body` 读 HTML，拼接 `table_caption` 和 `table_footnote`，`format` 标为 `html`。修复后 MinerU PDF 表格从 0 个 TextUnit → 正常进入切分与索引。
- **M2 · 表格并块类型修复**：标题刚开即紧接表格时标题+表格并入同一 TextUnit，但未置 `is_table=True`，`_dominant_type()` 按多数派（标题1 vs 正文0）会把 block_type 退化为 paragraph。修复：`chunker.py` 并块分支同时写 `cur["is_table"] = True`。索引内容不受影响（只有 re-parse 才触发重新索引），但对预览表格渲染与模块语义是必需的。
- **M7 · 预览接口透传 block_type**：`GET /docs/{doc_id}/preview` 返回字段新增 `block_type`，供前端识别表格单元。
- **M8 · 文档预览表格渲染**：`DocumentPreview.tsx` 中 `block_type === "table"` 的单元以 HTML 表格渲染（`dangerouslySetInnerHTML`，来源为自有解析器输出，可信），其他单元保持纯文本安全渲染。新增 `.preview-table-wrapper` 样式（边框合并、横向滚动、脚注小字灰、与浅暖色系一致）。
- **实测（默认知识库 default_ws 三元重建后）**：清空旧产物与白名单，仅用 `inputs/raw/季度销售业绩复盘报告.pdf` + `客户服务投诉处理SOP.md` + `智能客服系统产品需求文档.docx` 重跑 M1→M2→M3（ok=3/3，chunks=7/15/5，含 6 个表格单元）。问答验证：PDF「华东大区实际完成销售额」→ **3,120 万元**（引用成绩表 chunk，score 0.957）；DOCX「智能问答引擎预计工期」→ **8 周**（表格内容，score 0.99）；MD 投诉升级响应时效可完整分条回答。预览验证：PDF 7 单元含 2 个 `block_type=table` 单元（`<table>` 完整渲染）。

## [v5.0] 2026-09-21 —— 多对话管理（M7 v10 / M8 v5）

**影响模块**：M7 交互（v10）、M8 前端（v5.0）

- **M7 v10 · 后端会话持久化**：新增 `app/m7_interact/conversations.py`（注册表 + 明细分离），Collection 作用域，`conv_<uuid8>` 命名。
  - **数据布局**：注册表 `<working_dir>/conversations.json`（title/created_at/updated_at/message_count/preview）；明细 `<working_dir>/conversations/<conv_id>.json`（完整 messages，含 citations/meta）。
  - **API**：`GET/POST /conversations`、`GET/PATCH/DELETE /conversations/{conv_id}`，全部带 `collection_id` query（缺省 `default`）。
  - **答问落库**：`POST /answer` 与 `GET /answer/stream` 新增 `conversation_id` 参数；带会话时历史从后端读（build_query_with_history 取最近 4 条），答完 `append_round` 落库（加 asyncio.Lock 防并发写）。
  - **首问自动命名**：标题为占位「新对话」时，用第一条 user 消息前 30 字覆盖。
  - **向后兼容**：`conversation_id` 缺省 = 无会话模式（前端透传 history），WebUI / 旧客户端行为不变。
  - **验证**：会话 CRUD 全链路 curl 绿；SSE 流式答完正常落库（user + assistant 2 条，含 citations/meta），首问自动命名生效。

- **M8 v5.0 · 前端多会话 UI**：
  - **侧边栏会话区**（collection 切换器下方、导航上方）：「＋ 新对话」按钮 + 会话列表（hover 显重命名/删除，MVP 用 `window.prompt`/`confirm`）。
  - **TopBar 行为**：「清空会话」→「新对话」（新建空会话，不再原地清消息）。
  - **useChat 重写**：删除 localStorage 消息持久化；新增 `conversations/currentConversationId` 状态与 `loadConversations/selectConversation/newConversation/renameConversation/deleteConversation` 方法；发送时懒创建会话（无会话首问自动建）；流式期间切走会话则停止渲染（后端仍落库）。
  - **切库联动**：切换 collection 后加载该库会话列表并选中最近一个；USE_MOCK 模式会话区隐藏。
  - **验证**：`tsc --noEmit` 零错误；`npm run build` 通过（dist 1.68 MB）。

## [v4.0.4] 2026-09-20 —— 上传原件保留（供追溯）

**影响模块**：M7 交互（v9.3）

- **上传原件保留**：`documents.ingest_task` 不再 `unlink` 上传临时文件，原件保留在 `uploads/`（默认库 `data/uploads/`、非默认库 `data/collections/<col_id>/uploads/`，文件名 `<uuid8>_<原名>`）供后续追溯/审计。
- **不影响既有行为**：入库用显式 `src_path`，不扫描 `uploads/` → 不会重复入库；检索/预览走 chunks+sidecar+parse/，不依赖原件；软删文档也不动原件（追溯完整）。
- **副作用**：`uploads/` 不再自清理，文件随上传累积（个人规模可接受）。

## [v4.0.3] 2026-09-20 —— 默认库改名 default_ws + 历史文件态库归档

**影响模块**：M3 索引（v0.3.2）、M5 检索（v1.7）、M7 交互（命名对齐，能力不变）

- **归档**：`data/lightrag`（GLM 对照库）、`data/lightrag_deepseek`（DeepSeek 文件态正式库）→ `data/archive/`。二者为 M4 PG 上线前的文件态产物，角色已被 PG 取代；决策依据（GLM vs DeepSeek 实测对比）保留在 M3 §5 / v1 条目，历史实测记录里的归档目录名不动。
- **默认库重命名** `lightrag_m4` → `default_ws`：
  - 目录 `backend/data/lightrag_m4` → `backend/data/default_ws`（documents.json / m5_sparse.json / recent_queries.json 原位跟随，空嵌套残留已删）；
  - PG 13 张 `lightrag_*` 表 1670 行统一 `UPDATE workspace='default_ws'`（graph_edges 复合 FK 顺序校验冲突 → 事务内 `session_replication_role=replica` 跳过校验整体提交）；
  - 代码 9 文件 18 处（m3/m5/m6 runner 默认目录与用法示例、bootstrap/documents/graph workspace 引用、sparse_index 输出路径）改 `default_ws`；
  - 模块文档（M2/M3/M4/M5/M8）workspace/路径同步；「默认库 workspace = `default_ws`」写法统一。
- **验证**：PG 全表仅剩 workspace=`default_ws`（1670 行）；`lightrag_m4` 全仓（py+md）grep 零残留。⚠️ **服务需重启后新 workspace 才生效**（当前进程仍持旧值），重启验证见收尾。

## [v4.0] 2026-09-18 —— 双层图谱优化（M8 v4.0 Phase 1/2/3）

**影响模块**：M7 交互（v7/v8/v9）、M8 前端（v4.0）；规划：`docs/modules/GRAPH_OPTIMIZATION_v4.md`

- **M7 v7 · 文档级图谱**：`GET /graph?level=document`——节点=文档、边=概念关联（Jaccard 阈值 0.05）+ 话题聚类（networkx `greedy_modularity_communities` 社区发现，cluster 自动命名取高频实体拼接）。数据完全从实体归属关系派生，不新增表、与 collection 隔离天然兼容；文档<200 时计算毫秒级。
- **M7 v8 · 引用关系边 + 关系分类**：文档级新增 citation 边（文件名/编号变体正则匹配 TextUnit 文本，有向，含引用次数 + 首个 snippet）；实体级边新增 `rel_type`/`rel_type_name`（6 类关键词规则方案 A：归属/动作/因果/时间/同义/属性 + 未分类）。**实测**：默认库 191 条边中 133 条分类成功（≈70%），覆盖 5/6 类（无时间类数据）；文档交叉引用检测到 1 条（snippet 正确）。
- **M7 v9 · 实体筛选**：`collect_graph` 新增 `top_n`（>0 且节点≥40 时按 PageRank 取 top_n 核心节点，只保留节点间边）；实体类型归一化 7 大类（`normalize_entity_type`：organization/org/组织→组织、metric→概念…）。**实测**：默认库 160 节点/191 边 → top_n=60 得 60 节点/76 边，pagerank 降序、边全在集合内，核心 top3=公司/客户投诉/一级(紧急)。
- **M8 v4.0 · 前端**：Phase 1 文档级图谱视图（`DocGraphView` 双粒度切换、双击下钻、话题 legend）；Phase 2 实体级边按关系类型着色 + 关系类型 legend 点击过滤 + 详情卡类型标签；Phase 3 核心/全部实体切换（`top_n` 参数）+ 7 类实体类型过滤 chip（视图层过滤，联动剔除悬空边）。实测 headless CDP 9/9：默认核心 60、切全部 160 节点、chips 隐藏/恢复/多选累计、回归文档视图正常。

## [v4.0.2] 2026-09-18 —— 话题聚类算法升级 + 图谱交互体验优化（6项）

**影响模块**：M7 交互（v9.2）、M8 前端（v4.0.2）

- **M7 v9.2 · 话题聚类算法升级（问题 2）**：从 Jaccard 相似度 + greedy_modularity 改为**关键词加权余弦相似度 + 社区发现**。核心变化：
  - 实体按类型加权（组织/产品/事件×2.0、概念×1.5、人物×1.0、地点×0.8、其他×0.5）
  - **文档名/标题高权重（×4.0）**（用户要求：有意义的文档名应主导聚类）
  - 关键词归一化：去标点/通用后缀/数字前缀 + 日期碎片过滤
  - 余弦相似度构图（阈值 0.10）→ greedy_modularity_communities 社区发现
  - 簇名去冗余：停用词（时间碎片/通用组织词）+ 去包含关系子串 → 取 top 3 拼接
  - **效果**：4 篇文档（销售×2 + 办公用品 + 客户投诉）从 Jaccard=0 导致的 1 簇 → **3 簇**（销售业绩 / 办公用品 / 客户投诉），符合直觉。
- **M8 v4.0.2 · 前端**：
  - **布局收敛（问题 3）**：力导向布局加 `alphaDecay: 0.08` + `velocityDecay: 0.3`，节点 2s 内稳定不再颤动（headless 实测质心漂移 0px）。
  - **节点 hover 状态（问题 3）**：`node.state.hover` 配置 halo（金黄色 12px）+ stroke 加深 + enlarged 1.15 倍放大，鼠标悬停有视觉反馈。
  - **详情浮窗默认位置调整（问题 4）**：从 `right: 32px`（预览侧）移至 `right: 440px` / `top: 130px`，落在 graph 区内右上、靠近关系类型 legend 处，减少视觉跳跃。
  - **类型过滤后点击失效修复（问题 5 / Bug 5）**：将 `node:click` / `node:dblclick` 事件 handler 从独立 effect（只依赖 `dataVersion`）移入 init effect（graph 创建后立即注册），确保 `hiddenGroups`/`hiddenRelTypes` 变化触发 graph 重建时 handler 同步重新绑定。**根因**：类型过滤改变 `hiddenGroups` → init effect 重建 graph → 旧 effect 没重跑 → 新 graph 实例无点击监听。
  - **计数文本右边距（问题 6）**：`.graph-count` 新增 `margin-left: auto` + `margin-right: 3px`，"x节点x边" 的"边"字距右侧边栏恰好 3px。
  - **headless CDP 验证**：话题 3 簇（销售/办公用品/投诉语义正确）、布局 2s 漂移 0px、浮窗位置在 graph 区内右上且 position=fixed、类型过滤后点击详情正常出现、计数右边距 3px。

## [v4.0.1] 2026-09-18 —— 知识图谱 4 个 bug 修复

**影响模块**：M7 交互（v9.1 幽灵文档白名单对齐）、M8 前端（v4.0.1）

- **M7 v9.1 · 幽灵文档过滤（Bug 1/2 根因）**：`collect_document_graph` 文档级节点现在**只保留文档管理可见文档**（`doc_meta` 注册表白名单），幽灵文档（chunk 索引残留但从未走上传接口、注册表缺失）不再作为文档节点/概念边/话题聚类输入；`doc_meta` 为空时回退全量显示（纯离线建库兼容）。节点 label 回退顺序：注册表 filename → M2 chunk `file_path` 推断 → doc_id。**根因**：初始离线建库的 3 篇文档（含 2 篇重复索引副本）有 PG 索引残留、无注册表记录，实体级图谱已按白名单过滤、文档级图谱未对齐 → 文档视图凭空多出幽灵节点。修复后文档级图谱从 5 节点（含幽灵）→ **恰好 3 篇**，与文档管理一致。
- **M8 v4.0.1 · 前端**：
  - **详情浮窗化（Bug 3）**：点击节点弹出的详情从「flex 右栏」改为 `position: fixed` 浮窗（右上角、阴影、`max-height` 内滚动），**头部可拖拽**（pointer events + `setPointerCapture`，实时 `left/top`）、×按钮关闭；图谱画布恢复全宽，不再把「引用/预览」列挤出画面。
  - **文档节点以名显示 + 详情含文档ID（Bug 2）**：文档级节点 label 按注册表/推断链取文件名（不再退化显示 doc_id），详情卡新增「文档ID」行（等宽字号）。
  - **过滤下拉固定宽度（Bug 4）**：`.graph-filter-select` 固定 `width 160px + flex:none`，长文档名不再撑爆上方布局。
  - **实测 headless CDP 全过**：文档级「3 篇文档」、浮窗 `position:fixed`/含「文档ID」行/拖拽位移生效（left 空 → `1236px`）/×关闭生效、select 宽 160px 且选择长文档名后不变。

## [v3.0] 2026-09-17 —— 多知识库 + 仪表盘（M8 v3）

**影响模块**：M3 索引（v0.3.1）、M7 交互（v6.0）、M8 前端（v3.0）

- **M8 v3.0 · 前端**：
  - 新增「多知识库」（FR-33~37）：侧边栏顶部知识库切换器（切换 / 新建 / 重命名 / 删除），default 恒在且不可删；全部视图（问答 / 文档 / 图谱 / 仪表盘）按当前库上下文切换；`localStorage` 持久化当前库，库被删则回落默认并提示。
  - 新增「仪表盘」视图（FR-38~43）：库名 + 重命名/删除操作、统计卡片三连（文档数 / 实体数 / 关系数，点击跳文档管理 / 图谱）、最近问答列表（点击继续对话）、文档概览（前 5 条，更多跳文档管理）、快速操作按钮组；空库给上传引导，不以空列表糊脸。
  - 现有单库行为零迁移：未新建知识库时完全等同 v2，现有文档自动归属默认库。
- **M7 v6.0 · 后端**：
  - 按 collection 维度重构 `bootstrap.py`：`AppDeps` 增加 `collection_id` + `workspace`，`get_deps(collection_id)` 字典缓存，每库独立 rag/sparse/entities/sidecar/白名单。
  - 新增 `app/m7_interact/collections.py`：注册表 `data/collections.json`，CRUD + 每库最近问答（封顶 20）。删除 = registry 移除 + `shutil.rmtree` + best-effort 清 PG `lightrag_*` 所有该 workspace 行。
  - 全部业务路由加 `collection_id`（缺省 `default`）：`/answer` body、`/answer/stream` query、`/docs` query/form、`/graph` query。新增 `GET /collections`（含 doc_count）、`POST/PATCH/DELETE /collections`、`GET /stats`（doc/node/edge 计数 + recent_queries）。
  - 后端环境修复：移除 `backend/.env` 的 `POSTGRES_WORKSPACE`（postgres_impl import 时 `load_dotenv(".env")` 会把它重新注入，pop 无效，移除后 `db.workspace=None` → 实例 workspace 生效）；修复 `m3_index/runner.py::_load_dotenv` 内联注释剥离 bug（此前 `STORAGE_BACKEND=pg   # pg|local` 被读成完整串 → 静默落 local 存储）。修复后 M7 全线切回 PG（M4 应然状态）。
- **M3 v0.3.1 · 索引**：`build_rag(working_dir, workspace)` 增加 `workspace` 参数透传给 `LightRAG`；`_load_dotenv` 修复内联注释。
- **选型结论**：Collection = 一个 `workspace` 值（复用 LightRAG 原生行级隔离），放弃「同 workspace 加 `collection_id` 列」方案——后者需要 fork 官方子模块改 DDL/storage，维护成本高；spike 实证同进程多 workspace initialize 无冲突、双向隔离。

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