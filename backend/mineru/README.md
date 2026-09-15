# mineru/ —— MinerU 源码拆解放置区（本项目自用）

承载实施路线**步骤 ②「拆解 MinerU」**的产物，与代码实现解耦。

| 路径 | 内容 | 说明 |
|---|---|---|
| `source/` | MinerU 源码（clone：github.com/opendatalab/MinerU） | tag `mineru-3.4.5`；不随主仓库提交（gitignore） |
| `docs/` | 拆解记录（重点：数据流 + IO 接口规范） | `mineru.md` 待子代理产出 |

**拆解目的**（对齐架构 §6-②）：作为 M1 解析层主力解析器（PDF/扫描件 → 统一 Markdown），梳理**数据流**（输入 → 版面/OCR → 中间 blocks → 输出，页码/表格/公式/标题如何携带）与**输入输出接口规范**（CLI/API/产物清单），并给出与 LightRAG blocks.jsonl + `docs/contracts/textunit.md` 的对接点（在哪里补 page_label/block_type）。

拆解结论将作为 M1（mineru_adapter + blocks 扩展字段）的实现依据。