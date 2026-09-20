# 坑：DeepSeek v4-flash 思考模式导致 content 为空

> **分类：** LLM
> **严重度：** 高（会导致整个索引/生成链路卡死或失败）
> **首次踩坑：** 2026-09-13（M3 索引层 DeepSeek 定案时）
> **涉及模块：** M3 索引层 / M6 生成层 / M7 交互层

---

## 症状

DeepSeek v4-flash 调用反复失败或重试卡死，响应的 `content` 字段为空。

尤其容易出现在：
- `response_format=json_object` 的结构化抽取（M3 图谱抽取）
- 长文本生成（重试次数多，每次都烧在思考上）

## 根因

DeepSeek v4-flash 是**推理模型**，默认开启思考模式，`reasoning_tokens` 占预算 75–100%。

极端情况（`response_format=json_object`）下，`max_tokens` 全部烧在 reasoning 上，content 为零字节 → JSON 解析失败 → 触发重试 → 继续占满 → 无限循环。

## 修复方式

**关闭思考模式**，传 `{"thinking": {"type": "disabled"}}`。

注意：**OpenAI Python SDK 不接受 `thinking` 直接参数**，必须经 `extra_body` 透传：

```python
# 正确
extra_kwargs = {"extra_body": {"thinking": {"type": "disabled"}}}

# 错误（会报 AsyncCompletions.create() got unexpected keyword argument 'thinking'）
# thinking = {"type": "disabled"}
```

## 排除过的无效方案

| 方案 | 效果 |
|---|---|
| `reasoning_effort: "low"` | ❌ 仍占约 64% reasoning token |
| `reasoning: {effort: "none"}` | ❌ 未真正关闭 |
| `thinking: {type: "disabled"}` 直传 | ❌ SDK 不认参数名 |

依据：DeepSeek 思考模式文档（api-docs.deepseek.com/zh-cn/guides/thinking_mode）。

## 项目中的使用位置

- `app/m3_index/providers.py` — DeepSeek 分支的 `extra_kwargs`
- `app/m6_generate/generate.py` — 复用 providers 的 flash 配置

**新增 LLM 调用点时务必复用同一 providers 配置**，不要自写 LLM client，避免遗漏。

## 验证

调用后检查响应中 `reasoning_content` 字段是否存在：
- 关闭成功：字段不存在或为空，全部 token 归 content
- 未关闭：有 reasoning_content，content 明显短
