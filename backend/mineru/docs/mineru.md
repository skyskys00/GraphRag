# MinerU v3.4.5 源码拆解 — M1 解析层主力引擎分析

> 基线：tag `mineru-3.4.5-released`（commit `fbb1257a`；`mineru/version.py` 内 `__version__ = "3.4.4"`，tag 名保留 3.4.5 发布标记）。
> 目的：供 M1 解析层（PDF/扫描件 → 统一 Markdown + blocks 侧车）复用 MinerU，并把「页码 / 块类型 / bbox / 标题」从解析结果取出写进 blocks 扩展字段（对接 LightRAG `sidecar/writer.py` 与 `docs/contracts/textunit.md` 的 `page_range / anchor / block_type`）。
> 定位：本拆解以 **pipeline 后端**为主（详见 §7），vlm / hybrid 后端仅在与输出契约有差异处说明。

---

## 1. 定位与职责

MinerU（原 magic-pdf，v3 起更名为 mineru 包）是 Opendatalab 的文档智能解析引擎：

- **输入**：PDF / 图片（jpg/png 等自动转 PDF）/ Office（docx/pptx/xlsx，走独立分支）。
- **能力**：版面分析（PP-DocLayoutV2）→ 按版面区域分发 OCR / 公式识别（MFR）/ 表格识别（Slanet+ / Unet）→ 聚合成"块级中间表示" → 渲染为 Markdown / 结构化 content_list / 裁剪图像。
- **架构形态（v3.4.x）**：CLI（`mineru`）不再直连模型，而是**本地拉起一个临时 `mineru-api`（FastAPI）**，经 HTTP 提交任务并下载 zip 解压到 `-o` 目录（`mineru/cli/client.py:950-953`、`848-877`）。服务端常驻进程由 `mineru-api` 命令单独启动。
- **Backend 三族**：`pipeline`（自研模型链，通用、CPU/MPS 可跑）、`vlm-engine`（大 VLM，需 vllm/lmdeploy/mlx）、`hybrid-engine`（VLM 版面 + pipeline 原子模型兜底，**3.4.5 默认**）。HTTP 客户端变体 `*-http-client` 支持对接远端 OpenAI 兼容服务（`mineru/cli/backend_options.py:3-15`）。

---

## 2. 关键文件 / 入口（相对仓库根 `source/`）

| 文件 | 作用 |
|---|---|
| `mineru/cli/client.py:1037,1185` | `mineru` CLI 入口（click），参数解析 + `run_orchestrated_cli` |
| `mineru/cli/fast_api.py:212,1228` | `create_app` 建 FastAPI；`/file_parse`、`/tasks`(异步)、`/health` 端点 |
| `mineru/cli/common.py:668` `do_parse` | 统一解析调度（office→pipeline/vlm/hybrid 分发） |
| `mineru/cli/common.py:259` `_process_output` | **输出落盘**：md / middle.json / model.json / content_list / content_list_v2 / origin |
| `mineru/backend/pipeline/pipeline_analyze.py:157` | `doc_analyze_streaming`：pipeline 页批主循环 |
| `mineru/backend/pipeline/batch_analyze.py:408` | `BatchAnalyze.__call__`：**模型编排**（layout→公式→表格→OCR） |
| `mineru/backend/pipeline/model_json_to_middle_json.py:28` | 模型输出 → page_info / middle json 组装 + finalize |
| `mineru/backend/pipeline/pipeline_magic_model.py:17` | `MagicModel`：layout_dets → 块/行/span 结构 |
| `mineru/backend/pipeline/para_split.py:417` | `para_split`：段落合并 / list 识别 / 跨页文本标记 |
| `mineru/backend/pipeline/pipeline_middle_json_mkcontent.py:968` | `union_make`：middle json → Markdown / content_list(v1/v2) |
| `mineru/utils/enum_class.py:4,51` | `BlockType` / `ContentType` / `MakeMode` / `ModelPath` 枚举 |
| `mineru/utils/config_reader.py:17,105` | `~/mineru.json` 读取；`get_device` |
| `mineru/utils/models_download_utils.py:279` | 模型自动下载（HF / ModelScope / 本地 models-dir） |
| `mineru/cli/output_paths.py:9` | 解析产物目录命名规则 |
| `mineru/template.json`（仓库根） | 配置模板 |

---

## 3. 核心数据流（逐阶段形状演化）

```
PDF bytes → ①页图渲染 → ②模型输出(layout_dets) → ③page_info(块/span)
          → ④finalize(段落/跨页表/公式编号/标题分级) → ⑤输出(md / json / content_list / images)
```

阶段－形状速查：

| 阶段 | 关键文件 | 数据形状 | 持久化 |
|---|---|---|---|
| ① 渲染 | `pipeline_analyze.py:157` | `[{img_pil, scale}, …]`（每页） | 否 |
| ② 模型 | `batch_analyze.py:408` | 每页 `layout_dets[]` | `_model.json` |
| ③ 组装 | `pipeline_magic_model.py:17` / `model_json_to_middle_json.py:256` | 每页 `page_info{preproc_blocks, page_idx, page_size, discarded_blocks}` | 否（内存） |
| ④ finalize | `model_json_to_middle_json.py:216` + `para_split.py:417` | `page_info` 补 `para_blocks`；block 打 `level`/`CROSS_PAGE` | `_middle.json` |
| ⑤ 渲染 | `pipeline_middle_json_mkcontent.py:968` | md 字符串 / content_list(v1/v2) JSON | `.md` + `_content_list*.json` |

一个能找到的最小编写（`_middle.json` 的第 n 页）：

```json
{"pdf_info": [
  {"page_idx": 0,
   "page_size": [612.0, 792.0],
   "preproc_blocks": [
     {"type": "title", "level": 1, "index": 1,
      "bbox": [72.0, 80.0, 540.0, 110.0], "score": 0.98,
      "lines": [{"bbox": [...], "spans": [{"type": "text", "content": "Introduction"}]}]},
     {"type": "text", "index": 2, "bbox": [72.0, 130.0, 540.0, 200.0],
      "lines": [{"bbox": [...], "spans": [
        {"type": "text", "content": "We propose ", "score": 0.97},
        {"type": "inline_equation", "content": "x_i = f(x)"}]}]},
     {"type": "table", "index": 3, "bbox": [...],
      "blocks": [
        {"type": "table_body", "lines": [{"spans": [
          {"type": "table", "html": "<table>…</table>", "image_path": "a1b2.jpg"}]}]},
        {"type": "table_caption", "lines": [{"spans": [{"type": "text", "content": "Tab.1"}]}]}]}
   ],
   "discarded_blocks": [ {"type": "page_number", "lines": [...]} ]}],
 "_backend": "pipeline", "_version_name": "3.4.4"}
```

### ① 页图渲染 —— `pipeline_analyze.py`

- `doc_analyze_streaming`（:157）按 **processing window**（`MINERU_PROCESSING_WINDOW_SIZE`，默认 64 页/批，`config_reader.py:158`）切批，跨 doc 混排填充批次（:224-262）。
- `load_images_from_pdf_doc`（`utils/pdf_image_tools.py:449`）用 pdfium 200dpi 渲染（`utils/pdf_reader.py:11-33`：`scale = dpi/72 ≈ 2.78`，长边 3500 封顶）。
- `_get_ocr_enable`（:88-93）：`-m auto` 时 `classify`（`utils/pdf_classify.py:94`）抽样 10 页做**文本/扫描 PDF 判定**（字符数、CID 字体缺 ToUnicode、乱码信号、图像覆盖率等），返回 `txt`/`ocr`。

### ② 模型输出 —— `BatchAnalyze.__call__`（`batch_analyze.py:408`）

每页产出 `layout_dets`（**这就是 `_model.json` 的页面快照**）：

```
{ 'layout_dets': [ {cls_id, label, score(0-1), bbox:[x0,y0,x1,y1], index}, ... ],
  'page_info':   { 'page_no', 'width', 'height' } }
```

（`model_json_to_middle_json.py:67-69`；`pp_doclayoutv2.py:1019-1044`。`index` 来自阅读顺序模型 `order_seq`，1 起始。）

顺序：**layout**（PP-DocLayoutV2，`label` ∈ text/ocr_text/table/image/chart/display_formula/inline_formula/doc_title/paragraph_title/figure_title 等 20+ 类，映射见 `pipeline_magic_model.py:19-43`）→ **公式**（对 display/inline_formula 区域跑 UniMERNet，`latex` 写回 layout_res，:434-457）→ **表格**（表格方向分类 → 有线/无线分类 → 表格内 OCR det/rec → SlanetPlus(无线)/Unet(有线) 出 `html`，:519-699）→ **正文 OCR**（对 text 类区域 OCR det/rec，结果以 `ocr_text` 类型合并回 `layout_res`，:702-795）。

### ③ page_info（块/行/span）—— `MagicModel`（`pipeline_magic_model.py:17`）

`build_page_model_info` 传入后 `MagicModel.__init__`：

1. `__fix_axis`（:295-311）：det bbox **除以渲染 scale → PDF 点坐标系**；丢弃宽或高 ≤2 的 det。
2. `__post_process`（:313-338）：inline_formula / ocr_text 提升为 span 候选（`page_inline_formula`、`page_ocr_res`），其余 det 重编号 `index`=1..N。
3. `txt_spans_extract`（`utils/span_pre_proc.py:43`）：**txt 模式**下用 pdfium 文本层逐字符填充 span（横向 span 用 char 填充、竖排用行填充、空 span 待 OCR）。
4. 每 det → 文本块（`lines[].spans[]`）或视觉块（image/table/chart/interline_formula，先记 `all_image_spans` 供截图）。
5. `__classify_visual_blocks`（:340-459）：caption/footnote 归属视觉父块，组成**两层结构**：父块 `{'type':'image'|'table'|'chart'|'code', 'bbox','index','blocks':[body, caption, footnote]}`；body 的 span 类型为 `image/table/chart/interline_equation`。
6. `__build_return_blocks`（:199-220）：**header/footer/page_number/aside_text/page_footnote 进 `discarded_blocks`**，其余进 `preproc_blocks`。

产出 page_info（`model_json_to_middle_json.py:256-263`）：

```
{ 'preproc_blocks': [block...], 'page_idx': n(0起始), 'page_size':[w,h](PDF点), 'discarded_blocks':[...] }
```

**块/行/span 形状（关键，对接 M1 据此取字段）**：

- 文本块：`{'type':'text'|'list'|'index'|'abstract'|..., 'bbox':Pt点, 'index','score', 'lines':[{ 'bbox', 'spans':[...] }]}`；标题块额外带 `'level':1|2`（见④）。
- span 字段：
  - 文本 `{'type':'text', 'content':'…', 'score':x, 'bbox'}`；跨页续段时带 `SplitFlag.CROSS_PAGE=True`（`para_split.py:301,337,351`）。
  - 行内公式 `{'type':'inline_equation','content':'…'}`（裸 LaTeX，无包裹符）。
  - 独立公式 `{'type':'interline_equation','content':'…','image_path':'<sha256>.jpg'}`。
  - 图片 `{'type':'image','image_path':'…','content'?}`；图表 `{'type':'chart','image_path'}`；表格 `{'type':'table','html':'<table>…</table>','image_path'}`。
- 行合并：OCR span 按 y 重叠成行、行内按 x 排序（`utils/span_block_fix.py:52-167`）。

**5 个必答字段定位速答**（字段名 → 取值位置 → 语气/单位示例）：

| 诉求 | 字段 / 位置 | 示例形状 |
|---|---|---|
| 页码 | `page_info.page_idx`（0 起始，整页 1 个）；content_list 每项复制一份 `page_idx` | `"page_idx": 2` |
| bbox | 块/行/span 的 `bbox=[x0,y0,x1,y1]`，**PDF 点**坐标（`__fix_axis` 除以渲染 scale，`pipeline_magic_model.py:295-311`）；content_list 版归一化 0–1000 | `[72.0,130.0,540.0,200.0]` / `[118,164,882,253]` |
| 阅读顺序 | 块 `index`（页内 1..N，来自 layout 阅读顺序模型 `order_seq`，重编号于 `__post_process:313-338`）；markdown / content_list 均按此序输出 | `"index": 3` |
| 标题 | pipeline 仅 `doc_title`→`level:1`、`paragraph_title`→`level:2`（`model_json_to_middle_json.py:196-208`）；md 用 `#`×level、content_list 用 `text_level` | `"type":"title","level":2` / `"type":"text","text_level":2` |
| 表格行/单元格 | 表格 **HTML** 存 `table` span 的 `html` 字段（Slanet+/Unet 重构）；行/列结构在 html/`content_list_v2` 的 `table_type`/`table_nest_level` | `<table><tr><td>…` |
| 公式（LaTeX/图片） | 独立公式 span `{"type":"interline_equation","content":"<LaTeX>","image_path":"<sha256>.jpg"}`；行内 `{"type":"inline_equation","content":"<LaTeX>"}`（裸 LaTeX）；content_list 公式项 `text_format:"latex"` | `"content":"\\frac{a}{b}"` |
| 图片链接 | 视觉 span `image_path` = `images/<sha256>.jpg`（截图自整页图，`pdf_image_tools.py:487-514`）；md 链接 `![](images/<sha256>.jpg)` | `"image_path":"9f3c…e2.jpg"` |

**标注三个易混单位**：(1) middle.json 的 bbox 是 PDF 点（≈1/72 英寸，与 `page_size` 同系）；(2) content_list 的 bbox 归一化到 0–1000 permille（LEFTTOP 原点）；(3) 图片裁剪以渲染像素为单位（= bbox×scale）。三处互不相同，M1 取数时务必按来源区分，切勿混算。

### ④ finalize —— `finalize_middle_json`（`model_json_to_middle_json.py:216-231`）

顺序：可选 post-OCR（低置信度 span 补 OCR）→ `optimize_formula_number_blocks`（公式编号 `(1)` 飞归到相邻公式）→ `para_split`（跨页合并文本/list、索引识别，`para_split.py:417-437`；合并后 span 打 `CROSS_PAGE`）→ `cross_page_table_merge`（`MINERU_TABLE_MERGE_ENABLE`，`backend/utils/runtime_utils.py:10-26`）→ **LLM 可选标题分级**（`~/mineru.json` 开 `title_aided` 才触达，默认关）→ `_post_block_process`（:196-208，**pipeline 标题层级只来自两个 label：`doc_title→title level=1`、`paragraph_title→title level=2`**）。

### ⑤ 输出 —— `union_make`（`pipeline_middle_json_mkcontent.py:968-1011`）

- `MM_MD`：逐页 `para_blocks` → 段串；标题 `'#'*level + 文本`（:34-36）；文本块转义/空格重整（CJK 拼接、西文连字符合并，:391-419）；`image/chart/table` 渲染 `![](images/<sha256>.jpg)`（或表格 html 原样嵌 markdown）；独立公式 `$$…$$`、行内 `$…$`（定界符可配）。**`discarded_blocks` 不进 md。**
- `CONTENT_LIST`（v1，结构化 JSON 数组）：每项 `{'type','text',…, 'page_idx'(继承父页), 'bbox':[归一化0-1000], 标题带 text_level}`（:609-742；bbox 归一化 :478-489）。**包含 discarded（header/footer/page_number 等）**。
- `CONTENT_LIST_V2`：按页嵌套二维数组，条目为细粒度 span 级（`paragraph_content: [{type:span_text/equation_inline}]`），**条目本身无 page_idx**（:745-900, :993-1003）。

图片资产：`cut_image`（`utils/pdf_image_tools.py:487-514`）按 `sha256("图片类型/整页md5_页码_bbox")` 命名 `.jpg`，**扁平写入 `images/` 目录**。

---

## 4. 输入输出接口规范（CLI / Python API / 产物 / 配置）

### 4.1 CLI 完整用法（`mineru/cli/client.py:1037-1184`）

```bash
mineru -p <文件或目录> -o <输出目录> \
       [-b pipeline|vlm-engine|vlm-http-client|hybrid-engine|hybrid-http-client] \
       [-m auto|txt|ocr] [-l ch|en|...] \
       [-s 起始页] [-e 结束页] [-f true|false] [-t true|false] \
       [--effort medium|high] [--image-analysis true|false] \
       [--client-side-output-generation] [--api-url <http://...>] \
       [-v]
```

- `-p`：文件或目录；支持 pdf / jpg/png 等图片（内部自动转 PDF）/ docx / pptx / xlsx（`client.py:1040-1047`）。
- `-b`：默认 `hybrid-engine`（`backend_options.py:15`）。`pipeline` 不需 VLM。
- `-m`：`auto`（内部 `classify` 判定）/ `txt`（只用文本层）/ `ocr`（强制 OCR）——仅 `pipeline`/`hybrid-*` 生效（:1063-1076）。
- `-l`：OCR 语言（pipeline 的 rec 模型语言，默认 `ch`，`utils/ocr_language.py:3-19`）。
- `-s/-e`：0 起始页码切片。
- `-f/-t`：公式 / 表格识别开关。
- `--effort`：hybrid 力度（`medium` 更快、禁图分析；`high` 开图/表分析）。
- `--api-url`：指向**已启动**的 mineru-api；省掉本地临时拉起的冷启动。
- `--client-side-output-generation`：服务端只回 staged middle json + 图片 + 原文件，客户端本地再生 md/content_list（`client.py:1175-1184`）。

### 4.2 Python API（v3 形态）

- **没有** v2/magic-pdf 风格的 `from mineru import MinerU` 一次性对象 API（`mineru/__init__.py` 为空）。init / device / `from_pretrained` 概念已被内部 `MineruPipelineModel`（`model_init.py:231-291`，读 `MINERU_DEVICE_MODE`/`~/mineru.json` 自动拉模型）替代。
- 官方推荐调用路径有两条：
  1. **HTTP**：`from mineru.cli import api_client`，或直接起 `mineru-api`（`fast_api.py:1395`）后按 §4.2 的端点 POST；demo 见 `demo/demo.py:95-120`。
  2. **进程内函数**：`mineru.cli.common.do_parse(output_dir, pdf_file_names, pdf_bytes_list, p_lang_list, backend='pipeline', …)`（`common.py:668`）——office/pipeline 同步、vlm/hybrid 有 `aio_do_parse`（:760）。
- **mineru-api 端点**（`fast_api.py`）：

| 方法/路径 | 说明 | 行号 |
|---|---|---|
| `POST /file_parse` | 同步（等任务完成一次性回包） | :1228 |
| `POST /tasks` | 异步提交（202 + task_id） | :1276 |
| `GET /tasks/{id}` | 任务状态/进度 | :1296 |
| `GET /tasks/{id}/result` | 下载结果（zip 或 JSON+base64 图） | :1305 |
| `GET /health` | 健康检查 / 并发容量 | :1352 |

- 请求字段（form-data，`api_request.py:35-49,92-187`）：`lang_list / backend / method / effort / formula_enable / table_enable / image_analysis / server_url / start_page_id / end_page_id / return_md / return_middle_json / return_model_output / return_content_list / return_images / response_format_zip / return_original_file`。**本地 CLI 默认全开**（`client.py:681-700`），仅 `--client-side-output-generation` 时服务端省去 md/content_list 渲染。

其他服务入口（`pyproject.toml` console_scripts）：`mineru-api`（常驻服务）、`mineru-gradio`、`mineru-router`（多 worker 路由）、`mineru-vllm-server / mineru-lmdeploy-server / mineru-openai-server`（VLM 推理服务进程）、`mineru-models-download`（预下载模型）。

### 4.3 输出产物清单

```
<输出目录>/<文档名>/<parse_dir>/
   │  parse_dir：pipeline→txt|ocr；vlm→vlm；hybrid→hybrid_<method>；office→office（output_paths.py:9-26）
   ├─ <name>.md                    # MM_MD markdown；图片 ![](images/<sha256>.jpg)；表格内嵌 html；公式 $$...$$
   ├─ <name>_middle.json           # 信息最全：pdf_info[] + _backend/_version_name（model_json_to_middle_json.py:234）
   ├─ <name>_model.json            # 各页 layout_dets 原始快照（build_page_model_info:67-69）
   ├─ <name>_content_list.json     # 结构化 v1：每项含 type/page_idx/bbox(0-1000)/text_level
   ├─ <name>_content_list_v2.json  # 按页嵌套 v2（条目无 page_idx，span 级）
   ├─ <name>_origin.pdf            # 原文件备份（服务器路径默认含，fast_api.py:849-851）
   └─ images/                      # 裁剪图，扁平 <sha256>.jpg（pdf_image_tools.py:506）
```

写盘函数 `_process_output`（`common.py:259-348`）；服务器 zip 打包 `create_result_zip`（`fast_api.py:492-...`，arcname = `<name>/<parse_dir>/…`）。`layout.pdf / span.pdf` 可视化仅在直接调 `do_parse` 且 `f_draw_*_bbox=True` 时生成（服务器路径恒 False，`fast_api.py:844-845`）。

### 4.4 配置（`~/mineru.json` + env）

`mineru.template.json` 与 `config_reader.py:14-30`（文件名由 `MINERU_TOOLS_CONFIG_JSON` 覆盖）：

- `models-dir`: `{pipeline, vlm}` 本地模型根路径（`model-source:"local"` 时必填）。
- `model-source`: `auto`(默认, 探测网络)/`huggingface`/`modelscope`/`local`（`models_download_utils.py:190-219`）。
- `llm-aided-config.title_aided`: 标题分级 LLM（`enable:true` 才生效）。
- `latex-delimiter-config`: 公式定界符（默认 `$$` / `$`）。
- `bucket_info`: S3（`s3://` 输入/输出时用）。

环境变量（常用）：`MINERU_DEVICE_MODE`(cuda/mps/cpu…，`config_reader.py:105-137`)；`MINERU_PROCESSING_WINDOW_SIZE`(默认64)；`MINERU_API_MAX_CONCURRENT_REQUESTS`(默认3)；`MINERU_FORMULA_ENABLE / MINERU_TABLE_ENABLE / MINERU_OCR_DET_MASK_INLINE_FORMULA_ENABLE`；`MINERU_TABLE_MERGE_ENABLE`(默认true)；`MINERU_FORMULA_CH_SUPPORT`(公式模型切 `pp_formulanet_plus_m`，默认 `unimernet_small`)；`PYTORCH_ENABLE_MPS_FALLBACK=1`（源码已内置，`pipeline_analyze.py:31`）；`HF_ENDPOINT`（走 HF 下载时生效）。

模型（`enum_class.py:96-108`）：pipeline = `PDF-Extract-Kit-1.0`（Layout/OCR/MFR/TabRec/CLS 同仓子路径，按需 `snapshot_download(allow_patterns=…)` 懒加载）；vlm = `MinerU2.5-Pro-2605-1.2B`。

### 4.5 最小可跑 Python 调用（走 mineru-api HTTP）

```python
import httpx

async def parse_pdf(path: str, api: str = "http://127.0.0.1:8000") -> bytes:
    form = {
        "lang_list": ["ch"],
        "backend": "pipeline", "method": "auto",
        "formula_enable": "true", "table_enable": "true",
        "return_md": "true", "return_middle_json": "true",
        "return_content_list": "true", "return_images": "true",
        "response_format_zip": "true", "return_original_file": "false",
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(600), follow_redirects=True) as c:
        files = {"files": (path.rsplit("/", 1)[-1], open(path, "rb"), "application/octet-stream")}
        r = await c.post(f"{api}/file_parse", files=files, data=form)
        r.raise_for_status()
        return r.content  # response_format_zip=true → zip bytes（§4.3 布局）
```

要点：`/file_parse` 同步等待到 `TASK_SUCCESS`；长文档建议改为 `POST /tasks` + 轮询 `GET /tasks/{id}` + 最后 `GET /tasks/{id}/result`（`fast_api.py:1276-1349`）。zip 内结构即 §4.3 产物清单（arcname = `<name>/<parse_dir>/…`）。

### 4.6 输入－响应矩阵（三种本地 backend 对比）

| 维度 | pipeline | vlm-engine | hybrid-engine(默认) |
|---|---|---|---|
| 需要模型 | PDF-Extract-Kit-1.0（多小模型，CPU 可跑） | MinerU2.5-Pro 大 VLM（需 vllm/lmdeploy/mlx，推荐 GPU） | VLM 版面 + pipeline 原子模型（需 vllm/lmdeploy/mlx） |
| 解析质量/速度 | 通用，均衡 | 极高（版面语义级） | 高，`effort` 可控 |
| OCR / 表格 / 公式 | 原子模型链 | VLM 原生 + 原子模型补 | 复用 pipeline 原子链 |
| 输出形状 | 统一（见 §3/§4.3） | 统一（title `level` 更细，VLM 直出） | 统一（含 `_effort` 元字段） |
| CPU/MPS 可跑 | ✅（表格 onnx 恒 CPU） | 仅 macOS mlx（Apple Silicon） | 仅 macOS mlx / 不推荐 CPU |
| M1 建议 | **主力** | 备选（有 GPU 时提质） | 暂不采用（要 VLM） |

---

## 5. 与 M1 的对接点（blocks 侧车字段建议）

LightRAG 现已内置 MinerU 消费链：`MinerURawClient`（下载原始包）→ `MinerUIRBuilder`（`lightrag/source/lightrag/parser/external/mineru/ir_builder.py`）读 **`content_list.json`** → `IRDoc`（含 `IRPosition(type="bbox", anchor=页码1起始, range=bbox)`）→ sidecar writer → `blocks.jsonl`。**因此 M1 的 blocks 侧车数据源首选 `content_list.json`（v1），无需二次插桩。**

| 我们要的字段 | 从哪里取 | 证据 |
|---|---|---|
| `page_label`（页码） | content_list 每项 `page_idx`（0 起始；IR 层转 1 起始 anchor） | `pipeline_middle_json_mkcontent.py:740`；`ir_builder.py:663-681` |
| `block_type` | 每项 `type`（`text/list/index/title/table/image/equation/code/page_number`…）；`title` 的子类看 `text_level` | `pipeline_middle_json_mkcontent.py:609-742`；`ir_builder.py:577-594` |
| `bbox` | 每项 `bbox`（**已归一化 0–1000**，LEFTTOP） | `pipeline_middle_json_mkcontent.py:478-489` |
| 标题 heading/level | `text_level>0` → 语义为标题，`#`×level | `pipeline_middle_json_mkcontent.py:643-650` |
| 块内更细位置（行/span级） | `_middle.json`：先按 `pdf_info[i].page_idx` 迭代 `para_blocks`，块内 `lines[].spans[]` 的 `type/bbox/score`，**页号从父 page_info 继承**；跨页块由 span 的 `cross_page` 标记指示 | `model_json_to_middle_json.py:256-263`；`para_split.py:436-437` |

要点：

1. **扩展字段插入最自然的位置** = content_list 每项本身（已内嵌 `page_idx`+`bbox`+`text_level`），M1 只需原样透传给 blocks 侧车的扩展字段；**不要**从中间态重新推导页码/块类型，content_list 已完成所有聚合。
2. 若 M1 需要**比"段落一级"更细**的侧车（LightRAG P 策略按块切分只需段落级），才读 `_middle.json` 的 span 级数据自研一个转换器（此时必须按父 `page_info.page_idx` 补齐页号并自行归一化 bbox，公式同 `_build_bbox`）。
3. `content_list.json` **含 header/footer/page_number 项**——blocks 侧车如不想要布局噪声，按 `type == "page_number"` 跳过（IR builder 已这样处理，`ir_builder.py:282-283`）；md 里本来就没有（union_make 只渲染 `para_blocks`）。
4. 表格/公式/图片的引用：content_list 的 `table_body`(html) / `img_path` / `text_format=latex` 等已可直接消费（`ir_builder.py:414-569`）。

> **M1 实操示例**：`content_list.json` 中一条标题项与一段正文项 →
>
> ```json
> {"type":"text", "text":"1.1 背景", "text_level":2, "bbox":[0,100,500,120], "page_idx":3}
> {"type":"text", "text":"我们提出……", "bbox":[0,130,500,300], "page_idx":3}
> ```
>
> 映射进 blocks 侧车扩展字段：`block_type="heading/body"`（由 `text_level>0` 判定）、`page_label="4"`（`page_idx+1`）、`bbox` 原样随块（IRPosition.range）、heading=项文本、level=`text_level`。这段映射直接对应 LightRAG `ir_builder.py:_detect_heading`（:577-594）与 `_extract_bbox_position`（:698-711）已实现的逻辑，**M1 不必重写，只需在其上补 `block_type` 聚合规则**（如 `text_level>0→heading`，`type=table/image/equation→对应类型）。

---

## 6. 已知坑

1. **模型下载（国内网络）**：默认 `model-source:auto` 探测 HF，超时会回退 ModelScope。M1 环境务必设 `export HF_ENDPOINT=https://hf-mirror.com`（HF `snapshot_download` 走镜像）或 `~/mineru.json` 设 `model-source:"modelscope"`；完全离线时把整套模型放 `models-dir.pipeline`（相对结构要匹配 `models/…` 子路径）。
2. **Mac MPS / CPU**：`get_device` 会优先使用 MPS（若无显式 `MINERU_DEVICE_MODE`）；layout/OCR/公式是 PyTorch 落 MPS（内置 fallback），**表格 onnx 恒 CPU**（`onnxruntime_provider.py:35-47` 仅 cuda/cpu）。无高端 GPU 时 `batch_ratio=1`（`pipeline_analyze.py:353-363`），量大需调小 `MINERU_PROCESSING_WINDOW_SIZE` 控内存。MPS 某些算子偶有兼容问题，卡死时降级 `MINERU_DEVICE_MODE=cpu`。
3. **表格细节**：表格 html 由无线(SlanetPlus)/有线(Unet) 双链重构；`cls_score<0.9` 走有线。识别失败时表格仍保留 `image_path` 图片兜底（md 渲染 `![](images/…)`）。跨页表默认合并（`MINERU_TABLE_MERGE_ENABLE`），想逐页留原始块需关掉。
4. **公式细节**：UniMERNet 默认拉丁；含中文公式开 `MINERU_FORMULA_CH_SUPPORT=true` 切 `pp_formulanet_plus_m`。独立公式图片被裁出后也原样落 images/，md 优先用 LaTeX。
5. **大批量 / 并发**：服务端单 worker 串行消费 `AsyncTaskManager` 队列，`--api-url` 指向自建的 mineru-api（可多卡多实例）可外部并发；本地 CLI 每次冷启动临时服务，批量文件一次任务内串行按窗口执行。pdfium 渲染走 spawn 进程池（默认 ≤3 进程）。
6. **失败与部分成功语义**：坏页回退——pdfium 重写跳过坏页（`common.py:194-247`）；表格方向/分类失败仅告警降级；单 doc 任务失败 API 返回 409（`fast_api.py:1260-1267`），CLI 汇总 failures 报错并非零行为。**要排查建议开 `return_middle_json` 保留中间态**。
7. **版本差异**：`hybrid-*` 为 3.4.5 新增且为默认；其标题层级由 VLM 输出 `doc_title/paragraph_title` 映射（`hybrid_model_output_to_middle_json.py:165-180`）——字段形状与 pipeline 对齐，但 `_backend` 为 `hybrid`、额外 `_effort`。
8. **`-m txt` 与纯文本 PDF**：`txt` 模式跳过整页 OCR，只靠 pdfium 文本层 + post-OCR 兜底（`span_pre_proc.py:43` 高字符数页仍会回退 OCR），快很多，但**无字体/艺术效果 OCR 的好排版表现**；扫描件必须 `-m ocr` 或 `auto`（classify 会判）。
9. **`--image-analysis` 联动 hybrid**：`effort=medium` 时该开关被强制关掉（`hybrid_analyze.py:117-123`），想开图/表分析须 `--effort high`。
10. **并发上限与队列**：`MINERU_API_MAX_CONCURRENT_REQUESTS`（默认 3）限制同跑任务数，超出排队；任务有保留期（`get_task_retention_seconds`），过期结果被清理——**长期保留请自行下载 zip 归档**。

---

## 7. M1 采用建议

- **复用**：pipeline 后端整套（不选默认的 hybrid——本机无 VLM 运行条件）；CLI `mineru -b pipeline -m auto` 产出的 `content_list.json` + `images/` 作为 blocks 侧车**标准输入**，直接喂 LightRAG 现有 `MinerUIRBuilder` → `blocks.jsonl`，零改动闭环；`_middle.json` 作为溯源/兜底中间态长期保存。
- **自研**：一个轻量转换（从 `_middle.json` 的 `para_blocks` 级联页号做 span/行级侧车）**只在需要块内更细粒度时**才做；默认不建。若做，复杂度集中在「多条页号的继承 + bbox 0-1000 归一化 + 跨页标记保留」，建议固化进 M1 契约的 `page_range/anchor` 取值规则。
- **需改配**：`~/mineru.json`（`model-source`/`models-dir`）、`HF_ENDPOINT`、机器 `MINERU_DEVICE_MODE`、窗口/并发上限；请求参数务必 `return_middle_json=true + return_content_list=true + return_images=true`，并预留 `MINERU_ENGINE_VERSION`/`MINERU_BBOX_ATTRIBUTES`（IR builder 已支持，`ir_builder.py:86-91,93-116`）。
- 一条命令可固化进 M1 脚本：`mineru -p <in> -o <raw> -b pipeline -m auto -l ch -f true -t true`，然后 `content_list.json` 即侧车源、`<name>.md` 即统一 Markdown。