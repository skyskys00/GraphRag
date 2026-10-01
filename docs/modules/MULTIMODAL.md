# 多模态方案：说明书图片入索引

> **版本：** v0.3.2（M0–M8 全链已实施，M3/M5 零改动已验证）
> **状态：** 已实施（M0 契约 / M1 解析 / M2 切分 / M6 溯源 / M7 接口 / M8 前端）
> **更新：** 2026-10-01
> **定位：** 跨模块特性方案（M0 契约 / M1 解析 / M2 切分 / M6 溯源 / M7 接口 / M8 前端）——把说明书里的图片转为可检索文本块
> **契约：** 扩展 [`M0_contracts/parse.md`](M0_contracts/parse.md) §3（`img_path`）与 [`textunit.schema.json`](M0_contracts/textunit.schema.json)（`image_path`）
> **上游：** [M1 解析层](M1_parse.md) | **下游：** [M2 切分层](M2_chunk.md) → [M3 索引层](M3_index.md)
> **依据：** 有源器械说明书样本实测（2 份 54 张图全量视觉调用，见 §2.3）｜ memory `graphrag-multimodal`
> **运行：** 视觉增强随 M1 自动执行，开关 `VISION_ENABLED`
> **变更历史：** 见 [`CHANGELOG.md`](../CHANGELOG.md)

## 1. 定位与职责

把 PDF 说明书里的**图片**（界面截图、按键图标、连接示意图、图片型表格）转成**可被检索的文本块**，让「这个报警图标是什么意思」「如何连接血氧探头」这类只能从图上读到答案的问题能够被召回。

**不在本方案范围**：图片视觉问答（把原图喂给多模态大模型作答）。本方案只做**图片→文本描述**的单向转换，检索与生成链路保持纯文本。

## 2. 为什么必须做（现状实证）

### 2.1 图片是信息载体，不是装饰

对两份真实器械说明书（铭昇 H2-5000IBP 有创血压模拟仪 14 页 / 融柏 LSP-1C 注射泵 28 页）实测：

| | 样本1 铭昇 | 样本2 融柏 |
|---|---|---|
| content_list 中 image 条目 | 15 | 35 |
| images/ 落盘文件 | 16 | 38 |
| 表格 | 1（无真表格，参数为「标签：值」逐行） | 3（含真表格） |
| **图片型表格** | — | 有：「标准注射器内径」查找表**文本层完全为空**，该页仅一张 577×520 图 |
| 图片尺寸形态 | 7 张 ≤146×70 图标 + 1 张 390×285 装饰图 + 2 张实物照 + 6 张 ~670×487 界面/面板图 | 4 张 ≤98×84 图标 + 2 张 logo + 4 张连接示意 + 23 张 ~542×310 界面截图 + 4 张表格图 + 1 张产品照 |

> 上表尺寸为 **MinerU 落盘 `images/` 的实测值**。它与「直接抽 PDF」得到的尺寸不同（MinerU 按渲染 DPI 重新裁剪/缩放），故同一张「24 张菜单截图」在两种取法下分别是 480×272 与 542×310。

**判据 = 尺寸高度规整 ⇒ 截图/图表**（照片尺寸随机）。两份样本都有成批同尺寸的界面截图 ⇒ 图片是信息载体。

但实测发现**尺寸规整/面积大 ≠ 有检索价值**：样本1 的 390×285 装饰图、样本2 的 YS logo（面积 114,552，比部分截图还大）都无文字可读 ⇒ 判据需叠加视觉模型自评（§2.3）。

**结论**：图片型表格的答案**只存在于图上**，纯文本链路永远召回不到；界面截图承载按键/菜单/报警语义，是器械说明书的核心信息形态。

### 2.2 现状：图片在链路第一环就被静默丢弃

| 层 | 现状 | 证据 |
|---|---|---|
| **M1** `blocks_builder.py:63-65` | `image` 条目走 `else` 分支 `content = text`，而 MinerU 的 image 条目 `text` 为空 ⇒ **content 空**；`img_path` **完全未提取** | blocks.jsonl 中 drawing 块 content 空 **15/15、35/35**；含 img_path **0/15、0/35** |
| **M1** `mineru_adapter.py:61-64` | ✅ **图片文件已拷到产物** `<doc_dir>/images/`（16 / 38 张） | 图片没丢，只是没人引用 |
| **M2** `chunker.py:331-334` | `drawing` 无分支 ⇒ 走 `else` 进 `cur["blocks"]` ⇒ `_joined_content()` 跳过空 content ⇒ 末尾 `[u for u in out if u["content"].strip()]` **过滤掉** | 静默丢弃，无日志 |
| **M3** `runner.py` | 只取 `u["content"]` | 图片块 content 一旦非空即自然进索引 |
| **M6** `sidecar.py` | `ChunkMeta` 无图片字段 | 引用溯源拿不到图 |
| **M7** `api.py:134` | preview 返回 unit 无图片字段；根路径 `app.mount("/", StaticFiles(...))` 已占用 | 前端无图可显 |

**MinerU 的 `image_caption` / `image_footnote` 实测几乎全为空，且非空者多为噪声**（⚠️ 此处 2026-09-29 实施时修正，初稿曾断言「50 个 image 条目无一有值」，实测有误）：

| | 样本1 铭昇（15 条目） | 样本2 融柏（35 条目） |
|---|---|---|
| `image_caption` 有值的条目 | 0 | 23（共 26 条 caption item） |
| ↳ 其中**纯图号**（剥掉「图 N」后无正文） | — | 23 条 ⇒ **丢弃** |
| ↳ 其中**含真图注** | — | 3 条 ⇒ 保留（如「图17为先抽取后灌注界面…」） |
| `image_footnote` 有值 | 0 | 1（p22「在如图23中，外控选择处有三种选择模式：关闭，电平和脉冲。」） |

⇒ 免费文字线索**稀少且大部分是纯图号噪声**，图片语义主体仍必须靠视觉模型生成。
M1 对 caption 做可用性过滤（§4.2 `_usable_mineru_caption`），footnote 不过滤（实测无噪声，最小改动）。

### 2.3 视觉通路实证（DeepSeek 官方 API）

`.env` 已有 `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL`（`https://api.deepseek.com/v1`）⇒ **无需新增任何凭据**。

`GET /v1/models` 返回的可用模型中，**`deepseek-flash`（`DeepSeek-V4.1-Flash`）支持图片输入**：

| 模型 | `input_modalities` | context | 结论 |
|---|---|---|---|
| `deepseek-flash` | `["text","image"]` | 1,048,576 | ✅ 本方案采用 |
| `deepseek-v4-pro` | `["text"]` | — | ❌ 不支持图片 |

⚠️ **必须关思考**：`deepseek-flash` 是推理模型，不传 `extra_body={"thinking":{"type":"disabled"}}` 会 content 为空（同 [`pitfalls/deepseek-thinking-mode.md`](../pitfalls/deepseek-thinking-mode.md)）。

**全量实测**（两份样本 54 张图，5 并发，**总耗时 10.0s / 累计 16,782 tokens**）：

| | 样本1 铭昇（16 张） | 样本2 融柏（38 张） |
|---|---|---|
| 有可读文字 | 9（界面/面板图 6 + 实物照 2 + ENTER 图标 1） | 34（界面截图 23 + 表格图 4 + 连接示意 4 + 产品照 1 + logo 2） |
| 无文字（装饰/图标） | 7 | 4 |

**描述质量达标**：界面截图读出全部关键数值与按钮（如「先抽后灌模式 / 灌注量 50.00uL / 运行速度 302.8um/min / 运行时间 15.00sec」）；**图片型表格被完整转写**（「Hamilton、Unimetrics、Popper & Sons 品牌微量注射器容量与尺寸对应表」）—— 这正是纯文本链路永远召回不到的部分。

**关键发现：面积不是唯一判据。** 样本1 有张 390×285（111,150 px²）的图仍是装饰图标，样本2 有张 296×387（114,552 px²）的 YS logo —— 面积比部分信息图还大却无检索价值 ⇒ 面积阈值只能兜住小图标，**大尺寸装饰图需靠视觉模型自评**（§4.2 prompt 约定）。

## 3. 方案总览

```
M1 解析（扩展）
  源 PDF
    → MinerU（不变）
    → content_list.json + images/（不变）
    → 【新】vision.py：对 image 条目调 deepseek-flash → 描述文本
    → blocks_builder：drawing 块填 content=描述、img_path=images/<sha>.jpg
    → blocks.jsonl
        ↓
M2 切分（扩展）
    → 【新】drawing 分支：图片独立成 TextUnit（content=描述，image_path=...）
        ↓
M3 索引（零改动）    content 非空 ⇒ 自然进 LightRAG
M5 检索（零改动）    sparse 从 PG 读 content ⇒ 自然进稀疏索引
        ↓
M6 溯源（小改）      ChunkMeta 加 image_path
M7 接口（小改）      preview 返回 image_url + 新增图片静态路由
M8 前端（小改）      预览面板渲染 <img>
```

**设计取舍：视觉描述生成放在 M1，不放 M2。**

| 候选 | 否决/采纳理由 |
|---|---|
| M2 切分时调 | ❌ 违背 M2 既定原则「纯本地规则切分，**不依赖 LLM/embedding**」（[M2_chunk.md](M2_chunk.md) §1），且让切分层无法离线复跑 |
| M7 入库编排时调 | ❌ 离线建库（`python -m app.m1_parse.run`）走不到 M7，两条链路会分叉 |
| **M1 解析后置步骤** | ✅ 图片→文本属**解析增强**（对齐 Docling picture-description enrichment 的定位）；M1 是唯一同时持有 `img_path` 与 `images/` 文件的层；**M7 `ingest` 编排零改动** |

## 4. 分模块改动

### 4.1 M0 契约

| 文件 | 改动 |
|---|---|
| [`M0_contracts/parse.md`](M0_contracts/parse.md) §3 | 扩展字段表 **3 → 4 行**，新增 `img_path`：取值 MinerU `content_list[].img_path`（相对 doc_dir，如 `images/371ceffd….jpg`）；非图片块为 `null` |
| [`M0_contracts/textunit.schema.json`](M0_contracts/textunit.schema.json) | `properties` 新增 `image_path`（string，描述：图片块的原图相对路径 `<doc_dir>/images/<sha>.jpg`；仅 `block_type=drawing` 时存在）。`additionalProperties: true` 本已放行，此处**显式声明**以便契约自证 |
| [`M0_contracts/textunit.md`](M0_contracts/textunit.md) | 同步 `image_path` 字段说明 |

> `block_type` enum 已含 `drawing`，`sidecar.type` enum 已含 `drawing` —— **无需改动**。

### 4.2 M1 解析层

**新增 `backend/app/m1_parse/vision.py`**（约 170 行）：

```python
"""M1 图片语义增强：image 条目 → 视觉模型描述文本。

复用 .env 已有 DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL（官方 https://api.deepseek.com/v1），
模型 deepseek-flash（DeepSeek-V4.1-Flash，input_modalities 含 image）⇒ 无需新增凭据。

同步 HTTP（urllib）—— 不用 AsyncOpenAI：
M7 ingest() 在 async 上下文里同步调 process_one()，内部若 asyncio.run() 会抛
"cannot be called from a running event loop"。与 sparse_index._sparse_encode 同风格。

产物 <doc_dir>/image_captions.json：{img_path: {"caption": str, "model": str, "error": str|None}}
缓存：已有且无 error 的 key 不重复调用（重跑幂等，省成本）；有 error 的下次重试。
失败：单图失败记 error、caption 留空，不抛异常（不阻塞整篇解析）。

M1 CLI 不经 runner 加载 dotenv，模块内 _load_env() 兜底读 backend/.env（setdefault，幂等）。
"""

NO_INFO = "无有效信息"   # 模型自评标记：M1 侧据此丢弃 caption

def enabled() -> bool:      # VISION_ENABLED（默认 true）
def _model() -> str:        # VISION_MODEL（默认 deepseek-flash）
def _min_area() -> int:     # VISION_MIN_AREA（默认 10000）
def _image_area(p: Path) -> int:
    """图片**像素**面积（PIL 读文件头，不解码全图）。"""
    # ⚠️ 实施修正：阈值作用于像素面积，不是 content_list 的 bbox 面积。
    # MinerU bbox 是归一化 0–1000 坐标，其面积在阈值 10000 下滤 7/15 与 5/35，
    # 与 §2.3 尺寸形态对不上；改用像素面积后滤 6/15 与 4/35，与 ≤142×68 / ≤98×84 吻合。

def _caption_one(img_abs: Path, page_idx: int | None) -> str:
    """单图 → 描述文本。messages:
    {"type":"image_url","image_url":{"url":"data:image/jpeg;base64,<...>"}}
    prompt 要求模型输出 `类型|是否有可读文字|描述`，读出按钮名/菜单项/数值/报警文本；
    无任何可读文字与信息内容时只输出 NO_INFO（过滤大尺寸 logo/装饰图，零额外成本）。
    body 顶层传 "thinking":{"type":"disabled"}（deepseek-flash 是推理模型）。"""

def caption_images(doc_dir: Path, content_list: list[dict]) -> dict[str, str]:
    """对 content_list 中 image 条目批量生成描述，返回 {img_path: caption}。
    面积兜底 → 按 img_path 去重（MinerU sha256 命名，同图同路径）→ 读缓存 → 5 并发调用。
    NO_INFO 与空串不入返回 dict ⇒ 上层自然退化为纯文本链路。"""
```

**改 `blocks_builder.py`**：
- `build_blocks_from_mineru(data, doc_id, captions: dict[str,str] | None = None)` 加参数
- `typ == "image"` 分支（现落在 `else`）独立出来，并对 MinerU 图注做可用性过滤：
  ```python
  _FIGNO_RE = re.compile(r"图\s*[0-9０-９]+")   # 图号部分
  _MIN_CAPTION_CHARS = 6                        # 剥掉图号后剩余正文的最小字数

  def _usable_mineru_caption(cap: str) -> bool:
      """剥掉「图 N」后剩余正文 ≥6 字才可用（滤掉 '图 13'、'图 9\\n图 10'、'如图 22'）。"""
      return len(_FIGNO_RE.sub("", cap).strip()) >= _MIN_CAPTION_CHARS

  elif typ == "image":
      img = it.get("img_path") or None
      parts = [*(c for c in (it.get("image_caption") or []) if _usable_mineru_caption(c)),
               (captions or {}).get(img or "", ""),
               *(it.get("image_footnote") or [])]
      content = "\n".join(p for p in parts if p.strip())
      fmt = "plain_text"
  ```
  > 为什么需要过滤器：见 §2.2 —— 融柏 26 条 caption 中 23 条是纯图号，直接拼进 content 会让
  > 「图 13」这类无检索语义的块进索引；视觉调用失败时（实测 3/50 张 `RemoteDisconnected`）更会
  > 退化成纯图号块。过滤器保留 3 条真图注。**footnote 不过滤**（实测仅 1 条且有实义，最小改动）。
- block dict 新增 `"img_path": img`（契约 §3 扩展字段）

**改 `mineru_adapter.py`**：`build_blocks_from_mineru` 调用前插一行
```python
captions = vision.caption_images(doc_dir, data)   # VISION_ENABLED=false 时内部返回 {}
blocks = build_blocks_from_mineru(data, doc_id, captions)
```

**不改** `run.py`（`process_one` 签名与调用方零改动）、`docling_adapter.py`（第一版只覆盖 MinerU 链路，见 §7）。

### 4.3 M2 切分层

**改 `chunker.py`** —— 在 `bt == "table"` 分支之后新增：

```python
if bt == "drawing" and b.get("content"):
    # 图片独立成块：不与相邻正文合并（描述语义自足），仍带当前标题链上下文
    _flush(cur); cur = None
    chunks.append(_build_image_unit(b, len(chunks), doc_id, file_path, path))
    continue
```

**新增 `_build_image_unit()`**（结构仿 `_build_split_unit`）：`block_type="drawing"`、`content`=视觉描述、新增 `image_path`=`b["img_path"]`、`title_path`/`page_range`/`anchor` 照常聚合。

关键点：
- **原位**：遍历到该块时 append，`chunk_order_index` 天然落在正确位置
- **终态**：直接构造终态 TextUnit（无 `blocks` 键）⇒ 末尾 `out = [c if "blocks" not in c else _build_textunit(...)]` 原样保留（与表格子块同机制）
- **降级**：`VISION_ENABLED=false` 时 `vision.caption_images` 内部直接返回 `{}` ⇒ 图片块 content 只可能来自
  过滤后的 MinerU 图注（§2.2：实测仅 3 条真图注、4 个块），**不因视觉产生任何块**；小图标/装饰图
  既无图注也无描述 ⇒ content 空 ⇒ 走不到此分支 ⇒ 被末尾过滤丢弃。视觉调用失败（实测 3/50 张
  `RemoteDisconnected`）同此路径，不会退化成「图 13」这类纯图号块。

### 4.4 M3 / M5 索引检索层 —— 零改动

| 层 | 论证 |
|---|---|
| M3 [`runner.py`](M3_index.md) | `load_rag_data` 只取 `u["content"]`；图片块 content 是描述文本 ⇒ 自然进 `ainsert_custom_chunks` |
| M5 [`sparse_index.py`](M5_retrieve.md) | `_pg_chunks` 从 PG `lightrag_doc_chunks` 读 content（`WHERE content <> ''`）⇒ 图片块自然进稀疏索引；`extra_meta` 从 M2 jsonl 取 `block_type`，`drawing` 自然带上 |

**验证方式**：M2 产出后直接查 `data/chunks/<doc_id>.jsonl` 是否含 `block_type=drawing` 的非空块；M3 索引后查 PG `lightrag_doc_chunks` 是否有对应行。

> **零改动的边界**（2026-10-01 实测后澄清）：多模态本身**不需要**改 M3/M5。v5.25.1 对 M5 `sparse_index.build()` 的改动是修重建链一个**既有**对齐缺陷（M2↔PG 元数据关联键），与图片通路无关 —— 详见 [`docs/pitfalls/lightrag-chunk-id-dedup.md`](../pitfalls/lightrag-chunk-id-dedup.md)。

### 4.5 M6 溯源 ✅

**改 `sidecar.py`**：`ChunkMeta` 加 `image_path: str | None = None`；`load()` 里 `image_path=rec.get("image_path")`。

**改 `cite.py`（方案外补充）**：`Citation` 加 `image_path` + `to_dict()` 输出。§4.7 的引用卡片缩略图需要它，只改 `sidecar.py` 无法把 `image_path` 传到 M7/M8。`parse_citations` 用 `sidecar.resolve()` 拿到的 `ChunkMeta` 填充。

### 4.6 M7 接口层 ✅

**改 `api.py`**：

1. `preview_doc()` 返回的 unit 加 `"image_url"`：
   ```python
   img = u.get("image_path")
   ... "image_url": f"/docs/{doc_id}/{img}" if img else None,
   ```
2. **新增图片静态路由**（定义在 `api.py:172`，早于 `app.mount("/", StaticFiles(...))`（`api.py:333`）——Starlette 按注册顺序匹配，mount 是 catch-all）：
   ```python
   @app.get("/docs/{doc_id}/images/{name}")
   async def doc_image(doc_id: str, name: str, collection_id: str = Query("default")):
       """serve parse/<doc_id>/images/<name>。doc_id/name 白名单 + resolve 后确认在 images/ 之下。"""
   ```
   ⚠️ **安全**：实测 `..%2f` 编码穿越、`--path-as-is` 原始穿越、doc_id 段穿越三类均返回 404；正常图片返回 200 `image/jpeg`。

**`documents.py` 零改动**：`ingest()` 调 `process_one()` 时视觉步骤已内嵌 M1 ⇒ 上传链路自动获得多模态。

### 4.7 M8 前端 ✅

**改 `DocumentPreview.tsx`**：unit 渲染加 `block_type === 'drawing'` 分支 —— 渲染 `<img src={unit.image_url}>` + 描述文本，点击开 lightbox 放大。

**改 `frontend/src/types.ts`**：`PreviewUnit` 加 `image_url?: string | null`；`Citation` 加 `image_path: string | null`。

**改 `CitationPanel.tsx`**：引用卡片 `image_path` 非空则展示缩略图（点击跳原文档预览）。

**改 `lib/api.ts`（方案外补充）**：新增 `apiUrl(path, collection_id)` —— 后端返回**裸路径**（`/docs/<doc>/images/<name>`），前端补 `API_BASE` + `collection_id` 查询参数。

**改 `App.css`**：新增 `.preview-drawing` / `.preview-drawing-img` / `.preview-lightbox` / `.cite-thumb` 四组样式。

**改 `mocks/events.ts`**：三条 mock 引用补 `image_path: null`（`Citation` 类型加必填字段后 tsc 报错）。

### 4.8 配置

**`.env` 新增**（视觉通路**复用已有 `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL`**，无新增凭据）：

```bash
# 视觉模型（M1 图片语义增强）
VISION_ENABLED=true
VISION_MODEL=deepseek-flash   # DeepSeek-V4.1-Flash，input_modalities 含 image
VISION_MIN_AREA=10000         # px²，成本兜底：小于此面积不调模型（滤小图标）
```

同步更新 `.env.example`。

## 5. 成本控制

| 手段 | 说明 |
|---|---|
| **小图标过滤** | `VISION_MIN_AREA` 阈值作用于**图片像素面积**（PIL 读文件头，非 MinerU bbox 面积，见 §4.2 实施修正）。实测（10000）：样本1 滤 6/16、样本2 滤 4/38 |
| **模型自评过滤** | prompt 要求无信息图返回 `无有效信息`，M1 侧丢弃 caption（滤大尺寸 logo/装饰图，**零额外成本**，见 §2.3） |
| **按 img_path 去重** | MinerU 以 sha256 命名，同图同路径；重复引用只调一次 |
| **落盘缓存** | `<doc_dir>/image_captions.json`，已有且无 `error` 的 key 重跑不重复调用；失败的下次重试 |
| **开关** | `VISION_ENABLED=false` 一键关闭，完全退化为纯文本链路 |

**实测成本**：54 张图（阈值过滤前的全量）累计 **16,782 tokens**、5 并发 **10.0s** ⇒ 约 **311 tokens/图、0.19s/图**。
按有效图 ~42 张/2 份 ≈ 21 张/份 × 25 份 ≈ **525 次调用 ≈ 16 万 tokens**（`deepseek-flash` 单价极低，成本可忽略）。

## 6. 验证方法

| 步骤 | 检查项 | 实施实测（2026-09-29） |
|---|---|---|
| **1. M1 单元** | 对两份样本跑 `python -m app.m1_parse.run -s backend/corpus -o /tmp/m1_mm`：drawing 块 content **非空**、`img_path` **有值**；`image_captions.json` 生成；小图标**不在**其中 | ✅ 铭昇 15 drawing / content 非空 8 / img_path 15；融柏 35 / 30 / 35；`image_captions.json` 落盘；面积阈值滤 6 与 4 张 |
| **2. M2 单元** | `python -m app.m2_chunk.runner -s /tmp/m1_mm -o /tmp/m2_mm`：jsonl 含 `block_type=drawing` 且 content 非空的 TextUnit；`image_path` 字段存在；`chunk_order_index` 落在原位 | ✅ 铭昇 45 units / 9 drawing；融柏 123 / 27；`image_path` 全带、`title_path` 上下文正确。注：`chunk_order_index` 有缺口是 M2 **既有行为**（末尾 `[u for u in out if u["content"].strip()]` 丢弃空 content 块），与本次改动无关 |
| **3. 降级** | `VISION_ENABLED=false` 重跑 M1+M2：**不因视觉产生块**（`drawing` 块只可能来自过滤后的 MinerU 真图注） | ✅ 铭昇 **0** drawing；融柏 **4**（正是 §2.2 的 3 条真图注 + p22 footnote，零纯图号垃圾）。铭昇 29 units、融柏 83 units |
| **4. M3/M5 零改动** | 按 M3_index §3.5 重建链建**临时 workspace**（遵守探底纪律第 4 条，不污染标准库）：PG `lightrag_doc_chunks` 含图片块；`m5_sparse.json` 含图片块 | ✅ 已实测（2026-10-01，临时库 `mm_verify_ws`，用后即收）：M2 38 drawing → PG 38（24 按序号命中 + 14 按 content 命中）→ sparse 38 条 `block_type=drawing`，**M1/M2/M3/M5 零改动**。验证中另发现并修复重建链一个**既有**对齐缺陷（与多模态无关，见 CHANGELOG v5.25.1） |
| **5. 端到端** | 上传一份 PDF → preview 接口返回 `image_url` → 浏览器预览面板显示图片；检索「XX 图标含义」类问题能召回对应块 | ✅ **API 级已验证**（2026-10-01）：用 v5.25 遗留真实产物（铭昇 H2-5000IBP，doc `719a920e62722e29`，8 drawing 块 / 16 图）灌入临时库 `col_d2fb1ad8`，`GET /docs/{doc}/preview` → 43 units、8 drawing、8 带 `image_url`；图片路由 200 + `image/jpeg` + 穿越 404；M6 单测 `Citation.image_path` 正确填充。`npx tsc -b` 与 `npm run build` 通过。⚠️ **浏览器目视未完成** —— camofox 浏览器启动依赖外部 geoip 公网 IP 查询，当前出口不可用（与代码无关），M8 仅到「类型检查 + 构建 + 后端契约」级证据 |

> 降级验收口径的修正：初稿写「与现状逐字节一致」，实施时发现 MinerU 图注**并非全空**（§2.2），
> 且视觉调用失败会让块退化为「图 13」——故口径改为 **「不因视觉产生块」**：关闭视觉后，
> `drawing` 块的唯一来源是剥掉图号后仍有 ≥6 字正文的 MinerU 真图注。

## 7. 不做的事 / 已知边界

- **不做图片视觉问答**（VQA）：生成侧仍为纯文本，图片只以描述文本参与。
- **不覆盖 Docling 链路**：第一版只做 MinerU（PDF/office）。docling 的 `picture` 块后续按同一模式扩展。
- **不做图片去重（跨文档）**：同一厂商多份说明书可能有相同截图，第一版不去重（成本可控）。
- **不改 M3/M5 任何代码**：这是本方案的核心约束（透传式接入）。
- **不做扫描件 OCR 增强**：MinerU pipeline 已含 OCR，本方案不重复。

## 8. 待用户提供

无。视觉通路已用现有 `.env` 凭据实测通过（§2.3），可直接进入实施。

## 9. Changelog

- **2026-10-01 · v0.3.2（M6/M7/M8 展示层已实施）**：按 §4.5–4.7 落地展示层，多模态链路 M0→M8 全线打通。
  - **M6**：`sidecar.py` `ChunkMeta` 加 `image_path`；**方案外补充** `cite.py` `Citation.image_path` + `to_dict()`（§4.7 缩略图必需）。
  - **M7**：`preview_doc()` 返回 unit 加 `image_url`；新增 `GET /docs/{doc_id}/images/{name}` 静态路由（注册在 StaticFiles catch-all 之前），doc_id/name 白名单 + resolve 归属校验防穿越。
  - **M8**：`DocumentPreview` drawing 分支 + lightbox；`CitationPanel` 缩略图；**方案外补充** `lib/api.ts` 新增 `apiUrl()`（后端返回裸路径，前端补 `API_BASE`/`collection_id`）；`App.css` 四组样式；`mocks/events.ts` 补字段。
  - **验证**：临时库 `col_d2fb1ad8`（真实遗留产物，零 API 成本）走通 M6/M7；穿越三类 404；tsc + build 通过。浏览器目视因 camofox 外部 geoip 依赖不可用而未做（见 §6 步骤 5 注）。登记 CHANGELOG **v5.25.2**。
- **2026-10-01 · v0.3.1（M3/M5 零改动已验证）**：按 §6 步骤 4 建临时 workspace（`mm_verify_ws`，用后即收）跑重建链，**核心约束「M3/M5 零改动」经实测成立** —— M2 38 drawing 块全部进 PG 与 `m5_sparse.json`，M1/M2/M3/M5 零改动。验证中另暴露重建链一个**既有**对齐缺陷（LightRAG 同文档 content 去重 → `block_type` 按序号关联错位 34/171），已修（M5 `sparse_index.build()` 改按 content 关联、`_align_check` 改比 content 集合），登记 CHANGELOG **v5.25.1**，坑点记录 [`pitfalls/lightrag-chunk-id-dedup.md`](../pitfalls/lightrag-chunk-id-dedup.md)。**与多模态无关**，§4.4 加边界说明。
- **2026-09-29 · v0.3（M0/M1/M2 已实施）**：按 v0.2 方案落地前三段。
  - **M0**：`parse.md` §3 扩展字段加 `img_path`；`textunit.schema.json` / `textunit.md` 加 `image_path`。
  - **M1**：新增 `vision.py`（同步 urllib、面积兜底、按 img_path 去重、落盘缓存、5 并发）；
    `blocks_builder.py` image 分支独立并对 MinerU 图注做可用性过滤（`_usable_mineru_caption`）；
    `mineru_adapter.py` 调 `vision.caption_images`。
  - **M2**：`chunker.py` 新增 `drawing` 分支 + `_build_image_unit`，图片独立成块。
  - **实施修正 3 处**（与 v0.2 方案稿的差异）：
    1. **面积口径**：`VISION_MIN_AREA` 作用于**图片像素面积**（PIL 读文件头），不是 MinerU bbox 面积
       —— bbox 是归一化 0–1000 坐标，其面积在阈值 10000 下滤 7/15 与 5/35，与 §2.3 尺寸形态对不上；
       像素口径滤 6/15 与 4/35，吻合。
    2. **MinerU 图注并非全空**（§2.2 初稿断言「50 个条目无一有值」有误）：融柏 23/35 有值，
       但 19 条是纯图号噪声 ⇒ 新增 `_usable_mineru_caption` 过滤（剥掉「图 N」后剩余正文 ≥6 字才保留）。
    3. **降级验收口径**：由「与现状逐字节一致」改为「**不因视觉产生块**」（原因见 §6 末注）。
  - **实测**：视觉 OFF → 铭昇 0 drawing、融柏 4（零垃圾）；视觉 ON → 铭昇 9、融柏 27。见 §6。
- **2026-09-29 · v0.2**：视觉模型由「方舟网关别名」改为 **DeepSeek 官方 `deepseek-flash`**（用户决策：方舟套餐只支持 agent 应用，自开发项目只能用原生 API）。据此补入实测：`/v1/models` 模型清单、54 张图全量视觉调用（16,782 tokens / 10.0s）、描述质量样本；新增「模型自评过滤」手段（面积不足以判定大尺寸装饰图，见 §2.3）；配置节删去 ARK 凭据、改为复用已有 `DEEPSEEK_API_KEY`；§8「待用户提供」清空。
- **2026-09-29 · v0.1**：初稿。基于两份真实器械说明书全链路代码探查（M1/M2/M3/M5/M6/M7 逐层定位图片丢弃点）+ memory `graphrag-multimodal` 既定方向（图片独立成块、M3/M5 零改动）。方案未实施。
