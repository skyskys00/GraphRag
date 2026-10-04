# 器械说明书垂直场景落地方案

> **版本：** v0.8（§5.1 参数对比 + §5.2 场景 chip + 语料 10 份 + 30 题评测集 + 泄漏治理 v5.30 + 评测窗口收敛 top5 单窗口 v5.31）
> **状态：** 部分实施 —— 场景特化功能 ✅（v5.26 参数对比 / v5.27 场景 chip）｜ 语料扩充 ✅（v5.28，10 份）｜ 评测集 ✅（v5.29，30 题跑通，见 §11）｜ comparison 泄漏治理 ✅（v5.30，退役 per_doc_queries，见 §6.1/§11.3）｜ 评测窗口收敛 ✅（v5.31，top5 单窗口对齐生产 RERANK_TOP，见 §11）
> **更新：** 2026-10-04
> **定位：** 把「有源器械说明书」库从验证态做成**可展示的垂直场景**——回答 内部参考资料 P1「找一个垂直场景落地」
> **契约：** 复用现有 M7 接口；新增 `POST /compare`（见 §5.1，已落地）
> **上游：** [M5 检索层](M5_retrieve.md) / [M6 生成层](M6_generate.md) / [M7 交互层](M7_interact.md) | **下游：** [M8 前端](M8_frontend.md) / [M9 评测层](M9_evaluation.md)
> **依据：** [`内部参考资料.md`](../内部参考资料.md) P1 ｜ memory `medical-device-corpus-sources` ｜ [MULTIMODAL.md](MULTIMODAL.md)
> **运行：** 语料走 M7 `POST /docs` 上传（多模态自动生效；批量用 `backend/scripts/ingest_device_corpus.py`）；对比见 M8 侧栏「参数对比」；场景 chip 见问答页输入框上方（器械库）
> **变更历史：** 见 [`CHANGELOG.md`](../CHANGELOG.md) v5.26（§5.1 落地）/ v5.27（§5.2 落地）/ v5.28（语料 10 份）/ v5.28.1（sidecar 键更正）/ v5.29（30 题评测集 + M9 裁判 hit 修复 + comparison per-doc）/ v5.30（comparison 泄漏治理：退役 per_doc_queries）

## 1. 场景与用户

**场景**：有源医疗器械说明书的**智能问答与参数速查**。

**为什么选它**（不是从 内部参考资料 推荐列表里随便挑的）：

- 与用户背景天然咬合：医学生 + 图像识别硕士 ⇒「医学垂直 + 多模态」双重契合，面试叙事完整
- 多模态不是硬凑：器械说明书的核心信息形态就是**界面截图与图片型表格**（见 MULTIMODAL.md §2.1 实证），纯文本链路天然召不回
- 语料有真实结构：参数表（table_numeric）、按键/报警说明（fact_single）、连接图、界面截图、型号对照（comparison）——五类考点齐全

**目标用户**（面试叙事用，不实现用户体系）：

| 角色 | 典型诉求 |
|---|---|
| 医院设备科 / 临床工程师 | 「这台模拟仪的静态压力量程是多少」「报警 E03 怎么处理」 |
| 器械厂商售后 | 「LSP-1C 支持哪些规格的注射器、对应内径多少」 |
| 采购 / 检验科 | 「A 型号和 B 型号的精度等级差多少」 |

## 2. 现状与差距

**已有底子**（截至 2026-10-02）：

- 库「有源器械说明书」`col_b7b876b1`：**10 份真实说明书**（v5.28 完成扩充），PDF 原件在库内 `uploads/`；合计 857 chunk、149 个带图 drawing 块
- 多模态全链已打通：图片 → 视觉描述 → 独立 TextUnit → 索引 → 引用带图 → 前端渲染（v5.25 ~ v5.25.6 全部验证通过）
- 检索/生成/前端/评测骨架齐备（M5 三路召回 + M6 引用溯源 + M8 展示 + M9 评测框架）

**对照 内部参考资料 P1 的四项要求**：

| P1 要求 | 现状 | 本轮目标 |
|---|---|---|
| 真实数据 20-50 篇 | **10 份 ✅**（v5.28） | ~~~10 份~~ 已达 |
| 针对该场景的评测数据 | 无（现有 65 题是客服/行政） | **器械场景 30 题** + 跑指标 |
| 讲清解决了什么问题 | 有素材未成文 | 写入 README（§7） |
| 可演示 | 无 | 本轮不做（P3 部署另议） |

## 3. 方案总览

```
线 1：语料扩充（~10 份）
  厂商官网/公开渠道 → PDF → M7 POST /docs → 多模态自动生效（M1 视觉 → M2 → M3 → M5）
        ↓
线 2：场景特化功能
  参数对比（多型号并排）  ← 复用 M5 检索，新增薄接口
  场景化入口 chip（查询模板）  ← 纯前端，不新增接口
        ↓
线 3：场景评测集
  器械 30 题（5 类考点）→ M9 runner → 检索/生成指标
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

## 5. 线 2：场景特化功能

### 5.1 多型号参数对比 ✅（v5.26 已落地）

**场景**：用户想知道「同样一个参数，几个型号分别是多少」。

**设计取舍——不做 LLM 归并**。常见做法是「抽参数 → LLM 归并成表」，但那样引入不稳定性（归并错位、编造数值）。本方案改为**检索式对比**：

```
POST /compare
  body: { collection_id, query: "静态压力量程", doc_ids: ["<id1>", "<id2>", ...] }
  ↓ 对每个 doc_id 单独跑一次 M5 检索（限定该文档）
  ↓ 各取 top-1 片段（含引用溯源）
  → { query, rows: [{ doc_id, doc_name, snippet, page_range, score, image_path? }] }
```

- **优点**：零 LLM 成本、可溯源（每个值都指回原文页码）、不会编造
- **前端**：并排对比卡（列 = 型号，行 = 该参数的原文片段 + 页码 + 可选缩略图）
- **诚实边界**：某型号没有该参数时**如实留空**，不推断

**实现（v5.26）**：`app/m7_interact/compare.py` + `POST /compare`（M7）｜ `components/DeviceCompare.tsx` + 侧栏「参数对比」（M8）。

**实测**（铭昇 H2-5000IBP + 融柏 LSP-1C，三组 query）：

| query | 融柏（注射泵） | 铭昇（血压模拟仪） |
|---|---|---|
| 精度等级 | 未找到（留空） | 0.75 → ±0.15%F.S（P3） |
| 内径 | 0.717 内径输入（P4）+ 0.567 界面截图（P15） | 未找到（留空） |
| 电池 | 0.407 电源说明（P3） | 0.721 18650 锂电池（P2） |

相关性门槛实测生效：问「内径」时铭昇的无关片段（0.13 级）被正确滤除，前端显示「未找到该参数」而非硬凑。

### 5.2 场景化入口 ✅（v5.27 已落地）

问答页加器械场景的快捷入口（chip）：常用查询模板（「报警含义」「操作步骤」「规格参数」）。本质是 query 模板，**不新增接口**。

**实现（v5.27）**：`InputBar.tsx`（`deviceMode` prop + `DEVICE_TEMPLATES`）+ `App.tsx`（`currentCollection.name.includes('器械')`）+ `App.css`（`.scene-row`）。

- **显示条件**：按当前库名含「器械」判断，非器械库不显示（避免「报警含义」在客服库错位）
- **点击行为**：填入模板文本（如「___的报警含义是什么？」）并把光标停在待补全处，**不直接发送** —— 模板原文太短、直接检索质量差
- **实测**：器械库显示三 chip；点「报警含义」→ 输入框 `的报警含义是什么？` + 光标 0 + 聚焦；切「销售业绩」库 chip 消失（chrome-devtools-mcp）

### 5.3 不做的

- **器械卡片自动摘要**：需要 LLM 逐份抽取 + 存储，收益（展示价值）与成本不匹配，本轮不做
- **拍照识报警图标**：需要图像检索（CLIP 类），超出当前架构

## 6. 线 3：场景评测集

**规模**：30 题（对齐行政库 30 题口径，便于横向对比）。

**五类考点分布**（器械场景特有）：

| 题型 | 题量 | 示例 |
|---|---|---|
| `table_numeric` | 8 | 「50cc 规格注射器的内径是多少毫米」（图片型表格） |
| `fact_single` | 8 | 「静态压力量程是多少 kPa」 |
| `image_only`（**新增考点**） | 6 | 「灌注模式下界面显示的运行速度是多少」（答案只在图上） |
| `comparison` | 4 | 「H2-5000IBP 与 LSP-1C 的精度等级分别是多少」 |
| `unanswerable` | 4 | 「支持蓝牙吗」（负对照，防编造） |

> `comparison` 题（v5.29 曾要求配 `per_doc_queries` 子查询字段逐 doc 检索）——**v5.30 已退役**：每文档定制子查询是评测独有输入、线上 `compare.py` 不消费，属 ground_truth 信息泄漏红线（见 `CHANGELOG v5.30` 与项目 `CLAUDE.md`「评测信息泄漏红线」）。现改为每 doc 直接用完整 `question` 检索（与生产同形），字段已从题集删除。历史机制见 §6.1 第 4 项。

**关键纪律**（v5.25.3 教训，2026-10-02 收紧；v5.29 修正口径）：`image_only` 题的**答案完整内容必须存在于 `block_type=drawing` 块**——多模态有贡献的必要条件。

> ⚠️ **不要用 `pdftotext -layout` 文本层 0 命中当基线**——它不够严。实测 `YASEE`、`实时压力`、`绑定医生`、`多语言`、`UNITS`/`HOLD`/`DIF` 等串在文本层「0 命中」（图片里的字），却出现在 chunk 的 `paragraph`/`table` 块 content 中。文本层基线会误判为「干净」。
>
> 核验：在 `data/collections/<col_id>/chunks*.jsonl` 上按 `block_type` 过滤，逐串确认答案命中 `drawing` 块。注意 `page_range` 是 **0-based**（`page_range=N` = 人类第 N+1 页）。
>
> **口径修正（v5.29）**：早期「非 drawing 块 0 命中」过严。实测 6 题 **5 题严格合格**；**DV-IO-004 保留并记为已知例外**——其 KF3（绑定医生/医生解绑/多语言）除命中 `drawing`（第 19 页设置图，答案完整）外，还在 `table`（第 20 页）命中。原因非出题错误：**MinerU 把第 20 页「绑定医生机构」子页图误判为 `type=table`**（该条目同时带 `img_path`，本质是图片），OCR 出的子页功能名与设置页功能项重合。不变量应为「**答案完整存在于 drawing 块**」（DV-IO-004 满足），而非「答案词不许出现在其他块」。
>
> 附带发现：10 份文档 69 个 `table` 块中约 2 个属此类「图片被误判为 table」（博声 1、KE-2000 1），低频，是 M1 解析质量的真实缺陷，暂不修。

**运行**：需先补 4 处 runner 兼容改动（见 §6.1，已落地），再执行——

```bash
python -m app.m9_eval.runner --testset tests/testsets/testset_device_30.json \
    --mode retrieval --collection col_b7b876b1 \
    --report tests/reports/run_retrieval_device30.json
```

### 6.1 runner 兼容改动（2026-10-02，v5.29）

新式 collection（`col_<uuid8>`）与旧式扁平库（`default_ws` 等）布局不同，M9 runner 原本只认后者，跑器械库会解析到不存在的 `data/col_b7b876b1/`。改动四处：

| # | 文件 | 改动 |
|---|---|---|
| 1 | `app/m9_eval/testset.py` | `VALID_CATEGORIES` 补 `"image_only"`（原缺失 → 6 题校验失败） |
| 2 | `app/m9_eval/runner.py` | 新增 `_resolve_collection()`：`col_*` 走 `collection_paths()` 解析到 `data/collections/<col_id>/`，旧三库（`default`/`eval_cservice`/`eval_admin`）保持扁平映射，零回归 |
| 3 | `app/m9_eval/runner.py` | `_build_deps()` 补加载 entities（`load_entities_async`）——原缺失会系统性低估生产 recall（线上 `retriever.py` 是加载的） |
| 4 | `app/m9_eval/runner.py` | comparison 题走 **per-doc 检索**（`_comparison_targets` + `_interleave`）：v5.29 按题集 `per_doc_queries[文件名]` 子查询逐 doc 检索；**v5.30 退役该字段**，每 doc 直接用完整 `question`（见下勘误） |

**per-doc 为何曾需子查询**（v5.29 实测，可复现）：用完整对比问题（含两个型号名）做 per-doc 检索时，**其他型号名会把该文档的向量召回打到 0**（LightRAG 向量路 `cosine=0.2` 阈值下相似度不足）。实测 `allowed_docs=[LSP-1C]`：完整对比 query → **0 条**；去掉其他型号名 → **5 条**。复跑：`cd backend && python3 scripts/probe_perdoc_subquery.py`。

> ⚠️ **v5.30 勘误（泄漏治理）**：上述「配置子查询」机制**已废弃**。每文档定制子查询 = 评测独有输入（生产 `compare.py` 同一 query 打所有 doc），用它抬高的 comparison 数字只配当「检索上界」不能当产品参考——实测揭开：v5.30 退役后 comparison 用完整 question 检索，数字回落至真实对比检索质量。`probe_perdoc_subquery.py` 不改也不删（保留该检索缺陷的可复现证据，供后续检索改进参考）。

旧三库（行政/cservice）跑法不变，`--workspace <ws>` 仍走扁平路径。

## 7. 验证方法

| 步骤 | 检查项 |
|---|---|
| 1. 语料入库 ✅ | 10 份全部 `ready`（v5.28 已达成）；PG `lightrag_doc_chunks` 计数与 M2 产出对齐 |
| 2. 多模态生效 ✅ | 149 个 drawing 块带 `image_path`（v5.28 实测）；预览面板图片可渲染 |
| 3. 参数对比 | 对 3 个型号问同一参数 → 各自返回正确片段 + 页码；缺参数的型号如实留空 |
| 4. 评测集 | 30 题跑通，输出检索 + 生成指标（实测见 §11） |
| 5. 诚实性 | `unanswerable` 题正确拒答，不编造 |

## 8. 不做的事

- **不做用户体系/鉴权**——单机单用户，无意义
- **不做在线部署**（P3 另议）——当前后端依赖本地 Xinference（bge-m3 + reranker）与 PG，上云需重构
- **不做跨厂商参数标准化**——不同厂商参数命名不统一，强行归一化会引入错误；本方案用检索式对比规避
- **不改 M3/M5 核心**——复用现有检索链路，新增的只是 M7 薄接口

## 9. 里程碑

| 阶段 | 内容 | 依赖 |
|---|---|---|
| M1 | 语料扩充到 ~10 份并入库 | ✅ **已落地（v5.28，10 份 / 857 chunk / 149 图）** |
| M2 | 参数对比接口 + 前端对比卡 | ✅ **已落地（v5.26）** |
| M2.5 | 场景化入口 chip（查询模板） | ✅ **已落地（v5.27）** |
| M3 | 器械 30 题评测集 + 跑指标 | ✅ **题集已落地（v5.29）；指标见 §11** |
| M4 | README 场景化重写（作品集材料） | ⏳ 待 M2/M3 |

## 10. 待用户确认

1. ~~语料来源走哪条路~~ → **已定：用户自行搜集下载**（§4.2 路径 A），收齐后批量入库
2. ~~场景特化功能怎么做~~ → **已定并落地：§5.1 参数对比**（v5.26）
3. **评测集 30 题是否合适**（vs 扩到 50 题）—— 待语料到位后再定

## 11. 实测结果（2026-10-03 检索恢复 / 2026-10-04 生成项补齐）

> 库 `col_b7b876b1`（10 份器械说明书 / 857 chunk / 149 带图 drawing 块）。
> 跑法：`python -m app.m9_eval.runner --testset tests/testsets/testset_device_30.json --collection col_b7b876b1 --mode <retrieval|e2e>`。
> **§11.1 检索指标为 v5.30 泄漏治理后诚实口径**（报告 `backend/tests/reports/run_retrieval_device30_v531_leakfix.json`）。
> **⚠️ 口径说明（v5.31 起）**：@8 列已在文档层面作废——`eval_top_n` 默认回 5（对齐生产 `RERANK_TOP`），runner/report 双窗口收敛为单窗口，LLM 调用减半；v5.9 已实测「top5 是最优窗口」（`205b445` commit）。以下只报告 **@5 = 生产口径**，@8 仅作历史对照并标注「已否定诊断残留」。§11.1 摘要数字与报告 JSON 的 `@5` 列一致。
> **§11.2 生成指标为诚实口径**（2026-10-04 已重跑，报告 `backend/tests/reports/run_e2e_device30_honest.json`）；`run_e2e_device30.json`（v5.29 泄漏窗口口径）仅作上界参考，不可直接比较。
> **历史报告口径不同，均不可直接比较**：`*_pre_hitfix.json`（judge hit bug 前，假阳性）、`run_retrieval_device30.json`（v5.29 泄漏口径），见 §11.3。

### 11.1 检索指标（`--mode retrieval`，30 题，v5.30 诚实口径，耗时 721.1s，报告 `run_retrieval_device30_v531_leakfix.json`）

| 指标 | @5（生产口径） |
|---|---|
| Context Recall | **0.6635** |
| Context Precision | 0.2362 |
| Context Precision（加权） | 0.3263 |
| nDCG | 0.6906 |

> @8 对照（历史已否定诊断残留，仅存档）：Recall 0.7019 / Prec 0.1756 / PrecW 0.2861 / nDCG 0.7623。

**Gold Rank**：平均 2.43 / 中位 2.46。事实覆盖率：top1 **0.4551** → top3 0.7051 → top5 0.7276。（top8 0.8269 为 8 窗口产物，@8 已作废同上。）

**按题型**（Recall —— 仅 @5）：

| 题型 | 题数 | Recall@5 |
|---|---|---|
| table_numeric | 8 | 0.8125 |
| fact_single | 8 | 0.6562 |
| image_only | 6 | 0.6667 |
| comparison | 4 | 0.3750 ⚠️ |
| unanswerable | 4 | —（跳过） |

**逐题 comparison**（报告 `by_question`）：CP-001 0/3；CP-002 0/2；CP-003 2/2（top5 全中）；CP-004 1/2（top5 中博声侧漏 KE-2000 侧——真漏召回）。

**按难度**（Recall@5）：easy（6 题）0.6667｜medium（16 题）0.8542｜hard（8 题）0.3750。

**读数**：

- **总体 Recall 相比 v5.29 泄漏口径（0.7436）下降**——主因是 comparison 从「每文档定制子查询」回落为与生产同形的完整 question 检索（0.4583 → 0.3750@5）。这是泄漏治理的**预期效果**；旧数字不可比（§11.3）。
- `table_numeric` 依旧最稳（0.8125）——参数表格切分/检索最"干净"。
- **comparison 0.3750@5 是当前最大短板**——CP-004 fact2（博声 APP）确认为**真漏召回**（top5 上下文缺博声侧块），根因属 M2 切分粒度（`CHANGELOG` v5.29「遗留」段，另立任务）。
- **Context Precision 整体偏低（0.24）**：每题取 20 条上下文而多数题只需 1-3 条 ⇒ 分母天然偏大，绝对值不宜单独解读；结合 GoldRank（top1 覆盖 0.46、top5 覆盖 0.73）一起看。

### 11.2 生成指标（`--mode e2e`，30 题，诚实口径，耗时 1154.9s，报告 `run_e2e_device30_honest.json`）

> **声明（诚实口径）**：2026-10-04 用 v5.31 收敛后 runner 重跑（退役 `per_doc_queries`、单窗口 @5、p2 缓存键）——下表为**生产可复现口径**。旧表（v5.29 泄漏窗口口径，`run_e2e_device30.json`，1243.9s）仍在 `device30.json` 归档，仅作「comparison 检索上界」参考，数字不可直接比较（comparison 用 per_doc_queries 定制子查询 + @8 窗口）。
>
> **对比要点**：诚实口径下 `table_numeric`/`fact_single`/`image_only` **忠实度满格（1.0）**（旧泄漏口径 0.72–1.0 不等）——证 v5.29 的 comparison 低 faith 是**生成阶段拿不到第二型号而在上下文外编造**所致，非能力极限；诚实口径生成层整体显著更"诚实"。

| 指标 | 诚实（当前） | 旧泄漏口径（仅参考） |
|---|---|---|
| Faithfulness（忠实度） | **0.9843** | 0.7757 |
| Answer Relevance（答案相关性） | 0.7617 | 0.8650 |
| Correctness（正确性） | **0.6286** | 0.7950 |
| Citation Accuracy（引用准确率） | 0.6764 | 0.6707 |

**按题型**：

| 题型 | 题数 | Faithfulness | Answer Rel. | Correctness | Citation Acc. |
|---|---|---|---|---|---|
| table_numeric | 8 | **1.0000** | 0.9000 | 0.8571 | 0.5214 |
| fact_single | 8 | 1.0000 | 0.6937 | 0.5938 | 0.8396 |
| image_only | 6 | 1.0000 | 0.9333 | 0.7500 | 0.6833 |
| comparison | 4 | 0.8825 | 0.5000 | **0.0875** ⚠️ | 0.5312 |
| unanswerable | 4 | 1.0000 | 0.6250 | 0.6000 | 0.7947 |

**拒答验证（§7 第 5 项「诚实性」）—— 4/4 全部正确拒答，无编造**（诚实口径下依旧成立）：

| 题 | 问的是 | 答案首句 |
|---|---|---|
| DV-UA-001 | 哪款支持 Wi-Fi | 「根据现有检索材料，无法确认…没有出现任何关于"Wi-Fi""无线联网"…的描述」 |
| DV-UA-002 | 电池循环充放电次数 | 「根据现有检索材料，无法回答"是否有设备给出了电池循环充放电次数…"这一问题」 |
| DV-UA-003 | 双相波脉冲宽度 | 「根据给定的检索材料，无法回答…没有任何关于双相波除颤脉冲宽度（ms）的记载」 |
| DV-UA-004 | 手机 App 远程固件升级（OTA） | 「现有材料不足以回答该问题。材料中没有提到任何设备支持通过手机 App 进行远程固件升级（OTA）」 |

**读数**：

- **忠实度全面满格（0.9843）且零判例失败（judge_failed=0）**——生成层在诚实口径下几乎不再编造上下文外事实。这直接反证 v5.29 comparison 低 faith（0.43）= 泄漏上界喂出的上下文根本支撑不住答案的产物，诚实口径下答案自动收敛到上下文内。
- **comparison Correctness 0.0875 崩盘、faith 反升至 0.8825**——诚实口径下 4 题全部答非所问/只答一边：DV-CP-001/002/003 直接「现有检索材料中没有…无法比较」拒答（因为完整对比 query 一个型号的块都没进 top5），DV-CP-004 只答出英菲泰克一侧、博声侧数据没进上下文。**这是「诚实的失败」**：模型宁可拒答也不编造，但 Correctness 归零。根因仍在**检索侧 comparison 漏召回**（§11.1 Recall 0.375@5 + §6.1 完整对比 query 向量召回打 0 的机制），生成只是忠实呈现了检索短板。整条链路归因：**comparison 的瓶颈在 M5/M2 检索，不在生成**。
- **Answer Relevance 0.7617**（旧 0.8650）：诚实口径下比较题被迫拒答/答单边，相关性天然降低——同样归因于检索。
- `table_numeric` Correctness 0.8571 **夺冠且忠实度满分**（旧泄漏口径 0.8187）——唯一"检索+生成双稳"的题型。
- **Citation Accuracy 与旧口径基本持平（0.6764 vs 0.6707）**，`fact_single`/`table_numeric`/`unanswerable` 均 ≥0.52——引用准确率受检索短板影响小（引用对得上上下文即可）。`table_numeric` 引用仅 0.5214 维持低位现象依旧（参数表整表一块、复述正确数值但标块偏差）。

> **旧泄漏口径表格存档**（`run_e2e_device30.json`，仅上界参考，不可比较）：Faithfulness 0.7757 / Answer Relevance 0.8650 / Correctness 0.7950 / Citation Accuracy 0.6707；按题型——table_numeric 1.0/0.9375/0.8187/0.5542、fact_single 0.7188/0.9125/0.8812/0.9167、image_only 0.8750/0.9333/0.7500/0.8750、comparison 0.4300/0.7375/0.5875/0.2807、unanswerable 0.6375/0.6500/0.8500/0.4955。当时 4/4 拒答亦全部正确。

### 11.3 口径说明（重要）

> **本节为历史口径调查档案（v5.31 起）**：@8 窗口已在文档层面作废（`eval_top_n` 回 5 对齐生产 `RERANK_TOP`，§11 引语），以下含 @8 数字/切片分析者均为作废前调查记录，只作「为何 @8 不可信」的历史依据与可复现证据；**当前合法口径只有 @5 = 生产口径**。

1. **`unanswerable` 题不参与 Recall 计算**：runner 对这类题跳过 recall（`context_recall_detail=None`），`by_category.unanswerable.context_recall = 0.0` 只是**占位值**；**总体 Recall 的分母是 26 道可答题**（v531 诚实口径校验：4×0.375 + 8×0.8125 + 6×0.6667 + 8×0.6562 = 17.2498，17.2498/26 = **0.6635 ✓**）。这类题的考点在 e2e 的**拒答行为**（防编造），见 §11.2。
2. **旧报告（`*_pre_hitfix.json`）数字偏高，不可比**：其 Context Recall 0.9199 是**裁判 bug 造成的假阳性**——`judge.py` 曾丢弃 LLM 输出的 `hit` 字段，`context_recall.py` 遂用 `score >= 0.5` 反推命中；而 prompt 里 `score` 是「**判定置信度**」（`hit=false, score=0.95` = 95% 确信"上下文里没有"），被错误翻转成命中。扫 4255 条缓存实测矛盾率 **13.4%**。修复后（v5.29，`_PROMPT_VERSION` bump 至 `p1_llm_hit`）为 0.7436。
3. **comparison 的 0.625 → 0.4583 混合了两个改动**：旧报告既无 per-doc 检索（`comparison_mode` 缺失）也未修 hit。per-doc + 子查询**提升**该题型召回（机制与可复现证据见 §6.1：同一文档 `allowed_docs=[LSP-1C]`，完整对比问题 → **0 条**、去其他型号名 → **5 条**，`backend/scripts/probe_perdoc_subquery.py` 可复跑），hit 修复**压低**假阳性，净效果为下降。两项改动方向相反但都正确，详见 §6.1 与 memory。
4. **v5.30：退役 per_doc_queries，comparison 口径归位（泄漏治理，见 §6.1 勘误）**：v5.29 的 comparison Recall **0.4583 是「每文档定制子查询」抬出来的上界**，不是产品能复现的数字——生产 `compare.py` 同一 query 打所有 doc，没有任何评测独有输入。v5.30 起题集删除 `per_doc_queries`，`_comparison_targets` 每 doc 直接用完整 `question` 检索（窗口当时统一为 8；**v5.31 起收敛回 5 对齐生产**，见 §11 引语）。诚实口径实测（v531，§11.1）：总体 Recall **0.6635@5**，comparison **0.3750@5**。v5.29 的 0.4583 保留为「上界参考」。若产品后续真做「每 doc 多轮子查询」能力，再按生产能力重新引入评测方式（从属于检索改进，非本轮泄漏治理范围）。
5. **comparison Recall@8(0.125) < Recall@5(0.375) 非单调 = 裁判归因噪声，非 runner bug（v531 调查定论）**：排除顺序——① runner 窗口切片无 bug（同一交错列表 `[:5]`/`[:8]`，top8 ⊇ top5 硬保证）② 非检索泄漏 ③ 非缓存串键（`p2_llm_hit_fullctx` 缓存键含完整 `context_str`，top5/top8 上下文不同 ⇒ 键不同 ⇒ 各自独立 LLM 调用）。实证于 judge 缓存：DV-CP-003 fact2（KE-2000）同一事实两次调用判出 True/False 两条缓存——top5 按 doc id 归因判中、top8 要求逐字「KE-2000」型号字符串判 miss，同一证据块在场却两次标准不一。诚实值取 @5=0.3750；DV-CP-004 fact2 才是真漏召回。修复方向（让裁判以「文档归属」归因而非型号字符串逐字匹配）从属于检索改进，非泄漏治理范围，本轮不改。
6. **v5.29 各题型 @5=@8 精确相等 = p1 缓存键截断的共享产物（2026-10-04 复盘定论）**：p1 缓存键取 `context_str[:500]`（前 500 字符），top5/top8 交错上下文前缀相同 ⇒ 同一 judge 判定被两窗口共享 ⇒ **v5.29 的 @5 从未独立评测**。证据：v5.29 报告 30/30 题「@5 与 @8 逐 fact 全一致」（含 8 题多 fact 的 fact_single / table_numeric），非巧合。v531 键改完整 `context_str` 后独立判定——**fact_single 0.6562@5 / 0.8438@8**：DV-FS-002（满电持续、计时误差）与 DV-FS-008（更换荧光帽）三 fact 均「@5 miss / @8 hit」，v531 reason「上下文仅涉电磁/蓝牙/清洁方法」自洽，答案块真实落 top6-8。故 **v5.29 的 fact_single 0.8125 是 top8 判定回声，0.8125→0.6562 是口径回归而非随机噪声**（诚实的 top5 能力一直 ≈0.65）。
