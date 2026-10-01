# Pitfalls：踩坑记录

坑点集中存放地。模块文档的「已知坑 / 注意事项」小节只留一句话索引 + 链接，详细记录在这里。

## 分类索引

| 分类 | 文档 | 核心内容 | 涉及模块 |
|---|---|---|---|
| LLM | [`deepseek-thinking-mode.md`](deepseek-thinking-mode.md) | DeepSeek v4-flash 思考模式导致 content 为空，必须 `extra_body` 关闭 | M3 / M6 |
| 存储 | [`postgres-storage-pitfalls.md`](postgres-storage-pitfalls.md) | PG 存储层 7 个坑：向量表后缀 / HNSW 删改 / 单写者 / callback 异常 / 边规范化 等 | M4 / M5 |
| Embedding | [`bge-m3-xinference.md`](bge-m3-xinference.md) | bge-m3 在 Xinference 上的配置坑：return_sparse 启动参数 / sparse 索引构建 / reranker 接入 | M3 / M5 |
| 工具 | [`understand-anything-dashboard.md`](understand-anything-dashboard.md) | UA 生成的图谱 schema 错位：layers 缺 nodeIds / tour 字段漂移 → dashboard 结构视图与导览空白（数据无损可修） | —（第三方工具） |
| 框架 | [`lightrag-chunk-id-dedup.md`](lightrag-chunk-id-dedup.md) | LightRAG chunk id = hash(doc, content)，**同文档**重复块被静默丢弃 → PG `chunk_order_index` 整体前移，按序号与 M2 对齐错位（改按 content 对齐） | M3 / M5 |

## 新增 pitfall 的流程

1. 确认是**踩过的坑**（不是推测的风险），且有症状 + 根因 + 修复方式
2. 按主题归类，新建 `docs/pitfalls/<topic>.md`
3. 关联的模块文档「已知坑」小节加一句话 + 链接
4. 在这里的索引表登记

## 与其他文档的边界

- **模块文档**：只放当前模块的状态、架构、验收标准。坑点只留索引。
- **CHANGELOG**：记录"什么时候修了什么"，不记录坑的细节。
- **Pitfalls**：记录"坑是什么、为什么会踩、怎么修"，跨时间有效。
