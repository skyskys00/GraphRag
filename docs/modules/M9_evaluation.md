# M9 模块规划：评测层

> **版本：** v1.3
> **状态：** 可用（retrieval 模式 + gold_rank 诊断 + 双窗口 + nDCG）；Phase 2/3 待执行
> **更新：** 2026-09-24
> **定位：** 中文 RAG 系统量化评测——测试集 + 指标 + ablation + 回归
> **契约：** 测试集（question + contexts + ground_truth）→ 评测报告（各指标分数 + 对比基线）
> **上游：** [M5 检索层](M5_retrieve.md) / [M6 生成层](M6_generate.md) / [M7 交互层](M7_interact.md) | **下游：** 回归门禁 / 作品集量化数据 / README 展示
> **依据：** [`ARCHITECTURE.md`](../ARCHITECTURE.md) §2.7 ｜ [`FRAMEWORK_NOTES.md`](../FRAMEWORK_NOTES.md) §3 ｜ [`M9_testset.md`](M9_testset.md)（测试语料与测试集设计规范）
> **运行：** `cd backend && python -m app.m9_eval.runner --testset testsets/default.json --report reports/run_xxx.json`
> **变更历史：** 见 [`CHANGELOG.md`](../CHANGELOG.md) v5.6（Phase 1 落地）/ v5.7（裁判稳定性 + 测试集 GT 修正 + 表格双表示后新基线）

---

## 1. 定位与目标

### 1.1 为什么要有 M9

**当前痛点**：全链路跑通了，但「到底好不好」只有主观感受，没有量化数据。
- 面试官问「你的系统比普通 RAG 好多少」——答不上来。
- 改了检索策略，不知道是变好还是变差——全靠人工试几道题。
- 作品集上写了「三路召回 + RRF + rerank」，没有数字支撑，没有说服力。

**M9 的角色**：量化尺子 + 回归门禁。不参与线上问答链路（旁路评测），但在迭代时提供客观数据支撑。

### 1.2 目标（v1.0）

1. **可量化**：输出一组标准指标，能说清「当前系统在中文场景下的表现」。
2. **可对比**：支持 ablation study——关掉某一路/换个策略，分数变化一目了然。
3. **可回归**：每次大改动跑一遍，快速判断是否劣化。
4. **可展示**：评测结果能直接放进 README / 作品集，作为项目亮点的数据支撑。

### 1.3 边界（M9 不做什么）

- 不做在线评测（不接入 M7 接口做实时打分）——旁路离线评测即可。
- 不做人工标注平台——测试集手工构造，不做标注工具。
- 不做用户行为埋点 / A/B 测试——单机单用户，无意义。
- 不做端到端 latency benchmark——当前数据量太小，latency 不具备参考价值。

---

## 2. 评测指标体系

分三层：**检索质量** → **生成质量** → **端到端质量**。

### 2.1 检索层指标（M5 输出）

| 指标 | 含义 | 计算方式 | 关注点 |
|---|---|---|---|
| **Context Recall** | 标准答案所需信息在检索结果中的覆盖率 | ground_truth 中有多少事实点出现在 retrieved contexts 里 | 召回够不够，会不会漏关键信息 |
| **Context Precision** | 检索结果中相关 chunk 的比例 | top-k 里有多少 chunk 是真正相关的 | 噪音多不多，会不会把无关文档塞给 LLM |
| **nDCG@k** | 排序质量的标准化单一数字 | 用 per-chunk 相关度（LLM 裁判给的 score）算 DCG/IDCG，零额外成本 | 整体排序好不好，相关块排得够不够靠前 |
| **Hit Rate@k** | 标准答案至少出现在 top-k 中的比例 | 对每个问题，ground_truth 所在 chunk 是否在 top-k | 粗粒度召回能力 |
| **Gold Rank**（诊断） | 每个 gold fact 最早出现在第几块 | 对每个 key_fact，在检索结果中找第一个命中的 chunk，记录其排名；输出 avg/median/min/max + top-K 覆盖率曲线 | **排序质量诊断**——事实排得够不够靠前，top5 是否够用 |

> 参考：RAGAS Context Precision / Context Recall。但 RAGAS 默认用英文模型判分，**中文场景需要自己接 DeepSeek flash 当裁判**（见 §4）。

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

---

## 5. Ablation Study 方案

> 这是作品集上最有说服力的部分——用数据证明「你加的每一层都有用」。

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

**主表（README / 作品集用）**：

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
- **词汇模式的能力边界**：对数值型事实（含明确数字 token）命中率高，对推导型/纯文本事实大量漏检。实测 v5.9 基线 top5 覆盖率仅 52%（而 LLM recall 是 92%）。
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

---

## 8. 遗留 / 后续（v2.0 及以后）

1. **更多维度**：latency benchmark（文档量上去后做）、token 成本统计、抗扰动测试（同义改写问题看答案稳定性）。
2. **多库评测**：每个 collection 可以有自己的测试集，评测按库隔离。
3. **CI 集成**：接入 GitHub Actions，PR 自动跑冒烟集（当前单机项目不急）。
4. **可视化报告**：把评测结果做成简单的 HTML 仪表盘（当前 JSON + Markdown 够用）。
5. **垂直场景评测**：等 P1 落地垂直场景后，针对该场景建专属测试集（如学术论文 / 产品文档）。

---

## 9. 版本

- **v0.1**（2026-09-21）：初始规划。定义定位、6 个核心指标、50 题测试集方案、7 组 ablation 设计、三阶段实施路线。
- **v1.1**（2026-09-22）：Phase 1 落地。retrieval 模式可用，context_recall + context_precision + LLM 裁判缓存 + 35 题客服业务测试集。
- **v1.2**（2026-09-24）：新增 gold_rank 诊断维度 + 双窗口评测（top5/top8 同跑）。gold_rank 双模式（词汇/LLM），定位为排序质量诊断工具而非精确指标。
- **v1.3**（2026-09-24）：新增 nDCG@k 排序质量指标。从 CP 的 per_chunk score 推导，零额外 LLM 成本；MRR 不实现，由 nDCG 替代。
