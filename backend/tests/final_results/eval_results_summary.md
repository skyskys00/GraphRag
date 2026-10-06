# 评测结果总表（三库 × 检索 / e2e）

> **生成**：2026-10-06 ｜ **归档目录**：`backend/tests/final_results/`
> **口径**：全部为**当前口径（v5.31 之后，@5 生产口径）**；旧口径报告见 `history/`，**不可与下表横比**（口径变更详见 [retrieval_comparison.md](retrieval_comparison.md) 顶部「⚠️ 口径变更说明」）。
> **数据源**：各报告 JSON 的 `summary.overall` 字段直读，非手工转录。
> **模式说明**：`检索`（retrieval）= 只跑检索，仅出检索指标；`e2e` = 检索 + 生成 + 裁判，检索与生成指标同出。

## 0. 跨库总览

| 库 | collection | 检索题数 | 检索 Recall | 检索 nDCG@5 | e2e 题数 | e2e Context Recall | e2e Correctness | e2e nDCG@5 |
|---|---|---|---|---|---|---|---|---|
| 客服 cservice | `eval_cservice` | 50 | **0.8** | **0.9377** | 50 | **0.8** | **0.8149** | **0.9377** |
| 办公行政 admin | `eval_admin` | 30 | **0.8363** | **0.8935** | 30 | **0.8363** | **0.9222** | **0.8935** |
| 器械 device | `col_b7b876b1` | 50 | **0.8833** | **0.9305** | 50 | **0.8833** | **0.8733** | **0.9305** |

> ⚠️ 读数注意：
> - **三库的「检索」与「e2e」检索侧指标逐位相同**（同检索链路 + judge 缓存 → 确定性复现），是预期行为、非巧合；e2e 独有的增量是生成侧四项（Faithfulness / Answer Relevance / Correctness / Citation Accuracy）。
> - **客服库另有 35 题检索优化集**（Recall 0.7515 / nDCG 0.9443，见 §1.2），与上表 50 题**不同题集**，不可横比。
> - **器械库检索与 e2e 同题集（50 题）**，两列一致属预期。

---

## 1. 客服业务库（cservice，`eval_cservice`）

### 1.1 检索（retrieval，50 题 @5，与 e2e 同题集）

| 指标 | 数值 |
|---|---|
| Context Recall | **0.8** |
| Context Precision | 0.448 |
| Context Precision（加权） | 0.586 |
| nDCG@5 | **0.9377** |
| gold_rank（均值 / 中位） | 1.49 / 1.38 |
| 事实覆盖率 top1 / top3 / top5 | 0.6149 / 0.7996 / 0.8438 |
| 题数 | 50（unanswerable 4 题不计入 recall 分母） |
| 报告 | `run_retrieval_cservice_50_20261006.json` |

**按类目 Recall**：`fact_single` 0.9464（14）｜ `proper_noun` 0.875（8）｜ `comparison` 0.7361（6）｜ `table_numeric` 0.7083（6）｜ `fact_cross_doc` 0.6759（9）｜ `summary` 0.6（3）｜ `unanswerable` 0.0（4）

**按难度 Recall**：easy 0.8833（15）｜ medium 0.8233（29）｜ hard 0.4945（6）

### 1.2 检索（retrieval，35 题 @5，检索优化集）

> 题集 `testset_cservice_35.json`，为 [retrieval_comparison.md](retrieval_comparison.md) 的优化对照集，与 §1.1 的 50 题为**不同题集**。

| 指标 | 数值 |
|---|---|
| Context Recall | **0.7515** |
| Context Precision | 0.4971 |
| Context Precision（加权） | 0.6188 |
| nDCG@5 | **0.9443** |
| gold_rank（均值 / 中位） | 1.6 / 1.48 |
| 事实覆盖率 top1 / top3 / top5 | 0.554 / 0.7611 / 0.8227 |
| 题数 | 35（Recall 分母 33；unanswerable 2 题不计） |
| 报告 | `run_retrieval_cservice_35_20261006.json` |

**按类目 Recall**：`fact_single` 0.925 ｜ `proper_noun` 0.8333 ｜ `comparison` 0.6875 ｜ `table_numeric` 0.6458 ｜ `fact_cross_doc` 0.631 ｜ `summary` 0.4

### 1.3 端到端（e2e，50 题 @5）

| 指标 | 数值 |
|---|---|
| Context Recall | **0.8** |
| Context Precision | 0.448 |
| Context Precision（加权） | 0.586 |
| nDCG@5 | **0.9377** |
| gold_rank（均值 / 中位） | 1.49 / 1.38 |
| Faithfulness | **0.9975** |
| Answer Relevance | 0.952 |
| Correctness | **0.8149** |
| Citation Accuracy | 0.8594 |
| judge_failed | 0 |
| 题数 | 50 |
| 报告 | `run_e2e_cservice_50_20261006.json` |

---

## 2. 办公行政库（admin，`eval_admin`）

### 2.1 检索（retrieval，30 题 @5）

> 取代旧口径基线（`M9_evaluation.md` §8.1c，2026-09-25：Recall@5 0.9524 为修复前虚高值）。

| 指标 | 数值 |
|---|---|
| Context Recall | **0.8363** |
| Context Precision | 0.42 |
| Context Precision（加权） | 0.5937 |
| nDCG@5 | **0.8935** |
| gold_rank（均值 / 中位） | 1.39 / 1.38 |
| 事实覆盖率 top1 / top3 / top5 | 0.6661 / 0.8476 / 0.878 |
| 题数 | 30（unanswerable 2 题不计入 recall 分母） |
| 报告 | `run_retrieval_admin_30_20261006.json` |

**按类目 Recall**：`fact_single` 0.9394（11）｜ `table_numeric` 0.8133（5）｜ `proper_noun` 0.8125（4）｜ `fact_cross_doc` 0.775（4）｜ `comparison` 0.6666（4）｜ `unanswerable` 0.0（2）

**按难度 Recall**：easy 0.9479（16）｜ medium 0.6773（13）｜ hard 0.8（1）

### 2.2 端到端（e2e，30 题 @5）

| 指标 | 数值 |
|---|---|
| Context Recall | **0.8363** |
| Context Precision | 0.42 |
| Context Precision（加权） | 0.5937 |
| nDCG@5 | **0.8935** |
| gold_rank（均值 / 中位） | 1.39 / 1.38 |
| Faithfulness | **0.9777** |
| Answer Relevance | 0.9567 |
| Correctness | **0.9222** |
| Citation Accuracy | 0.8753 |
| judge_failed | 0 |
| 题数 | 30 |
| 报告 | `run_e2e_admin_30_20261004.json` |

---

## 3. 器械库（device，`col_b7b876b1`）

> 题集：`testset_device_v2.json`（v5.34 全删重建，50 题，25 easy / 15 medium / 10 hard；含 image_only 10 题）。检索与 e2e **同题集**。

### 3.1 检索（retrieval，50 题 @5）

| 指标 | 数值 |
|---|---|
| Context Recall | **0.8833** |
| Context Precision | 0.296 |
| Context Precision（加权） | 0.4723 |
| nDCG@5 | **0.9305** |
| gold_rank（均值 / 中位） | 1.39 / 1.36 |
| 事实覆盖率 top1 / top3 / top5 | 0.747 / 0.862 / 0.906 |
| 题数 | 50 |
| 报告 | `run_retrieval_device_50_20261005.json` |

**按类目 Recall**：`fact_single` **1.0**（18 题全满）｜ `image_only` 0.9 ｜ `table_numeric` 0.7917 ｜ `summary` 0.7667

**按难度 Recall**：easy 0.88 ｜ medium **0.9667** ｜ hard 0.7667

### 3.2 端到端（e2e，50 题 @5）

| 指标 | 数值 |
|---|---|
| Context Recall | **0.8833** |
| Context Precision | 0.296 |
| Context Precision（加权） | 0.4723 |
| nDCG@5 | **0.9305** |
| Faithfulness | **0.995** |
| Answer Relevance | 0.9314 |
| Correctness | **0.8733** |
| Citation Accuracy | 0.896 |
| judge_failed | 0 |
| 题数 | 50 |
| 报告 | `run_e2e_device_50_20261005.json` |

**按难度 Correctness**：medium **1.0**（15 题全对）｜ easy 0.84 ｜ hard 0.7667

---

## 附：旧口径历史对照（`history/`，**不可与上表横比**）

| 库 | 模式 | 题数 | 日期 | Context Recall | nDCG@5 | Correctness | Faithfulness | gold_rank | 报告 |
|---|---|---|---|---|---|---|---|---|---|
| 客服 cservice | e2e | 50 | 2026-09-24 | 0.9601 | 0.8898 | 0.8353 | 0.9194 | 1.74 | `history/run_e2e_cservice_50_20260924.json` |
| 办公行政 admin | e2e | 30 | 2026-09-25 | 0.9524 | 0.8567 | 0.9713 | 0.884 | 2.04 | `history/run_e2e_admin_30_20260925.json` |

> 旧口径 recall 为修复前假阳性复用导致的**虚高值**；同期 nDCG/gold_rank 较当前值更差，说明是口径修复而非链路退化（决定性证据见 [retrieval_comparison.md](retrieval_comparison.md)「口径变更说明」）。
