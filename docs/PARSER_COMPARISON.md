# 解析器选型复核：MinerU vs Docling（源码拆解后）

> **版本：** v1.3
> **状态：** 已落地（选型结论修订）+ 实测复核（2026-09-21，见 §10）
> **更新：** 2026-09-21
> **定位：** 基于源码实测复核解析器选型与分工，检验 textunit 契约字段合理性
> **契约：** 选型结论 → [M1 解析层](modules/M1_parse.md) 实现依据 → [textunit 契约](modules/M0_contracts/textunit.md) 字段验证
> **上游：** [`ARCHITECTURE.md`](ARCHITECTURE.md) §2.2 | **下游：** [M1_parse.md](modules/M1_parse.md) / [M0_contracts/textunit.md](modules/M0_contracts/textunit.md)
> **依据：** MinerU v3.4.5 / Docling v2.126.0 源码拆解
> **运行：** 分析文档，无运行命令
> **变更历史：** 见 [`CHANGELOG.md`](CHANGELOG.md)

---

## 1. 结论先行

**原分工成立，无需推翻**：MinerU = PDF/扫描件主力；Docling = 非 PDF（office/网页）多格式兜底。但有三处**必须修订认知**：

1. **MinerU 不是「内置 PP-OCRv6」**——v3.4.5 的 OCR 是 PDF-Extract-Kit-1.0 权重 + pytorchocr 推理（Paddle 系架构的 PyTorch 复刻），pip 主链不依赖 paddlepaddle（ARCHITECTURE §2.2 表述需修正）。
2. **LightRAG 官方 parser 同时内置了 mineru 与 docling 两套消费链**（`parser/external/{mineru,docling}/`，client/ir_builder/parser），M1 写 adapter 时**优先沿用官方链**。
3. **docx 原文定位是两家的共同盲区**（MinerU 不支持 docx；Docling 支持 docx 但**不读取 w14:paraId**），我们契约里 `anchor` 的 docx 部分必须自研——已在契约 v2 标注。

---

## 2. 定位与格式覆盖

| 维度 | MinerU v3.4.5 | Docling v2.126.0 |
|---|---|---|
| 定位 | PDF/扫描件/图片 + Office 文档解析，面向中文文档 | 多格式统一解析（office/网页/PDF/电子书/Markdown） |
| 输入格式 | PDF（数字+扫描）、常见图片、**docx / pptx / xlsx**（原生解析，零模型） | pdf、docx、pptx、xlsx、html、epub、md、txt、图片、URL |
| 底层 | 自研 pipeline（PDF：layout/公式/表格/OCR；Office：原生解析） | SimplePipeline（office 零模型）+ StandardPdfPipeline（PDF 重模型） |
| 中文版式 | 强（中文界第一梯队） | 弱于 MinerU（原判保持，实测见 §10） |

## 3. 结构保真与元数据可得性（与 TextUnit 契约直接相关）

| 信息 | MinerU | Docling | 对契约的影响 |
|---|---|---|---|
| 页码 | ✅ `content_list` 每项 `page_idx`（0 起始）；`_middle.json` 在 `page_info` | ✅ PDF `prov.page_no`；PPTX slide+1；XLSX sheet；HTML 估算页（不稳） | `page_range` ✅ 取值机制已确认 |
| 块坐标（bbox） | ✅ content_list 内嵌（归一化 0–1000）；middle.json 用 PDF 点——**三套单位易混** | ✅ `prov.bbox` | `anchor`（PDF）✅ `page_idx:bbox` |
| 块类型 | span 类型 + `text_level`（doc_title→1 级 / paragraph_title→2 级，**仅两档**） | `label`（paragraph/table/picture/formula/heading/list_item…） | `block_type` ✅ 映射规则已备 |
| 标题层级 | ❌ 只有两档，**深标题文档层级不足** | ⚠️ heading 有 level，但来自版面推断（非原文大纲） | `title_path` ⚠️ 复杂文档下预案：MinerU 档位受限，需接受 1–2 级或增强 |
| 表格 | ✅ 表格以 HTML 输出（可转 Markdown） | ✅ 结构化表格（单元格/行列） | 表格整块为 chunk：都可用 |
| 公式 | ✅ LaTeX + 图片（sha256 落 images/） | ✅ formula 块（模型输出 LaTeX） | 均可 |
| 阅读顺序 | ✅ 块 `index` 1..N | ✅ 文档按版面顺序组织 | 排序稳定 |
| 字节/字符级定位 | ⚠️ span 级有 | ⚠️ `prov.charspan` 在 PDF 是伪 `(0,len)` | 精细溯源优先级低，暂不设字段 |
| 原文图片可追踪 | ✅ images/ + 链接 | ✅ picture 块 + 资源 | 多模态后续 |

> 关键提醒：**Markdown 导出是"丢信息"出口**——Docling 的 `label/level/prov` 只在**JSON 导出**里；MinerU 的结构信息同样在 content_list/JSON。**M1 一律走 JSON/content_list，别用裸 Markdown 做结构对接**（Markdown 仅作轻量视图）。

## 4. 与 LightRAG 的兼容性

| | MinerU | Docling |
|---|---|---|
| LightRAG 官方链 | ✅ `parser/external/mineru/`（ir_builder 读 content_list→blocks.jsonl） | ✅ `parser/external/docling/`（同构 client/ir_builder/parser） |
| 接入形态 | 官方已把 page_idx/bbox/text_level 转成 blocks 结构 | 官方把 DoclingDocument 转 IR block（逃不掉的 label/prov 映射） |
| M1 建议 | **沿用官方链**，让 MinerU 直接产出 content_list+images（引擎 `-b pipeline`） | 沿用官方链 + 自补 docx 定位（见 §5） |

两边**兼容性相当**，差异只在"哪些是官方已铺好的、哪些要我们补"。

## 5. OCR 与模型依赖（实测核实）

| | MinerU v3.4.5 | Docling v2.126.0 |
|---|---|---|
| OCR 引擎 | **pytorchocr**（PyTorch 复刻推理 **PP-OCRv6** 权重，PDF-Extract-Kit-1.0 打包）——**主链不依赖 paddlepaddle** | **RapidOCR**（第三方库，onnxruntime/torch 跑 **PP-OCRv4/v5**，v6 语言码在接入；默认 ~52 语言） |
| 模型下载 | huggingface_hub / modelscope；`models download` 预取 | huggingface_hub snapshot_download（`HF_ENDPOINT` 镜像或预取） |
| 非 PDF 依赖 | 不适用（不支持 docx 等） | 零模型（SimplePipeline，纯解析） |
| PDF 依赖 | pipeline 引擎 CPU/MPS 可跑；hybrid 需大型 VLM（本机**不可行**） | StandardPdfPipeline 重模型（layout/table/formula/RapidOCR），MPS 支持弱 |

### 5.1 pytorchocr vs RapidOCR ——「同父异胞」的具体差异

同源：都源自 Paddle 团队的 **PP-OCR 模型架构与权重设计**（文本检测 DBNet 系 + 识别 CRNN/SVTR 风格），差别只在**复刻推理引擎与权重代际**。

| 维度 | MinerU（pytorchocr） | Docling（RapidOCR） |
|---|---|---|
| 权重代际 | **PP-OCRv6**（`ppocrv6_dict.txt`，`mineru/model/utils/pytorchocr/base_ocr_v20.py`；PDF-Extract-Kit-1.0 打包） | **PP-OCRv4 / v5**（rapid_ocr_model.py `_PPOCRV4_CODES`/`_PPOCRV5_CODES`；v6 语言码在接入） |
| 推理引擎 | 项目内自研 `pytorchocr`（PyTorch **加载 v6 权重**，`mineru/model/ocr/pytorch_paddle.py`） | 第三方库 `rapidocr>=3.9.1`（onnxruntime / torch 两后端） |
| 与主流程集成 | 与 layout/公式/表格模型**同一进程统一调度**（OCR 是 pipeline 一环） | 仅 **PDF 扫描件**的 OCR stage；office/web 不经过 |
| 语言覆盖 | 中英为主（面向中文文档） | 多语言（v4/v5 语言包 + v6 系，默认 ~52 语言） |
| 运行依赖 | 主链**不依赖 paddlepaddle** | 不依赖 paddlepaddle |
| 表格/公式配套 | 同进程另有 Paddle 系表格模型（PaddleTableModel / UnetTableModel）、公式模型 | 表格走 TableFormer/原生结构；OCR 只管文本 |

**差别大吗？** 对最终文本识别**结果差别不大**：同属 Paddle 系、中文都能识别，收敛质量取决于各自所用权重与默认语言。真正的差别在**代际与集成**：
- MinerU = **更新的 v6 权重 + 深度集成**（OCR 嵌进文档 pipeline，和版面/表格/公式统一调度）——贴合它"PDF 主力"的定位；
- Docling = **lib 化接入、只服务扫描 PDF**（office/web 根本不进 OCR）——作为兜底够用。

**对项目的影响**：两者都不需要我们单独部署 OCR，选型结论不变。唯一要记住的修正口径是：**MinerU 底子就是 PP-OCRv6 权重**（PyTorch 复刻推理、不装 paddlepaddle 运行时），与此前"误以为没有 v6"的表述不同。

## 6. 与 TextUnit 契约的相互校验

**契约字段合理性：成立。** 逐字段：
- `page_range`：两解析器对 PDF 都能给 → 合理。docx 无页码 → 契约明确不设，成立。
- `block_type`：都有结构标签 → 合理。
- `title_path`：依赖 heading——**MinerU 标题只有两档**，深标题（三级+）文档会不足。预案：接受 1–2 级（覆盖绝大多数章节结构），或 M2 对 MinerU 输出做标题级增强（低优先级）。
- `anchor`（docx）：Docling 不提供 paraId → **契约 v2 已把 docx 从"待拆"改为"需自研补丁"**，成立但要预期工作量。

**两家都缺、但对我们当前项目重要的字段**：无新增强需求。候选被排除的：`charspan`（精细溯源，伪跨度且优先级低）；`阅读顺序`（已由 index/顺序覆盖）；`图片资源`（多模态非本期）。

**一个来自契约反向的收窄**：既然 MinerU 走 content_list 即可拿到 page_idx/bbox/text_level，**契约的 `anchor`（PDF）不需要另设复杂定位**，`page_idx:bbox` 足够支撑「点击引用定位原文」MVP。

## 7. 运行 / 资源 / 部署

| | MinerU | Docling |
|---|---|---|
| CPU 可用 | ✅（pipeline） | ✅（office 零模型；PDF 慢） |
| MPS | ✅（pipeline 可用） | ⚠️ PDF 侧模型 MPS 支持弱 |
| 显存 | pipeline 轻；hybrid 需 ≥8GB（本机不启用） | PDF 链路重；office 链极轻 |
| 部署依赖 | 模型权重需预收（find HF 镜像） | 仅 PDF 需要模型权重 |
| 版本风险 | 迭代快，锁 tag | 迭代快，锁版本（含 docling_core） |

## 8. 结论与分工（修订，v1.2，附工作项）

1. **分工修订**：
   - **PDF / 扫描件 / 图片 → MinerU**（`-b pipeline`、`-l ch`；勿用默认 hybrid-engine）；
   - **docx / pptx / xlsx → MinerU**（office 后端，原生解析零模型，表格结构识别优于 Docling，实测见 §10）；
   - **html / epub / md / txt / rst / doc → Docling**（MinerU 不支持）。
2. **M1 落地要点**（自源码确认）：
   - 主线走 **content_list（JSON）+ images**，不要用裸 Markdown 做结构对接；
   - **沿用 LightRAG `parser/external/{mineru,docling}` 官方链**，在官方 IR block 上补我们的扩展字段（page_label / block_type / docx anchor），而不是重写解析器；
   - docx 的 `anchor`：两家都不提供 paraId → 自研补丁或暂退化为「文件+文本片段」（不影响核心链路）。
3. **ARCHITECTURE §2.2 需修订**：OCR 表述（PP-OCRv6 → pytorchocr/RapidOCR 实测）、补充 LightRAG 双消费链事实、补充「结构信息走 JSON 不走 Markdown」的工程纪律、**补充 MinerU 原生支持 docx/pptx/xlsx**（原表述仅说 PDF/图片）。

> v1.1 旧结论：「docx→Docling」已被实测推翻——MinerU office 后端表格识别更准、段落合并更合理，且同样零模型依赖。

## 9. 依据

- `mineru/docs/mineru.md`：MinerU 数据流、IO、content_list 结构、`-b pipeline` 选型。
- `docling/docs/docling.md`：DoclingDocument、prov、docx 无 paraId、OCR RapidOCR、JSON vs Markdown 信息差异。
- `docs/modules/M0_contracts/textunit.md`：契约 v2 字段与取值机制。

---

## 10. 实测复核（2026-09-21）

> 触发：选型落地后首次真机对比，验证 §2/§3 的断言。样本从 `backend/inputs/raw` 复制到 `backend/src_cmp/`（临时），产物落 `backend/data/parse_cmp/{mineru,docling}/`（**保留供溯源**）。MinerU 全程 `34.43s user 5.31s system 87% cpu 45.599 total`（2 PDF）；Docling office/web 零模型、PDF 走 pip 内置 RapidOCR（PP-OCRv6_small）。统计数据源为 M1 契约产物 `blocks.jsonl`。

### 10.1 样本与基线

| 类别 | 素材（raw） | 引擎 |
|---|---|---|
| PDF ×2 | 季度销售业绩复盘报告.pdf / EfficientNet-ZSR.pdf | MinerU + Docling 双向 |
| DOCX ×2 | 农作物季度种植运维工作总结.docx / 智能客服系统产品需求文档.docx | MinerU + Docling 双向 |
| HTML ×2 | 测试模板.html / 随访记录模板.html | Docling 单向（MinerU 不支持） |

> 注：md 不在对比内（MinerU 不支持）；两 html 为本地测试模板（`backend/inputs/raw/` 下），无真实个人信息；素材清单中 html 两项曾写重，按「测试模板.html + 随访记录模板.html」执行。

### 10.2 关键数据（blocks.jsonl 统计）

| 文档 | 引擎 | 块数 | 去空白字符 | heading | 表格块 | 样本 |
|---|---|---|---|---|---|---|
| 季度复盘.pdf | MinerU | 23 | 3318 | 5 | 2 | 6×5 销售区域表 |
| 季度复盘.pdf | Docling | 22 | 2144 | 6 | 2 | 同表（单元格 100% 一致） |
| EfficientNet.pdf | MinerU | 83 | — | 16 | 3 | 论文多列 |
| EfficientNet.pdf | Docling | 102 | — | 17 | 3 | 同上 |
| 农作物总结.docx | MinerU | 21 | 2572 | 0 | 2 | 4×5 种植面积表 |
| 农作物总结.docx | Docling | 21 | 2318 | 0 | 2 | 同表（单元格 100% 一致） |
| 智能客服PRD.docx | MinerU | 12 | 1697 | 5 | 2 | 5×4 功能表 + 6×4 里程碑表 |
| 智能客服PRD.docx | Docling | 23 | 1482 | 5 | 2 | 同表（但 Docling 把表头拆成 8 个独立 paragraph 块） |
| 测试模板.html | Docling | 64 | — | 4 | 0 | — |
| 随访记录模板.html | Docling | 13 | — | 5 | 2 | — |

> 注：raw 字符数（含空白与 HTML 标签）对比意义不大——MinerU 表格 HTML 每格带 `rowspan=1 colspan=1` 冗余属性、heading 文本带 `**` markdown 加粗，会虚高 30–60%。**「去空白字符」列为去除空白与 HTML 标签后的纯文本量，是内容保真度的真实口径**。

### 10.3 结论：分工修订（多维度总表）

**原分工「PDF→MinerU、非PDF→Docling」部分推翻。修订为：PDF + docx/pptx/xlsx → MinerU；html/epub/md/txt → Docling。**

下表综合 PDF（×2）、DOCX（×2）、HTML（×2）三组实测，按维度横向对比两引擎：

| 维度 | MinerU v3.4.5 | Docling v2.126.0 | 胜出方 |
|---|---|---|---|
| **格式覆盖** | PDF / 图片 / **docx / pptx / xlsx**（office 后端） | PDF / docx / pptx / xlsx / **html / epub / md / txt / rst / doc** / URL | 覆盖各有侧重；Docling 更广 |
| **中文 PDF 文本保真** | 全（含 bullet 列表、短行） | 长段落 ~95%，**短文/列表（bullet）易整段丢**（季度复盘「五、下季度策略建议」5 条全丢） | MinerU |
| **DOCX 文本保真** | 完整（零模型原生解析） | 完整（零模型原生解析） | 持平 |
| **表格结构（PDF）** | 内容完整，HTML 带冗余 `rowspan=1 colspan=1` | 内容完整，HTML 紧凑 + 表头语义化 `<th>` | 内容持平；格式 Docling 更优 |
| **表格结构（DOCX）** | **表头在 table 块内，结构正确**（PRD 12 块） | **表头被拆成独立 paragraph 块**（PRD 23 块里 8 块是拆出来的表头 + 3 个空段，块数虚高、内容重复） | **MinerU** |
| **标题 / heading** | PDF 两档（doc_title / paragraph_title），**图形化大标题会整丢**（当图吞）；docx 依赖 Word 原生样式 | PDF heading 有 level（版面推断），大标题能提；docx 同样依赖原生样式 | PDF：Docling 略优；docx：持平 |
| **anchor / page_label** | **PDF 齐全**（`page:bbox`，每块都有）；docx 无 paraId | PDF 有 prov 但 adapter 未接（实现留白）；docx 无 paraId | PDF：MinerU（已落地）；docx：都没有 |
| **模型依赖** | PDF 需 layout/OCR/table/公式模型（pipeline）；**office 零模型** | office/web 零模型；PDF 需 StandardPdfPipeline（layout/OCR/table） | office：都零模型；PDF：都重模型 |
| **性能参考（本机 CPU）** | 2 PDF = 45.6s；2 DOCX = 13.1s（含 API 启动开销） | office 秒级；PDF 慢于 MinerU（CPU 下同份约 2–3×） | office：持平；PDF：MinerU 更快 |
| **适用场景** | PDF（中文优先）、docx/pptx/xlsx（表格/结构优先） | html/epub/md/txt 等 MinerU 不支持的格式 | 按格式分工 |

### 10.4 HTML / 其他格式（MinerU 不支持）

- MinerU 代码层面仅 pdf + image + office 三类入口，**不支持** html / md / epub / txt / rst / doc。
- 这些格式继续走 Docling（已测 html：结构完整、emoji 保留、heading 识别可用）。

### 10.5 与 §8 工作项的对照

- §8.1「分工」**已修订**：docx/pptx/xlsx 从 Docling 改归 MinerU（office 后端零模型、表格结构更准）。
- §8.2「主线走 content_list(JSON) + images」✓ 验证通过：MinerU office 产物结构与 PDF 一致（content_list / _middle.json / .md / images），adapter 只需补 `office/` 目录定位。
- §8.2「沿用官方链 + 自补 docx anchor」✓ 维持；实测确认 docx anchor 两家都不给。
- 新注意：Docling 结构类型含 `mixed`（相邻多类型混块），MinerU 无；比对时 block_type 口径不同。M1 下游不受影响。

### 10.6 数据留档

- `backend/src_cmp/`（输入副本）、`backend/data/parse_cmp/`（10 份产物：mineru 4 份 + docling 6 份）已按用户要求保留，供后续 M9 评测复用或溯源。