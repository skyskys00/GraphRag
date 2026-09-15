# Docling v2.126.0 源码拆解（M1 多格式兜底解析器）

> 版本与佐证：本拆解基于 `source/pyproject.toml`（`name = "docling-slim"`、`version = "2.126.0"`）。运行时环境（graphrag conda env）实测依赖：`docling_core 2.95.0`、`docling_parse`、`pydantic v2`。
> 重要前提：**统一数据模型 `DoclingDocument` 不在 docling 仓库内，而在 `docling_core` 包**（`docling_core/types/doc/`，`docling/datamodel/document.py:30` 直接 re-export）。拆解 docling 必须同时读 docling_core。
> 文中相对路径以 `source/docling/` 为前提；docling_core 文件标注 `[core]`。行号以当前 checkout 为准。

---

## 1. 定位与职责

Docling = 多格式文档 → 统一 `DoclingDocument`（Pydantic v2 树形模型）→ 导出 Markdown / JSON / DocTags / HTML / chunk 的 Python SDK + CLI。

- 无论输入是 PDF、DOCX、PPTX、XLSX、HTML、图片还是录音，产出的都是**同一个数据模型**；差异只在"谁负责解析"。
- 两条完全不同的转换路径（见 §3）：
  1. **PDF/图片**：`StandardPdfPipeline`（ML 模型流水线：版面 / OCR / 表格 / 公式 / 阅读顺序）。
  2. **其余格式（DOCX/PPTX/XLSX/HTML/MD/CSV/Latex…）**：`SimplePipeline` = 直接调用 backend 的 `backend.convert()` 生成 DoclingDocument，**不跑任何 ML 模型**（只可能跑 enrichment：图片分类/描述，默认关）。
- 数据模型在 docling_core；docling 主包负责 backend 读取 + ML 流水线 + I/O。

**对我们 M1 的意义**：Docling 不是"PDF 解析器"，它是"文档统一表示引擎"。我们拿它当 DOCX/PPTX/XLSX/HTML 兜底正好落在它的 `SimplePipeline` 路径上——轻、无模型依赖；而 PDF 路径能力强大但要下载多个模型（见 §7 坑）。

---

## 2. 关键文件与入口

| 职责 | 相对路径 | 关键类/函数 |
|---|---|---|
| 核心入口 | `document_converter.py:393` | `DocumentConverter`（`convert/convert_all/convert_string`） |
| 输入文档/格式猜测 | `datamodel/document.py:128` / `:619` | `InputDocument` / `_DocumentConversionInput._guess_format` |
| pipeline 基类 | `pipeline/base_pipeline.py:60` / `:79` | `BasePipeline.execute`（build→assemble→enrich 三段） |
| 非 PDF 流水线 | `pipeline/simple_pipeline.py:19` | `SimplePipeline`（backend 直出 doc） |
| PDF 流水线 | `pipeline/standard_pdf_pipeline.py:585` | `StandardPdfPipeline`（线程化多 Stage） |
| 无模型 PDF | `pipeline/native_pdf_pipeline.py` | `NativePdfPipeline`（读 PDF 原生文本，无版面/OCR） |
| 各格式读取器 | `backend/*.py` | `MsWordDocumentBackend`、`MsExcelDocumentBackend`、`MsPowerpointDocumentBackend`、`HTMLDocumentBackend`、`ThreadedDoclingParseDocumentBackend`(PDF) 等 |
| **统一数据模型** | `[core] types/doc/document.py:174` | `DoclingDocument`（`add_text/add_table/add_picture/add_heading`…） |
| 模型 stage | `models/stages/*/` | layout、ocr、table_structure、reading_order、page_assemble、heading_hierarchy、page_preprocessing |
| CLI | `cli/main.py:745` / `:461` | `convert` 命令 / `export_documents` |
| 模型下载 | `cli/models.py:82` | `docling-tools models download` |
| 远程服务客户端 | `service_client/client.py` / `cli/remote.py` | `DoclingServiceClient` / `convert-remote` |
| 转换选项 | `datamodel/pipeline_options.py` | `PdfPipelineOptions`、`ConvertPipelineOptions`、`LayoutObjectDetectionOptions`、`OcrOptions` |
| 格式常量 | `datamodel/base_models.py:97` | `InputFormat` / `OutputFormat` / `FormatToExtensions` / `FormatToMimeType` |

---

## 3. 核心数据流（输入 → 逐阶段形状 → 导出）

### 3.1 格式识别与 backend 打开

1. `_DocumentConversionInput.docs()`（`datamodel/document.py:624`）迭代输入：Path / str(URL，经 `resolve_source_to_stream` 下载到 BytesIO) / `DocumentStream` / `HttpSource`。
2. `_guess_format`（`datamodel/document.py:766-857`）：优先按扩展名/mime；`filetype.guess_mime` 识别；Office 文件按 ZIP 内容嗅探（`word/document.xml` 等，`:868`）；HTML/XML 按内容正则探测；最终映射 `InputFormat` 并选 `FormatOption`（`_get_default_option`，`document_converter.py:345`）→ 选 backend 类。
3. `InputDocument` 构造（`datamodel/document.py:156`）：打开 backend、记录 document_hash、**page_count**、校验文件大小/页数上限（`DocumentLimits`）。
4. `DocumentConverter._execute_pipeline`（`document_converter.py:835`）→ `pipeline.execute(in_doc)`。

### 3.2 路径 A：非 PDF（SimplePipeline，我们兜底的主要路径）

`BasePipeline.execute`（`base_pipeline.py:79`）→ `SimplePipeline._build_document`（`simple_pipeline.py:42-44`）：

```
backend.convert()  →  DoclingDocument（完整，含 prov/meta）
```

各 backend 直接产出 doc：

- **DOCX**：`MsWordDocumentBackend.convert`（`backend/msword_backend.py:559`）逐段 `w:body` 线性遍历（`_walk_linear`，`:750-940`）组装。**不产 prov（无页码/bbox）**。
- **PPTX**：`MsPowerpointDocumentBackend`，每页 = 一个 slide，`add_page(page_no=slide_ind+1, size=slide_size)`（`mspowerpoint_backend.py:1332`），每个元素 `_generate_prov`（`:264-280`）产 `page_no=slide+1, bbox=shape bbox, charspan=(0,len(text))`。
- **XLSX**：`MsExcelDocumentBackend`（`msexcel_backend.py:249`）每 sheet = 一个 page（`:533-551`），bbox 是单元格网格坐标，charspan=(0,0)。
- **HTML**：`HTMLDocumentBackend` 自己按渲染高度几何分页：`page_no = int(top // page_height) + 1`（`html_backend.py:1263-1268`），每个元素 `_make_prov`（`:1388-1410`）产 page/bbox/charspan。

然后 `_enrich_document`（`base_pipeline.py:157`）跑 enrichment_pipe（默认只含图片描述模型 `ConvertPipeline.__init__`，`base_pipeline.py:223-234`，`do_picture_description=False` 时是 no-op 桩）。

### 3.3 路径 B：PDF/图片（StandardPdfPipeline）

`StandardPdfPipeline._init_models`（`standard_pdf_pipeline.py:601-685`）初始化全部模型，`_create_run_ctx`（`:715-787`）把 6 个 stage 接成线程链：

```
preprocess(PagePreprocessingModel: 页解码/旋正/画布scale)
  → layout (LayoutModel, 默认 RT-DETR heron:datamodel/pipeline_options.py:1672 默认 layout_object_detection=OBJECT_DETECTION_LAYOUT_HERON; HF docling-project/docling-layout-heron, stage_model_specs.py:1010)
  → ocr (默认 OcrAutoOptions auto→按平台选 rapidocr/ocrmac; ocr=true 时对位图 OCR, pipeline_options.py:311,2109)
  → layout_postprocess(把 textline 聚类归并到 layout 区域)
  → table (TableStructureModel, TableFormer: pipeline_options.py:153 kind=docling_tableformer; 检测→结构→cell 匹配)
  → assemble (PageAssembleModel: 每个 layout cluster 转成 PageElement, page_assemble_model.py:180-269)
```

随后 `_assemble_document`（`standard_pdf_pipeline.py:1032-1131`）：

1. 按页收集 `AssembledUnit(elements/headers/body)`。
2. **ReadingOrderModel**（`models/stages/reading_order/readingorder_model.py`）把 PageElement 按阅读顺序写入 DoclingDocument，并**在此构造 prov**：`ProvenanceItem(page_no, bbox(转 bottom-left 坐标), charspan)`（`:107-209`）。
3. **HeadingHierarchyModel**（`heading_hierarchy_model.py` 顶部 docstring）：给 `SectionHeaderItem.level` 赋值，信号优先级= PDF 目录书签（`get_document_outline`，标准流水线 `_build_document:810-811`）> 编号（PART I/1./1.1/(a)）> 视觉样式。
4. 可选 generate_page_images / element 截图（`ImageRef`）。
5. 聚合 confidence（layout/parse/table/ocr 分数，`standard_pdf_pipeline.py:1096-1123`）。

最后 `_enrich_document`：enrichment_pipe 默认含 `CodeFormulaVlmModel`（关）→ `DocumentPictureClassifier`（关）→ 图片描述 + 可选图表抽取（`standard_pdf_pipeline.py:664-675`）。**这些都是可关闭的 `do_*` 开关。**

### 3.4 导出

`conv_res.document`（DoclingDocument）→ 任意 `export_to_*` / `save_as_*`（见 §4.2）。图片导出还能回写 `doc.pages[page_no].image`（`standard_pdf_pipeline.py:1049-1053`）。

---

## 4. 输入输出接口规范（占重头）

### 4.1 CLI

```
docling [convert] <source...> [选项]        # 裸命令自动路由到 convert（cli/main.py:317-333）
docling-tools models download [-o DIR] [layout|tableformer|rapidocr|...]   # 预下载模型
docling --version
```

`convert` 常用选项（`cli/main.py:745-1144`，默认值见代码）：

| 选项 | 默认 | 说明 |
|---|---|---|
| `--from` | 全部格式 | 限定输入格式（`odf` 展开为 odt/ods/odp） |
| `--to` | `md`（main.py:1335-1336） | `OutputFormat`：`md/json/yaml/html/html_split_page/text/doctags/vtt/doclang/dclx/chunks/latex`（`base_models.py:135-147`） |
| `--output` | `.`（当前目录，main.py:1035） | 输出目录，**无目录骨架/子目录，全部平铺** |
| `--image-export-mode` | `embedded`（main.py:795-801） | `placeholder`（只有 `<!-- image -->`）/ `embedded`（base64）/ `referenced`（另存 png） |
| `--pipeline` | `standard` | `standard/legacy/native/vlm`；native 只支持从 PDF |
| `--ocr/--no-ocr` | 开 | 位图 OCR；`--ocr-mode full_page` 整页 OCR |
| `--tables/--no-tables` | 开 | TableFormer 表格结构模型 |
| `--enrich-code/--enrich-formula` | 关 | VLM 代码/公式富化（重型，需 torch+模型） |
| `--page-range` | 全部 | 只转区间（PDF/XLSX/PPTX 支持，main.py:958-965） |
| `--artifacts-path` | `~/.cache/docling/models` | 离线模型目录 |
| `--abort-on-error/--no-abort-on-error` | 不开 | 失败是否中止整个批次 |
| `--chunks-type hybrid\|hierarchical` | `hybrid` | `--to chunks` 时用（见 §4.2 最末） |

输出文件一律 `<stem>.<ext>`（json、yaml、html、md、txt、doctags、vtt、dclg.xml、dclx、tex、chunks.jsonl）（`export_documents`，`cli/main.py:461-742`）；`referenced` 图片落在 JSON/MD 旁 artifacts 目录（`[core] document.py:4003 _get_output_paths`）。

### 4.2 Python：DocumentConverter

```python
from pathlib import Path
from docling.document_converter import DocumentConverter

conv = DocumentConverter(
    allowed_formats=None,                    # None = 全部（document_converter.py:448-450）
    format_options={...},                    # 按格式定制 backend/pipeline
)
res = conv.convert(source,                   # Path|str(URL)|DocumentStream|HttpSource
                   headers=None,
                   raises_on_error=True,     # False 时失败进 res.errors
                   max_num_pages=sys.maxsize,
                   max_file_size=sys.maxsize,
                   page_range=(1, ...)       # PageRange
)                                            # → ConversionResult
res.document                              # DoclingDocument
res.status                                # conversion_status: success/partial_success/failure/skipped
res.errors                                # ErrorItem(component_type, module_name, error_message, category, page_no)
res.pages / res.timings / res.confidence  # profiling 与置信度
```

- `convert` = 单文档封装，`convert_all(iterable)` 批量返回 `Iterator[ConversionResult]`（`document_converter.py:579`），默认按 `settings.perf` 分页并发。
- `convert_string(content, format=MD/HTML/XML_DOCLANG)`：Markdown/HTML 字符串直接转（`:666`）。
- **`ConversionResult` 定义的 3 个关键出口**：`document`（DoclingDocument）、`status`、`errors`（`datamodel/document.py:590`）。

### 4.3 导出选项与结构

`DoclingDocument` 的导出（`[core] document.py`）：

| 方法 | JSON 结构 / 行为 |
|---|---|
| `export_to_dict()`/`save_as_json(filename, image_mode, indent, coord_precision)`（:3551, :3662） | 顶层字段：`schema_name/version/name/origin/pages/body/texts/pictures/tables/key_value_items/form_items`。每个 item = `{self_ref, parent, children[s]?, label, prov[s], content_layer, text/orig, ...}`；`pages` = `{page_no: {size, image?, page_no}}`。图片默认 base64（EMBEDDED），REFERENCED 时写 artifacts 目录 |
| `export_to_markdown(...)`（:3743） | 纯 Markdown：标题 `#`、表格为 pipe 表（`compact_tables` 紧凑格式）、列表 `- 1.`；`to_element/from_element/labels` 切片；`page_break_placeholder` 可插分页标记（**默认无页码**）；`image_mode` 决定图片表现 |
| `export_to_text`（:3890） | 去 Markdown 装饰的纯文本，保留列表/表格分隔符 |
| `export_to_doctags`（:4790） | DocTags：每 token 前带 `<loc_...>` 定位串（由 bbox 归一化到 500×500 网格得到），定位来自 `prov`。**docx 无 prov 所以无定位** |
| `save_as_yaml/save_as_html/vtt/doclang/dclx` | 同上模式 |
| chunk（CLI `--to chunks`，`cli/main.py:636-683`） | 用 docling_core 的 `HybridChunker/HierarchicalChunker`（需花式 tokenizer）产出 `*.chunks.jsonl`，每条含 `text/headings/captions/doc_items(self_ref 列表)/page_numbers/prov.page_no 集合` |

**JSON 是唯一能拿到块级定位/结构的出口**；Markdown 里没有页码、没有 self_ref。M1 若要 block_type/page_label 必须解析 JSON（或直接 API 层面遍历 document）。

### 4.4 支持格式清单

`InputFormat`（`base_models.py:97-132`）与扩展名（`:150-184`）：

- Office：docx/dotx/docm/dotm、doc/dot、rtf、pptx/potx/ppsx/pptm…、ppt/pot/pps、xlsx/xlsm…、xls/xlt、odt/ods/odp、iwork_pages
- Web/文本：html/htm/xhtml、mhtml、md/txt/qmd/rmd、adoc/asciidoc、csv、latex/tex、email(eml/msg)、epub、boxnote、ebcdic
- PDF/图像：pdf、jpg/jpeg/png/tif/bmp/webp、mets_gbs(tar.gz)
- XML：xml_jats(.xml/.nxml)、xml_xbrl、xml_uspto、xml_doclang(.dclg/.dclg.xml)、dclx、json_docling
- 多媒体：audio(wav/mp3/m4a/…)、video(mp4/avi/mov/…)、vtt

### 4.5 服务（server/service）

Docling 主包**不含 server**。`service_client/`（client.py:229 `DoclingServiceClient`、job.py:67 `ConversionJob` 轮询/watch）是**连远程 `docling-serve` 的 HTTP 客户端**；CLI 在有 `service-client` extra 时注册 `docling convert-remote`（`cli/main.py:1719-1730`）。我们用不到；这里只确认"docling 本体可被以 Python 进程内方式调用"。

---

## 5. DoclingDocument 数据模型详解（M1 的信息源）

`[core] types/doc/document.py:174`。容器结构：

```
DoclingDocument
 ├─ name / origin(DocumentOrigin: filename+mimetype+hash) / version("1.1.0")
 ├─ body: GroupItem（children=[RefItem $ref="json-pointer"]）  ← 阅读顺序树
 ├─ texts[]    : TitleItem|SectionHeaderItem|ListItem|CodeItem|FormulaItem|...|TextItem
 ├─ pictures[] : PictureItem（FloatingItem）
 ├─ tables[]   : TableItem（FloatingItem，含 TableData）
 ├─ key_value_items[] / form_items[] / field_regions[] / field_items[]   ← 表单类，默认为空
 └─ pages: {page_no: PageItem(size, image?, page_no)}
```

### 5.1 Block 类型（`DocItemLabel`，`[core] types/doc/labels.py:6-40`）

`TEXT、PARAGRAPH、TITLE、SECTION_HEADER、LIST_ITEM、CODE、FORMULA、TABLE、PICTURE、CAPTION、FOOTNOTE、PAGE_HEADER、PAGE_FOOTER、CHECKBOX_SELECTED/UNSELECTED、DOCUMENT_INDEX、CHART、FORM、KEY_VALUE_REGION、REFERENCE、HANDWRITTEN_TEXT、FIELD_*、MARKER、GRADING_SCALE`。

- 块的具体类：`TextItem`（`orig`未处理原文 / `text`清洗后，`[core] items/text.py:19-40`）、`SectionHeaderItem(level: int 1-100)`、`ListItem(枚举/项目符号)`、`CodeItem`、`FormulaItem`、`PictureItem`（`image: ImageRef`，mimetype/dpi/size/uri）。
- 层级 = 树：每个 `NodeItem` 有 `self_ref/parent/children(RefItem)`（`[core] items/node.py:22-33`）；子树顺序即阅读顺序；`iterate_items()` 深度优先遍历。
- 标题层级：`SectionHeaderItem.level`，PDF 路径由 HeadingHierarchyModel 推算（bookmarks>编号>样式），DOCX 路径由样式名/`outlineLvl` 推（`msword_backend.py:1452-1523`）。`body` 与 `furniture`（页眉页脚）分离（`content_layer: BODY/FURNITURE/NOTES/...`）。

### 5.2 prov（原文定位）——我们最关心的字段

`ProvenanceItem`（`[core] types/doc/common/reference.py:182-192`）：

```python
class ProvenanceItem(BaseModel):
    page_no: int                # 页码（1 起）
    bbox: BoundingBox           # {l, r, b, t, coord_origin(bottom-left 默认)}
    charspan: CharSpan          # (start, end) 0-indexed，对整段文本通常是 (0, len(text))
```

- **没有 heading 字段**——标题层级在 `SectionHeaderItem.level`，不在 prov。
- **没有 paraId**——docling 主包与 docling_core 均无 w14:paraId 的支持（§6.1）。
- `charspan` 需要警惕：PDF/HTML/PPTX 里它基本是 `(0, len(该块文本))` 的"伪跨度"，不是原文中的字符偏移，**不能当精确 span 用**（M1 深链需自行按文本子串定位）。
- 每个块 `prov: list[ProvenanceItem]`——跨页块（如跨页表格/段落）有多个条目。

**各格式 prov 的可得性（决定 M1 从哪取 page/paraId）：**

| 格式 | page_no | bbox | charspan | heading |
|---|---|---|---|---|
| PDF (standard pipeline) | ✅ 真实页 | ✅ layout cluster | ✅ (0,len) | level（模型推） |
| DOCX | ❌ 无 prov | ❌ | ❌ | level（style 推） |
| PPTX | ✅ slate: slide+1 | ✅ shape | ✅ (0,len) | 无标题概念 |
| XLSX | ✅ sheet 序页 | ✅ 网格坐标 | ✅(多为 (0,0)) | 无 |
| HTML | ⚠️ 几何估算页 | ✅ | ✅ (0,len) | level=None，标题=label |

### 5.3 表格存法

- `TableItem`（FloatingItem）→ `data: TableData(num_rows, num_cols, table_cells: list[TableCell])`（`[core] items/table/table_data.py:20-101`）。
- `TableCell`：`text、row_span、col_span、start_row_offset_idx/end_row_offset_idx、start_col_offset_idx/end_col_offset_idx、column_header、row_header、bbox?`。
- **表头** = 每个 cell 的 `column_header` 布尔（不是独立 header 行结构）；docx 约定首行 `column_header=(row_idx==0)`（`msword_backend.py:3001`）；PDF 路径由 TableFormer 的 `do_cell_matching` 判。**合并单元格** = row/col_span + 偏移区间（docx 用 `vMerge=continue` 扩行，`msword_backend.py:2938-2945`）。`TableItem.export_to_dataframe()` 可还原 pandas 表。
- 转换工具：`_ExportToDataframeMixin`/`parse_otsl_table_content`（`[core] document.py`、`types/doc/utils.py`）。

---

## 6. 与 M1 的对接点（paraId / 页码 / 扩展字段位置）

### 6.1 docx 的 paraId：Docling 不提供，必须自研补丁

结论（r=whole repo grep）：`docling` 与 `docling_core` **没有任何地方读取 w14:paraId**（仅 `msword_backend.py` 里 `para_id` 是同文件内注释映射局部变量名）。docx 元素也**完全没有 prov**（`msword_backend.py` 全文件无 `prov=` 调用；`_handle_text_elements` 的 `add_text` 均不传 prov）。因此：

- 用 Docling 导出的 Markdown 或 JSON，**拿不到 paraId、也拿不到页码**。
- 官方/社区也没有 docx 页码机制——docx 无稳定页码是格式本质（`supports_pagination()==False`，`msword_backend.py:542-545`），我们的 TextUnit 契约（docx 用 paraid 定位）与 Docling 的 API 设计互补。

**正确取 paraId 的方式**：直接在原始 `.docx` 上取（不依赖 Docling）——`word/document.xml` 中 `w:p/w:pPr/w14:paraId`（及 `w14:webHidden` 等）。若坚持用 Docling 产文本，需自 patch：

- 最自然的插入点 = `backend/msword_backend.py::_handle_text_elements`（`:2149`）：`Paragraph(element, ...)` 已有 `element`（lxml），可 `element.findall('.//w:pr/w14:paraId')` 等读取，然后映射 `paragraph → TextItem.self_ref` 并写入 `item.meta`（`BaseMeta` 派生可任意自定义字段，node.py:33）或 `paragraph_to_items`（后端已有该映射容器，`:514`）。这样每个 `TextItem` 就带 paraId，导出 JSON 即含定位。
- 或者 post-process：解析 Docling JSON，用 `body` 树顺序 ≈ docx `w:body` 线性顺序（`_walk_linear`按 body 顺序追加），把 paraId 序列按文档序 zip 回去（有 risk：表格内嵌套、文本框、列表组的归属会偏，优先用 patch 方案）。

**M1 落地方案建议**：docx 文本块 = Docling JSON 的 `texts` 按 `body` 树序（heading 有 `level`，文本有 `text`），paraId 从我们自己的 docx XML 快读拿到；两者按顺序+文本内容对齐，断裂时以 XML 快读为准做块（XML 快读本身就能产完整块，Docling 的价值主要在"复杂版式/表格/公式"）。

### 6.2 PDF / HTML 页码

- PDF：`block.prov[0].page_no`（真实页码，1 起）；跨页块遍历 `prov` 数组。drop-in。
- HTML：`block.prov[0].page_no`，但**是几何估算页**（按渲染高度 `top//page_height+1`，`html_backend.py:1263`），浏览器渲染不同会变；`--html-image-fetch` 只影响图片，不影响分页。对 HTML 建议不依赖页码或用 1（整篇）。
- block_type：Docling 的 `label` 枚举（TEXT/PARAGRAPH=正文，SECTION_HEADER=标题，TABLE，PICTURE，LIST_ITEM，FORMULA，CODE…）→ 映射我们 blocks.jsonl 的 `block_type`。**注意 CATPTION/FOOTNOTE/PAGE_HEADER/PAGE_FOOTER 在 markdown 导出时可能被剔除或归 furniture，JSON 里都在**。

### 6.3 在哪一步插入"扩展字段"最自然

我们的解析层自己产 `统一 Markdown + blocks.jsonl（块行带 page_label/block_type，docx 用 paraid）`。清理职责归属：

1. **PDF via Docling**（若临时用）：直接遍历 `DoclingDocument` / 解析 JSON，`prov.page_no`→page_label、`label`→block_type、`text`→content；分块聚合后写 JSONL。**零改动 Docling**。
2. **docx/pptx/xlsx/html**：docx 用 §6.1 的 XML 快读补 paraId；pptx/xlsx/html 直接用 Docling 的 prov。
3. 若长期把 docx 交给 Docling，就 fork `msword_backend.py` 的 `_handle_text_elements` 加 paraId 收集（约 30 行 diff），并把它装成 `format_options={InputFormat.DOCX: WordFormatOption(backend=MyWordBackend)}` 注入 `DocumentConverter`——不必动上游。

### 6.4 Docling ↔ MinerU（同一份 PDF）

我们以 MinerU 为主、Docling 兜底非 PDF。二者差异要点：

- **输出模型**：MinerU 出 `blocks.jsonl`（自带 `page_label / block_type / span / 文本`）；Docling 出 item 树 + prov + 可选 chunks.jsonl（`doc_items/pag`）。MinerU 块 = 版面检测结果直接落地；Docling 块 = 版面 cluster → 阅读顺序重排 + heading level。
- **定位口径**：MinerU block 有像素级 span（页面/坐标），Docling charspan 是伪 (0,len)。
- **表结构**：两者都做表格识别，但单元格坐标/表格体判定/合并行不同；跨库对齐块只能用"文本内容匹配"，**不能用块 id**（两者 id 体系无关）。
- **标题层级**：Docling 有 HeadingHierarchy（书签/编号/样式→level，PD 路径），MinerU 标题识别靠分类模型，层级较弱。

结论：同源 PDF 不要既跑 MinerU 又跑 Docling 再合并（对齐成本高）；不同源文档走各自主通道即可。

---

## 7. 已知坑

1. **模型下载直连 HF 不通**（本机环境已确认）：所有 model 通过 `huggingface_hub.snapshot_download`（`models/utils/hf_model_download.py:24,34`），默认仓库 `~/.cache/docling/models`。必须 `HF_ENDPOINT=https://hf-mirror.com` 或者 `docling-tools models download -o <dir>` 预下载后 `--artifacts-path=<dir>` 离线跑。
2. **重型依赖随"默认流水线"**：torch/transformers（VLM enrichment、TableFormer）、docling-parse（Rust PDF 解码）、pypdfium2、onnxruntime、easyocr/tesseract 可选。非 PDF 路径（SimplePipeline）**不需要这些**——只要 backend 依赖（python-docx、openpyxl、python-pptx、beautifulsoup4、libmagic…）。装 `docling-slim[format-office,format-web]` 即可兜底 Office/HTML。
3. **docx 无 prov/页码/paraId**（§6.1）——与 TextUnit 契约冲突点，必自补。
4. **charspan 不可当精确字节 span**：PDF/PPTX/HTML 都是 `(0,len)` 伪跨度。
5. **HTML 页码是估计值**；XLSX 的 "page" 是 sheet 不是页；PPTX 页码=slide 序号——三种"页码"语义不同，跨格式勿混用 page 概念到 TextUnit。
6. **OCR / 表格副作用**：默认 `--ocr` 且默认 `--tables` 会在 PDF 路径触发模型（rapidocr + tableformer），扫描 PDF 慢且吃内存；M1 若只在非 PDF 兜底，这些默认值不适用。中文 OCR 要另装语言包。
7. **版本分离**：docling 2.126.0 ↔ docling_core 2.95.0 是独立发布；docling_core 里有 `CURRENT_VERSION`（JSON schema 版本 1.1.x），我们解析 JSON 时应按 `version` 字段防御。
8. **批量语义**：`convert_all` 默认 `raises_on_error=True` 是"批量假 raise"（`convert_all:641-658` 逐文档抛 ConversionError，**遇第一个失败就中断整个批次**）；`raises_on_error=False` 才能逐文档继续并读 `status/errors`。CLI 的 `--abort-on-error` 翻转同一语义。
9. **空输出**：CLI 对导出为空的 md 会记 `ErrorItem` 并把 status 置 FAILURE（`cli/main.py:586-600`）。

---

## 8. M1 采用建议

| 项 | 做法 | 理由（证据） |
|---|---|---|
| **复用** | 用 `DocumentConverter` 的 SimplePipeline 路径，对 **docx/pptx/xlsx/html/md** 产出 DoclingDocument；导出 **JSON（权威，含 label/level/prov）+ Markdown（人读/下流传真）** | 非 PDF 路径零模型依赖；JSON 才有块级信息（§5.2）；`backend.convert()` 直出（§3.2） |
| **复用** | PDF 掉落给 Docling 时用 JSON 的 `prov.page_no`、`label`、`SectionHeaderItem.level`、表格 `TableData` | 均有稳定字段（§5） |
| **自研** | docx 的 **paraId 采集**：post-process 读原始 `word/document.xml` 的 `w14:paraId`，与 Docling body 树文序对齐；长期则 fork `msword_backend._handle_text_elements` 约 30 行把 paraId 写进 `item.meta` | Docling 全局无 paraId 也无 docx prov（§6.1） |
| **自研** | 把 Docling JSON → 我们 `blocks.jsonl` 的转换器（block_type 映射表、page_label/paraid 兜底、跨页块拆/合、footnote/caption/page_header 的分层归类） | Docling 不产 blocks.jsonl；我们契约已定 |
| **需改配** | 环境：`HF_ENDPOINT=hf-mirror.com`（若要 PDF 路径）；按需装 `docling-slim[format-office,format-web]`，**不要装 `docling` 全量**（避免 torch/模型全家桶） | 坑 1/2 |
| **不采用** | 不用 `--to chunks`（要额外 tokenizer/模型）、不用 `convert-remote`（外置服务）、不用 HTML 页码做定位、docx 的产出**不写 page**（标 paraId 或段落序号） | 语义与契约不符（§4.4/§6.2/§6.4） |

一句话：**Docling 当"Office/HTML 的版式保真解析器 + 统一 JSON 结构源"用**；docx 定位自补 paraId，PDF 以 MinerU 为主、Docling 只作量外兜底；扩展字段在"Docling JSON → blocks.jsonl"这道我们自己的适配层插入，尽量不改 Docling 上游。