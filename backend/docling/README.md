# docling/ —— Docling 源码拆解放置区（本项目自用）

承载实施路线**步骤 ②「拆解 Docling」**的产物，与代码实现解耦。

| 路径 | 内容 | 说明 |
|---|---|---|
| `source/` | Docling 源码（clone：github.com/docling-project/docling） | tag 待锁定；不随主仓库提交（gitignore） |
| `docs/` | 拆解记录（重点：数据流 + IO 接口规范） | `docling.md` 待子代理产出 |

**拆解目的**（对齐架构 §6-②）：作为 M1 解析层的**多格式兜底**（DOCX/PPTX/XLSX/HTML → 统一 Markdown），梳理**数据流**（输入 → pipeline 分片/表格/OCR → `DoclingDocument` → 导出）与**输入输出接口规范**（CLI/Python/导出格式），重点核实 **docx 的 paraid（w:paraId）如何给出**（docx 无稳定页码是格式本质，TextUnit 契约对此用 `anchor: paraid` 兜底），并给出与 LightRAG blocks.jsonl + `docs/contracts/textunit.md` 的对接点。

拆解结论将作为 M1（docling_adapter + blocks 扩展字段）的实现依据。