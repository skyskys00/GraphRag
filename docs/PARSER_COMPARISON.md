# 解析器选型复核：MinerU vs Docling（源码拆解后）

> **版本：** v1
> **状态：** 已落地（选型结论）
> **更新：** 2026-09-12
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
| 定位 | PDF/扫描件/图片打通，面向中文文档解析 | 多格式统一解析（office/网页/PDF） |
| 输入格式 | PDF（数字+扫描）、常见图片（PNG/JPG…） | **pdf、docx、pptx、xlsx、html、epub、md、txt、图片、URL** |
| 底层 | 自研 pipeline（layout/公式/表格/OCR） | SimplePipeline（office 零模型）+ StandardPdfPipeline（PDF 重模型） |
| 中文版式 | 强（中文界第一梯队） | 弱于 MinerU（ARCHITECTURE 原判保持） |

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

## 8. 结论与分工（维持，附工作项）

1. **分工维持原方案**：PDF → MinerU（`-b pipeline`）；非 PDF（docx/pptx/xlsx/html/…）→ Docling。
2. **M1 落地要点**（自源码确认）：
   - 主线走 **content_list（JSON）+ images**，不要用裸 Markdown 做结构对接；
   - **沿用 LightRAG `parser/external/{mineru,docling}` 官方链**，在官方 IR block 上补我们的扩展字段（page_label / block_type / docx anchor），而不是重写解析器；
   - docx 的 `anchor`：自研 paraId 定位补丁 或 暂退化为「文件+文本片段」（不影响核心链路）。
3. **ARCHITECTURE §2.2 需修订**：OCR 表述（PP-OCRv6 → pytorchocr/RapidOCR 实测）、补充 LightRAG 双消费链事实、补充「结构信息走 JSON 不走 Markdown」的工程纪律。

## 9. 依据

- `mineru/docs/mineru.md`：MinerU 数据流、IO、content_list 结构、`-b pipeline` 选型。
- `docling/docs/docling.md`：DoclingDocument、prov、docx 无 paraId、OCR RapidOCR、JSON vs Markdown 信息差异。
- `docs/modules/M0_contracts/textunit.md`：契约 v2 字段与取值机制。