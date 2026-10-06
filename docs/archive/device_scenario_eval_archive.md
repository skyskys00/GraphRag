# 器械场景评测旧题集 / 未采纳分析归档

> **本档收纳 DEVICE_SCENARIO.md 在 `testset_device_v2.json`（v5.34 全删重建）之前的全部评测蓝图、旧题集实测与口径调查**。它们均建基于旧 30 题 / 河谷 50 题（v5.33）题集与 comparison/unanswerable 考点，数值与现行 v2 题集**不可横比**；技术落地项（runner 兼容改动、image_only drawing 块纪律、泄漏治理红线）已保留在主体文档。完整原文、表格、报告路径与探针脚本均在本档，可复现。
>
> **版本时序**：v5.29（30 题初建 + runner 兼容）→ v5.30（comparison 泄漏治理：退役 per_doc_queries）→ v5.31（评测窗口收敛 top5）→ v5.32（§5.4 localize_query 落地）→ v5.33（扩 50 题难度 5:3:2）→ **v5.34（题集全删重建 v2，现行权威）**。

## 旧五类考点分布蓝图（v5.29 定，30 题）

**规模**：30 题（对齐行政库 30 题口径，便于横向对比）。

| 题型 | 题量 | 示例 |
|---|---|---|
| `table_numeric` | 8 | 「50cc 规格注射器的内径是多少毫米」（图片型表格） |
| `fact_single` | 8 | 「静态压力量程是多少 kPa」 |
| `image_only`（新增考点） | 6 | 「灌注模式下界面显示的运行速度是多少」（答案只在图上） |
| `comparison` | 4 | 「H2-5000IBP 与 LSP-1C 的精度等级分别是多少」 |
| `unanswerable` | 4 | 「支持蓝牙吗」（负对照，防编造） |

## image_only 纪律与 DV-IO-004 例外（v5.29 修正）

**关键纪律**（v5.25.3 教训，2026-10-02 收紧；v5.29 修正口径）：`image_only` 题的**答案完整内容必须存在于 `block_type=drawing` 块**——多模态有贡献的必要条件。

> ⚠️ **不要用 `pdftotext -layout` 文本层 0 命中当基线**——它不够严。实测 `YASEE`、`实时压力`、`绑定医生`、`多语言`、`UNITS`/`HOLD`/`DIF` 等串在文本层「0 命中」（图片里的字），却出现在 chunk 的 `paragraph`/`table` 块 content 中。文本层基线会误判为「干净」。
>
> 核验：在 `data/collections/<col_id>/chunks*.jsonl` 上按 `block_type` 过滤，逐串确认答案命中 `drawing` 块。注意 `page_range` 是 **0-based**（`page_range=N` = 人类第 N+1 页）。
>
> **口径修正（v5.29）**：早期「非 drawing 块 0 命中」过严。实测 6 题 **5 题严格合格**；**DV-IO-004 保留并记为已知例外**——其 KF3（绑定医生/医生解绑/多语言）除命中 `drawing`（第 19 页设置图，答案完整）外，还在 `table`（第 20 页）命中。原因非出题错误：**MinerU 把第 20 页「绑定医生机构」子页图误判为 `type=table`**（该条目同时带 `img_path`，本质是图片），OCR 出的子页功能名与设置页功能项重合。不变量应为「**答案完整存在于 drawing 块**」（DV-IO-004 满足），而非「答案词不许出现在其他块」。
>
> 附带发现：10 份文档 69 个 `table` 块中约 2 个属「图片被误判为 table」（博声 1、KE-2000 1），低频，是 M1 解析质量的真实缺陷，暂不修。

## runner 兼容改动明细 & comparison per-doc 子查询历史（v5.29 → v5.30）

新式 collection（`col_<uuid8>`）与旧式扁平库（`default_ws` 等）布局不同，M9 runner 原本只认后者，跑器械库会解析到不存在的 `data/col_b7b876b1/`。改动四处（均仍落地，保留于主体 §6.1）：

| # | 文件 | 改动 |
|---|---|---|
| 1 | `app/m9_eval/testset.py` | `VALID_CATEGORIES` 补 `"image_only"`（原缺失 → 6 题校验失败） |
| 2 | `app/m9_eval/runner.py` | 新增 `_resolve_collection()`：`col_*` 走 `collection_paths()` 解析到 `data/collections/<col_id>/`，旧三库保持扁平映射，零回归 |
| 3 | `app/m9_eval/runner.py` | `_build_deps()` 补加载 entities（`load_entities_async`）——原缺失会系统性低估生产 recall（线上 `retriever.py` 是加载的） |
| 4 | `app/m9_eval/runner.py` | comparison 题走 **per-doc 检索**（`_comparison_targets` + `_interleave`）：v5.29 按题集 `per_doc_queries[文件名]` 子查询逐 doc 检索；**v5.30 退役该字段**，每 doc 直接用完整 `question` |

**per-doc 为何曾需子查询**（v5.29 实测，可复现）：用完整对比问题（含两个型号名）做 per-doc 检索时，**其他型号名会把该文档的向量召回打到 0**（LightRAG 向量路 `cosine=0.2` 阈值下相似度不足）。实测 `allowed_docs=[LSP-1C]`：完整对比 query → **0 条**；去掉其他型号名 → **5 条**。复跑：`cd backend && python3 scripts/probe_perdoc_subquery.py`。

> ⚠️ **v5.30 勘误（泄漏治理）**：上述「配置子查询」机制**已废弃**。每文档定制子查询 = 评测独有输入（生产 `compare.py` 同一 query 打所有 doc），用它抬高的 comparison 数字只配当「检索上界」不能当产品参考——实测揭开：v5.30 退役后 comparison 用完整 question 检索，数字回落至真实对比检索质量。`probe_perdoc_subquery.py` 不改也不删（保留该检索缺陷的可复现证据，供后续检索改进参考）。

## 扩充到 50 题（v5.33，2026-10-05）

> 本节已被 v5.34（v2 题集全删重建）取代，方案/清单/实测均归档。

**动机（用户拍板）**：30 题的难度配比是 **6 易 / 16 中 / 8 难**（易 20%、难 27%），**偏难、不贴近真实使用**——真实场景里简单查询占多数。用户要求把题集扩到 **50 题、难度分布 5:3:2（易:中:难 = 25:15:10）**，并明确「**改题不改标签**」：通过**真出更多简单题**抬高占比，而不是把难题的 `difficulty` 字段涂成 easy（后者是糊弄自己，且难度标签根本不参与 recall 计算——recall 按题平均，difficulty 只是分组维度）。

**语料新增 2 份**（用户 2026-10-05 上传，`~/Downloads/device_pdfs`）：

| 文件 | 来源格式 | 入库路径 | 内容 |
|---|---|---|---|
| 护理不良事件上报系统培训.pptx | pptx | MinerU | 27 页：不良事件分类 / 四级等级划分（Ⅰ~Ⅳ，含 A~H 损害程度）/ 报告制度（口头·书面·网络）/ 案例分析 |
| 骨科手术器械类产品技术审评规范.doc | 老式 OLE2 doc | `textutil` 预转 docx → MinerU | 2012 版：适用范围（无源类，带电不在范围）/ 管理类别 I·II 类 / 产品类代号 6810 / 材料·硬度·耐腐蚀性·表面粗糙度等主要技术要求 / 注册单元划分 |

> ⚠️ `.doc` 是**老式 OLE2 格式**，Docling 路由需 LibreOffice（本机未装）→ 用 macOS `textutil -convert docx` 预转换。**代价：原文件嵌入的器械示意图全部丢失**（2MB→9KB），仅保留文字。本规范价值在条文（材料牌号/硬度值/粗糙度/标准号），图片为辅助示意，**丢失不影响出题**。

**难度配比（算术说明）**：现有 30 题冻结不动（用户已明令「回滚标签」），新增 20 题可达的最近配比：

| | 易 | 中 | 难 | 合计 |
|---|---|---|---|---|
| 现有 30 题（冻结） | 6 | 16 | 8 | 30 |
| 新增 20 题 | **19** | 0 | **1** | 20 |
| **合计** | **25** | **16** | **9** | **50** |
| 占比 | 50% | 32% | 18% | — |
| 目标 5:3:2 | 25 | 15 | 10 | — |

> **精确 25/15/10 无法在「不动现有题」前提下达成**。取最近配比 **25/16/9**（易=50% 精确达标；中/难各偏 1）。

**新增 20 题清单**（ID 延续 `DV-` 前缀；骨科审评规范 12 题 `DV-N-FS-001~011` + `DV-N-SUM-001`，护理不良事件 8 题 `DV-N-FS-012~019`）：全部为 easy 事实题（管理类别/适用范围/材料牌号/硬度值/粗糙度/不良事件分类/报告形式/反馈周期/案例映射），加 1 题 summary hard（骨科主要技术要求含哪几项）。**题型分布：fact_single 19 / summary 1**（护理 pptx 与器械说明书不同子域、无同型号可比，无法生成 comparison 题；pptx 图片页未经 OCR 核验，暂不出 image_only）。

**护理子域注记**：骨科审评规范属器械域契合；护理 pptx 同属医疗大领域、用户有意纳入以验证多格式处理能力，**不视为违背 `M9_testset.md` §0.1 铁律 1**；50 题里 8 题来自护理子域，读数时按子域分层看。

**✅ 执行结果（2026-10-05，50 题，耗时 2645.8s，judge_failed=0，报告 `run_retrieval_device50.json`）**：

- **入库**：12 份 `ready` / 888 jsonl chunk 行 / 149 drawing 块（新文档无图）。
- **总体 Recall 0.7909**（分母 46，4 题 unanswerable 跳过）——距 80% 目标差 0.91pp。30 题基线 0.6763 → **+0.1146**。
- **新增 20 题平均 ≈0.915**（骨科 12 题全满分 / 护理 8 题 0.7875）。
- **拖累项全是旧题**：image_only 0.6667 / comparison 0.4583 / hard 段 0.4815（四题 0：TN-005 / FS-008 / IO-004 / CP-002）。
- **骨科全满分成因（粒度效应）**：骨科 docx 被 MinerU 切成**单块 4265 字符**，该文档所有 fact 都在这一个块里——块进 top5 即 12 题全命中。单块语义稀释风险被「块被稳定召回」抵消，docx 切分细化后这 12 题将各自独立受检。

**未满分题清单（15 题）**：DV-TN-005/007、DV-FS-002/005/006/008、DV-IO-002/004/006、DV-CP-001/002/004（均旧题）+ DV-N-FS-012/017/019（新题：护理分类 0.8 / 口头报告对象 0.5 / 青霉素案例 0.0——答案疑似在 pptx 图片页）。逐 fact 归因见下节。

## 30 题检索指标（v5.32 生产口径，`run_retrieval_device30_v532_localize.json`）

> 跑法：`--mode retrieval --collection col_b7b876b1 --testset tests/testsets/archive/testset_device_30.json`，v5.32 当前生产口径 = 纯 top5 单窗口，耗时 738.9s。

| 指标 | @5（生产口径，v532） | v531 基线 |
|---|---|---|
| Context Recall | **0.6763** | 0.6635 |
| Context Precision | 0.2533 | 0.2362 |
| Context Precision（加权） | 0.3533 | 0.3263 |
| nDCG | 0.7508 | 0.6906 |

Gold Rank 平均 1.67 / 中位 1.66；事实覆盖率 top1 0.5 → top3 0.7308 → top5 0.7404。

**按题型 Recall@5**：table_numeric(8) 0.8125 / fact_single(8) 0.6562 / image_only(6) 0.6667 / comparison(4) **0.4583** / unanswerable(4) 跳过。**按难度**：easy(6) 0.6667 / medium(16) 0.8542 / hard(8) 0.4167。

**comparison 逐题**：CP-001 0/3→1/3（L2 localize 恢复）；CP-002 0/2（精排截断）；CP-003 2/2；CP-004 1/2（博声侧内容匹配不足）。**⚠️ v532 的 0.4583 与 v5.29 泄漏口径 0.4583 数值巧合相等、机制完全不同**——v5.29 = 每文档定制子查询（泄漏，已退役）；v532 = 同一完整 question + 生产 `localize_query` 逐 doc 去噪（诚实可复现）。

## 30 题生成指标（v5.31 诚实口径，`run_e2e_device30_honest.json`）

| 指标 | 诚实（当前） | 旧泄漏口径（仅参考） |
|---|---|---|
| Faithfulness | **0.9843** | 0.7757 |
| Answer Relevance | 0.7617 | 0.8650 |
| Correctness | **0.6286** | 0.7950 |
| Citation Accuracy | 0.6764 | 0.6707 |

**按题型 Correctness**：table_numeric(8) 0.8571 / fact_single(8) 0.5938 / image_only(6) 0.7500 / **comparison(4) 0.0875 崩盘** / unanswerable(4) 0.6000。faithfulness 全面满格 0.9843、judge_failed=0；4/4 unanswerable 正确拒答无编造。

**comparison Correctness 0.0875 崩盘归因**：诚实口径下 4 题全答非所问/只答一边（完整对比 query 一个型号的块都没进 top5 → 拒答/答单边）——**comparison 瓶颈在 M5/M2 检索不在生成**（本档「30 题检索指标」节 Recall 0.4583@5、口径档案第 5 条 Recall 0.375@5，以及上方「runner 兼容改动」节完整对比 query 向量召回打 0 机制）。`table_numeric` 是唯一检索+生成双稳题型。

> **旧泄漏口径存档**（`run_e2e_device30.json`，仅上界参考）：Faithfulness 0.7757 / AR 0.8650 / Correctness 0.7950 / Citation 0.6707。当时 4/4 拒答亦全部正确。

## 口径说明档案（v5.31 起，含 @8 作废前的历史调查）

> 当前合法口径只有 **@5 = 生产口径**（`eval_top_n` 回 5 对齐生产 `RERANK_TOP`）。以下含 @8 数字者均为作废前调查记录，只作「为何 @8 不可信」的历史依据。

1. **`unanswerable` 题不参与 Recall 计算**：runner 跳过 recall，`by_category.unanswerable.context_recall = 0.0` 只是**占位值**。总体 Recall 分母 = 可答题数。（v2 题集已主动剔除 unanswerable，此节仅留口径记录。）
2. **旧报告（`*_pre_hitfix.json`）数字偏高不可比**：Context Recall 0.9199 是**裁判 bug 假阳性**——`judge.py` 曾丢弃 LLM 输出的 `hit` 字段，`context_recall.py` 用 `score >= 0.5` 反推命中；prompt 里 `score` 是「判定置信度」（`hit=false, score=0.95` = 95% 确信"上下文里没有"），被错误翻转成命中。扫 4255 条缓存矛盾率 **13.4%**。修复后（v5.29，`_PROMPT_VERSION` → `p1_llm_hit`）为 0.7436。
3. **comparison 0.625 → 0.4583 混合两个改动**：per-doc + 子查询**提升**该题型召回，hit 修复**压低**假阳性，净下降。方向相反但都正确。
4. **v5.30 退役 per_doc_queries**：v5.29 的 comparison Recall 0.4583 是「定制子查询」抬出的上界，产品不可复现。退役后 comparison 用完整 question，数字回落。
5. **Recall@8 < Recall@5 非单调 = 裁判归因噪声**：DV-CP-003 fact2（KE-2000）同一事实两次调用判 True/False 两条缓存——top5 按 doc 归属判中、top8 要求逐字「KE-2000」字符串判 miss。诚实值取 @5=0.3750。
6. **v5.29 各题型 @5=@8 精确相等 = p1 缓存键截断的共享产物**：p1 缓存键 `context_str[:500]` 前 500 字符，top5/top8 前缀相同 ⇒ 同一判定被两窗口共享 ⇒ **v5.29 的 @5 从未独立评测**。v531 键改完整 `context_str` 后独立判定——fact_single 0.6562@5 / 0.8438@8（DV-FS-002/008 真落 top6-8），v5.29 的 fact_single 0.8125 是 top8 判定回声，**0.8125→0.6562 是口径回归非随机噪声**。

## 未满分题逐 fact 漏召归因（v5.33 探针实测）

> 探针 `backend/scripts/probe_hard_miss_rank.py`，日志 `backend/tests/reports/probe/probe_hard_miss_rank.log`。复现 runner 生产检索形态，把每个失败 fact 的目标块当「鱼」解剖三路候选 / RRF 全序 / fused top40 / 精排 top5。常量 `RRF_K=60` / `FUSED_TOP=40` / `RERANK_TOP=5`。

**前置结论**：15 道未满分题共 **25 个失败 fact**，按失败性质三分：

- **检索漏召 20 个**——目标块内容确在语料（逐条 grep 实测确认），只是**没进 top5**。探针覆盖其中 13 个目标块。
- **裁判字面假阴性 3 个**——内容**已在上下文内**，裁判因措辞不逐字匹配判否（FS-012「包括其他类」×1、FS-017「包括科主任 / 包括总值班」×2，同句枚举内判罚不一致）。**属评测口径 artifact**。
- **推理型 2 个**——语料有原始数据但**无该断言**（FS-019 案例→Ⅰ级、IO-002 Type 排列比较）。

**13 个目标块排名**：**精排截断 9/13（主因）**——目标块已进 fused top40（位次 7–23），cross-encoder 精排后掉出 top5（段落 3 / 表格 2 / 图像 2 / 标题 2，跨题型普遍）；**候选池未进 4/13**——三路全 miss、四个目标块全为短块（23–56 字），稠密与稀疏索引均未命中。分层：easy 0.8720（漏召 3）+ 裁判假阴性 3 + 推理 1；medium 0.8542（漏召 4）；hard 0.4815（漏召 13 + 推理 1）。

**修复方向（仅结论，未动手）**：主战场在 **M5 精排侧**（短块/图像/表格块特征补偿，把 fused top40 位次 7–23 的正确块推入 top5）；次战场是候选池未进 4 例（均短块，稀疏索引失明）。评测窗口 `eval_top_n=5` 对齐生产 `RERANK_TOP=5`，**不得为抬分放宽**（红线）。

---

## 可复现文件清单

- 题集（已归档）：`backend/tests/testsets/archive/testset_device_30.json` / `testset_device_50.json`
- 报告：`backend/tests/reports/retrieval/current/run_retrieval_device30_v531_leakfix.json` / `run_retrieval_device30_v532_localize.json` / `run_e2e_device30_honest.json` / `run_e2e_device30.json` / `run_retrieval_device50.json` / `run_retrieval_device_v2.json` / `run_e2e_device_v2.json`
- 探针：`backend/scripts/probe_perdoc_subquery.py` / `probe_compare_localize.py` / `probe_hard_miss_rank.py` / `probe_comparison_miss_paths.py`，日志 `backend/tests/reports/probe/probe_hard_miss_rank.log`
- 交叉参考：检索全库归纳见 `docs/archive/retrieval_probing_archive.md`；v5.34 v2 题集实测见 `backend/tests/reports/retrieval_comparison.md` v5.34 节