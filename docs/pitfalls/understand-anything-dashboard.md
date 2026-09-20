# 坑：Understand-Anything 生成的图谱 schema 错位导致 dashboard 空白

> **分类：** 工具（Understand-Anything 图谱分析）
> **严重度：** 中（结构视图/导览完全空白，但数据无损可修）
> **首次整理：** 2026-09-20（UA 2.9.7）
> **涉及模块：** —（第三方工具，非 M 模块代码）

UA `/understand` 生成的 `.ua/knowledge-graph.json` 与 dashboard 消费端 schema 系统性错位：
生成端输出的字段结构，不符合 dashboard `@understand-anything/core` 的 `LayerSchema` / `TourStepSchema` 校验要求，
导致层与导览全部被校验丢弃 → dashboard「结构」视图与导览空白，同时校验面板报 `[Dropped]`。**领域视图不依赖 layers，故正常，容易误判为"只有 5 个节点"的正常俯视图。**

---

## 坑 1：layers 缺 nodeIds → 结构视图空白

**现象：** dashboard 顶栏切「结构」视图空白；「领域」视图只显示 5 个业务领域（此为设计，非 bug）。校验面板无报错（*或仅表现为层被静默 drop*）。

**根因：** 生成的 `layers[]` 每条只有 `{id, name, description, order}`，**缺 `nodeIds`**。`LayerSchema`（schema.ts）要求 `nodeIds: z.array(z.string())` 必填，校验时 8 层全被 `dropped → removed`。dashboard 拿到空 layers → `GraphView` `if (layers.length === 0) return null` → 空白。中间产物（`intermediate/layered-graph.json`、`toured-graph.json`）同样缺该字段，说明生成环节就没写。

**应对：** nodeIds 可**无损重建**——每个 node 有 `layer` 字段（如 `layer:frontend-presentation`），按层聚合 node.id 即可：

```python
import json
d = json.load(open(".ua/knowledge-graph.json"))
by_layer = {}
for n in d["nodes"]:
    by_layer.setdefault(n.get("layer"), []).append(n["id"])
for l in d["layers"]:
    l["nodeIds"] = by_layer.get(l["id"], [])
json.dump(d, open(".ua/knowledge-graph.json", "w"), ensure_ascii=False, indent=2)
```

312 节点实测 8 层 nodeIds 合计 313、去重 313、无游离。

---

## 坑 2：tour 字段名漂移 → 导览全部丢弃

**现象：** 校验报 `tour[N]: Invalid input: expected number, received undefined — removed`，10 步导览全被丢。

**根因：** 生成端输出 tour 步骤为 `{id, title, summary, focusNodes}`，但 `TourStepSchema` 要求 `{order, title, description, nodeIds}`。缺 `order`（number）即失败；即便补上，`summary`/`focusNodes` 也不是消费端字段名。

**应对：** 纯字段映射，内容无损：`order`=数组下标、`description`=summary、`nodeIds`=focusNodes（过滤 dangling）：

```python
tour = [{ "order": i, "title": t["title"], "description": t["summary"],
          "nodeIds": [n for n in t.get("focusNodes", []) if n in node_ids] }
        for i, t in enumerate(d["tour"])]
```

---

## 注意：重跑会复发

**`/understand` 重跑或新会话 auto-update（SessionStart hook）重新生成图谱时，坏字段会原样再现**（生成器行为未变），需按上法重补。UA 发版修复前此为持久坑。修复只动 `.ua/knowledge-graph.json` 数据，不影响项目代码；dashboard 刷新（Cmd+Shift+R）即生效，原文件备份在 `/tmp`。