# M9 模块规划：评测层

> **版本：** v1.14
> **状态：** 可用（retrieval 模式 + gold_rank 诊断 + 双窗口 + nDCG + **e2e 生成四指标** + **行政库完整版 30 题检索基线 / NL 摘要激活复测 / 30 题全量 e2e / 裁判一致性抽检 10/10** + **可選 reranker=llm（LLM listwise 终审）** + **gold_rank 词汇匹配器强化（v5.23）** + **裁判 hit 字段修复（v5.29：context_recall 假阳性修正 + comparison per-doc 检索）** + **评测窗口收敛 @5（v5.31）** + **三库评测横向汇总表 + 客服 50 题 / 行政 30 题 retrieval 当前口径重跑（v5.37）**）；Phase 3 待执行
> **更新：** 2026-10-06
> **定位：** 中文 RAG 系统量化评测——测试集 + 指标 + ablation + 回归
> **契约：** 测试集（question + contexts + ground_truth）→ 评测报告（各指标分数 + 对比基线）
> **上游：** [M5 检索层](M5_retrieve.md) / [M6 生成层](M6_generate.md) / [M7 交互层](M7_interact.md) | **下游：** 回归门禁 / 评测量化数据 / README 展示
> **依据：** [`ARCHITECTURE.md`](../ARCHITECTURE.md) §2.7 ｜ [`FRAMEWORK_NOTES.md`](../FRAMEWORK_NOTES.md) §3 ｜ [`M9_testset.md`](M9_testset.md)（测试语料与测试集设计规范）
> **运行：** `cd backend && python -m app.m9_eval.runner --testset testsets/default.json --report reports/run_xxx.json`；重排终审：`--reranker llm`
> **变更历史：** 见 [`CHANGELOG.md`](../CHANGELOG.md) v5.6（Phase 1 落地）/ v5.7（裁判稳定性 + 测试集 GT 修正 + 表格双表示后新基线）/ v5.14（Phase 2 生成四指标 + 50 题全量）/ v5.15（裁判 prompt 校准：correctness 语义对齐 + judge 明细字段透传）/ v5.21（--reranker llm 可选终审）/ v5.29（裁判 hit 字段修复 + comparison per-doc 检索）

---

## 1. 定位与目标

### 1.1 为什么要有 M9

**当前痛点**：全链路跑通了，但「到底好不好」只有主观感受，没有量化数据。
- 答不上「你的系统比普通 RAG 好多少」——没有量化依据。
- 改了检索策略，不知道是变好还是变差——全靠人工试几道题。
- 宣称「三路召回 + RRF + rerank」比 baseline 好，没有数字支撑，没有说服力。

**M9 的角色**：量化尺子 + 回归门禁。不参与线上问答链路（旁路评测），但在迭代时提供客观数据支撑。

### 1.2 目标（v1.0）

1. **可量化**：输出一组标准指标，能说清「当前系统在中文场景下的表现」。
2. **可对比**：支持 ablation study——关掉某一路/换个策略，分数变化一目了然。
3. **可回归**：每次大改动跑一遍，快速判断是否劣化。
4. **可展示**：评测结果能直接放进 README，作为项目核心能力的量化支撑。

### 1.3 边界（M9 不做什么）

- 不做在线评测（不接入 M7 接口做实时打分）——旁路离线评测即可。
- 不做人工标注平台——测试集手工构造，不做标注工具。
- 不做用户行为埋点 / A/B 测试——单机单用户，无意义。
- 不做端到端 latency benchmark——当前数据量太小，latency 不具备参考价值。

---

## 2. 评测指标体系

分三层：**检索质量** → **生成质量** → **端到端质量**。

### 2.1 检索层指标（M5 输出）

四个核心指标，各司其职：

| 指标 | 含义 | 计算方式 | 关注点 | 主要用途 |
|---|---|---|---|---|
| **Context Recall** | 标准答案所需信息在检索结果中的覆盖率 | ground_truth 中有多少事实点出现在 retrieved contexts 里 | 召回够不够，会不会漏关键信息 | 召回层改动的核心对比指标 |
| **Context Precision** | 检索结果中相关 chunk 的比例 | top-k 里有多少 chunk 是真正相关的 | 噪音多不多，会不会把无关文档塞给 LLM | 窗口大小 / 召回质量的辅助指标 |
| **nDCG@k** | 排序质量的标准化单一数字 | 用 per-chunk 相关度（LLM 裁判给的 score）算 DCG/IDCG，零额外成本 | 整体排序好不好，相关块排得够不够靠前 | **排序层改动的核心对比指标** |
| **Gold Rank**（诊断） | 每个 gold fact 最早出现在第几块 | 对每个 key_fact，在检索结果中找第一个命中的 chunk，记录其排名；输出 avg/median/min/max + top-K 覆盖率曲线 | 排序质量诊断——事实排得够不够靠前，top5 是否够用 | 定位问题、指导优化方向 |

> 参考：RAGAS Context Precision / Context Recall。但 RAGAS 默认用英文模型判分，**中文场景需要自己接 DeepSeek flash 当裁判**（见 §4）。

**不做的指标**：
- **Hit Rate@k**：信息密度低于 Context Recall（只看有没有、不管覆盖率），冗余，不实现。
- **MRR**：只看第一个相关块，信息太少，由 nDCG 替代（考虑所有相关块位置 + 相关度分级）。

### 2.2 生成层指标（M6 输出）

| 指标 | 含义 | 计算方式 | 关注点 |
|---|---|---|---|
| **Faithfulness**（忠实度） | 答案是否只基于检索上下文，有没有编造 | 答案中的每个陈述能否在 retrieved contexts 中找到依据 | 会不会幻觉 |
| **Answer Relevance**（答案相关性） | 答案有没有答非所问 | 答案与问题的语义相关度 | 会不会跑题 |
| **Correctness**（正确性） | 答案与标准答案的事实一致性 | 关键事实点是否一致 | 答得对不对 |

> Faithfulness 和 Answer Relevance 是 RAGAS 核心指标。Correctness 更严格，需要 ground_truth 做对照。

### 2.3 端到端指标（综合）

| 指标 | 含义 | 用途 |
|---|---|---|
| **综合得分** | 加权：faithfulness×0.3 + context_recall×0.25 + answer_relevance×0.25 + correctness×0.2 | 单一数字对比版本 |
| **引用准确率** | 答案中的 [n] 引用是否真的能支撑对应陈述 | 引用系统质量（M6 引用标注 + M5 检索共同决定） |
| **拒答率** | 系统正确拒绝回答（「材料不足」）的比例 | 兜底能力 |

### 2.4 指标选型说明

- **不用 BLEU / ROUGE**：中文 RAG 答案表述灵活，词重叠指标无意义。
- **不用 BERTScore / 余弦相似度**：语义相似但事实相反会得高分，不可靠。
- **用 LLM 裁判**：这是当前 RAG 评测的事实标准。DeepSeek v4-flash 便宜、速度快、中文能力够用，作为裁判模型。
- **关键事实点人工核对**：LLM 裁判有偏见，**每个版本的核心数据必须人工抽检 20% 验证**（验证裁判本身的可靠性）。

---

## 3. 中文测试集设计

> 测试集质量直接决定评测可信度。宁少勿滥——30 道高质量题 > 300 道随便凑的题。
> 测试语料（知识库文档）与测试题集的详细设计规范见 [M9_testset.md](M9_testset.md)。以下为概要。

### 3.1 测试集规模与分类（v1.0 = 50 题）

| 类别 | 数量 | 难度 | 说明 |
|---|---|---|---|
| **事实问答（单文档）** | 15 | ★☆☆ | 答案在单个文档的明确段落里 |
| **事实问答（跨文档）** | 10 | ★★☆ | 需要综合 2+ 篇文档的信息 |
| **专名 / 术语查询** | 8 | ★★☆ | 测试中文专名检索质量（对应 M5 v1.5 预处理） |
| **对比 / 差异问答** | 5 | ★★★ | "A 和 B 有什么区别"类问题 |
| **数字 / 表格问答** | 5 | ★★☆ | 答案是具体数字，来自表格（对应 v5.0.1 表格链路） |
| **综述 / 总结类** | 4 | ★★★ | 需要归纳多个段落的信息 |
| **无法回答（拒答）** | 3 | ★★☆ | 材料中没有答案，系统应拒答而非编造 |

**合计 50 题**。覆盖当前默认库的所有文档类型（PDF 含表格 / DOCX / MD）。

### 3.2 单条测试用例格式

```json
{
  "id": "q001",
  "category": "fact_single",
  "question": "客户投诉的分级标准是什么？",
  "ground_truth": "投诉分为三级：一级（紧急，24小时内响应）、二级（重要，48小时内响应）、三级（一般，5个工作日内响应）。",
  "key_facts": [
    "投诉分为三级",
    "一级=紧急=24小时内响应",
    "二级=重要=48小时内响应",
    "三级=一般=5个工作日内响应"
  ],
  "must_have_docs": ["客户服务投诉处理SOP.md"],
  "difficulty": "easy"
}
```

**字段说明**：
- `key_facts`：标准答案拆解为原子事实点，用于 context recall 和 correctness 的细粒度判分。
- `must_have_docs`：人类标注的「回答此题必须引用的文档」，用于引用准确率计算。
- 不要求 `ground_truth` 措辞完美——关键是事实点完整，LLM 裁判看语义不看措辞。

### 3.3 测试集存放

```
backend/tests/
  testset_default.json        # 默认库 50 题
  testset_small.json          # 快速冒烟集（10 题），供每次提交跑
reports/                      # 评测输出（gitignore，关键版本存档）
  run_v5.2_baseline.json
  run_v5.2_ablate_no_graph.json
  ...
```

> 测试集入 git（测试集 = 代码资产）；报告目录入 `.gitignore`（运行产物），但里程碑版本的报告会在 CHANGELOG 里记录关键数据。

---

## 4. 技术实现方案

### 4.1 总体架构

```
测试集 JSON → 评测 Runner → 逐题调用检索/生成 → LLM 裁判打分 → 汇总报告
                                                         ↑
                                                    DeepSeek flash
                                                    （同生成模型，
                                                     单独一个 client）
```

**纯旁路设计**：不修改 M5/M6/M7 的任何业务代码，通过它们的现有接口（或直接调用模块函数）获取输出。

### 4.2 调用方式（两种模式）

| 模式 | 调用对象 | 用途 | 速度 |
|---|---|---|---|
| **模块直调** | 直接 `import` M5 `retriever.retrieve()` / M6 `orchestrator.answer()` | 精确评测单模块，ablation 研究 | 快（跳过 HTTP） |
| **API 模式** | 调 M7 `POST /answer` 完整接口 | 端到端评测，含 SSE 解析 | 慢（含网络开销） |

**v1.0 先做模块直调**——速度快、迭代方便。API 模式作为 v1.1 增强。

### 4.3 LLM 裁判实现

**方案：DeepSeek v4-flash 当裁判**

理由：
- 已经在用，零额外接入成本；
- flash 便宜（0.1¥/百万 token 级别），50 题评测成本几毛钱；
- 中文能力比通用开源模型强；
- RAGAS 的内置 LLM 裁判提示词可以直接迁移。

**实现要点**：
- 每个指标独立一次 LLM 调用（faithfulness / context_recall / answer_relevance / correctness 各一次），提示词参考 RAGAS 但**中文化**。
- 打分用 0-1 连续值 + 简短理由，方便事后排查。
- 加缓存：同一题 + 同一答案 + 同一指标，结果缓存到本地 JSON，重复评测不重复花钱。
- **裁判自验证**：v1.0 落地时，人工抽检 10 道题的裁判结果，计算「人 vs 裁判」一致性——如果低于 80%，说明提示词或模型有问题，需要调优后再正式使用。

**已知偏差（2026-09-27 两库重跑 × 20 题人工抽检发现）**：

1. **judge 数值幻觉**：precision 判定把标准答案的 78.5% 幻读成 68.5%（4 处，reason 自述「与标准答案 68.5% 不一致」），根因是 `context_precision.py` 的 `CHUNK_PROMPT_TEMPLATE` 将 `ground_truth[:500]` 摘要直接喂给裁判（[context_precision.py:58](backend/app/m9_eval/metrics/context_precision.py#L58)）。**实测标准答案（testset 的 ground_truth + key_facts）与 sparse 全库均只有 78.5%**，68.5 是裁判复制数字时出错。影响：仅污染 reason 叙述，`relevant/score` 判定本身不受影响。
2. **judge 无条件过宽**：抽检 20 题发现 5 处（CS-FC-001 / CS-PN-001 / CS-SM-001×2 / CS-TN-003 / adm_q008），reason 自述「上下文中未出现 / 无等价表述 / 无法推导」却仍判 hit=0.9+。成因：① recall SYSTEM_PROMPT 允许「明确的等价表述就算命中」——合理宽松设计，容忍 paraphrase；② LLM answer-bias：对上下文「拎半相关即判中」；③ `score>=0.5` 一刀切、无 partial 档。影响：recall 绝对值轻微虚高（CS-TN-003 单题实质 R 0.667→0.333，其余每题 ~0.1-0.25/fact），但**对「重跑无波动 / 横向 A/B 对比」结论不构成威胁**——历史基线与重跑用同套裁判（同 prompt/模型/缓存），虚高同幅作用于两侧。
3. **修复分级（v5.22 已实施 P0 + P1，P2 未做）**：
   - **P1 已落地**（precision 数字纪律）：`context_precision.py` 不再把 `ground_truth[:500]` 整段喂裁判（68.5 幻读根源），改 `build_gt_points` 归一化 = 要点句 + 显式【关键数值】清单（`extract_number_facts` 确定性提取）+ prompt「数字纪律」。缓存版本 `p1_norm_gt`。**验证：两库重跑 precision reason 含 68.5 处数 0（修复前 4），judge 显式按清单逐字核对**。
   - **P0 已落地**（recall 证据强制）：`context_recall.py` SYSTEM_PROMPT 要求 hit 必引原文证据 + 代码层 `_enforce_evidence` 兜底（`_PROMPT_VERSION=p0_evidence`）。**验证：原人工标注 6 处无条件过宽全部降 miss，`[证据强制校正]` 触发 27 次（v5.22 粗校版）**。
   - **落地边界（已精准化，v5.22.1）**：v5.22 粗校版「有否定词即降 miss」误杀恰 20 处（客服 12 / 行政 8，v5.22.1 逐一核对恢复）——有块引用证据却夹带免责否定（CS-TN-002「[1]表格列出华南168为最高…其他/总部未给出」）、逐字命中（adm_q011 领用方式/价值）、负向断言 type（CS-CP-004/adm_q010×2，命中证据恰是「未出现/X未定义」）、算术/映射推导型（CS-TN-001/004、CS-SM-001 问题1、adm_q014）。**v5.22.1 把降级收窄为「空洞否定」**：仅当 reason 自述否定且无任何证据信号（数值锚点非否定出现 / 推导缺口基础单位 / `上下文[N]` 块引用 / ≥4 字核心词组非否定出现）才校正 miss，负向断言型 fact 整体豁免（`_judge_fact` → `_enforce_evidence` + `_primary_nums`/`_num_found_affirmed` 三轨/`_block_affirmed`/`_core_needles`）。**验证**：27 例离线回归人工标签 STRICT 20/20、SOFT 5/7；两库重跑校正 27→5/2，recall 0.7778→0.8626 / 0.8571→0.9321（+8.5pt/+7.5pt），误杀恢复；剩余 7 次校正皆符合人工标注（CS-FC-001 / CS-FC-005 Q3 / CS-TN-003 / adm_q004 / adm_q008 真实缺口；CS-PN-003 / CS-SM-001 问题3 概念归纳边界）——留档见 CHANGELOG v5.22.1 + `tests/reports/probe/P0P1_precise_rerun_contrast_20260927.md`。
   - **P2 未做**（partial 0.5 档，降低 score>=0.5 一刀切）。

> 逐题抽检细节见 [`tests/reports/checklists/human_checklist_rerun_20260927.md`](tests/reports/checklists/human_checklist_rerun_20260927.md)（20 题 + 汇总发现表）。

### 4.4 代码结构（`app/m9_eval/`）

| 文件 | 职责 |
|---|---|
| `runner.py` | CLI 入口：`--testset` / `--mode`(retrieval|e2e) / `--ablate` / `--report` |
| `testset.py` | 加载测试集、校验格式、按 category 分组 |
| `metrics/` | 各指标实现 |
| `metrics/context_recall.py` | 检索召回率（LLM 裁判 + key_facts 对照） |
| `metrics/context_precision.py` | 检索精确度 |
| `metrics/gold_rank.py` | Gold Rank 诊断（每个 fact 最早出现的排名，词汇模式 + LLM 模式） |
| `metrics/ndcg.py` | nDCG@k 排序质量指标（从 CP 的 per_chunk score 推导，零额外成本） |
| `metrics/faithfulness.py` | 答案忠实度 |
| `metrics/answer_relevance.py` | 答案相关性 |
| `metrics/correctness.py` | 答案正确性（需 ground_truth） |
| `metrics/citation_accuracy.py` | 引用准确率 |
| `judge.py` | LLM 裁判封装（DeepSeek flash + 缓存 + 重试） |
| `ablation.py` | ablation 配置与多轮对比报告生成 |
| `report.py` | 汇总指标 → JSON + 人类可读 Markdown |

### 4.5 与现有模块的接口

**不侵入业务代码**。评测层只读 M5/M6 的公共接口：

```python
# M5 接口（retriever.py）
from app.m5_retrieve.retriever import retrieve
results = await retrieve(query=q, rag=deps.rag, sparse_index=deps.sparse_index,
                         mode="mix", top_k=8)

# M6 接口（orchestrator.py）
from app.m6_generate.orchestrator import answer
answer_result = await answer(query=q, contexts=results, ...)
```

如果 M5/M6 的接口变了，M9 跟着改——因为 M9 是消费者，不是生产者。

### 4.6 评测 workspace 与索引版本纪律

> **背景（2026-09-26）**：bge-m3 sparse 是一次性构建产物（`m5_sparse.json`），建完即冻结 content，不随代码/ chunk jsonl 前进。v5.10 曾为测量列名前缀在旁路 workspace `eval_cservice_v510_ws` 上临时重建，评测结果有效但**业务库 `eval_cservice_ws` 停在 v5.13 之前（无前缀），用标准命令 `--collection eval_cservice` 重跑复现不出 v5.13 报告数字**。

**评测命令约定：**

1. **必须显式指定 workspace**：`--workspace <ws>` 直接传入（绕过 `collection→workspace` 硬编码映射）。依赖硬编码映射有落错库风险。
2. **报告自带 workspace 自证**：报告 `config.workspace` 字段记录实际所用库。归档/对比报告时先核对这一字段，确保两个报告可比。
3. **索引层改动后重建语义**：评测若依赖新索引内容，先按 [M3_index.md §3.5](M3_index.md) 全链路重建标准库，再跑评测；不另建旁路 workspace（见项目根 CLAUDE.md 探底纪律第 4 条）。
4. **标准命令的已知矩阵**（runner.py `collection→workspace` 映射）：`default→default_ws`、`eval_admin→eval_admin_ws`、`eval_cservice→eval_cservice_ws`。其余 `collection` 按 id 即 workspace。

---

## 5. Ablation Study 方案

> 这是最有说服力的部分——用数据证明「系统每一层改动都有可度量收益」。

### 5.1 实验设计（7 组对比）

| 实验组 | 配置 | 验证的假设 |
|---|---|---|
| **A. baseline** | 纯向量召回（naive）+ 直接生成（无 rerank） | 基线，最朴素的 RAG |
| **B. + 图召回** | 图+向量（mix）+ 直接生成 | 图召回是否提升召回质量 |
| **C. + 关键词路** | 三路召回（图+向量+关键词）+ RRF 融合 | 关键词路 + RRF 是否有用 |
| **D. + rerank** | 三路 + RRF + bge-reranker 精排 | rerank 的增益有多大 |
| **E. + query 预处理** | D + M5 v1.5 同义词扩展 + 专名加权 | 中文 query 预处理的效果 |
| **F. - 图召回**（消融） | 向量+关键词 + rerank（去掉图路） | 验证图召回的边际贡献 |
| **G. - rerank**（消融） | 三路 + RRF（去掉 rerank） | 验证 rerank 的边际贡献 |

**核心结论应该是什么样的**（假设）：
- 从 A 到 E，分数逐步提升，每一步都有正向增益；
- 图召回对「关系类 / 跨实体」问题提升最大，对「单事实点」问题提升有限；
- rerank 对 precision 提升明显，对 recall 影响小；
- query 预处理对专名问题提升大，对普通问题影响中性。

### 5.2 如何做 ablation（不改业务代码）

在 M9 runner 里用配置控制：

```python
# ablation 配置示例
ABLATIONS = {
    "baseline": {"graph": False, "keyword": False, "rerank": False, "query_prep": False},
    "plus_graph": {"graph": True,  "keyword": False, "rerank": False, "query_prep": False},
    "full":       {"graph": True,  "keyword": True,  "rerank": True,  "query_prep": True},
}
```

检索时按配置决定调用哪几路、是否走 rerank。**完全在 M9 内部实现**，不动 M5 的业务代码。

### 5.3 结果呈现

**主表（README 展示用）**：

| 配置 | Context Recall ↑ | Faithfulness ↑ | Answer Relevance ↑ | Correctness ↑ | 综合分 ↑ |
|---|---|---|---|---|---|
| 纯向量（baseline） | 0.xx | 0.xx | 0.xx | 0.xx | 0.xx |
| + 图召回 | 0.xx | 0.xx | 0.xx | 0.xx | 0.xx |
| + 关键词路 + RRF | 0.xx | 0.xx | 0.xx | 0.xx | 0.xx |
| + rerank | 0.xx | 0.xx | 0.xx | 0.xx | 0.xx |
| + query 预处理 | 0.xx | 0.xx | 0.xx | 0.xx | 0.xx |

**分类明细表（内部用）**：按 7 个题型分别统计，看每类问题的增益分布——用于指导后续优化方向。

---

## 6. 里程碑与验收

### 6.1 实施路线（分三阶段）

#### Phase 1：骨架 + 检索指标（~2 天）
- 建 `app/m9_eval/` 目录与 `runner.py` / `testset.py`
- 构造 **10 题冒烟集**（覆盖主要题型）
- 实现 **context_recall** 和 **context_precision** 两个检索指标
- 跑通 baseline（纯向量）vs 当前配置的对比
- **验收**：10 题能跑通，输出 JSON 报告，两个指标有数值

#### Phase 2：生成指标 + 完整测试集（~3 天）
- 构造 **50 题完整测试集**
- 实现 faithfulness / answer_relevance / correctness 三个生成指标
- 实现 citation_accuracy（引用准确率）
- 加 LLM 裁判缓存
- 人工抽检 10 题验证裁判一致性（≥80%）
- **验收**：50 题全量评测耗时 < 10 分钟（含生成 + 裁判），报告有全部 6 个指标 + 分类统计，裁判一致性通过

#### Phase 3：ablation + 回归门禁（~2 天）
- 实现 7 组 ablation 自动对比
- 输出对比表格 + Markdown 报告
- 写 README 评测结果章节
- 加 `make evaluate` / `./eval.sh` 一键命令
- **验收**：一条命令跑完全部 ablation，输出对比表；README 有量化数据展示

**合计约 7 天**（兼职节奏，每天 2-3 小时的话 ~2 周）。

### 6.2 验收清单（v1.0）

- [ ] 50 题中文测试集，覆盖 7 类题型，格式校验通过
- [ ] 6 个核心指标全部实现：context_recall / context_precision / faithfulness / answer_relevance / correctness / citation_accuracy
- [ ] LLM 裁判人工抽检一致性 ≥ 80%
- [ ] 7 组 ablation 对比可一键运行，输出结构化对比报告
- [ ] 单次全量评测（50 题 × 4 指标）成本 < 1 元（DeepSeek flash）
- [ ] 评测结果已写入 README 显眼位置
- [ ] CHANGELOG 登记 v1.0 评测基线数据

---

## 7. 关键决策与权衡

### 7.1 用 LLM 裁判还是传统指标？

**选 LLM 裁判**。理由：
- 中文 RAG 的答案开放性高，词重叠指标（BLEU/ROUGE）无意义；
- 语义相似度指标（BERTScore）无法区分「事实正确」和「措辞相似但事实相反」；
- LLM 裁判是当前业界事实标准，RAGAS / TruLens 等框架都用这个方案；
- 成本可接受——50 题全量评测几毛钱。

**风险**：裁判本身有偏差。缓解：①人工抽检校准；②同一版本用同一模型同一提示词，保证前后可比（相对值靠谱 > 绝对值精准）。

### 7.2 测试集谁来出？

**自己出**——就用当前默认库的文档出题。理由：
- 50 题量不大，手动构造质量可控；
- 这是「回归评测」不是「排行榜评测」——不需要第三方独立测试集，只要同一套题测不同版本，能看出相对变化就行；
- 未来加垂直场景时，再对新库单独建新测试集。

### 7.3 为什么不直接用 RAGAS 库？

RAGAS 是好工具，但**不直接用**，核心原因：
1. **中文适配差**：RAGAS 的 prompt 和内置指标都是英文-centric，直接用中文数据会有偏差；
2. **太重**：引入一堆依赖（langchain / datasets 等），而我们只需要 5-6 个指标的核心逻辑；
3. **侵入性**：RAGAS 假设你的 pipeline 是 LangChain 式的，我们的自研 M5/M6 接口需要额外适配。

**做法**：参考 RAGAS 的指标定义和提示词设计，但**自己实现精简版**——提示词中文化、直接对接我们自己的 M5/M6 接口、依赖最少（只需要 DeepSeek client，而这已经在 M0 里了）。

### 7.4 评测频率？

- **每次改 M5/M6 相关逻辑**：跑 10 题冒烟集（~2 分钟），确认没劣化；
- **每个版本发布前**（如 v5.3 / v6.0）：跑 50 题全量 + 记录到 CHANGELOG；
- **ablation study**：v1.0 做一次完整的，后续大改检索策略时再重跑。

### 7.5 Gold Rank 的定位：诊断工具，不是精确指标

**Gold Rank 是排序质量诊断工具，不用于版本间的精确对比。** 原因：

- **双模式设计**：词汇模式（默认，零成本）+ LLM 精确模式（flag 可选）。
- **词汇模式的能力边界**：对数值型事实（含明确数字 token）命中率高，对推导型/纯文本事实大量漏检。实测 v5.9 基线 top5 覆盖率仅 52%（而 LLM recall 是 92%）。**v5.23 已强化数值侧**：数字边界正则 + 分隔符归一化 + 结论位/强锚/弱锚分类后，客服 35 题 top8 事实覆盖率 0.517→**0.838**、gold_rank_avg 2.08→**1.71**；**v5.23.3 同口径补行政库** 30 题 top8 覆盖率 0.345→**0.914**、gold_rank_avg 2.04→**1.59**（两评测库统一，judge 判据零改动）。**推导型边界仍成立**：结论数值不出现（如推导型「增长约 1.29 倍」）词法无法支持，仍需 LLM 模式。
- **正确用法**：
  - ✅ 看版本间 gold_rank 的**趋势变化**（avg_rank 降了/升了）
  - ✅ 看 top-K 覆盖率曲线的**形状**（top1/top3/top5 分别多少，判断窗口是否够用）
  - ✅ 定位「哪些 fact 排得靠后」，指导排序优化方向
  - ❌ 不要拿 gold_rank top5 覆盖率当 recall 用（严重低估）
  - ❌ 不要跨题型比较绝对值（数字型 vs 文本型命中率差异巨大）
- **双窗口评测（top5 + top8）**：一次评测同时出两套 recall/precision，零额外检索成本，recall 约 +50% LLM 调用。用于回答「top5 够用吗」这类窗口敏感性问题。

### 7.6 nDCG：排序质量的标准化单一对比数字

**nDCG 是版本间排序质量对比的首选单一数字。** 与 Gold Rank 的定位互补：

| 维度 | nDCG | Gold Rank |
|---|---|---|
| 粒度 | 查询级（一个 query 一个分数） | 事实级（每个 fact 一个排名） |
| 计算方式 | 从 CP 的 per-chunk 相关度算 DCG/IDCG | 逐 fact 找最早命中 chunk |
| 成本 | 零额外 LLM（纯后处理） | 词汇模式零成本，LLM 模式贵 |
| 用途 | 版本间排序质量的单一对比数字 | 诊断排序细节，定位哪些 fact 排得靠后 |
| 对推导型事实 | 准确（基于 LLM 裁判的相关度） | 词汇模式漏检多，LLM 模式才准 |

**不做 MRR 的原因**：MRR 只看第一个相关块的倒数排名，信息太少——nDCG 考虑所有相关块的位置 + 相关度分级，是更全面的排序质量指标。MRR 从未在代码中实现，直接用 nDCG 替代。

**公式**：DCG@k = Σ(2^rel_i - 1) / log2(i+1)，nDCG@k = DCG@k / IDCG@k（理想排序下的 DCG）。相关度直接用 LLM 裁判给的 per-chunk score（0~1 连续值）。

### 7.7 LLM listwise 终审（`--reranker llm`）：正式使用

**解决的问题**：cross-encoder（bge-reranker-v2-m3）对数字型/专名表格语义失明——表块能进 `fused_top40` 候选池，但被低分压出 top5。v5.21 前四类手段（前缀 v5.10 / 特征 boost v5.11-12 / NL 摘要 v5.13 / sparse 注入 v5.20）都只改变分数量级、不改变排名结构。

**机制**：把 `fusion.fused_top40` 前 `LLM_POOL_SIZE=20` 块交给 LLM listwise 重排（RankGPT 式），取前 `eval_top_n` 作为评测上下文，绕过 cross-encoder 失明。

**启用方式**：

```bash
cd backend && python -m app.m9_eval.runner \
  --testset testsets/cservice.json --report reports/run_xxx_llm.json \
  --reranker llm
```

**效果（v5.21，行政库 30 题）**：

| 指标 | v5.20 baseline | --reranker llm |
|---|---|---|
| nDCG@5 | 0.8567 | **0.9479** |
| Re@5 | 0.9524 | **0.9857** |
| gold_rank avg | 2.04 | **1.76** |

**决策边界（v5.21 拍板，本模块不改变它）**：
- **仅 M9 评测可选**——不进入生产检索路径（M5），`RERANK_TOP` 生产仍为 5；
- **不接 answer 链路**（流式不兼容 + 评测同源偏置风险 + 生产 token 成本）；
- 生产侧以 v5.20 baseline 为准，输入侧改造已封顶（详见 [`RETRIEVAL_OPTIMIZATION.md`](RETRIEVAL_OPTIMIZATION.md) 组 C/E）。

**归属**：`app/m9_eval/llm_rerank.py`（`rerank_with_llm`），runner 接线于 `evaluate_retrieval`。

---

## 8. 评测结果快照（按库）

### 8.1 办公行政库（eval_admin）检索基线（2026-09-25，M9 v1.5 → v1.7）

15 题最小集（`testsets/testset_admin_15.json`，建库 3 篇 117 chunks）retrieval 模式评测（`tests/reports/retrieval/history/run_retr_admin_baseline.json`，双窗口 top5/top8）：

| 指标 | top5 | top8 | 说明 |
|---|---|---|---|
| Context Recall | **0.9405** | 0.9487 | 关键事实在检索结果中的覆盖率 |
| Context Precision（加权） | **0.5878** | 0.5767 | top-k 中相关块占比（按答案相关度加权） |
| nDCG | **0.8518** | 0.8712 | 排序质量的标准化单一数字 |
| gold_rank avg | 1.67 | — | 命中 fact 最早出现在第几块的平均值 |
| gold_rank top1/3/5 覆盖率 | — | 0.176 / 0.240 / 0.261 | **词汇模式诊断，不混用为 recall** |

gold_rank top5 覆盖率仅 0.261（客服库 v5.9 基线 52%）——行政库事实以纯文本为主（「以旧换新」「台账管理」等），数字 token 少，词汇模式漏检多，符合 [§7.5](M9_evaluation.md) 已知边界。

**逐题检索质量发现（四大问题，均为检索/语料层问题，非评分问题）**：
- **q013（表碎片化）**：A1 部门配纸量表被切分到多个 chunk，「20-50 人=4 箱」行与「50 人以上」行错位，nDCG@5 0.76。与客服库 v5.13 table_nl_summary 解决的问题同源——行政库表格链路待补表格语义摘要。
- **q004（跨表依赖）**：城市分级表未被随住宿表召回，recall 0.5（缺「上海属于一类城市」fact）。城市分级（表 A）与住宿标准（表 B）是两张表，需跨表关联。
- **q010（字符串命中）**：抓到 A3「关联文件」块（FIN-FAS-2024-002 字符命中），prec 0.2——正是该干扰题想验证的场景，系统确实会被字符命中骗到，但凭此块不足以作答，不影响 GT 判断。
- **q015（拒答前提）**：prec 0.0（无相关块）→ 拒答前提成立，e2e 时验证系统正确拒答。

报告：`backend/tests/reports/retrieval/history/run_retr_admin_baseline.json`。e2e 生成四指标评测留待下一轮。

### 8.1a 表格 NL 摘要激活后复测（2026-09-25，M9 v1.6 → v1.7）

**背景**：v5.16 对 q013 的「建库未套用 table_nl_summary」推断经排查**修正**——真正根因是稀疏索引缺 `block_type` 元数据（M7 `build_workspace_deps` 重建 sparse 漏传 `chunks_dir`，`sparse_index.build` 从不读 chunk jsonl 顶层的 block_type），导致 `build_rerank_text` 对 admin 表格块从未触发 NL 摘要注入。修复 + 重建后复测（`tests/reports/retrieval/history/run_retr_admin_nl.json`）：

| 指标 | v5.16 基线 | v5.17（NL 摘要激活） | Δ |
|---|---|---|---|
| Context Recall@5 | 0.9405 | **0.9583** | +0.0178 |
| Context Precision（加权）@5 | 0.5878 | **0.6122** | +0.0244 |
| nDCG@5 | 0.8518 | **0.8649** | +0.0131 |
| gold_rank avg | 1.67 | **1.50** | -0.17 |

- **最大受益题** `adm_q012`（comparison，城市分级↔住宿标准关联）：nDCG@5 **0.8291 → 0.9877**、加权 Prec@5 0.3285 → 0.6569——NL 摘要注入后 cross-encoder 对表格块语义匹配显著提升，是能力已激活的直接证据。
- **边界**：`adm_q013`/`adm_q014`（table_numeric）排序逐项不变——q013 碎片化的本质是 **M2 行级切分**（配纸量表拆成 3 行 + 1 行的块），NL 摘要提供的是每个碎片的局部摘要，不跨行补全；recall@5 均 1.0 证明事实未被漏掉，属排序质量问题。与客服库 v5.13 探底结论一致。

详见 [CHANGELOG](../CHANGELOG.md) v5.17。

### 8.1b 行政库 e2e 生成质量评测（2026-09-25，M9 v1.7 → v1.8）

15 题最小集全量 e2e（`tests/reports/e2e/history/run_e2e_admin_15.json`，judge_failed=0，耗时 556.4s≈9.3 分钟 < 10 分钟验收线），生成输入为 v5.17 NL 摘要激活后的检索结果：

| 指标 | e2e 值 | 客服库 50 题参考 | 说明 |
|---|---|---|---|
| Faithfulness | **0.9277** | 0.9194 | 答案对检索上下文的忠实度 |
| Answer Relevance | **0.9000** | 0.9390 | 答案与问题的相关度 |
| Correctness | **0.9373** | 0.8353 | 与标准答案的事实一致性 |
| Citation Accuracy | **0.8928** | 0.8465 | [n] 引用支撑对应陈述的准确率 |
| Context Recall@5 | 0.9583 | 0.9601 | 关键事实覆盖率（检索侧，与 v5.17 复测一致） |
| Context Precision（加权）@5 | 0.6122 | 0.5120 | top5 相关块占比（加权） |

> 两库题型/题数不同，数值不作横向排名——仅作为「同一套系统参数在第二垂直域端到端表现正常」的泛化信号。

> ⚠️ **口径注记（2026-10-06）**：上表「客服库 50 题参考」列为 **v5.15 旧口径**（Context Recall 0.9601 为虚高值，早于评测口径修复）；当前口径重跑 Context Recall **0.8**，详见 [M9_testset.md](M9_testset.md) §8 与 [CHANGELOG](../CHANGELOG.md) v5.36。

**逐题要点**：
- **拒答验证通过** `adm_q015`（unanswerable）：材料无年假天数事实（v5.16 检索诊断 prec@5 0.0「拒答前提成立」→ e2e 正确拒答），correctness **1.0**。
- **干扰题行为正确** `adm_q010`（FAS）：材料仅含编号片段无业务定义，系统如实作答（correctness 1.0），answer_relevance 0.2 属该题设计预期（检索不被字符命中骗到即达标）。
- **检索缺口传导** `adm_q004`（跨表依赖）：城市分级表未随住宿表召回 → correctness **0.5**（缺「上海属一类城市」），检索缺口如实反映到生成分数，指标链路有效。
- **faithfulness 最低 `adm_q011` 0.57**：剩余短板，待人工抽检区分「裁判严判 vs 真实幻觉」。

详见 [CHANGELOG](../CHANGELOG.md) v5.18。

### 8.1c 行政库 30 题全量检索基线（2026-09-25，M9 v1.9）

六篇文档完整版（203 chunks）+ 30 题测试集 v0.2 全量 retrieval 模式（`tests/reports/retrieval/history/run_retr_admin_30_baseline.json`，733.4s）。top8 窗口与 gold_rank 同跑（零额外检索成本）：

| 指标 | 30 题 | 15 题 NL 摘要基线（v5.17） | 说明 |
|---|---|---|---|
| Context Recall@5 / @8 | 0.9524 / 0.9524 | 0.9583 | 双窗口同值，30 题中关键事实早入 top5 |
| Context Precision（加权）@5 / @8 | 0.619 / 0.536 | 0.6122 | @8 加权下降 = 扩展窗口引入更多噪声块 |
| nDCG@5 / @8 | 0.8567 / 0.9019 | 0.8649 | @8 排序好于 @5，长窗口允许后续相关块归位 |
| gold_rank avg / median | 2.04 / 1.98 | 1.50 | 上升系题型结构变化（新增跨文档/表格题），非检索回归 |

**拒答前提成立**：unanswerable 题（q029 年度经营目标 / q030 CEO 姓名）prec@5 = 0.0，检索未召回任何可答片段，与 e2e 拒答行为自洽。

> ⚠️ **口径注记（2026-10-06）**：上表为 **v5.19 旧口径**数值（早于评测口径修复），**Context Recall@5 0.9524 为修复前虚高值**，与当前口径不可横比。当前口径重跑（`tests/final_results/run_retrieval_admin_30_20261006.json`，289.8s）：Context Recall **0.8363** / Precision 0.42 / 加权 Precision 0.5937 / nDCG@5 **0.8935** / gold_rank 1.39（中位 1.38）/ 事实覆盖率 top1 0.6661 · top3 0.8476 · top5 0.878；**nDCG/gold_rank 较旧值改善**，证明为口径修复而非链路退化。按类目 recall：`fact_single` 0.9394 ｜ `table_numeric` 0.8133 ｜ `proper_noun` 0.8125 ｜ `fact_cross_doc` 0.775 ｜ `comparison` 0.6666 ｜ `unanswerable` 0.0。口径变更详见 [CHANGELOG](../CHANGELOG.md) v5.36/v5.37 与 [retrieval_comparison.md](../../backend/tests/reports/retrieval_comparison.md)「口径变更说明」。三库横向汇总见 [`eval_results_summary.md`](../../backend/tests/final_results/eval_results_summary.md)。

### 8.1d 行政库 30 题全量 e2e + 裁判一致性抽检（2026-09-25，M9 v1.9）

30 题全量 e2e（`tests/reports/e2e/history/run_e2e_admin_30.json`，1298.9s ≈ 21.6 分钟，judge_failed=0），与 15 题基线 / 客服库 50 题参考：

| 指标 | 30 题 | 15 题基线 | 客服库 50 题参考 | 说明 |
|---|---|---|---|---|
| Faithfulness | **0.884** | 0.9277 | 0.9194 | 新题含更多跨文档/数值题，简短答案内引用易漏 |
| Answer Relevance | **0.9333** | 0.9000 | 0.9390 | 与客服库同级 |
| Correctness | **0.9713** | 0.9373 | 0.8353 | 当前三个库/版本最高 |
| Citation Accuracy | **0.8187** | 0.8928 | 0.8465 | 表格拆碎 + 跨文档交叉引用增多拉低 |

**裁判一致性人工抽检（10 题样本，验收线 ≥80%）= 10/10 = 100% 达标**（`tests/reports/checklists/human_checklist_run_e2e_admin_30.md`）：
- 平均 |人工−裁判| 0.049、最大 0.15（adm_q010），与客服库校准后抽检（0.043/0.15）同级。
- **低分题评价一致**：adm_q016（裁判 0.60 / 人工 0.50——制度名+适用岗位全对，但标准/弹性/综合工时制的量化要求 3 处缺失）；adm_q008（0.89 / 0.75——5 事实点缺赔偿标准）。
- **拒答与干扰题评价一致**：adm_q029 正确拒答 1.0、adm_q010 干扰项正确拒答 1.0（裁判与人工同判）。
- **检索缺口暴露（记录供检索优化）**：① q008 差旅设备丢失的赔偿标准（A3 6.2 表格 chunk [30][31]）未召回 top8 → 系统诚实拒答该细节；② q013「每箱=5 包=2500 张」换算事实未随答召回；③ q004 系统误称「材料未直接标注上海城市类别」（实际 [A2] 城市分级表已列），生成层对检索结果利用不充分。
- 残留：人工判定由 Claude（与裁判同源）执行，建议用户抽看 q004/q008/q016 复核。

详见 [CHANGELOG](../CHANGELOG.md) v5.19。

---

## 9. 遗留 / 后续（v2.0 及以后）

1. **更多维度**：latency benchmark（文档量上去后做）、token 成本统计、抗扰动测试（同义改写问题看答案稳定性）。
2. **多库评测**：每个 collection 可以有自己的测试集，评测按库隔离。（行政库已建 own workspace + 测试集，评测 runner 已支持 `--collection`；客服库 / 行政库各自独立跑。）
3. **CI 集成**：接入 GitHub Actions，PR 自动跑冒烟集（当前单机项目不急）。
4. **可视化报告**：把评测结果做成简单的 HTML 仪表盘（当前 JSON + Markdown 够用）。
5. **垂直场景评测**：等 P1 落地垂直场景后，针对该场景建专属测试集（如学术论文 / 产品文档）。

---

## 10. 版本

- **v0.1**（2026-09-21）：初始规划。定义定位、6 个核心指标、50 题测试集方案、7 组 ablation 设计、三阶段实施路线。
- **v1.1**（2026-09-22）：Phase 1 落地。retrieval 模式可用，context_recall + context_precision + LLM 裁判缓存 + 35 题客服业务测试集。
- **v1.2**（2026-09-24）：新增 gold_rank 诊断维度 + 双窗口评测（top5/top8 同跑）。gold_rank 双模式（词汇/LLM），定位为排序质量诊断工具而非精确指标。
- **v1.3**（2026-09-24）：新增 nDCG@k 排序质量指标。从 CP 的 per_chunk score 推导，零额外 LLM 成本；MRR 不实现，由 nDCG 替代。
- **v1.4**（2026-09-25）：Phase 2 落地。e2e 生成四指标：faithfulness / answer_relevance / correctness / citation_accuracy（各自 LLM 裁判 + 缓存）；50 题客服业务测试集全量跑通 + 逐题可追溯（ground_truth/key_facts/retrieval/gen_meta）；裁判一致性人工抽检（10 题，LLM 判定与人工一致性约 7/10，偏差集中在「表述不同但实质覆盖」被裁判低估）。测试集 GT 修正 5 题（CS-FC-005/007、CS-CP-003/004、CS-SM-001，含 CS-CP-004 人工抽检发现 v1.0 指标目标纯属幻觉→GT 改为「不可比」）。
- **v1.5**（2026-09-25）：裁判校准。correctness 裁判 prompt 校准（分母改标准答案事实点 + 语义对齐含中文译名 + 额外正确信息不计入分母 + 部分覆盖按比例），judge 透传 total_facts/correct_facts/incorrect。全量重判 correctness 0.8321→**0.8353**（详见 [CHANGELOG](../CHANGELOG.md) v5.15）。校准后重做人工一致性抽检 **10/10 达标**（|diff|≤0.25 判一致，平均 0.043、最大 0.15；报告 `tests/reports/checklists/human_checklist_run_e2e_cservice_50_v2.md`）。
- **v1.7**（2026-09-25）：行政库表格 NL 摘要激活。排查并修正 v5.16 推断（q013 非「建库未套用摘要」，真因是稀疏索引缺 block_type → M7 `build_workspace_deps` 漏传 `chunks_dir`）；M7 修复 + admin sparse 重建后复测（`tests/reports/retrieval/history/run_retr_admin_nl.json`），Recall@5 0.9405→0.9583、加权 Prec@5 0.5878→0.6122、nDCG@5 0.8518→0.8649，`adm_q012` nDCG 0.83→0.99（能力生效直接证据）；q013/q014 排序不变——表格碎片化确认为 M2 行级切分独立问题。详见 [§8.1a](#81a-表格-nl-摘要激活后复测2026-09-25m9-v16--v17) 与 [CHANGELOG](../CHANGELOG.md) v5.17。
- **v1.6**（2026-09-25）：行政库（eval_admin）首轮检索基线。15 题最小集 retrieval 模式评测出值（Recall@5 0.9405 / Prec@5 0.5878 / nDCG@5 0.8518），逐题检索质量诊断定位四大问题（q013 表碎片化、q004 跨表依赖、q010 字符串命中、q015 拒答前提成立），gold_rank 词汇模式 top5 覆盖率 0.261 印证 [§7.5](M9_evaluation.md) 文档型事实边界。详见 [CHANGELOG](../CHANGELOG.md) v5.16 与本文 §8.1。
- **v1.9**（2026-09-25）：行政库 30 题完整版评测。六文档 203 chunks 建库 + 测试集 v0.2（30 题），全量检索基线（Rec@5/8 0.9524、Prec@5 加权 0.619 / @8 0.5359、nDCG@5 0.8567 / @8 0.9019、gold_rank avg 2.04）+ 全量 e2e（faithfulness 0.884 / answer_relevance 0.9333 / correctness 0.9713 / citation_accuracy 0.8187，judge_failed=0）+ 裁判一致性抽检 **10/10 达标**（平均 |diff| 0.049、最大 0.15）。详见 [§8.1c/8.1d](#81c-行政库30题全量检索基线2026-09-25m9-v19) 与 [CHANGELOG](../CHANGELOG.md) v5.19。
- **v1.8**（2026-09-25）：行政库 e2e 生成质量评测。15 题全量四指标首次出值（faithfulness 0.9277 / answer_relevance 0.9000 / correctness 0.9373 / citation_accuracy 0.8928，judge_failed=0，耗时 9.3 分钟），拒答验证通过（q015 正确拒答）、干扰题行为正确（q010）、检索缺口传导到生成（q004 correctness 0.5）。详见 [§8.1b](#81b-行政库e2e生成质量评测2026-09-25m9-v17--v18) 与 [CHANGELOG](../CHANGELOG.md) v5.18。
- **v1.10**（2026-09-27）：LLM listwise 终审正式化（`--reranker llm`，对应 CHANGELOG v5.21）。新增 §7.7 正式使用小节（机制 / 启用 / 决策边界）；nDCG@5 0.8567→**0.9479**、Re@5 0.9524→**0.9857**、gold_rank avg 2.04→**1.76**。仅 M9 评测启用，不接 answer / 生产 M5。
- **v1.11**（2026-09-27）：judge 可靠性修复 P0 + P1 落地（对应 CHANGELOG v5.22）。P1 precision 数字纪律（归一化标准答案 → 要点句 + 【关键数值】清单，消除 68.5 类数值幻读，两库重跑 0 处）；P0 recall 证据强制（hit 必引原文 + `_enforce_evidence` 兜底，原 6 处过宽全降 miss）。v5.22 粗校版误杀 20 处合法命中（客服 12 / 行政 8，v5.22.1 逐一核对全恢复），**v5.22.1 精准化已落地**（空洞否定才降 miss，27 例离线回归 STRICT 20/20，两库重跑校正 27→5/2、recall +8.5pt/+7.5pt）——详见 §4.3 第 3 条 + CHANGELOG v5.22.1。
- **v1.12**（2026-09-27）：gold_rank 词汇匹配器修复（对应 CHANGELOG v5.23）。`metrics/gold_rank.py` `_lexical_match` 四层修复：数字边界正则（千分位等价 + 防「30」误中「130」）、分隔符归一化（「疏忽大意30%」↔分离表格块跨单元格连续匹配）、文本佐证累计匹配长度、锚定分类（结论位 = 等式右值必中 / 弱锚版本年份差值序数不算强证据）。三态回归 35 题 135 facts：NEW 107/135=79.3%（vs STRICT 找回 43，新增漏仅 2 皆如实缺口：CS-TN-003 推导型 1.29 倍、CS-FC-005 Q3 AHT 等式主结论 top8 无支撑）；伪命中被拒：合计扣分=30、剩余=70 分。两库定向重跑（客服 35 题）：judge 判据零改动（context_recall/precision/ndcg 逐位不变），gold_rank_avg 2.08→**1.71**、top8 事实覆盖率 0.517→**0.838** ——详见 §7.5 词汇模式边界更新 + CHANGELOG v5.23。
- **v1.13**（2026-10-06）：**客服库评测口径回填**（对应 CHANGELOG v5.36，代码零改动）。v5.23/v5.29/v5.30/v5.31 四次口径修复（gold_rank 匹配器、裁判 hit 字段、context_recall 缓存键含完整 context_str、评测窗口 @5）后，旧 recall 判定为修复前口径的虚高值。§8.1b 加口径注记；当前代码重跑 35 题 retrieval（Recall 0.7515 / nDCG 0.9443 / gold_rank 1.6）与 50 题 e2e（Recall 0.8 / Correctness 0.8149 / nDCG 0.9377），nDCG/gold_rank 均较旧值改善证明非链路退化，报告归档 `tests/final_results/`。详见 [CHANGELOG](../CHANGELOG.md) v5.36 与 [retrieval_comparison.md](../../backend/tests/reports/retrieval_comparison.md)「口径变更说明」。
