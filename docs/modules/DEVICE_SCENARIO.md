# 器械说明书垂直场景落地方案

> **版本：** v0.13（v0.12 基础上：旧题集/旧实测归档 → [device_scenario_eval_archive.md](../archive/device_scenario_eval_archive.md)；主文档对齐 v5.34 权威数据；参数对比状态更正为 ⏳ 待开发）
> **状态：** 场景特化功能 —— 参数对比 **⏳ 待开发**（v5.26 曾实现、v5.35 前端入口隐藏、后端 `POST /compare` 保留，视为未交付，设计思路见 §5.1；检索去噪能力已就绪见 §5.4）｜ 场景 chip ✅（v5.27）｜ 语料扩充 ✅（v5.28 10 份 → v5.33 12 份）｜ 评测集 ✅（v5.34，50 题 v2 全删重建，Recall 0.8833 / e2e Correctness 0.8733，见 §11 与 [retrieval_comparison.md](../../backend/tests/reports/retrieval_comparison.md) v5.34 节）
> **更新：** 2026-10-05
> **定位：** 把「有源器械说明书」库从验证态做成**可展示的垂直场景**
> **契约：** 复用现有 M7 接口；新增 `POST /compare`（见 §5.1，⏳ 待开发）
> **上游：** [M5 检索层](M5_retrieve.md) / [M6 生成层](M6_generate.md) / [M7 交互层](M7_interact.md) | **下游：** [M8 前端](M8_frontend.md) / [M9 评测层](M9_evaluation.md)
> **依据：** [MULTIMODAL.md](MULTIMODAL.md) ｜ 语料来源调研（`medical-device-corpus-sources`）
> **运行：** 语料走 M7 `POST /docs` 上传（多模态自动生效；批量用 `backend/scripts/ingest_device_corpus.py`）；评测用 `tests/testsets/testset_device_v2.json`（当前权威，§6.3）；场景 chip 见问答页输入框上方（器械库）
> **变更历史：** 见 [`CHANGELOG.md`](../CHANGELOG.md) v5.26（§5.1 曾实现）/ v5.27（§5.2 落地）/ v5.28（语料 10 份）/ v5.28.1（sidecar 键更正）/ v5.29（30 题评测集 + M9 裁判 hit 修复）/ v5.30（comparison 泄漏治理）/ v5.31（评测窗口收敛 top5）/ v5.32（§5.4 localize_query）/ v5.33（旧题集扩 50 题）/ v5.34（题集全删重建 v2，50 题现行权威）/ v5.35（comparison 弃用：前端入口隐藏、后端保留）；本次为文档修订（v0.13）：旧题集与旧实测归档、§5.1 状态更正为待开发

## 1. 场景与用户

**场景**：有源医疗器械说明书的**智能问答与参数速查**。

**为什么选它**：

- 「医学垂直 + 多模态」双契合：既有垂直领域确定性，又能最大化多模态链路的展示价值
- 多模态不是硬凑：器械说明书的核心信息形态就是**界面截图与图片型表格**（见 MULTIMODAL.md §2.1 实证），纯文本链路天然召不回
- 语料有真实结构：参数表与图片型表格（table_numeric）、按键/报警/功能说明（fact_single）、界面截图（image_only）、整档综合描述（summary）——四类考点覆盖 v2 题集口径（型号对照 comparison 因检索短板未闭环已弃用，见 §5.1/§6.3）

**目标用户**（场景定位参考，不实现用户体系）：

| 角色 | 典型诉求 |
|---|---|
| 医院设备科 / 临床工程师 | 「这台模拟仪的静态压力量程是多少」「报警 E03 怎么处理」 |
| 器械厂商售后 | 「LSP-1C 支持哪些规格的注射器、对应内径多少」 |
| 采购 / 检验科 | 「A 型号和 B 型号的精度等级差多少」（参数对比，§5.1 ⏳ 待开发） |

## 2. 现状与差距

**已有底子**（截至 2026-10-05）：

- 库「有源器械说明书」`col_b7b876b1`：**12 份文档**（v5.28 10 份说明书 + v5.33 骨科审评规范.docx / 护理不良事件.pptx），PDF 原件在库内 `uploads/`；合计 888 chunk、149 个带图 drawing 块
- 多模态全链已打通：图片 → 视觉描述 → 独立 TextUnit → 索引 → 引用带图 → 前端渲染（v5.25 ~ v5.25.6 全部验证通过）
- 检索/生成/前端/评测骨架齐备（M5 三路召回 + M6 引用溯源 + M8 展示 + M9 评测框架）
- **评测集已就位**：50 题 v2（`testset_device_v2.json`，v5.34 全删重建），实测 Recall 0.8833 / nDCG 0.9305 / e2e Correctness 0.8733（见 §11）

**对照垂直场景落地的四项要求**：

| 要求 | 现状 | 本轮目标 |
|---|---|---|
| 真实数据 20-50 篇 | **12 份 ✅**（v5.28→v5.33） | 已达（10 份说明书 + 2 份规范/培训文档） |
| 针对该场景的评测数据 | **50 题 v2 ✅**（v5.34） | 已达：Recall 0.8833 / Correctness 0.8733（§11） |
| 讲清解决了什么问题 | 有素材未成文 | 写入 README（§7） |
| 可演示 | 无 | 本轮不做（P3 部署另议） |

## 3. 方案总览

```
线 1：语料扩充（~10 份 → 12 份 ✅，v5.28→v5.33）
  厂商官网/公开渠道 → PDF → M7 POST /docs → 多模态自动生效（M1 视觉 → M2 → M3 → M5）
        ↓
线 2：场景特化功能
  参数对比（多型号并排）  ⏳ 待开发 —— 设计方案已定（检索式对比，§5.1）；
                          v5.26 曾实现、v5.35 前端入口隐藏（视为未交付），
                          后端接口 + §5.4 去噪能力已就绪，重启只需重做入口/前端
  场景化入口 chip（查询模板）  ✅ 已落地（v5.27，纯前端）
        ↓
线 3：场景评测集
  器械 50 题（v5.34 v2 全删重建，12 文档全覆盖、每文档三档）
      → M9 runner → 检索/生成指标 ✅（Recall 0.8833 / Correctness 0.8733，§11）
```

三条线**可并行**：线 1 是线 2/3 的前提（语料不够，功能和评测都撑不起来）。

## 4. 线 1：语料扩充（~10 份）

### 4.1 选料标准

1. **有源器械**（需通电工作）——无源器械（手术刀、敷料）无界面/按键，多模态优势体现不出来
2. **含真表格或图片型表格**——纯文本拍平的源拿不到 table_numeric 考点
3. **含界面截图/按键图标**——多模态链路的展示面
4. **单份 10-30 页**——符合「语料精简」约束（memory `medical-device-corpus-sources` 实测单份 11k-22k 字符）

### 4.2 来源路径（2026-10-02：自动抓取受阻 → 改由用户手工下载 ✅）

> **结论（v5.28）**：自动抓取不可行，最终走**路径 C —— 用户手头已有/自行下载的语料**。用户 2026-10-02 交付 8 份（`~/Downloads/device_pdfs`），与原有 2 份合计 10 份，已全部入库。下表保留受阻记录，供后续再找语料时参考。

**已确认可行的方法论**（memory `medical-device-corpus-sources`）：Google `filetype:pdf` 直挂厂商官网。已排除：经销商内容农场（表格拍平）、文库站（付费墙）、NMPA（只公开注册证）、必应中文（无结果）。

**⚠️ 本轮实测：自动抓取当前受阻**，四条路都不通：

| 路径 | 结果 |
|---|---|
| `wigolo search` | 引擎池崩溃（5 引擎全超时，`pool_collapsed`），返回结果完全无关 |
| `curl` + 代理抓 Google | 返回 `support.google.com` 反爬页 |
| chrome-devtools 开 Google | 跳 `google.com/sorry` 人机验证 |
| chrome-devtools 开百度 | 跳 `wappass.baidu.com` 图形验证码 |

**根因**：chrome-devtools-mcp 启动的是**全新无 cookie 的独立 Chrome profile**，搜索引擎对无历史 profile 一律要求验证；且该 Chrome 未走代理（直连 Google 超时，实测代理本身正常：`curl -x 127.0.0.1:7897 google.com` → HTTP 200）。

**待选的解决路径**（需用户定，见 §10）：

| 路径 | 说明 | 成本 |
|---|---|---|
| A. 用用户日常 Chrome 搜 | 有历史 cookie，大概率不触发验证；由用户手动搜 + 下载，或开 Apple Events JS 后我驱动 | 低（用户配合） |
| B. 直接给厂商域名 | 我逐个扫产品页找 PDF（融柏 `rongbaipump.com` 可达但无下载中心；`xzmsdz.com` 当前不可达） | 中（需已知域名清单） |
| C. 用户手头已有的语料 | 直接入库 | 最低 |
| D. 复用已知可达源 | ResMed `document.resmed.com`（全球文档库，英文）、三诺 `sinocare.com` 等 | 中（需逐个验证） |

### 4.3 入库方式 ✅（v5.28 已完成）

走 **M7 `POST /docs`**（真实上传链路）——多模态自动生效，无需任何额外步骤。批量入库用脚本：

```bash
cd backend && python3 scripts/ingest_device_corpus.py \
    --dir ~/Downloads/device_pdfs --collection col_b7b876b1
```

- **幂等**：脚本先查库内文档，跳过同名且 `ready`/`processing` 的——`processing` 也跳是关键（后端入库是后台任务，轮询脚本中断不代表入库停止，重跑不能重复提交）。
- **轮询容错**：后端串行处理多份文档时 `GET /docs` 可能响应很慢，超时给到 300s 并在失败时重试而非退出。
- 单份耗时参考：v5.25.3 实测 2 份 11m16s（视觉 + 建图）；v5.28 实测 10 份串行约 47 分钟。
- 视觉调用有成本（`deepseek-flash`，单价极低）；`image_captions.json` 落盘缓存，重跑零成本。
- **入库后无需重启**：`image_path` 不落 PG（`lightrag_doc_chunks.sidecar` 为空），由 M6 `Sidecar` 从 `data/chunks/*.jsonl` 构建，键 = PG chunk 主键（`make_custom_chunk_id`，见 M6_generate §4.2）。M7 入库任务（`documents.ingest`）完成时会在**同一个 AppDeps 对象上就地重建 sidecar**，故新文档图片溯源即时生效，不必 `./dev.sh restart api`。（该「必须重启」说法在 `build_workspace_deps` 引入 `ingest()` 后已过时，v5.28.1 更正。）

**实测结果（v5.28，10 份全部 `ready`）**：857 chunk / 149 个带图 drawing 块 / 约 4.6 万视觉 tokens。
单份最大智能便携式检测仪 223 chunk·39 图，最小数字差压计 22 chunk·2 图。
验证：问「脉搏血氧仪的正常血氧饱和度范围」→ 生成正确且引用带 `image_path`（新入库文档可检索 + 图片溯源生效）。
（v5.33 追加骨科审评规范.docx + 护理不良事件.pptx 后，库内 12 份 / 888 chunk / 149 drawing 块，见 §6.3。）

## 5. 线 2：场景特化功能

### 5.1 多型号参数对比 ⏳（待开发）

**场景**：用户想知道「同样一个参数，几个型号分别是多少」。

**设计取舍——不做 LLM 归并**。常见做法是「抽参数 → LLM 归并成表」，但那样引入不稳定性（归并错位、编造数值）。本方案改为**检索式对比**：

```
POST /compare
  body: { collection_id, query: "静态压力量程", doc_ids: ["<id1>", "<id2>", ...] }
  ↓ 对每个 doc_id 单独跑一次 M5 检索（限定该文档，配合 §5.4 localize_query 去噪）
  ↓ 各取 top-1 片段（含引用溯源）
  → { query, rows: [{ doc_id, doc_name, snippet, page_range, score, image_path? }] }
```

- **优点**：零 LLM 成本、可溯源（每个值都指回原文页码）、不会编造
- **前端**：并排对比卡（列 = 型号，行 = 该参数的原文片段 + 页码 + 可选缩略图）
- **诚实边界**：某型号没有该参数时**如实留空**，不推断

**状态：⏳ 待开发（视为未交付）**。v5.26 曾完整实现并实测（`app/m7_interact/compare.py` + `POST /compare` ｜ `components/DeviceCompare.tsx` + 侧栏「参数对比」），v5.35 前端侧栏入口已隐藏（comparison 弃用），后端接口保留但**无前台入口、不可达**——视为**未交付**。v5.26 三组 query 实测表、v5.32 前后端生产链路实测均已存档到 [device_scenario_eval_archive.md](../archive/device_scenario_eval_archive.md)；后端接口 `compare_params` 与 §5.4 检索去噪能力仍在运行，**重启本功能只需重做前端入口与对比卡**。

### 5.2 场景化入口 ✅（v5.27 已落地）

问答页加器械场景的快捷入口（chip）：常用查询模板（「报警含义」「操作步骤」「规格参数」）。本质是 query 模板，**不新增接口**。

**实现（v5.27）**：`InputBar.tsx`（`deviceMode` prop + `DEVICE_TEMPLATES`）+ `App.tsx`（`currentCollection.name.includes('器械')`）+ `App.css`（`.scene-row`）。

- **显示条件**：按当前库名含「器械」判断，非器械库不显示（避免「报警含义」在客服库错位）
- **点击行为**：填入模板文本（如「___的报警含义是什么？」）并把光标停在待补全处，**不直接发送** —— 模板原文太短、直接检索质量差
- **实测**：器械库显示三 chip；点「报警含义」→ 输入框 `的报警含义是什么？` + 光标 0 + 聚焦；切「销售业绩」库 chip 消失（chrome-devtools-mcp）

### 5.3 不做的

- **器械卡片自动摘要**：需要 LLM 逐份抽取 + 存储，收益（展示价值）与成本不匹配，本轮不做
- **拍照识报警图标**：需要图像检索（CLIP 类），超出当前架构

### 5.4 对比检索 query 去噪 `localize_query`（能力已实现，v5.32）

**定位**：comparison 类对比 query 同时含多个型号名时，**其他文档的型号名会把本文档的向量召回打到 cosine 阈值以下**（`probe_perdoc_subquery.py` 可复现：完整对比 query 检索该文档 → 0 条，去掉其他型号名 → 5 条）。`localize_query` 在逐 doc 检索前**删除 query 中属于其他文档的型号名**，再喂 `retrieve(..., allowed_docs=[doc_id])`。

**数据源（两条，均为生产已存在数据，与评测试题/key_facts 零交集——红线合规）**：

| 层 | 型号名来源 | 归属判定 |
|---|---|---|
| L1 | 文档 `documents.json` 文件名提炼的型号 token + 品牌短名单 | 勿删「目标 doc 自己的候选」 |
| L2 | L1 ∪ 图实体表（`lightrag_graph_nodes`） | 型号名出现在目标 doc 内容（sparse chunks 文本）→ 保留，否则删除 |

**关键不变量**：非对比 query（无型号名命中）→ 输出与输入一致，零回退；`retrieve()` 签名不动。

**状态：能力已落地（v5.32），但当前无前台消费方**——参数对比 ⏳ 待开发（§5.1）、comparison 题已从评测集剔除（§6.3）。L1/L2 方案设计、8 侧探针实测表、生产链路实测（CP-004 博声侧由空恢复 3 条）、实体候选收紧教训（防纯数字型号token误删）均存档到 [device_scenario_eval_archive.md](../archive/device_scenario_eval_archive.md)。功能重启时直接复用。

## 6. 线 3：场景评测集

**规模**：50 题（`testset_device_v2.json`，v5.34 全删重建，**现行权威**）。

**考题类型与分布**（v5.34 定，**全部为单文档题**）：

| 题型 | 题量 | 说明 |
|---|---|---|
| `fact_single` | 18 | 单文档事实题（参数/按键/报警/功能说明） |
| `table_numeric` | 12 | 表格数字题（图片型表格在内） |
| `image_only` | 10 | 答案只在界面截图/图上（纪律见下） |
| `summary` | 10 | 整档综合/总结型（hard 段主力） |

- **难度**：25 easy / 15 medium / 10 hard（≈5:3:2），12 文档全覆盖、每文档≥2 档、内容充足的 10 份覆盖 hard。
- **主动剔除**：`comparison`（跨文档归属模糊，破坏「每文档三档」）与 `unanswerable`（无文档归属）——设计理由见 §6.3，旧题集含这两类并已归档（§6.2）。
- **出题流程**：12 并行 agent 各出一文档 → `assemble_device_testset.py` 统一重编号 + 校验（key_facts「数字/英文 token 全命中 + 中文 2-gram 覆盖率 ≥0.5」，容忍 paraphrase、拒绝编造，50 题 0 编造）。

**关键纪律（image_only）**：`image_only` 题的**答案完整内容必须存在于 `block_type=drawing` 块**——多模态有贡献的必要条件。核验：在 `data/collections/<col_id>/chunks*.jsonl` 按 `block_type` 过滤，逐串确认答案命中 `drawing` 块。

> ⚠️ **不要用 `pdftotext -layout` 文本层 0 命中当基线**——它不够严。实测 `YASEE`、`实时压力`、`绑定医生`、`UNITS`/`HOLD`/`DIF` 等串在文本层「0 命中」（图片里的字），却出现在 chunk 的 `paragraph`/`table` 块 content 中，文本层基线会误判为「干净」。另注意 `page_range` 是 **0-based**。
>
> 已知例外与 M1 解析缺陷（MinerU 偶将图片误判为 `type=table`，VC-IO 类题有先例）的完整记录见 [device_scenario_eval_archive.md](../archive/device_scenario_eval_archive.md)（image_only 纪律节）。

**运行**：runner 兼容改动已落地（§6.1），当前跑法：

```bash
cd backend && python -m app.m9_eval.runner --testset tests/testsets/testset_device_v2.json \
    --mode retrieval --collection col_b7b876b1
```

### 6.1 runner 兼容改动（2026-10-02，v5.29，已落地）

新式 collection（`col_<uuid8>`）与旧式扁平库（`default_ws` 等）布局不同，M9 runner 原本只认后者，跑器械库会解析到不存在的 `data/col_b7b876b1/`。改动四处：

| # | 文件 | 改动 |
|---|---|---|
| 1 | `app/m9_eval/testset.py` | `VALID_CATEGORIES` 补 `"image_only"`（原缺失 → 6 题校验失败） |
| 2 | `app/m9_eval/runner.py` | 新增 `_resolve_collection()`：`col_*` 走 `collection_paths()` 解析到 `data/collections/<col_id>/`，旧三库（`default`/`eval_cservice`/`eval_admin`）保持扁平映射，零回归 |
| 3 | `app/m9_eval/runner.py` | `_build_deps()` 补加载 entities（`load_entities_async`）——原缺失会系统性低估生产 recall（线上 `retriever.py` 是加载的） |
| 4 | `app/m9_eval/runner.py` | comparison 题 per-doc 检索（`_comparison_targets` + `_interleave`）——**v2 题集已无 comparison 题，此路径保留但无题集触发**；其 v5.29 子查询 / v5.30 退役的泄漏治理史见 [归档](../archive/device_scenario_eval_archive.md) |

旧三库（行政/cservice）跑法不变，`--workspace <ws>` 仍走扁平路径。

### 6.2 旧题集（v5.29 30 题 / v5.33 50 题）—— 已归档

> 30 题版（v5.29）的五类考点分布蓝图（table_numeric 8 / fact_single 8 / image_only 6 / comparison 4 / unanswerable 4）、DV-IO-004 已知例外与文本层基线警告、v5.33 扩 50 题方案（难度 5:3:2 配比算术、新增 20 题清单、实测 Recall 0.7909），以及全部旧实测（30/50 题检索与生成、口径调查档案、漏召逐 fact 归因）均归档到 → **[device_scenario_eval_archive.md](../archive/device_scenario_eval_archive.md)**。
>
> 旧题集 JSON：`backend/tests/testsets/archive/testset_device_30.json` / `testset_device_50.json`。**数值与现行 v2 题集（§6.3/§11）不可横比**——题集全删重建非增量。

### 6.3 题集全删重建 v2（v5.34，2026-10-05，现行权威）

**用户指令（2026-10-05）**：「50 全部删除，重新出题，考虑器械库所有文档。按照难度 5:3:2 的比例，但是每个文档应该都有三个难度的题！」

**拍板三点（AskUserQuestion）**：
1. **N=50 近似 5:3:2**（25/15/10）——数学约束：12 文档 × 每档≥1 ⇒ 各档≥12，5:3:2 ⇒ hard=0.2N≥12 ⇒ **N≥60**，N=50 严格无解，接受近似。
2. **骨科审评规范.docx（全文仅 1 chunk）豁免 hard**，只出 easy/medium。
3. **旧题集全部归档不改**（`testset_device_30.json` + `testset_device_50.json` → `tests/testsets/archive/`）。

**成果**：`backend/tests/testsets/testset_device_v2.json`（50 题）——

- 难度 **25 easy / 15 medium / 10 hard**，12 文档全覆盖、每文档≥2 档、内容充足的 10 份覆盖 hard，逐文档达标（含校验脚本 per-doc 配比核对）。
- 题型 `fact_single` 18 / `table_numeric` 12 / `image_only` 10 / `summary` 10。**全部为单文档题**：comparison（跨文档归属模糊，破坏「每文档三档」）与 unanswerable（无文档归属）**主动剔除**，由归档旧题集覆盖。
- **流程**：`scripts/dump_device_chunks.py` dump 12 文档全部 chunk → **12 个并行 agent 各出一文档**（逐题记 `_evidence_idx`）→ `scripts/assemble_device_testset.py` 合并、校验、**统一重编号**（agent 自拟 id 跨文档冲突，如多份同用 DV-FS-009）。
- **校验口径**：key_facts 为出题改写概括，按「数字/英文 token 全命中 + 中文 2-gram 覆盖率 ≥0.5」判据（容忍 paraphrase、拒绝编造），50 题全通过、0 编造。
- **验证**：结构加载校验通过；`--mode retrieval --limit 2` 冒烟（Recall 1.0 / nDCG 0.82，`run_1791192530.json`）后跑 **50 题全量 retrieval + e2e**：
  - **检索**（`run_retrieval_device_v2.json`）：Recall **0.8833** / nDCG 0.9305；按难度 easy 0.8800 / medium 0.9667 / hard 0.7667；按题型 fact_single **1.0** / table_numeric 0.7917 / image_only 0.9 / summary 0.7667。
  - **生成**（`run_e2e_device_v2.json`）：Faithfulness **0.9950** / Correctness **0.8733** / Answer Relevance 0.9314 / Citation 0.8960；按难度 medium **1.0**（15 题全对）/ easy 0.8400 / hard 0.7667。
  - **未满分 8 题**与检索漏题对应：easy 三题（DV-IO-007、DV-TN-005、DV-TN-010，recall 0）+ DV-TN-011（medium 0.5）+ summary hard 四题（DV-SUM-003/007/008/009，0.25–0.75）→ summary 新题型 hard 段为当前最大拖累项；DV-FS-001 / IO-005 为「主 fact 答、副 fact 漏」非检索缺口。**详表与逐题归因见 [retrieval_comparison.md](../../backend/tests/reports/retrieval_comparison.md) v5.34 节**。

**读数**：

1. **hard 段 0.7667（旧 0.4815 → +0.2852）是 v5.34 最大变化**，但**不是链路变强**——题集全删重建、题目完全重出，hard 更多是「单文档多 fact 聚合/综合型」（summary）而非旧题的 comparison/表格变态。**跨题集对比必须按题型/难度分层读**。
2. **未满分 8 题画像**：DV-TN-005 跨题集复现 0（同一文档同一张表的检索性短板稳定复现，属「表格数值块被精排压出 top5」经典画像，v5.13 NL 摘要/权重探底均已闭源勿重复）；summary hard 4 题全漏 = 新最大拖累项（gold 需跨块聚合，与精排截断同源）。
3. **检索满分但 e2e correctness 掉分 2 题**（DV-FS-001 / DV-IO-005，0.5）——主 fact 回答、副 fact 遗漏（如 FS-001「不能用于人体临床」答出、遗漏「只能用于科学研究」），材料在上下文（recall 1.0），是生成覆盖问题非检索缺口。

## 7. 验证方法

| 步骤 | 检查项 |
|---|---|
| 1. 语料入库 ✅ | **12 份全部 `ready`**（v5.28 10 份 + v5.33 2 份）；PG `lightrag_doc_chunks` 计数与 M2 产出对齐 |
| 2. 多模态生效 ✅ | 149 个 drawing 块带 `image_path`（v5.28 实测）；预览面板图片可渲染 |
| 3. 参数对比 | ⏳ **待开发**（§5.1）：验证口径已定——对 3 个型号问同一参数 → 各自返回正确片段 + 页码；缺参数的型号如实留空（设计见 §5.1，后端接口与去噪就绪） |
| 4. 评测集 ✅ | **50 题 v2 跑通**（v5.34），检索 Recall 0.8833 / nDCG 0.9305 / e2e Correctness 0.8733（§11） |
| 5. 诚实性 ✅ | e2e Faithfulness **0.9950**、judge_failed=0（v2 已无 unanswerable 负对照题，诚实性由 Faithfulness 判决，见 §11.2） |

## 8. 不做的事

- **不做用户体系/鉴权**——单机单用户，无意义
- **不做在线部署**（P3 另议）——当前后端依赖本地 Xinference（bge-m3 + reranker）与 PG，上云需重构
- **不做跨厂商参数标准化**——不同厂商参数命名不统一，强行归一化会引入错误；本方案用检索式对比规避
- **不改 M3/M5 核心**——复用现有检索链路，新增的只是 M7 薄接口

## 9. 里程碑

| 阶段 | 内容 | 依赖 |
|---|---|---|
| M1 | 语料扩充（~10 份 → 12 份）并入库 | ✅ **已落地（v5.28→v5.33）** |
| M2 | 参数对比接口 + 前端对比卡 | ⏳ **待开发**（§5.1；后端接口与检索去噪 v5.26/v5.32 已实现，v5.35 移除前端入口，视为未交付——重启只需重做入口/前端） |
| M2.5 | 场景化入口 chip（查询模板） | ✅ **已落地（v5.27）** |
| M3 | 器械真题评测集 + 跑指标 | ✅ **已落地（v5.34，50 题 v2，Recall 0.8833 / Correctness 0.8733，§11）** |
| M4 | README 场景化重写（展示材料） | ⏳ 主要待 M2（评测 ✅ 可先行补工作流/成绩） |

## 10. 待用户确认

1. ~~语料来源走哪条路~~ → **已定：用户自行搜集下载**（§4.2 路径 A），收齐后批量入库
2. ~~场景特化功能怎么做~~ → **已定并落地：§5.2 场景 chip**（v5.27）
3. ~~评测集 30 题 vs 50 题~~ → **已定：v5.34 全删重建 50 题 v2**（§6.3）
4. **参数对比是否重启**（§5.1 ⏳ 待开发）——前端入口已隐藏、视为未交付；后端接口 + §5.4 去噪已就绪，如需重启由用户确认重新开放前端

## 11. 实测结果（v5.34 v2 题集，当前权威）

> 库 `col_b7b876b1`（12 份文档 / 888 chunk / 149 带图 drawing 块）。
> 跑法：`python -m app.m9_eval.runner --testset tests/testsets/testset_device_v2.json --collection col_b7b876b1 --mode <retrieval|e2e>`（standard reranker，@5 生产口径）。
> 报告：`backend/tests/reports/retrieval/current/run_retrieval_device_v2.json` / `run_e2e_device_v2.json`；**详表与逐题归因见 [retrieval_comparison.md](../../backend/tests/reports/retrieval_comparison.md) v5.34 节**。
> ⚠️ **旧题集（30/50 题）全部实测已归档**：[device_scenario_eval_archive.md](../archive/device_scenario_eval_archive.md)。题集全删重建非增量，**数值与 v2 不可横比**。

### 11.1 检索指标（`--mode retrieval`，50 题 v2，@5 生产口径，`run_retrieval_device_v2.json`）

| 指标 | 数值 |
|---|---|
| Context Recall | **0.8833** |
| Context Precision | 0.2960 |
| Context Precision（加权） | 0.4723 |
| nDCG | **0.9305** |
| gold_rank（平均/中位） | 1.39 / 1.36 |
| gold_rank 事实覆盖率 top1/top3/top5 | 0.747 / 0.862 / 0.906 |

**按难度**（Recall@5）：easy **0.8800**（25 题）｜medium **0.9667**（15 题）｜hard **0.7667**（10 题）。

**按题型**（Recall@5）：

| 题型 | 题数 | Recall@5 | nDCG |
|---|---|---|---|
| fact_single | 18 | **1.0000** | 0.9836 |
| table_numeric | 12 | 0.7917 | 0.9059 |
| image_only | 10 | **0.9000** | 0.9821 |
| summary | 10 | 0.7667 | 0.8127 |

### 11.2 生成指标（`--mode e2e`，50 题 v2，`run_e2e_device_v2.json`）

| 指标 | 数值 |
|---|---|
| Faithfulness | **0.9950** |
| Answer Relevance | 0.9314 |
| Correctness | **0.8733** |
| Citation Accuracy | **0.8960** |

**按难度**（Correctness）：easy 0.8400（25）｜medium **1.0**（15 题全对）｜hard 0.7667（10）。

**按题型**（Correctness / Faithfulness）：fact_single 0.9722 / 1.0 ｜ table_numeric 0.8333 / 1.0 ｜ image_only 0.85 / 1.0 ｜ summary 0.7667 / 0.975。

**未满分 8 题（Correctness < 1）**：DV-IO-007 / DV-TN-005 / DV-TN-010（easy，检索 0）+ DV-TN-011（medium 0.5）+ summary hard 四题（DV-SUM-003/007/008/009，0.25–0.75）。retrieval 满分但 e2e 掉分 2 题（DV-FS-001 / DV-IO-005，0.5）= 主 fact 答、副 fact 漏，材料在上下文非检索缺口。

**读数**：

1. **总体 Correctness 0.8733（旧 30 题诚实口径 0.6286 → 对比含 comparison 题旧口径 +0.2447 主要来自剔除 comparison）**——comparison 检索短板并未闭环，只是新题集不再考核。诚实性由 Faithfulness 0.9950 保障（judge_failed=0），无编造。
2. **summary 是最大拖累项**：新题型，hard 段 4 题 Correctness 0.25–0.75 全漏——目标块分散、需跨块聚合，与既有「精排截断短块/数字块」同源（§6.3 读数 2）。
3. **fact_single / image_only / table_numeric 三题型检索+生成双稳**：retrieval 除 table_numeric（0.7917）均 ≥0.9，e2e Correctness ≥0.83；多模态图块（image_only）在 v2 题集下表现良好（0.9 / 0.85）。
4. **⚠️ 跨题集不可横比**：v5.33 旧题集按题型/难度分层数字均已成历史（归档），上述数字只与本库后续版本纵向演进对比。