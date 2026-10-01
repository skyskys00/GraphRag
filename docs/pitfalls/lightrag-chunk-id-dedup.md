# LightRAG chunk 同文档 content 去重 → 按序号对齐错位

> 分类：框架行为 ｜ 涉及模块：M3 / M5 ｜ 首次踩坑：2026-10-01（多模态 M3/M5 零改动验证）

## 症状

1. **重建链对齐校验报错**（`scripts/rebuild_standard_lib.py --execute` 末尾）：
   ```
   [FAIL] 对齐失败：PG=171  chunks_jsonl=172  sparse=171
   ```
   M2 产出 172 个 unit，PG 只落了 171 行 —— 差 1，且不是随机差，而是**某个文档整体少 1**。
2. **下游元数据错配**：M5 `m5_sparse.json` 的 `block_type` 大面积标错（实测 171 块里 **34 块**错配，
   表现为 drawing↔paragraph、table↔paragraph 成对互换）。检索**内容**不丢，但 `table_summary.py`
   按 `block_type == "table"` 决定是否做表格 NL 摘要 ⇒ 表格块丢失摘要处理、普通段落被误当表格。

## 根因

LightRAG `ainsert_custom_chunks`（`lightrag/lightrag.py`）为每个 chunk 生成 id：

```python
chunk_id = make_custom_chunk_id(doc_key, chunk_text)   # = hash(doc_id, content)
```

并在**单次调用内**用 `seen_chunk_ids` 去重 ⇒ **同一文档内 content 完全相同的块被静默丢弃**
（跨文档不去重，因为 `doc_key` 不同，hash 也不同）。

由此产生两个连锁后果：

1. **PG 行数 < M2 unit 数**。LightRAG 落 PG 时写入的 `chunk_order_index` 是**去重后列表里的位置**，
   不是 M2 的原始序号。一旦某文档发生去重，该文档**从被丢块的位置起，后续所有 chunk 的
   `chunk_order_index` 整体前移 1**。
2. **按序号对齐必错位**。任何「用 `(full_doc_id, chunk_order_index)` 把 M2 元数据贴到 PG 行上」的逻辑，
   在发生去重的文档里从偏移点开始全部错位（本例：融柏说明书从 M2 idx 69 起，后面 34 块全错）。

**触发条件**：文档内存在重复块。实测样本 —— 融柏 LSP-1C 说明书页眉「保定融柏恒流泵制造有限公司」
在 blocks.jsonl 出现 17 次（anchor `97:70:381:91`，页 1/3/5/7/9/11/13/15/17/19），其中 2 个（M2 idx 56、69）
成为独立的同内容 paragraph 块 ⇒ 第二个被丢弃 ⇒ PG 127→126。

> ⚠️ 只去重**同一文档内**的重复。跨文档的相同内容（如多份文档都有的「第一章 总则」）**不会**被去重
> —— `eval_admin_ws` 有 4 条跨文档重复，PG 行数与 M2 unit 数完全一致，不受影响。
> 初判时曾误以为「任何重复都会塌缩」，被 `eval_admin_ws` 证伪，正确口径是「同文档内」。

## 修复

**对齐键从序号改为 content**（content 是 LightRAG 生成 id 的输入，按它对齐必命中）：

- `app/m5_retrieve/sparse_index.py::build()` —— 从 M2 jsonl 建 `(full_doc_id, content)` → 元数据表，
  查 PG 行时用 content 而非 `chunk_order_index`。
- M2 侧的 content 必须过 `sanitize_text_for_encoding`（LightRAG 落 PG 前对 chunk 做过
  `strip + html.unescape + 去控制字符`），否则两边 content 差几个字符对不上 —— 见
  `sparse_index.content_key()`。

**对齐校验改为比 content 集合而非计数**（`scripts/rebuild_standard_lib.py::_align_check`）：
去重只丢重复副本、不丢任何唯一内容，故 `set(M2 的 (doc, content)) == set(PG 的 (doc, content))`
才是真正的不变量；计数比对会把「合法的去重」误报为失败。

## 影响面与验证

- **检索内容零损失**：被丢的是重复副本，唯一内容全在。
- **仅元数据标注受影响**：`block_type` 是唯一实际消费者（`table_summary.py`）。
- **现有标准库不受影响**：`default_ws`(34) / `eval_cservice_ws`(99) / `eval_admin_ws`(203)
  三库**同文档重复内容组均为 0** ⇒ M2 序号 == PG 序号 ⇒ 新旧 join 结果逐块一致，历史评测结论有效。
- **验证口径**（2026-10-01，临时库 `mm_verify_ws`，用后即收）：旧序号 join 错配 **34** →
  新 content join 错配 **0**；38 个 drawing 块全部命中正确 `block_type`。

## 相关

- 重建链与对齐校验：`docs/modules/M3_index.md` §3.5
- M5 sparse 构建：`docs/modules/M5_retrieve.md` §2.1 / §5
- 版本登记：`docs/CHANGELOG.md` v5.25.1
