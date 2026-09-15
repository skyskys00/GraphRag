# LightRAG v1.5.7 LLM 接入拆解

> 拆解对象：`lightrag/source/lightrag/`（下文中所有「相对路径」均指该目录）。
> 面向：GraphRAG 重做项目 M0（`llm_client` 封装）。目标 = 复用内核 + 用 DeepSeek（OpenAI 兼容）接全链路。
> 版本基准：v1.5.7 源码 SNAPSHOT（`__version__ = "1.5.7"`，`lightrag/_version.py:9`）。
> 四个核心均自闭门供参考认知：**v1.5.7 没有 `create_llm_client` 这个函数**，正确姿势是传 `llm_model_func` 回调（详见 §1 / §3.2 / §7）。
> 补充问答（API/.env 含义、前端引用展示、GLM 免费档）：见 [qa-notes.md](qa-notes.md#D)。

---

## 1. 定位与职责

| 模块 | 位置（行号） | 职责 |
|---|---|---|
| `llm/openai.py`（1397 行） | `llm/openai.py` | **OpenAI 兼容绑定的唯一真源**：`openai_complete_if_cache`（305 行）是事实上的通用 OpenAI 兼容 completion 实现（DeepSeek / vLLM / 各种网关都走它），`create_openai_async_client`（177 行）管 client 构建（`api_key` / `base_url` / 超时 / 代理），`openai_embed`（1054 行）管 embedding，`openai_complete`（937 行）/ `gpt_4o_mini_complete`（985 行）/ `gpt_4o_complete`（961 行）是「把 model 名字写死在闭包里」的快捷回调。该模块顶部 `load_dotenv(".env")`（77 行），可读环境变量 |
| `llm/binding_options.py`（954 行） | `llm/binding_options.py` | 各 provider 的 **provider options 容器**（dataclass）：`OpenAILLMOptions`（719 行）等。字段 = 抽样参数（temperature / top_p / max_tokens / frequency_penalty…）+ `extra_body` 透传。只负责「配置到 dict」，不负责调用。API server 用它生成 CLI 参数、环境变量、role 级覆盖 |
| `llm/azure_openai.py` / `gemini.py` / `ollama.py` / `bedrock.py` / … | `llm/*.py` | 其他 provider 的绑定，均实现 **同一条** `model_if_cache(prompt, system_prompt, history_messages, **kwargs) -> str` 回调协议 |
| `llm_roles.py`（586 行） | `llm_roles.py` | **分环节模型的核心**：`ROLES` 注册表（52 行，`extract` / `keyword` / `query` / `vlm` 四个 role）、`RoleLLMConfig`（62 行）、`_RoleLLMMixin`（93 行）负责把 base `llm_model_func` 按 role 包一层独立并发队列 + 注入 `hashing_kv`（LLM 缓存句柄）与 role kwargs。还提供运行时热更新（`update_llm_role_config` 360 / `aupdate_llm_role_config` 395）、密钥脱敏快照（`get_llm_role_config` 484） |
| `lightrag.py`（6953 行） | `lightrag.py` | `LightRAG` 主类：`llm_model_func` / `role_llm_configs` / `llm_model_kwargs` / `enable_llm_cache` 等字段定义在 **dataclass 字段区**（§3.4 有参数表），`__post_init__`（1506–1583 行）构建 role 状态并包装队列，`llm_response_cache`（1430 行）实例化缓存存储，`_build_global_config`（1200 行）把 role llm funcs + 缓存身份打包成 dict 下传给 operate/pipeline |
| `operate.py`（6889 行） | `operate.py` | **各环节对 LLM 的调用点**：抽取（4181 行）、gleaning 补抽（4256）、描述摘要（611）、关键词抽取（5047）、查询回答 / 查询缓存（4766–4818 / 6860）、naive 查询（6652）。全部经 role wrapper 调用 |
| `utils.py`（7533 行） | `utils.py` | LLM 缓存读写原语：`handle_cache`（4145）/ `save_to_cache`（4191）/ `use_llm_func_with_cache`（5155、抽取/摘要专用缓存调用壳）、键构成 `compute_args_hash`（773）/ `generate_cache_key`（962）/ `get_llm_cache_identity`（840）/ `serialize_llm_cache_identity`（865）、并发队列 `priority_limit_async_func_call`（1052） |
| `kg/json_kv_impl.py` | `kg/json_kv_impl.py` | 默认 KV 后端。缓存落盘文件名由 `namespace` 推导：`working_dir/[workspace/]kv_store_<namespace>.json`（152 行）→ 默认即 **`kv_store_llm_response_cache.json`**（namespace 常量见 `namespace.py:10`） |
| `api/config.py` / `api/lightrag_server.py` | `api/config.py:757-804` / `api/lightrag_server.py:1951,1980,2098` | **API server 侧的绑定工厂**：从 `LLM_BINDING` / `LLM_MODEL` / `LLM_BINDING_HOST` / `LLM_BINDING_API_KEY` + `{ROLE}_LLM_*` role 环境变量解析出每个 role 的独立函数与 metatda（binding/model/host/api_key），拼出 `role_llm_configs` 传给 `LightRAG`。**页面/API 部署才走这条，程序内嵌用不到** |

调用关系一句话：**任何环节要调 LLM，都从 `global_config["role_llm_funcs"][role]` 取一个带队列的包装函数；包装函数内部是 `partial(某 binding 的 *_if_cache 函数, hashing_kv=缓存KV, **model_kwargs)`。缓存由两层接力：`use_llm_func_with_cache`（抽取/摘要）或 operate 手写缓存读写（关键词/查询回答）。**

---

## 2. 关键文件/函数索引

### 2.1 回调协议与 OpenAI 兼容绑定（`llm/openai.py`）
| 项 | 位置 | 说明 |
|---|---|---|
| `openai_complete_if_cache` | `llm/openai.py:305` | 通用 OpenAI 兼容 completion。参数：`model`（位置参数）=模型名；`prompt`、`system_prompt`、`history_messages`、`enable_cot`、`base_url`、`api_key`、`stream`、`timeout`、`image_inputs` + **`**kwargs**`（透传给 `client.chat.completions.create`，如 `max_tokens` / `temperature` / `response_format` / `stop` / `extra_body`）。重试在函数上（§3.5） |
| `create_openai_async_client` | `llm/openai.py:177` | client 工厂。**参数名就是 `base_url` / `api_key`**（不是 host）；Azure 走 `azure_endpoint` / `azure_deployment` 分支。`client_configs` 可塞代理/自定义头；`max_retries` 默认置 0（重试全部上交 tenacity） |
| `openai_embed` | `llm/openai.py:1054` | embedding。`@wrap_embedding_func_with_attrs(embedding_dim=1536, max_token_size=8192, …)` 包装后成为 `EmbeddingFunc`，返回 `np.array`。**与我们无关（我们用 Xinference），但注意「换 embedding 模型必须清数据目录」（见 §6）** |
| `gpt_4o_mini_complete` / `openai_complete` | `llm/openai.py:985 / 937` | 官方 README 示例用的快捷闭包：`openai_complete` 从 `hashing_kv.global_config["llm_model_name"]` 取模型名（949 行），`gpt_4o_mini_complete` 硬编码 `"gpt-4o-mini"` |
| `azure_openai_complete_if_cache` | `llm/openai.py:1216` | 只是 `use_azure=True` 的包装（`azure_openai.py:1-22` 近乎空壳），DeepSeek 不需要 |

### 2.2 role 机制（`llm_roles.py`）
| 项 | 位置 | 说明 |
|---|---|---|
| `ROLES` | `llm_roles.py:52-57` | 四个 role：`extract` / `keyword` / `query` / `vlm`；每个带 `env_prefix`（`EXTRACT` / `KEYWORD` / `QUERY` / `VLM`）。**所有角色级配置循环都遍历它，加新 role 只改这一处** |
| `RoleLLMConfig` | `llm_roles.py:62-79` | 每个 role 的可选覆盖：`func` / `kwargs` / `max_async` / `timeout` / `metadata`，任何字段 `None` 就回落 base 设置 |
| `_wrap_llm_role_func` | `llm_roles.py:180-200` | 核心包装：`priority_limit_async_func_call(max_async)`( `partial(raw_func, hashing_kv=self.llm_response_cache, **effective_kwargs)` )。**`hashing_kv` 从这里注入**，raw func 自己 `kwargs.pop("hashing_kv")`（openai.py:414） |
| `_get_effective_role_*` | `llm_roles.py:162-178` | 有效值回滚：role 有 `kwargs` 用 role 的，没有则用 base `llm_model_kwargs`；cross-provider 时返回 `{}` |
| `_rebuild_role_llm_funcs` | `llm_roles.py:202-210` | base `llm_model_func` **故意不包并发队列**——并发全在 role 层做 |
| `update_llm_role_config` / `aupdate_llm_role_config` | `llm_roles.py:360 / 395` | 运行时热切换某 role 的模型/绑定/超时；`aupdate` 异步等旧队列排空（有上限 = `timeout*2+15` 秒） |
| `register_role_llm_builder` | `llm_roles.py:130` | 注册「由 metadata 重建 role func/kwargs」的 builder（API server 用，程序内嵌不强制） |

### 2.3 各环节调用点（`operate.py`）
| 环节 | 位置 | 用的 role | 缓存类型 |
|---|---|---|---|
| 实体/关系抽取（首次） | `operate.py:4181` | `extract` | `use_llm_func_with_cache(..., cache_type="extract")` |
| 抽取 gleaning（补抽） | `operate.py:4256` | `extract` | 同上（带 `history_messages`） |
| 描述 map-reduce 摘要 | `operate.py:611` | `extract` | `cache_type="summary"` |
| 关键词抽取 | `operate.py:5047` | `keyword` | 手写 `handle_cache`/`save_to_cache`（`cache_type="keywords"`） |
| 查询回答（kg，含 local/global/hybrid/mix 生成） | `operate.py:4778` | `query` | 手写缓存读写（`cache_type="query"`，带 query 参数化键） |
| 查询回答（naive 向量生成） | `operate.py:6860` | `query` | 同上 |
| 多模态图像分析 | `pipeline.py:6576` | `vlm` | `analyze`（与我们无关） |

---

## 3. 核心机制

### 3.1 调用链总览

```
LightRAG(llm_model_func=回调, role_llm_configs=…, llm_model_kwargs=…)
  └ __post_init__ (lightrag.py:1506-1583)
      └ 对 ROLES 每个 role：raw_func = role.func or base_llm_func
             (lightrag.py:1576)
      └ _rebuild_role_llm_funcs → 每 role 一个 priority_limit 队列包装
  └ 注入 global_config (lightrag.py:1200-1235): role_llm_funcs / llm_cache_identities / tokenizer / …
  └ operate.py 各调用点:
      role_func = global_config["role_llm_funcs"]["extract"|"keyword"|"query"]
      → (role queue, 绑 hashing_kv + role kwargs)
      → 内层 = openai_complete_if_cache(model, prompt, system_prompt, history_messages,
             base_url, api_key, **kwargs)
      → create_openai_async_client → client.chat.completions.create(model, messages, …)
```

**关键事实**：rollup 的函数协议是 `async def f(prompt, system_prompt=None, history_messages=None, **kwargs) -> str | AsyncIterator[str]`。`model` / `base_url` / `api_key` 必须在 closures 或 `llm_model_kwargs` 里预先绑定好——**「登录、base_url、模型名都与调用点解耦」**，这正是 M0 封装 `llm_client` 能整车覆写的接口面。

### 3.2 llm_model_func 注入与分环节模型（Q1 / Q2 的答案）

**Q1：怎么接一个 OpenAI 兼容 provider（DeepSeek）？**

- **v1.5.7 没有 `create_llm_client`**（全源码 grep 无此符号）。替代词：`create_openai_async_client`（只建 SDK client 对象，不建函数）、API server 里的 `create_llm_model_func`（要给 `--llm-binding` 参数才走）。程序内嵌/自研的正确写法就是**自己写一个 async 回调，传给 `LightRAG(llm_model_func=…)`**。
- 官方内置的三个快捷回调（`gpt_4o_mini_complete` 等）就是把 model 名写死在闭包里的 17 行函数（`:985-1006`）——**照它的形状写你就是对的**：

```python
from lightrag import LightRAG
from lightrag.llm.openai import openai_complete_if_cache

async def deepseek_complete(
    prompt, system_prompt=None, history_messages=None, **kwargs
) -> str:
    # model / base_url / api_key 绑定在这一层；kwargs 透传 max_tokens、
    # temperature、response_format、stream 等
    return await openai_complete_if_cache(
        "deepseek-v4-pro",                 # 模型名（位置参数）
        prompt,
        system_prompt=system_prompt,
        history_messages=history_messages,
        base_url="https://api.deepseek.com/v1",  # 参数名就是 base_url
        api_key=os.getenv("DEEPSEEK_API_KEY"),
        **kwargs,
    )

rag = LightRAG(
    working_dir="./rag_storage",
    llm_model_func=deepseek_complete,
    llm_model_name="deepseek-v4-pro",   # 用于缓存身份兜底（见 §3.3）
    llm_model_kwargs={"temperature": 0.1, "max_tokens": 8000},  # 可选，全局采样参数
    embedding_func=…,                    # 必填，v1.5.7 无默认
)
await rag.initialize_storages()          # 永远别漏
```

  - 参数名对齐：`base_url`（不是 `host`）、`api_key`、`model` → 这是 OpenAI SDK 的拼写，DeepSeek 是 OpenAI 兼容因此同款。
  - 不想自己写闭包？直接复用 `openai_complete_if_cache` 也可以，但**不能把它原样当 `llm_model_func` 传**——LightRAG 只按 `(prompt, system_prompt=…, history_messages=…, **kwargs)` 调用它，不会给 `model`（它是必填位置参数），所以必须外层闭包补 `model`。写闭包本身就是最小正确解。

**Q2：抽取与生成是否必须同一个 func？否，v1.5.7 原生支持分环节模型。**

- `ROLES` 拆了四个 role，**每个 role 独立** raw_func、kwargs、并发上限、超时、以及缓存身份里的 `model`（`llm_cache_identities`，见 §3.3）。role 没配 `func` 就回落 base `llm_model_func`（lightrag.py:1576）。
- 两种配置途径：
  1. **编程式**（我们 M0 要走的路）：`LightRAG(role_llm_configs={...})`，见 `lightrag.py:696`。示例：

```python
from lightrag.llm_roles import RoleLLMConfig

async def deepseek_flash(prompt, system_prompt=None, history_messages=None, **kwargs): …
async def deepseek_pro(prompt,  system_prompt=None, history_messages=None, **kwargs): …

rag = LightRAG(
    llm_model_func=deepseek_pro,          # base = 查询/生成
    llm_model_name="deepseek-v4-pro",
    role_llm_configs={
        "extract": RoleLLMConfig(
            func=deepseek_flash,          # 抽取用 flash（便宜、够快）
            metadata={"binding": "openai", "model": "deepseek-v4-flash",
                      "host": "https://api.deepseek.com/v1", "is_cross_provider": False},
        ),
        "keyword": RoleLLMConfig(func=deepseek_flash, metadata={…}),
        # query 不写 → 用 base deepseek_pro；vlm 不需要就不配
    },
)
```

  2. **API server / .env（WebUI 部署才需要）**：base 变量 `LLM_BINDING=openai` `LLM_MODEL=…` `LLM_BINDING_HOST=…` `LLM_BINDING_API_KEY=…`，role 级变量 `EXTRACT_LLM_BINDING=openai` `EXTRACT_LLM_MODEL=deepseek-v4-flash` `EXTRACT_LLM_BINDING_HOST=…` `EXTRACT_LLM_BINDING_API_KEY=…` `EXTRACT_MAX_ASYNC_LLM=…` `EXTRACT_LLM_TIMEOUT=…`（`api/config.py:757-804` 对 `ROLES` 循环生成；同一模式 `KEYWORD_*` / `QUERY_*` / `VLM_*`）。role 级 provider options 另有 `{ROLE}_OPENAI_LLM_<FIELD>`（如 `QUERY_OPENAI_LLM_TEMPERATURE`，binding_options.py:429-497）。
- 效果：API server 的 `resolve_role_llm_settings`（lightrag_server.py:1980）优先级 = 运行时 override > `{role}_*` 环境变量 > base；`create_role_llm_func`（:2098）为每个 role 各建一个把 model/host/api_key 封进闭包的独立函数——**这就是「抽取用 flash、生成用 pro」的标准能力位**。
- 细节：metadata 里 `binding`/`model`/`host` 进缓存身份，`api_key` 只进私有 metadata（`get_llm_role_config` 输出会剥离密钥，llm_roles.py:448-482）。

### 3.2.1 GLM（bigmodel）免费档接入（低成本测试备选，2026-09-11 实测）

> 定位：**测试 / 评测 / 降级**用的低成本 provider，主链仍是 DeepSeek。同一份闭包写法，换 `base_url` + `role_llm_configs` 即可。

- **端点**：`https://open.bigmodel.cn/api/paas/v4`（OpenAI 兼容；curl 连通性已实测 OK）。
- **模型**：主用 `glm-4.5-flash`、`glm-4-flash-250414`（免费档）；`glm-4.7-flash` 访问量过大、大概率不可用，**勿作第一选择**。
- **示例闭包**（同一个 `openai_complete_if_cache`）：

```python
from lightrag.llm.openai import openai_complete_if_cache

async def glm_flash_complete(prompt, system_prompt=None, history_messages=None, **kwargs) -> str:
    return await openai_complete_if_cache(
        "glm-4.5-flash",
        prompt,
        system_prompt=system_prompt,
        history_messages=history_messages,
        base_url="https://open.bigmodel.cn/api/paas/v4",  # bigmodel 端点
        api_key=GLM_API_KEY,                              # ⚠️ 临时 key：80b00ee9eeb44dd6885f5121c8720740.WU6qmPZLlyx5i7be（部署前替换/移入 .env）
        **kwargs,
    )
```

- **接法**：`LightRAG(llm_model_func=glm_flash_complete, role_llm_configs={...})`，或整链测试时直接把 `llm_model_func` 换上——roles 机制不变（见 §3.2）。
- **注意**：GLM 免费档并发/限流较紧，抽取大批量时建议 `llm_model_max_async` 调小；`temperature` 默认 1.0 偏高，测试同样要显式压低。
- **`glm-4.7-flash` 可用性**：2026-09-11 访问量大、大概率报错——把 `glm-4.5-flash` / `glm-4-flash-250414` 定为测试态默认，勿依赖 4.7。

### 3.3 LLM 缓存（Q3：键构成 / 持久化位置 / 命中逻辑）

**开关**：`enable_llm_cache`（默认 True，lightrag.py:791）与 `enable_llm_cache_for_entity_extract`（默认 True，:794）。API server 读 `ENABLE_LLM_CACHE` / `ENABLE_LLM_CACHE_FOR_EXTRACT`（api/config.py:749-754）。注意：**抽取缓存不仅仅省钱，它是文档删除后「基于缓存重建 / 补录图」的依赖**（`rebuild_knowledge_from_chunks`，operate.py:1101）——注释明确"Should not be disabled"。

**存储位置**：JSON KV 后端（默认）→ 文件 `working_dir/[workspace/]kv_store_llm_response_cache.json`（`kg/json_kv_impl.py:152`，namespace `llm_response_cache` 见 `namespace.py:10`）。换别的 KV 后端（Redis / PG）则落到对应存储、collection 前缀 `llm_response_cache`。每次初始化加载进共享 dict（`get_namespace_data`），`flush` 落盘。

**键构成**（两段式）：
- 存储键 = `generate_cache_key(mode, cache_type, hash)`（utils.py:962）→ 形如 `default:extract:<md5>` / `query:query:<md5>` / `<mode>:keywords:<md5>`。
- 抽取/摘要（`use_llm_func_with_cache`，utils.py:5232-5252）的 md5 输入 = `user_prompt + system_prompt + history_messages` 拼接 + **`<response_format>` 序列化** + **`<llm_identity>` 序列化**；`llm_identity` = `{role, binding, model, host}`（`get_llm_cache_identity`，utils.py:840-862；**故意不含 api_key 与 provider options**）。
- 关键词（operate.py:5003-5009）md5 输入 = `param.mode + text + language + <llm_identity>`。
- 查询回答（operate.py:4730-4763）md5 输入 = `mode + query + response_type + top_k + chunk_top_k + max_entity/relation/total_tokens + hl/ll_keywords + effective_user_prompt + enable_rerank + enable_content_headings + <llm_identity>`；缓存 entry 还带 `queryparam` 明细（含 `_ANSWER_CACHE_POLICY_VERSION` 策略版本号，operate.py:4546 注释）。
- **不含**：temperature 等抽样参数（官方文档明说改 `temperature` 不换缓存键，见 §6）。

**命中逻辑**：`handle_cache`（utils.py:4145-4177）→ 按 `enable_llm_cache`（mode=查询）或 `enable_llm_cache_for_entity_extract`（mode=default）分流 → `hashing_kv.get_by_id(flattened_key)`，命中返回 `(content, create_time)`，miss 返回 None。写入：`save_to_cache`（utils.py:4191-4236），流式响应不缓存（有 `__aiter__` 跳），**截断响应（`finish_reason=length`）不缓存**（严格说，`use_llm_func_with_cache` 的写入在 operate 调用层跳过 `is_truncated_response`，查询回答层同样判断 operate.py:4789）。命中统计：`statistic_data` 计数（utils.py:496）。
- 查询回答的缓存是**手动**做的：先 `handle_cache` 读，miss 再 `use_model_func` 调用并 `save_to_cache`（kg 查询 operate.py:4765-4818；naive 同款 6820-6864）。抽取则统一走 `use_llm_func_with_cache`。
- 抽取还维护了反向索引：text_chunks 的 `llm_cache_list`（每条命中的缓存键追加到 chunk 记录，lightrag.py:4839），供删除后重建 `_get_cached_extraction_results` 反查（operate.py:1414）。

**怎么清**：API `/documents/clear_cache`；或临时 `ENABLE_LLM_CACHE=false`（官方文档推荐 klar；注意清 LLM 缓存同时清掉抽取缓存——那正是重建依赖）。

### 3.4 中文 / 温度 / 结构化输出（Q4）

| 需求 | 参数 | 位置 |
|---|---|---|
| 中文 | `addon_params={"language": "zh"}` → `_resolved_summary_language`；进抽取/摘要/关键词 prompt（`Ensure the output language is {language}` 模板）。**中文是 prompt 级软约束，不是 API 参数** | lightrag.py `_refresh_addon_params_cache`（1171-1189）；prompt 模板见 `prompt.py:135` / 抽取 prompt `operate.py:3996-4042` |
| 温度（全局） | `llm_model_kwargs={"temperature": …}`（LightRAG 构造）→ role 包装 partial 注入。role 级：`RoleLLMConfig(kwargs={"temperature": …})` 或 `{ROLE}_OPENAI_LLM_TEMPERATURE`。**默认 `DEFAULT_TEMPERATURE = 1.0`**（constants.py:93）；`OpenAILLMOptions.temperature` 同默认（binding_options.py:733）。注意：**默认 1.0 偏高**，抽取/生成建议显式压低 |
| 其他抽样 | `max_tokens` / `max_completion_tokens` / `top_p` / `frequency_penalty` / `presence_penalty` / `stop` / `extra_body`（OpenRouter/vLLM 透传 `reasoning` 等）透传 `**kwargs` | `OpenAILLMOptions`（binding_options.py:719-751） |
| 仅 JSON 输出（JSON 模式抽取） | LightRAG 开关 `entity_extraction_use_json=True`（lightrag.py:743，`ENTITY_EXTRACTION_USE_JSON`）；抽取调用直接发 `response_format={"type": "json_object"}`（operate.py:4189 / 4265） | `<delimiter>` 文本模式 vs JSON 模式由它切换；解析走 `_process_json_extraction_result`（operate.py:906，`json_repair` 容错） |
| 结构化输出（json_schema） | `response_format={"type":"json_schema", ...}`（dict 形态）**只支持在** `openai_complete_if_cache` 直通层（openai.py:499-507 `create()` 单点分派）；**走 `use_llm_func_with_cache` 会抛错**（`_validate_cached_response_format`，utils.py:870-884，只放行 `json_object`）。Typed/Pydantic `response_format` 直接 `TypeError`（openai.py:133-141） | 我们是 OpenAI 兼容，dict 形态 json_schema 能通。**但注意别把 json_schema 塞进 `llm_model_kwargs` / role kwargs**（那样会绕过缓存层校验、且缓存键/模型身份不同步，见 §6） |
| 思考链（reasoning_content / COT） | 查询路径显式 `enable_cot=True`（operate.py:4782）；非流式把 reasoning 用 `... ` 包裹拼进 content（openai.py:819-845）；流式边收边吐（openai.py:614-649）。**一旦 `response_format` 非空，`enable_cot` 被强制关掉**（openai.py:440-441） | DeepSeek 推理模型返回 `reasoning_content`——配 `enable_cot=True` 可以看到思考内容，否则 reasoning 被丢弃（openai.py:848） |
| 推理 token 预算/思考级 | `OpenAILLMOptions.reasoning_effort`（默认 `medium`，openai 系列）；DeepSeek 沿用 `extra_body` / SDK 参数 | 与手机无关项，按需 |
| 超时 | `default_llm_timeout`（`LLM_TIMEOUT`，默认见 constants；lightrag.py:739）；role 级 `{ROLE}_LLM_TIMEOUT`。队列超时级联按 `llm_timeout*2+15` 上限 | `priority_limit_async_func_call`（utils.py:1104-1114） |

### 3.5 重试、并发、错误

- **重试**：`openai_complete_if_cache` 上挂 `@retry(stop=stop_after_attempt(3), wait=wait_exponential(1, min=4, max=10))`（openai.py:288-304），骑的是 **十次** 谓词：429（非预算耗尽型）、408/409 `APIStatusError`、连接错误、超时、`InvalidResponseError`、5xx、可迁移 JSON-parse-400。SDK client `max_retries=0`（重试所有权上收，openai.py:99-107 注释）。
- **空内容**：`finish_reason=length` → 抛 `EmptyTruncatedResponseError`（**不重试**，one-shot）；其他空 → `InvalidResponseError`（重试 3 次）。截断（length 且非空）→ 返回 `TruncatedResponse`（str 子类，缓存层凭它跳过持久化）。
- **并发**：role 层一个 `priority_limit_async_func_call(max_async)` 队列（`MAX_ASYNC_LLM` / `{ROLE}_MAX_ASYNC_LLM`，默认见 constants）；抽取内还有一层 `asyncio.Semaphore(llm_model_max_async)`（operate.py:4446）。优先级常量：查询 5、摘要 8（`DEFAULT_QUERY_PRIORITY` / `DEFAULT_SUMMARY_PRIORITY`，constants.py:673-676）。
- 错误类别：`RateLimitError` 里预算耗尽（LiteLLM `budget_exceeded` / OpenAI `insufficient_quota`）快速失败（`llm/_error_utils.py`）。Doc 失败信息会带 provider 原始 message（doc_status.error_msg）。

---

## 4. 输入输出

### 4.1 llm_model_func 回调协议（我们 M0 必须遵守的接口）

```
输入: prompt: str
      system_prompt: str | None
      history_messages: list[dict] | None   # [{"role": "user"|"assistant", "content": str}, …]
      **kwargs: response_format / max_tokens / stream / enable_cot / temperature…
输出: str（默认）
      或 AsyncIterator[str]（stream=True）→ operate 的查询路径直接透传给客户端
      TruncatedResponse（finish_reason=length 时的半截 str → 上游可容忍解析）
```

调用点约定（operate.py 各 `use_llm_func`/`use_model_func` 都按 `f(prompt, system_prompt=…, history_messages=…, **kwargs)` 调，**不传 role、不传 model**）：

| 场景 | 输入里额外有什么 | 期望输出 |
|---|---|---|
| 文本模式抽取 | `response_format=None`；prompt 内嵌分隔符 | 分隔符文本 |
| JSON 模式抽取 / gleaning | `response_format={"type":"json_object"}`；gleaning 带 `history_messages` | JSON 文本（`_process_json_extraction_result` 容错解析） |
| 关键词抽取 | `response_format={"type":"json_object"}` | JSON：`{high_level_keywords:[], low_level_keywords:[]}` |
| 查询回答 | `enable_cot=True`、`stream=param.stream`、`history_messages=conversation_history` | 纯文本 / 流 |
| 描述摘要 | — | 短摘要文本 |

`use_llm_func_with_cache` 还会给你补两件 SDK 无关的事：文本 sanitize（`sanitize_text_for_encoding`，防 UTF-8 坏编码）与上文缓存。

### 4.2 `openai_complete_if_cache` 完整签名（llm/openai.py:305-322）
`model, prompt, system_prompt=None, history_messages=None, enable_cot=False, base_url=None, api_key=None, token_tracker=None, stream=None, timeout=None, keyword_extraction=False, use_azure=False, azure_deployment=None, api_version=None, image_inputs=None, **kwargs`。

---

## 5. 「内建 vs 自研」边界

| 能力 | 建议 | 理由 |
|---|---|---|
| 复用：`openai_complete_if_cache` | **直接用，不重写** | OpenAI 兼容一键全套：base_url/api_key 参数名、重试、COT、截断判定、空响应诊断、流式话柄全齐。DeepSeek 是 OpenAI 兼容，直接吃 |
| 复用：role 分发 / 缓存 / 队列 | **直接用** | `ROLES` + `RoleLLMConfig` + `priority_limit_async_func_call` + `kv_store_llm_response_cache` 已把「分环节模型、并发、缓存」三件套做完；M0 不该再造 |
| 自研：`llm_client` 薄封装 | **可以做，但只做「工厂」一层** | 我们需求是封装模型选择（deepseek-flash/pro）+ 统一客户端（api_key/base_url 注入）+ 监控。对应代码位 = 一个返回 async 闭包的工厂（照 `create_optimized_openai_llm_func` lightrag_server.py:1843 的形状），落地为 `LightRAG(llm_model_func=…)` / `role_llm_configs` / `llm_model_kwargs`。**不要自己实现调用协议之上的抽象** |
| 自研：embedding | 全自研（Xinference），但**必须满足 `EmbeddingFunc` 协议** | `@wrap_embedding_func_with_attrs(embedding_dim, max_token_size)` → `.func` 是内部异步函数；`EmbeddingFunc(func=…)` 注意用 `.func`（见 AGENTS.md 提示）。换 embedding 模型必须清向量库 |
| 不碰 | `binding_options.py` 的 CLI/env 解析、`api/` 的 FastAPI 壳、VLM/多模态 | 属于 WebUI 部署路径或与我们无关 |

---

## 6. 已知坑

1. **没有 `create_llm_client`**：别找这个 API，v1.5.7 的暴露面是「异步函数回调」。搜代码只会找到 `create_openai_async_client`（只建 SDK client）与 API server 的 `create_llm_model_func`。
2. **`llm_model_func` 不能传 `openai_complete_if_cache` 本体**：它缺 `model` 必填参数，必须包闭包（§3.2）。
3. **改 temperature 不换缓存键**：LLM 缓存键只含 prompt + `response_format` + `llm_identity`（role/binding/model/host），**不含 temperature / max_tokens 等抽样参数**（官方文档 §10 明示）。线上调参后旧缓存仍命中。抽取/关键词/查询回答同病。清法：`/documents/clear_cache` 或临时 `ENABLE_LLM_CACHE=false`。
4. **`use_llm_func_with_cache` 只放行 `response_format={"type":"json_object"}`**：json_schema / typed response_format 走它直接 `ValueError`（utils.py:870）。别把 json_schema 塞 `llm_model_kwargs` / role kwargs——那会绕过校验，同时缓存身份里没有 json_schema 这个维度，schema 变更不失效缓存。
5. **默认 `temperature=1.0` 偏高**（constants.py:93），且 `reasoning_effort=medium` 默认值只是文档值、未设置就不发送（`argparse.SUPPRESS` 语义，见 LLMProviderOptions.md §1.2）。DeepSeek 抽取建议显式 `temperature` 低一些；不确定就都不发，让 provider 用默认。
6. **`enable_cot` 与 `response_format` 互斥**：给某调用传了 `response_format`，COT 会被静默关掉（openai.py:440-441）→ 抽取（JSON 模式）不会看到思考内容，也永远不拿 reasoning 兜底。
   推理模型若只吐 `reasoning_content` 内容为空，会抛 `InvalidResponseError`，报错里带提示「consider disabling thinking mode for this role」（openai.py:870-906）。DeepSeek 推理模型配 `reasoning_effort` / `extra_body` 控制思考预算（对抽取/关键词把思考关掉，见 LLMProviderOptions.md §11）。
7. **换 embedding 模型必须清数据目录**（或至少向量库）：旧向量在新模型空间里没有意义（AGENTS.md 有提示；`openai_embed` 自带 tiktoken 截断到 `max_token_size`，最大 8192 token，Xinference 模型上限低于此就要自己降）。
8. **基础 `llm_model_func` 不包并发队列**（llm_roles.py:205-208）：所有调用必须走 role wrapper。自研新链路如果绕过 `role_llm_funcs` 直调 base func，会丢掉并发与 `hashing_kv` 注入两件套。
9. **空/截断响应的重试语义**：`finish_reason=length` 抛 `EmptyTruncatedResponseError` **不重试**（一次失败；弹 max_tokens 或思考预算）。缓存层对 `TruncatedResponse` 跳过持久化，防止半截结果污染缓存（openai.py:912-921）。
10. **role 级环境变量才有 CLI 对应物缺失**：role provider options（`EXTRACT_OPENAI_LLM_*`）仅从环境读、无 CLI 参数（LLMProviderOptions.md §1.1）。程序内嵌那条路不受影响。

---

## 7. 我们项目的采用建议（M0 llm_client + DeepSeek 全链路）

1. **接法**：不引入任何 `LLMClient` 概念——M0 交付一个 **闭包工厂**（照 `openai_complete_if_cache` 协议），输出 `async def (prompt, system_prompt=None, history_messages=None, **kwargs)`。工厂参数：`model`、`base_url`（默认 `https://api.deepseek.com/v1`，可配 `/v1` 后缀）、`api_key`、`extra_kwargs`（temperature 等）。内部直接 `await openai_complete_if_cache(model, prompt, system_prompt=…, history_messages=…, base_url=…, api_key=…, **{**extra_kwargs, **kwargs})`。
2. **分环节模型**：`LightRAG(llm_model_func=deepseek_pro, role_llm_configs={"extract": RoleLLMConfig(func=deepseek_flash, metadata={binding/model/host}), "keyword": RoleLLMConfig(func=deepseek_flash, metadata={…})})`。query 不覆盖=用 pro。metadata 三件套（binding/model/host）必须填对，否则缓存身份回落 `llm_model_name`，两个模型共用一个缓存桶（键会歧义）。
3. **缓存**：用默认 `enable_llm_cache=True` + `enable_llm_cache_for_entity_extract=True`。别关——它是文档删除后重建图 / 补录的属性。
   - 调优注意：抽样的 temperature 不参与缓存键，**调参先清缓存**；正式跑之前先用小文档验一遍键的唯一性。
4. **中文**：`addon_params={"language": "zh"}`，并准备自己的中文 entity_types_guidance / 抽取示例（走 prompt 模板/profile，M3 的事）。中文是 prompt 级软约束，别指望 API 参数。
5. **JSON 抽取**：`entity_extraction_use_json=True`（`response_format={"type":"json_object"}` 由内核自动发），DeepSeek 兼容。不要走 json_schema（缓存层限制 + DeepSeek 的 json_object 已够抽取用）。
6. **思考链**：查询走 `enable_cot=True`（内核自动），想要的生成可展示 reasoning；抽取/关键词建议在 provider 侧关思考（`extra_body`/`reasoning_effort`），避免思考吞掉 JSON 输出预算触发 §6.6 的空响应。
7. **不碰**：`api/` 的 FastAPI 壳 / WebUI / binding_options CLI 解析 / VLM / embedding（Xinference 自研但遵守 `EmbeddingFunc` 协议）。我们的内核边界 = `llm_model_func` 回调协议 + `role_llm_configs` + `llm_model_kwargs` + 缓存存储文件这四处。
8. **监控接缝**：`token_tracker`（openai.py 每响应记 usage）与 `statistic_data`（utils.py:496，llm_call/llm_cache 计数）是现成的用量统计挂点；M0 若需成本计量，在这两个位点接即可，不必改内核。