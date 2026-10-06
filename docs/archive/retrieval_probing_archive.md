# 检索探底归档（未采纳 / 旧数据集分析）

> **归档说明**：本文档收纳 `backend/tests/reports/retrieval_comparison.md` 中被**归档、未采纳、或建基于已作废数据集**的旧分析。全部内容原样搬迁自 retrieval_comparison.md 的历史版本（含 2026-10-03 压缩前的完整明细、git HEAD `044d8e2` 内容），保留原始数字、报告路径与探针脚本名，供日后复跑/回溯（2026-10-05 归档）。
>
> 判断标准：探底三连收尾未落地、结论「不调配置/keep 现状」、数据建基于已作废的旧 30/35 题集（数据集合结构已 v5.34 重建）。**凡是已落地为正式功能/修复的（如本章 fix、v5.10 列名前缀、v5.13 NL 摘要、gold_rank 修复），不在本档，见 retrieval_comparison.md 或 CHANGELOG。**
>
> 与主线的关系：主表 `retrieval_comparison.md` 现只保留（a）客服库 v5.6–v5.13 历史对照总表（作采集基线）；（b）v5.23.3 gold_rank 修复（已采纳）；（c）当前题集的结论段与 v5.34 更新（现行权威）。本档各节均可在主表找到出处锚。

---

## 行政库表格语义注入与 title_path 校准（v5.19→v5.20，2026-09-26）

> 出处：retrieval_comparison.md「# 行政库（eval_admin）表格语义注入与 title_path 校准（v5.19→v5.20，2026-09-26）」整节。
> 测试集：`testset_admin_30.json`（行政库 30 题） | 探索目标：**表格块因 content 无 query 词汇重叠而进不了候选集**的 M2 层修复探索。
> 本轮所有指标为**同测试集、同裁判、同配置**的横向对比；nDCG 为排序层唯一权威指标（judge 的 context_precision 波动大）。基准线：v5.19 baseline + 本轮的 「chapfix 数据无注入」复现（两者逐题一致 = 数据链干净）。

### 关键前提验证（confound 排除）

- PG 重建后各 doc chunk 数 = 磁盘 chunks.jsonl 行数（40/25/42/35/21/40，零重复）→ dense/graph 无重建残留污染。
- **chapter fix 数据下「无注入」纯检索 = 0.8567 / recall 0.9524，与 v5.19 baseline 逐题完全一致**（q002/q009/q013 对得上）→ 证明 (a) chapter fix 零检索伤害、(b) 重建前后 PG 完全等价。

### 已落地：chapter fix（M1 解析正确性，v5.20）

- **改什么**：`blocks_builder._CHAPTER_RE` 对 `第X章/第3节/第一篇/卷` 式标题在 PDF 链路强制 level=1。MinerU 模型推断 text_level 常把章误判为与「X.Y」节同级（=2），导致 chunker 弹栈丢章、title_path 退化为「文档名/节」、sibling 展开为整文档目录。docx（读 Word 大纲）章值本为 1，恒等不变。
- **效果**：title_path 分层恢复（章→节）；检索指标与 baseline 完全一致（0.8567）——**纯解析修复，语义零回归**。
- 报告：`tests/reports/retrieval/history/run_retr_admin_30_v520.json`。

### 已确定无效（探底三连收尾，不落地）

| 变体 | nDCG@5 | Rec@5 | 判定与根因 |
|---|---|---|---|
| baseline / chapfix 无注入 | **0.8567** | 0.9524 | — |
| 注入 sib+genmeta（全量表块前缀「父标题+本表章节含…」重编码 sparse） | 0.8473 | 0.9613 | **-0.94pt**：q002 0.919→0.748 / q009 0.989→0.911 / q012 0.988→0.902 / q013 0.760→0.698，三个注入变体完全一致 → **真实排序回归，非 judge 噪声** |
| 注入 + NL_SUM（表格 NL 摘要进 sparse 向量） | 0.8499 | 0.9613 | **+0.0026 杯水车薪**：摘要进不进召回池不是瓶颈 |

- **注入对 title_path 结构敏感**：旧（错误）title_path 下 #×0.0006、正确 title_path 下 -0.94pt——全量 sibling 前缀在正确层级下过曝回吐。
- 探针脚本留档：`scripts/probe_m2_inject_all.py`（全量）、`scripts/probe_m2_inject_rank.py`（排序级判定）；报告 `tests/reports/probe/probe_m2_inject_*`。

### 可能有效的线索（数据指向，暂不落地）

- 注入的**唯一稳定增益是 3 题查全**：q010 rec5 0.5→0.75、q020 0.773→0.866、q024 0.949→0.975——确凿证明「语义前缀能把表块拉进 top5」对特定 query 有效，且这 3 题全部是表块此前进不了候选集的缺口题。
- **可挖方向**：不做全量 sibling 展开，改为「只加父标题」（扁平退化 FLAT_DEGRADE 已在探针中验证无大碍但未细测）、或按 query 类型定向注入。当前未达落地门槛（全局 -1pt 不抵 3 题增益），留待更优口径。
- **共同教训（与客服库 v5.13 呼应）**：表格语义缺口可发生在召回层（表进不了候选集）也可在 reranker 层（表在池内但 cross-encoder 失明）。admin 探针证明召回层缺陷能靠前缀打开（q010/q020/q024），但全量展开的代价是过曝——**缺口真实存在，手段需精准**。

### 根因分析（2026-09-26，纯分析，无代码改动）

对「表格块进不了候选集」三类失败模式 + 三向注入全无效的根因归位，作为后续方向决策依据（证据：`probe_admin_gaps.json` + 各表块 content 实证）。

#### 「进不了候选集」是三种不同性质的失败，不是一种病

- **模式 A：字面词命中但 sparse 不激活**——q004 的 A2 3.1 城市分级表块含 query 原词「上海」（`| 一类城市 | 北京、上海、广州、深圳 |`），graph/vector/keyword 三路全失、RRF 池未进。bge-m3 sparse ≠ 词法 BM25：中文高频专名（城市/人名）在训练中当作背景词，稀疏端几乎不分配激活权重 → query「上海」与 doc「上海」点积 ≈ 0。字面重合≠稀疏命中。
- **模式 B：真实词汇失配 + 跨文档维度错位**——q008 的 A3 6.2 赔偿标准表块，词是「遗失/赔偿比例/责任类型」，query「笔记本丢失」的「笔记本/丢失」在表内不存在；答案表在 A3（IT 规范），场景词「出差」在 A2（差旅）。sparse 字面匹配天然对不上，graph 路也连不上（query 未提任何实体名）。
- **模式 C：复合 query 信息分片**——q004 的 query 三信息点（上海→3.1 城市分级 / 普通员工+住宿标准→3.2 住宿费标准）分散两块；单一 sparse query 向量被「住宿标准」等强激活词主导，对 3.1 表块贡献趋近 0，无任何单块承载 query 主要稀疏质量。
- **反例（勿归并）**：q013 配纸量表其实**三路全中**、rrf_has=true、final_rank=2 —— 它不属于「进不了候选集」，是排序/reranker 层的另一类缺口（top5 窗口内排名靠后、nDCG 低），与 q004/q008 画像不同。

#### 结论与根因

- 缺口存在于**两个平面**：召回层（模式 A/B/C 让表进不了池）与 reranker 层（表已在池内但 cross-encoder 对数字表失明，v5.13 CS-TN-003 已证：三路召回、RRF 池第 7、rerank 仅 0.133 落第 13）。
- 三向注入全部作用于召回层 sparse 编码：全量 sibling 前缀在正确 title_path 下过曝（-0.94pt 真实排序回归）；NL_SUM 证明摘要进不进池不是瓶颈；唯一增益 q010/q020/q024 恰是「词汇激活不足」类。
- **根因一句话：手段（sparse 前缀注入）与病灶（激活盲区 + reranker 失明 + 维度错位）错配**——不是方向全错，是剂量与配方错：精确注入口径（只加父标题/题型定向）可保留 q010/q020/q024 查全增益并消除过曝。

### 遗留缺口（表格进候选集）的后续候选方向

1. **表格结构化专用索引/检索**（列名→语义、行值→数值匹配），绕过 sparse 词汇失明。
2. **LLM 生成高质量表摘要**（v5.13 规则版升级路径，`table_summary.py` 已留接口）。
3. **注入口径精准化**（父标题 only / 题型定向），在保住 q010/q020/q024 增益的同时消除过曝回归。

### 「结构化表索引」探针验证（2026-09-26，方向 1 预审）

> 脚本 `scripts/probe_struct_table.py`（纯本地解析 eval_admin 表块，无 LLM/Xinference）；报告 `tests/reports/probe/probe_struct_table.json`。
> 验证问题：把表块解析为「列名 + 行 cell」后，用 query 实体做**值匹配**（专名精确/数值区间），能否绕过 sparse 激活盲区，给缺口表块候选资格。

**结论：方向成立，三类典型缺口可救；q008 类跨文档错位须配文档关联扩展。**

| 题 | 需求缺口 | 结构化判定 | 证据 |
|---|---|---|---|
| q004 | 模式 A（sparse 专名盲区）+ C（信息分片） | **可救** | `上海` 值命中 3.1 城市分级；`普通员工`+列语义`住宿→元/晚`命中 3.2 住宿费标准 |
| q013 | 数值区间隐含 | **可救**（强度最高） | `25∈20-50人` 区间命中 2.3 配纸量表，score=5 |
| q020 | 表值场景词 | **可救** | `入职培训` 值命中 2.1 会议室清单「适用场景」cell |
| q008 | 模式 B（跨文档维度错位） | **部分，暴露边界** | `笔记本电脑` 只命中 2.1 设备分类表；答案表 6.2 赔偿标准仅 col_sem=1 分被压制 → 需同库/同文关联扩展 |
| q010/q024 | 无表口径 | 无救（边界确认） | 二者非表类缺口，结构化索引不是解 |

**与 v5.10 列名前缀的关系（澄清：不是重做，是补 cell 值层）**：

- **列名前缀【列：】是 v5.10 正式 M2 功能**（`chunker.py` `col_prefix_parts`），客服库 / 行政库共用同一 chunker——**行政库是继承而非重做**（建于 v5.19 2026-09-25，晚于客服库，用的正是含表格双表示 + 列名前缀 + NL 摘要的最终版逻辑）。
- **v5.10 只做了「列名层」**：把列名注入 content 帮 dense/sparse 编码理解表，但**从不覆盖「行 cell 值层」**——query「上海」在 cell `北京、上海、广州、深圳`（不在列名）、query「25人」vs cell `20-50人`（数值区间），列名前缀都覆盖不到。
- **探针补的正是 cell 值层**：`上海 ∈ cell值`（q004）、`25 ∈ [20-50]`（q013）这类命中**从未进过正式检索**。探针不是重复 v5.10，而是诊断出 **v5.10 只做了一半（列名层），行 cell 值层是空白**——同样的 schema 数据一直在 content 里躺着，但此前检索只对列名受益、从未消费过行 cell 值。

**实现注意点（落地前必读）**：
1. **query 数字抽取须单位标定**——`4箱` 被裸抽成 `(4)` 曾误命入职流程表的「顺序=4」列，需 `(num, unit)` 双元 + 列单位校验（`detect_numeric_cols` 已有雏形）。
2. **col_sem 权重平衡**——score=1 的列语义命中不足以对抗噪声表，q008 已证；专名/数值命中（3/2 分）才是本索引的差异化能力。
3. **进池 ≠ 进 top5**——CS-TN-003 教训仍在：rerank 对数字表失明。结构化只解决「进候选池」，最终名次仍待融合+rerank 实测（正文对应 v5.17 已激活 NL 摘要，数字表 rerank 失明未根治）。

**落地形态建议**：M5 检索线第四路候选源——query 抽取 {专名实体, 数值约束, 列语义词} 后查结构化表块索引（列名倒排 + cell 值倒排 + 数值列区间），产出候选并入 RRF 融合；或先做「结构化补召回」兜底。属跨 M2/M5 的非 trivial 改动，落地前需出正式方案。

**B 方案落地验证（2026-09-26，已三连收尾不落地）**：按「先做兜底 + 验证 rerank 表现」落地探底——`struct_table.py` 结构化补召回（M5 第四路兜底）：从内存 `sparse_doc["chunks"]` 解析表块为「列名+行 cell」，query 侧三信号打分（score=3×value + 2×num + 1×col）、score≥2 补进 RRF 池，随 rerank+融合竞争 top5（报告 `run_retr_admin_30_structB.json`）。

| 题 | struct 补池 | 判断 |
|---|---|---|
| q004 | **2**（3.1 城市分级 `上海` 值命中 + 3.3 交通费「标准」噪声） | 真缺口块补进池 ✓ |
| q013 | **1**（配纸量表 `25∈[20-50]`） | 真缺口块补进池 ✓ |
| q020 | **0** | 会议室块本就在三路池（非真缺口），`already_in_pool` 正确跳过 |
| q008 | 0（col=1 被阈值拦） | 与探针「部分救」一致 |

**机制全部按设计生效，但 30 题全量指标与 v5.20 baseline 逐位一致**（nDCG@5 0.8567 / Rec@5 0.9524 / Prec@5 0.4600 / gold_rank_avg 2.04）。根因链条完全符合预期：补块 sparse/rrf 特征为 0、排名几乎只由 rerank 分决定 → **cross-encoder 对数字/专名表失明，补块全部被压出 top5**（q004 城市分级块补进池后融合排名仍在 top5 之外）。**结论：结构化补召回能解决「进候选池」，但最终名次卡在 reranker 失明，无任何指标收益 → 不落地。** 与 v5.11/12（特征增益无效）、v5.13（NL 摘要探针有效整体无效）一脉相承：**召回层手段（前缀注入 / NL 摘要 / 结构化补召回）都无法抵消 rerank 对表格语义的失明**，缺口需 reranker 侧改造或 LLM 高质摘要才能突破。代码已 git 回滚，`struct_table.py` 未接线删除。

## LLM listwise 终审探底 A/B（v5.21，2026-09-27，已落地为 M9 可选 reranker）

> 出处：retrieval_comparison.md「## LLM listwise 终审探底 A/B（v5.21…）」整节。
> **决策背景**：B 方案（结构化补召回）证明「进池靠召回层手段、出位卡 reranker 失明」。终审权移交方向：把 `fusion.fused_top40` 前 20 交给 LLM listwise 重排定 top-N —— **谁握终审权比顺序更关键**，cross-encoder 降级为召回/初筛。
> 探针 `scripts/probe_rankgpt_listwise.py`（报告 `probe_rankgpt_listwise.json`）→ 全量 A/B `tests/reports/retrieval/history/run_retr_admin_30_llm_final.json`（30 题，同库同裁判）。
> **归档备注**：本节基于旧 admin 30 题集。落地状态与主线一致：仅 M9 评测 `--reranker llm` 可选，生产检索 `retriever.py` 用 bge-reranker、从未接 LLM 终审（2026-10-04 附录核对）。

### A/B 总体

| 指标 | v5.20 baseline | --reranker llm | Δ |
|---|---|---|---|
| nDCG@5 | 0.8567 | **0.9479** | **+9.1pt** |
| Recall@5 | 0.9524 | **0.9857** | +3.3pt |
| Prec@5 加权 | 0.619 | **0.7281** | +10.9pt |
| gold_rank avg | 2.04 | **1.76** | 更靠前 |

### 逐题画像（升/降/不动，诚实标注）

- **大幅提升（符合「救表块」机制）**：q021 nDCG 0.743→1.000（prec 0.4→1.0）、q011 0.906→0.981（recall 0.667→1.0）、q004 recall 0.5→1.0、q010 prec 0.2→1.0、q013 table 0.760→0.916。
- **个别反向（LLM 排序引入噪声）**：q005 nDCG 0.973→0.826、q009 0.989→0.868、q019 0.991→0.907；q014/q008 recall 1.0→0.8（LLM 把含 fact 的块排出 top8）。单题波动含 judge 随机性，但 net +9.1pt 远超噪声可解释。
- **四类评分**：comparison 0.9667 / fact_single 0.9475 / proper_noun 0.9561 / **table_numeric 0.9519（原最弱项，gold_rank avg 1.00）**。

### 结论与边界（决策依据）

- **LLM 终审治「reranker 层失明」**：池内表块（CS-TN-003 两表、q020 会议室表、q004/q012 城市分级表）4/4 救回 top5 —— cross-encoder 失明的终审权问题已解决。
- **只治池内不治池外**【同日探底更正】：q008 实为 3/5 被 fused40 覆盖（017 rank1 载 fact5、029 rank5 载 fact0/1/2），「5 facts 全 miss」是 `_lexical_match` 数字粘连假阴性；真缺失的 fact3 宿主 030/031 连 fused40 候选池都没进（探针 DEBUG 确认池外）—— **仍属召回/融合边界缺口**，LLM 终审救不了（见下文 children/neighbor 探底）。
- **成本**：每问 +1 次 LLM 调用（pool 20 块 ≈ 6k tokens 输入，+5~10s）；与 e2e judge 叠加后单题 ~25s。
- **决策**：仅 M9 `--reranker llm` 可选，**不接 answer 链路**（流式不兼容 + 评测同源偏置风险 + 生产 token 成本）、**不引入 M5 生产检索**。

---

## children/neighbor 展开探底（2026-09-27，三连收尾不落地）

> 出处：retrieval_comparison.md「## children/neighbor 展开探底（2026-09-27，三连收尾不落地）」整节。

**目标**：验证「标题孤岛（children 展开，M2 构建期静态引用）+ 表格 sibling（neighbor 展开，检索期±K）」两类池外答案块补召回。`scripts/probe_expand.py` 留档可复跑。

**判定修正（前置）**：gold_rank `_lexical_match` 把「疏忽大意30%」这类数字粘连中文词作整串 keyword，对文档 `|疏忽大意|…|30%|`（数字与词分离）永远失配 —— 系统性假阴性，曾致 q008 误报「5 facts 全 miss」。探针改用「数字边界精确匹配（避 `50` 误中 `5000`）+ SequenceMatcher 公共子串（≥4 字或 ratio≥0.5）」从宽判定。

**全量 30 题（eval_admin_ws，仅检索无 judge；28 题可判，99 facts）**：

| 维度 | 结果 |
|---|---|
| children 救回缺失 fact | **0**。多数题 `heading_in_pool≥1`（标题块确会进池），但展开后无一救回缺失 fact；孤儿标题块（028 第六章，11 字）在 q008 完全没进 fused40，触发点不存在 |
| neighbor 救回缺失 fact | 仅 **q008 = 1 fact**（6.2 赔偿表 030，靠 029 rank5 ±3 拉入，且 029 已在 top8） |
| 展开噪声 | 28 题新增 **2290 块**，救回 99 facts 中 1 个（收益 1.0%）——噪声比远超验收线 |

**三个观察**：
- **children 触发点先天稀疏**：孤儿标题块内容过短（≤11 字）向量检索难命中；即便命中（q009/q010），其子树也不是缺失答案块。
- **多数「缺失 fact」无宿主**：q010 的 fact0/3 「本库未定义 FAS」是否定性事实，无答案块可救，children/neighbor 救不回是正确行为。
- **q008 缺口实为召回/融合边界**（更正此前「排序层」误判）：030/031 连 fused40 候选池都没进（探针 DEBUG 确认池外），v5.21 「只治池内不治池外」对它们成立；neighbor（029 rank5±3）能拉回 030，恰恰证明这是**池外补法**，非 reranker 排序层——之前把「029 在池内」误推广为「030 也在向量近邻」。

**结论**：children 无收益证据（M2 零改动）；neighbor 仅 q008 一例、噪声大，不落地。若未来要缓池塘外表格子块，只宜对「表格族命中块」做 ±1 或按表边界展开。遗留：gold_rank `_lexical_match` 假阴性建议修（已被 v5.23 采纳修复，影响历史 gold_rank 数字，幅度小）。

---

## 两线收口：检索优化阶段结束（2026-09-27）

> 出处：retrieval_comparison.md「## 两线收口：检索优化阶段结束（2026-09-27）」整节。
> 承 v5.21（LLM listwise 终审落地）+ children/neighbor 探底（三连收尾）之后的最终归位：**q008 = 召回/融合边界缺口，承认并搁置；其它题 = rerank/排序层已封顶**。两条线的证据链在此并表归档，检索（M5/M9）侧优化阶段收口。

### 线一：q008 召回缺口 —— 承认搁置

- **缺口定性**（v5.21 更正）：030/031 连 fused40 候选池都没进（探针 DEBUG 确认池外），非 reranker 排序问题，rerank 层任何手段结构性碰不到它们。
- **验证过的全部手段**（均已探、均不落地）：

  | 手段 | 验证结论 |
  |---|---|
  | children 展开（M2 构建期静态引用） | 0 救回；孤儿标题块（≤11 字）向量检索先天难命中，触发点不存在 |
  | neighbor ±K 展开（检索期） | 能拉回 030（q008 唯一 1 fact），但 28 题新增 2290 块、噪声比远超验收线 |
  | 结构化补召回（query 实体匹配表 cell） | 能进候选池，但 rerank 压出 top5，30 题指标逐位不变 |
  | NL 摘要注入（sparse 向量或 rerank 输入） | 结构性不适用——rerank 只在 fused40 之后打分，对池外块不可达 |

- **结论**：承认这一召回缺口并暂时搁置（`e2e` 已证明系统诚实拒答该细节，不幻觉，可用性不受损）。若未来要缓池塘外表格子块，唯一保留的最小成本路径是「对已命中表格族块做 ±1 或按表边界展开」；否则长期接受。

### 线二：其它题 rerank/排序层 —— 已封顶（瓶颈定位）

- **评测侧（M9）**：v5.21 LLM listwise 终审把「cross-encoder 对数字/专名表失明」这一池内问题整体绕开——nDCG@5 0.8567→**0.9479**（+9.1pt）、Re@5 0.9524→0.9857、gold_rank avg 2.04→1.76；池内 27 题 Re@5 全 1.000（唯一不满的 q008 是池外，非 rerank 问题）。
- **生产侧（M5 cross-encoder）输入侧改造已试尽**——这是「已封顶」的关键证据：

  | 手段 | 版本 | 效果 |
  |---|---|---|
  | 列名前缀进度 | v5.10 | Recall 纯 gain（+0.012），CP 微降 |
  | numeric_match 特征增益 | ~~v5.11/v5.12~~ | 检索结果逐字节一致，零效果（top5 排序由 rerank 主导） |
  | 规则版表格 NL 摘要进 rerank 输入 | v5.13 | 单块 rerank 0.133→0.352（+165%），融合排名 13→10，top5 组成不变 → 类目指标与 v5.10 持平 |
  | 摘要/前缀注入 sparse 向量 | v5.20 | 三连无效（全量 -0.94pt ~ +0.0026） |
  | LLM 版摘要进 rerank 输入 | v5.20 探针 | 数据复原：2023 块 rerank 0.154→0.714（融合 rank12→6）、2026E 块 0.102→0.358（rank14→11），**top5 名单逐位不变**（详见本档「补档」节） |

- **病根**：cross-encoder 对数字/专名表语义失明，rerank 输入文本怎么喂（列名/摘要/特征）都只改变分数量级、不改变排名结构 → **输入侧已到天花板**，瓶颈在 reranker 本身。
- **剩余缺口**：0.9479→1.0 的 ~5pt 是 LLM listwise 的排序噪声（q005 0.973→0.826 / q009 0.989→0.868 / q019 0.991→0.907 反向；q014/q008 Re@5 1.0→0.8）。这类只能靠 prompt 稳定/多拍投票打磨，且**只对评测侧有意义，生产侧捞不到**。
- **生产侧决策不变**（v5.21 已定）：M5 不引 `--reranker llm`、answer 链路不接（流式不兼容 + 评测同源偏置 + token 成本），生产检索以 v5.20 baseline 为准。

### 收口结论

检索（M5/M9）侧优化到此收口。两条线各归一：召回层只剩 q008 一处池外缺口（搁置），排序层已由「终审权移交」（评测侧）或「接受天花板」（生产侧）终结。后续若要再动检索，方向只剩换赛道——M2 语料/切分源头、评测端稳定化打磨，属新课题，不在本表续记。

---

## 补档：LLM 版表格摘要进 rerank 输入（v5.20 探针数据复原，2026-09-27）

> 出处：retrieval_comparison.md「## 补档：LLM 版表格摘要进 rerank 输入（v5.20 探针数据复原，2026-09-27）」整节。

**背景**：线二生产侧表中「LLM 版摘要进 rerank 输入 | v5.20 探针」曾因 commit 归档错位而"未归档"。本小节复原该探针的完整 stdout 数据（来源 `/tmp/probe_chain_full.log` + `/tmp/probe_llm_summary_cache.json`），并把 probe 脚本留档可复跑（`scripts/probe_llm_summary.py` / `scripts/probe_llm_summary_chain.py`，commit 772515a）。

**归档错位说明**：commit 772515a 的 message 将另一链路（NL_SUM→sparse 向量，v5.20 三连无效之一）+0.0026 的结论错误附加到本探针 commit 上，造成"已归档"假象；本探针真实 rerank 分数只存在于 /tmp stdout，从未写入正式报告。这是链路混淆 + 数据没留，不是忘记。

**实验设置**：CS-TN-003 单题。对两个待提升表块分别注入 LLM 生成的表格摘要，替换规则版 NL 摘要进 rerank 输入，对比 fused 融合排名。

| 表块 | chunk_id | 池外/池内 | rerank 分（baseline→注入） | fused 分（baseline→注入） | 融合排名 |
|---|---|---|---|---|---|
| 2023 块（186） | `chunk-83f981999d0efd98ca39703dc7c9b2c1` | eval_cservice_ws 池内 | 0.1538→0.7145 | 0.3742→0.6549 | rank12→6 |
| 2026E 块（425） | `chunk-cfdc3495c50fd5b028194d353f0e138f` | eval_cservice_ws 池内 | 0.1022→0.3584 | 0.3397→0.4680 | rank14→11 |

**关键结论**：
- **top5 名单逐位不变**（baseline 与注入后同为 `591791 / b3966b / c23bdf / f882dd / ea6ee3`）：2023 块注入后 fused 0.6549 仍低于 top5 门槛 fused≈0.672，差一档。
- **与规则版同构**（= v5.13 结论）：哪怕 LLM 摘要单块提升更高（rank12→6），也只是把分数推高，不改变 top5 排名结构 → 病根仍在 cross-encoder 失明，**输入侧换内容救不了结构**。
- 顺带确认：两表块都在 eval_cservice_ws 池内，非 q008 那类池外缺口，此探针不涉及召回层。

---

## 召回路数对照（route ablation，v5.24，2026-09-29）

> 出处：retrieval_comparison.md「## 召回路数对照（route ablation，v5.24，2026-09-29）」整节。

**背景**：回答「每条召回路由（graph / vector / keyword）各带多少增益」——`--ablation-routes` 开关按 active set 裁剪三路召回输入（不改检索/融合/判据逻辑），admin 30 题跑 vector / vector+graph / 三条全量三组对照；verify 组（0 ERROR）复跑逐项一致，初跑 judge ERROR 为已重试成功的瞬时日志，未污染。

**总体指标**（报告 `tests/reports/ablation/run_ablation_admin_{vector,vector_graph,graph_vector_keyword}_20260929.json`）：

| 指标 | vector | vector+graph | full（三路） | 2026-09-27 基线 |
|---|---|---|---|---|
| Recall@5 | 0.9440 | 0.9440 | 0.9321 | 0.9321 |
| Prec@5 | 0.4200 | 0.4333 | 0.4200 | 0.4200 |
| wPrec@5 | 0.5876 | 0.5966 | 0.5920 | 0.5920 |
| nDCG@5 | 0.8415 | 0.8426 | 0.8450 | 0.8450 |
| gold_rank avg | 1.37 | 1.37 | 1.59 | 1.59 |
| GR top1 / top3 / top8 | 0.642 / 0.878 / 0.884 | 与 vector 全同 | 0.666 / 0.848 / 0.914 | 与 full 全同 |

**每加一路的增量**：

- **graph 路（vg−vector）**：召回层**零增益**。8 题 chunk 集合与纯向量完全一致、仅排序微调；Prec@5 +0.0133 / wPrec +0.9pt / nDCG@5 +0.001 全部来自 adm_q009/q020 两题 judge 判 0.4→0.6，量级等于 LLM judge 单题波动（±0.13pp），不构成可靠增益。gold_rank 全部维度逐位不变。
- **keyword 路（full−vg）**：**双刃**。
  - 正向：fact_cross_doc Re@5 0.775→**0.900**（+12.5pt）、GR top8 0.884→**0.914**（+3.0pt）；rank7 救援 3 个 key fact（`25人在20-50人区间内` [adm_q013] / `3F大会议室容量40人` + `适用全员大会/入职培训/季度总结` [adm_q020]）；GR avg 1.37→1.59。
  - 代价：top5 净 recall −1.2pt——fact_single Re@5 1.0→0.9545、table_numeric 1.0→0.9333、GR top3 0.878→0.848（关键词路由把个别事实从 top5 挤到 top6-8）。
- **full = 2026-09-27 基线逐位一致**（Δ 全 0）：三路就是生产配置，pipeline + judge 复现性确认。

**结论**：生产 top5 净效果 ≈ 中性；三路收益集中在跨文档召回 + top6-8 覆盖，由 RERANK_TOP=8 窗口兜底。**不因此调检索配置**——现状「keep 三路」合理。逐题 top5 差异：full vs vector 12/30、full vs vg 10/30（`scripts/compare_ablation.py` 可复跑逐题归因）。

### 客服库复现（cservice 35 题，2026-09-29）

**动机**：admin 30 题的「三路 ≈ 中性」结论是否只在行政库成立？在另一领域（客服业务库，`eval_cservice_ws`）跑同口径对照（vector / 三路全量两组）。

**总体指标**（报告 `tests/reports/ablation/run_ablation_cservice35_{vector,graph_vector_keyword}_20260929.json`）：

| 指标 | vector | full（三路） | Δ |
|---|---|---|---|
| Recall@5 | 0.8692 | **0.8753** | +0.61pt |
| Prec@5 | 0.4914 | **0.5029** | +1.15pt |
| wPrec@5 | **0.6281** | 0.6208 | −0.73pt |
| nDCG@5 | 0.8934 | **0.8960** | +0.26pt |
| gold_rank avg / median | 1.69 / 1.50 | **1.64 / 1.44** | 更靠前 |
| GR top1 / top3 / top5 / top8 | 0.5465 / 0.7611 / 0.8177 / 0.8303 | **0.5540 / 0.7763 / 0.8227 / 0.8379** | 全面 +0.8~1.5pt |

**结论**：**与 admin 库同向** —— 三路 vs 纯向量在客服库上同样是「小幅度、方向不一」的微调（recall/prec/gold_rank 略优，wPrec 略劣），所有 Δ 均 < 1.2pt，量级与 LLM judge 单题波动相当。**跨领域复现了「keep 三路、不调配置」的判断**：三路的价值是稳健兜底（尤其 GR top-k 覆盖），不是单窗口的显著增益。

---

## M5 query 扩展消融探底（v5.29 探底，2026-10-03，三连收尾不落地）

> 出处：retrieval_comparison.md「# M5 query 扩展消融探底（v5.29 探底，2026-10-03，三连收尾不落地）」完整版（2026-10-03 压缩前）。

**动机**：`query_preprocess` 的 A2「实体名反向模糊匹配」把噪声词喂进 vector/keyword 两路——器械库实测查询含「融柏」时扩展出 `保定融柏恒流泵制造有限公司 / 保险 / 特点 / 简介`，这些通用词稀释真正相关块的 sparse 分数。探针 `probe_expand_ablation.py`（monkeypatch 强制 `expanded = original`，不改生产代码）三库对照。

**检索指标**：

| 库 | 指标 | 扩展 ON | 扩展 OFF | Δ |
|---|---|---|---|---|
| device（30 题） | Context Recall | 0.7436 | 0.7340 | −0.0096 |
| | Context Precision | 0.2492 | **0.2558** | +0.0066 |
| | Precision（加权） | 0.3538 | **0.3736** | +0.0198 |
| | nDCG@8 | 0.7730 | **0.8045** | +0.0315 |
| | gold_rank 均值 | 2.30 | **2.16** | −0.14 |
| cservice（35 题） | Context Recall | 0.7586 | 0.7495 | −0.0091 |
| | nDCG | 0.9312 | **0.9352** | +0.0040 |
| admin（30 题） | 全指标 | — | — | ON = OFF（不触发扩展） |

**检索侧读数**：关扩展**排序质量净升**（nDCG / gold_rank / precision 全面改善），代价是 ~1% 覆盖面（recall）。报告：`run_ablation_{device30,cservice35,admin30}_expON/OFF_20261003.json`。

**e2e 生成侧（device 30 题）——反方向，导致不落地**：correctness 0.7950→**0.7473**、faithfulness 0.7757→**0.7490**、answer_relevance 0.8650→**0.8300**（仅 citation +0.0134）。报告 `run_e2e_device30_expOFF_20261003.json`。

**逐题定位（关键）**：检索侧改动**只影响 1/30 题**——DV-FS-008（fact_single，LH-T600 荧光帽维护周期）丢了含「维护周期表」的 chunk（recall 0.25→0.00），生成遂正确拒答（correct 0.8→0.0）；**其余 29 题逐题检索完全相同**，correctness 却仍升降剧烈（升：DV-CP-003 +0.47、DV-TN-006 +0.25；降：DV-CP-004 −0.65、DV-FS-007 −0.25）⇒ 生成降幅 **≈一半是 DV-FS-008 单题真退化（−0.80 / −1.43）、一半是 LLM 生成/裁判噪声**。

**决策**：不采纳、已回滚（`retriever.py` 维持 `q_vec/q_kw = prep.expanded`）。理由：检索净升幅度（nDCG +3pt）不足以抵消一道真实退化题带来的生成损失，且该题正是「扩展恰好帮上忙」的场景（扩展词帮助召回了维护周期表）。

**副产品（排除 comparison 漏召回的「M5 稀疏权重」假设）**：探针 `probe_cmp_miss.py` 逐环定位目标块 `chunk-bb7dc287…`（per-doc 限 LSP）——关扩展后 keyword 路它擦边进池（第 39/40 名），但 RRF 跨路聚合下单路 rank 39 仅贡献 `1/(60+39)≈0.0101`，被多路命中块碾压；该块 vector（第 65/287）/ graph（第 92）两路 0 贡献 ⇒ 仍进不了 RRF 池。**⇒ 真瓶颈是目标块语义匹配本身不足（M2 切分粒度），非 M5 稀疏权重**。（注：此归因后被 v5.32 探针证伪为「精排截断/匹配太弱」两型，见下节。）

---

## comparison 评测口径泄漏探查（v5.30 泄漏治理，2026-10-03）

> 出处：retrieval_comparison.md「# comparison 评测口径泄漏探查（v5.30 泄漏治理，2026-10-03）」完整版（2026-10-03 压缩前）。
> **处置已落地**：②③ 是正式改动（删除 `per_doc_queries` 字段、runner 用完整 question、窗口统一回 8），见 CHANGELOG v5.30。本节归入归档因其完整分析基于已作废旧题集；处置结论本身仍在主线（压缩 stub 保留）。

**背景（用户 ML 类比触发）**：器械评测集 comparison 题的 `per_doc_queries`（每文档定制子查询）是**评测独有输入**——线上 `compare.py` 同一 query 打所有 doc，没有任何评测侧定制输入 ⇒ 用定制子查询抬高的 comparison 数字 = 训练/服务偏差 + 标签污染，只配当「检索上界」不能当产品参考。

**证据（探针 `probe_prod_vs_eval_shape.py`，可复跑；需 PG + Xinference）**：取 4 题 `per_doc_queries` 并集（= 生产形态：用户对每台设备各发起一次 compare 调用、参数名相同），两种口径并排跑：

| 题号 | eval-shape（每 doc 定制子查询，top12） | prod-shape（同一套 query 打所有 doc，top3） |
|---|---|---|
| DV-CP-001 | 2/3 | **0/3** |
| DV-CP-002 | 2/2 | 2/2 |
| DV-CP-003 | 2/2 | 2/2 |
| DV-CP-004 | 2/2 | 2/2 |
| **汇总** | **8/9 = 0.8889** | **6/9 = 0.6667** |

**读数**：落差集中在 DV-CP-001（0/3）——该题 `per_doc_queries` 把「X 型号灌注/抽取/连续模式」拆成单型号子查询，生产完整对比问题直接召回失败（其他型号名把向量召回打到 0）。**v5.29 的 comparison Recall 0.4583 是定制子查询抬出来的上界**。

**处置（v5.30 已落地，见 CHANGELOG v5.30 条目）**：① 题集删除 4 题 `per_doc_queries` 字段；② runner 每 doc 直接用完整 `question` + `allowed_docs`（与生产同形）；③ 评测窗口统一回全题型 8（删 `COMPARISON_WINDOW=12`）。诚实口径数字见 `run_retrieval_device30_v531_leakfix.json` 与 `DEVICE_SCENARIO.md` §11。

---

## comparison 剩余漏召逐路定位（v5.32 探底，2026-10-04，M2 切分假设被证伪）

> 出处：retrieval_comparison.md「# comparison 剩余漏召逐路定位（v5.32 探底，2026-10-04，M2 切分假设被证伪）」完整版（2026-10-03 压缩前）。当前对比前置：v5.32 localize_query 落地于生产与评测镜像，本题已无再生产物；本档仅保留逐级诊断可复跑余地。

**动机**：v5.32 localize_query 后 comparison Recall 0.375→0.4583@5，剩余漏召长期归因「M2 切分粒度遗留」（v5.29 `chunk-bb7dc287` 推断）。本次探针 `probe_comparison_miss_paths.py`（只读诊断、可复跑）把每个漏召 fact 的目标块当「鱼」，在生产检索形态（`localize_query` + `retrieve` + `allowed_docs=[doc]`）下逐路解剖：主路三路 rank / RRF 全序 / fused top40 / final top5，加放宽 kw@200、vec@200、graph@100。锚点取自语料 chunk实测内容，与 ground_truth/key_facts 零交集。

**4 个漏召 fact 逐条结论（0/4 由切分粒度导致）**：

| fact | 目标块 | 主路三路 | RRF 全序 | fused | final top5 | 放宽（200/200/100） | 定论 |
|---|---|---|---|---|---|---|---|
| CP-001.f3（瑞创 140mm） | 123 字单行 table | g5 / v3 / k8 全进 | **rank 3** | 7 | **MISS**（rerank 0.42 vs top1 0.957） | — | **精排截断**（= 客服库 CS-TN-003 同款画像，调权重方向已封闭） |
| CP-002.f1（KE-2000 血压） | 468 字 paragraph | g12 / v12 / k23 全进 | **rank 11** | 6 | **MISS**（rerank 0.304） | — | **精排截断** |
| CP-001.f2（融柏 注射器） | 111 字 paragraph | 全 MISS | — | — | — | kw@200=82(0.0965)、vec@200=140、graph MISS | **匹配太弱**（非切分，目标块单主题） |
| CP-004.f2（博声 会诊） | paragraph | 全 MISS | — | — | — | kw@200=140、vec@200=109 | **匹配太弱** |

**对 v5.29「遗留」归因的直接证伪**：旧归因指向 `chunk-bb7dc287`（LSP-1C「一块塞内径/行程/时钟被长度归一化稀释」）。该 fact（CP-001.f2）语料内可定位目标块实为 **111 字单主题 paragraph**；v5.29 探针自记该块 keyword 第 39 名擦边进池——**稀疏打分可达，差在 RRF 跨路聚合而非切分**。若切分是主因，4 块应全像 f2/博声那样进不了候选；而 2/4 已进 RRF 全序前 11，切分假设不成立。

**后续方向（历史已封闭，勿重复探底）**：f3/CP-002 精排截断 = 客服库 CS-TN-003 同款画像，该方向客服/admin 库已系统探底并封闭——v5.11/12 numeric boost 逐字节无效、v5.13 NL 摘要分数变序不变、v5.20 结构化补召回指标逐位不变，贯穿病根是 bge-reranker 对数字/专名块失明（rerank 主导排名结构）；唯一被验证有效（救池内块）的 v5.21 LLM listwise 终审已决策不接生产（流式不兼容 + 评测同源偏置 + token 成本）。故 f3/CP-002 要动名次需 reranker 侧换赛道（LLM 终审进生产 / 换 reranker 模型），f2/博声属纯匹配问题另案——均属方向决策，非探底能自行开启。本次探底零代码改动，无需回滚；探针留档 `backend/scripts/probe_comparison_miss_paths.py`。

---

## 三库检索效果横向对比（2026-10-04）：器械「差」非系统退化，是考核难度结构差异

> 出处：retrieval_comparison.md「# 三库检索效果横向对比（2026-10-04）：器械「差」非系统退化，是考核难度结构差异」**旧版主体全文**（含详细三库对照表与 v5.33 更新，截至 git HEAD `044d8e2`）。
> 归档提示：主线 `retrieval_comparison.md` 只保留本节的**压缩结论 stub + v5.34 更新**（v5.34 题集全删重建后 50→50 全重新出题，与本节旧数字不可横比，方向参考）。本档保留原表供 30/35 题历史口径回溯。

**动机（用户疑问）**：「为什么行政库 / 客服库效果好像远好于器械库？」直接横向对比绝对值会产生误导——三套题库的难度配比、题型构成、语料规模都不是一个量级。下表拉齐真实数据（器械 = v532 `run_retrieval_device30_v532_localize.json`（诚实 @5 口径）；行政 = `run_retrieval_finalcheck_admin30_20260927.json`；客服 = `run_retrieval_finalcheck_cservice35_20260927.json`；均 standard reranker、@5 口径。按难度 Recall 由各报告 `questions[].difficulty` + `metrics.context_recall(_top5)` 现算并排除 unanswerable）。

| 维度 | 器械（col_b7b876b1） | 行政（eval_admin_ws） | 客服（eval_cservice_ws） |
|---|---|---|---|
| 题数 / hard 占比 | 30，**8 hard（27%）** | 30，**1 hard（3%）** | 35，**5 hard（14%）** |
| 题型构成 | table_numeric 8 + image_only 6 + fact_single 8 + comparison 4 + unanswerable 4 | fact_single 11 + fact_cross_doc 4 + proper_noun 4 + comparison 4 + table_numeric 5 + unanswerable 2 | fact_single 10 + fact_cross_doc 7 + proper_noun 6 + comparison 4 + table_numeric 4 + summary 2 + unanswerable 2 |
| 语料 | **10 份 / 844 chunk** | 6 份 / 203 chunk | 5 份 / 99 chunk |
| 块形态 | **table 25.2% + drawing 17.5%**（约 43% 非纯文本） | heading 39.4% + paragraph 35.5% + table 24.6% | heading 33.3% + paragraph 41.4% + table 25.3% |
| Recall / nDCG（standard） | 0.6763 / 0.7508（v532） | 0.9321 / 0.8450 | 0.8753 / 0.8960 |
| hard Recall | **0.4167** | 0.80（仅 1 题，无统计意义） | **0.71** |
| medium Recall | **0.8542** | 0.9212 | 0.8222 |
| easy Recall | 0.6667 | 0.9479 | 1.0 |

**结论：落差是结构性三重差异，不是「器械库检索更差」（同难度段链路没崩）：**

1. **难度结构完全不同**：器械 hard 占 27%，行政仅 3%（1 题）、客服 14%；hard 硬题 = table_numeric + image_only 密集，恰好全是 cross-encoder 失明高发题型。
2. **题型构成**：器械首次引入 image_only（6 题，答案在 drawing 截图块）+ 8 题 table_numeric（数值表格）；行政/客服以 fact_single/proper_noun 文本问答为主——文本题型对 reranker 友好。
3. **语料规模放大竞争**：844 chunk 候选池比 99/203 大 4~8 倍，相似块相互挤兑，rerank 把目标块压出 top5 概率成倍上升（探针精排截断画像）。

**对照读数**：medium 段器械 0.8542 ≈ 客服 0.8222 ≈ 行政 0.9212（同量级）；差距集中在 hard 段（0.42 vs 0.71）与题型构成。=> 器械 0.6763 是「把 cross-encoder 数字失明在真实难题上暴露度放大」的结果，不是系统退化。日后纵向对比应**按题型/难度分层读**，勿拿三库 aggregate 绝对值直接排优劣。

**📌 v5.33 更新（2026-10-05，器械扩到 50 题、难度收敛 5:3:2）**：语料 10→**12 份**（+骨科审评规范.docx / 护理不良事件.pptx），题集 30→**50 题**，难度从「6 易/16 中/8 难（hard 27%）」调为「**25 易/16 中/9 难（hard 18%）**」。实测（`run_retrieval_device50.json`，standard，@5，分母 46）：

| 维度 | 器械 50 题（v5.33） | 器械 30 题（v532） | 变化 |
|---|---|---|---|
| 题数 / hard 占比 | 50，**9 hard（18%）** | 30，8 hard（27%） | hard 占比 −9pp |
| 语料 | **12 份 / 888 chunk** | 10 份 / 844 chunk | +2 份 |
| Recall / nDCG | **0.7909 / 0.7737** | 0.6763 / 0.7508 | **+0.1146 / +0.0229** |
| easy Recall | **0.8720**（25 题） | 0.6667（6 题） | +0.2053 |
| medium Recall | **0.8542**（16 题） | 0.8542 | 持平 |
| hard Recall | **0.4815**（9 题） | 0.4167 | +0.0648 |

**读数**：

1. **难度配比是器械 aggregate 的主要杠杆**——把 hard 占比从 27% 压到 18%（贴近真实使用），aggregate Recall 从 0.6763 升到 0.7909（**+0.1146**）。这正面验证上文「器械『差』主要是考核难度结构差异」的结论：**不是链路变强了，而是考核分布变真实了**。
2. **hard 段本身也小幅上移（0.4167→0.4815）**，主因是 v5.32 `localize_query` 让 comparison CP-001 恢复（CP 属 hard）——即检索侧真实改进，与难度重配解耦。
3. **新增 20 题（骨科 12 全满分 / 护理 8 均 0.7875，合计 0.9150）**证明「多格式（PDF/PPTX/DOCX）+ 表格 + 多模态」通用 RAG 在新格式文档上可用；护理 FS-019 的 0（答案疑在 pptx 图片页）与既有 image_only 短板同源。
4. **新语料零回归**：table_numeric / image_only / comparison 三题型 @5 与 v532 逐题一致，跨域入库未稀释器械题精度。

**附录：LLM 终审进生产的现状核对（2026-10-04）**：`rerank_with_llm` 定义与调用点仅在 M9 评测（`llm_rerank.py` + `runner.py --reranker llm`）；生产检索 `retriever.py` 用 bge-reranker，M7/api 无引用——「v5.21 采用过 LLM 终审」只发生在评测量测，生产问答/compare 从未接入。

---

## 精排文本工程探底（增补 / 去噪双向无效，v5.33 探底，2026-10-05，三连收尾不落地）

> 出处：retrieval_comparison.md「# 精排文本工程探底（增补 / 去噪双向无效，v5.33 探底，2026-10-05，三连收尾不落地）」完整版（2026-10-03 压缩前）。
> 归档提示：主线保留本节的**压缩结论（合并结论「方向封闭」+ 收尾）**。

**动机**：v5.33 逐 fact 归因显示 13 个目标块（段落 3 / 表格 2 / 图像 2 / 标题 2 + 未满分题漏召 20 fact 的主力）里，多数块**已进 fused top40 但被精排压出 top5**。已知 v5.13 给**表格块**加 NL 摘要（`generate_table_summary`）实测单块 rerank 分 +165% 且进基准；于是提出对称假设：**给图像块 / 段落块做同类文本工程，是否同样能把它们推入 top5？** 分两轮：A 增补、B 去噪。改同一文件 `backend/app/m5_retrieve/table_summary.py`，复用既有探针 `backend/scripts/probe_hard_miss_rank.py`（零新增脚本），跑 13 目标块，对比基线 `probe_hard_miss_rank.log`，每轮后 `git checkout` 回滚。

### 轮 A：增补（假设「缺语境」）

**做法**：drawing caption 自然化、paragraph 规格列表补类型前缀、table 走原 v5.13 逻辑。
**结果：无效（净负面）**：final5 **0/13**（同基线）；fused40 位次 5 变差（TN-005 −3 / IO-006 −3 等）/ 1 变好（IO-004 +4）/ 7 不变。机制 = **前缀稀释**：给已有完整语义的块加冗余前缀，拉长文本摊薄 query 关键词密度，cross-encoder 打分反降——与 v5.13 表格摘要「结构化重建补缺失骨架」性质相反，属净噪声。

### 轮 B：去噪（假设「有噪声」）

**做法**：`_denoise_for_rerank` 删 markdown 分隔行、折叠连续空白、还原转义、去「有/无」空列。
**结果：仍无效（净负面）**：final5 **0/13**；fused40 4 变差（IO-004 −9 最烈）/ 1 变好（IO-006 +1）/ 8 不变。

### 合并结论

**两轮方向相反（A 加字、B 删字）却同为净负面 ⇒ cross-encoder 对块文本的表层格式工程免疫。** 位次漂移是文本扰动的**随机噪声**，不是系统性改进——最有力的证据是 IO-004：它对扰动最敏感，A 轮 23→19（变好）、B 轮 23→32（变差），正负翻转，说明这类改动只是把块推进「分数相近的一堆」里随机洗牌。失败是**语义层**（短块 / 数字块 / 图像描述块与 query 语义不匹配），不是**格式层**。

**方向封闭**：精排侧「文本工程」（增补 / 去噪 / 权重调参）三路同归无效，与客服库 v5.11/12 numeric boost、v5.13 NL 摘要（分数变序不变）、v5.20 结构化补召回（指标逐位不变）一致。要动名次只剩换赛道（LLM 终审进生产 / 换 reranker），属方向决策。

**收尾**：两轮均 `git checkout` 整文件回滚（工作区无残留）；日志归档 `backend/tests/reports/probe/probe_rerank_text_context.log`（A）/ `probe_rerank_text_denoise.log`（B）；探针复用未新增脚本；零生产代码改动、零索引重建。