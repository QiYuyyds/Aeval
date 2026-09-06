# 宿主字段覆盖对照（任务 8.1）

对照对象：宿主 `bitdance-agenthub` 后端（`backend/app/**`）实际写下的 span 属性，与 Aeval 在 ①→② 之间的取证路径。
证据来源：**两侧源码逐点核对**（写入点行号见下表），非宿主运行时实测。

## 结论先说

切换**不会**把大量真实数值换成缺失。旧实现硬编码读的 9 个名字里，宿主**只写其中 3 个**；另外 6 个从来就没有值可读。归一化之后可读字段反而变多。

## 逐字段对照

「旧」= 变更前框架硬编码的属性名；「后」= 变更后的默认映射（OTel GenAI 标准名）；「需映射条目」= 宿主接入时必须声明的一条表项。

| 标准字段 | 旧硬编码读的名字 | 宿主实际写了吗 | 默认映射（标准名） | 切换后可读性 |
|---------|----------------|--------------|------------------|------------|
| `tool.name` | `agenthub.tool_name` | 写（`tools/registry.py:114`） | `gen_ai.tool.name` | 持平 —— 需一条映射条目 |
| `tool.success` | `agenthub.success` | 写（`registry.py:120/129/138`、`services/agent_runner.py:3315`） | 无（规范未定义） | 持平 —— 需一条映射条目 |
| `usage.total_tokens` | `agenthub.total_tokens` | 写（`agent_runner.py:3287`，仅根 span） | 无（规范未定义） | 持平，但**口径变了**，见下 |
| `session.id` | `agenthub.session_id` | **从未写** | `gen_ai.conversation.id` | 原来就是空的；改映射到 `agenthub.conversation_id`（写：`agent_runner.py:3285`、`dag_executor.py:188`）后**新增可读** |
| `agent.name` | `agenthub.agent_name` | **从未写** | `gen_ai.agent.name` | 原来就是空的；宿主可映射到 `agenthub.agent_id`（写：`agent_runner.py:3283`）或保持缺失 |
| `agent.version` | `agenthub.agent_version` | **从未写** | `gen_ai.agent.version` | 原来就是空的，切换后仍缺失（诚实报缺失，不再假装在读） |
| `artifact.type` | `agenthub.artifact_type` | **从未写**（只有宿主自己的 grader 在 `eval_integration/graders/artifact.py:79` 读它，一直读到 `""`） | 无（规范未定义产物属性） | 无退化：两边都读不到。宿主侧这是一个既有的空转路径，值得单独修 |
| `artifact.id` / `artifact.content` | `agenthub.artifact_id` / `agenthub.content` | **从未写** | 无 | 同上 |

旧实现完全没读、切换后经映射**新增可读**的字段：

| 标准字段 | 宿主写入点 | 需要的映射条目 |
|---------|-----------|--------------|
| `usage.input_tokens` | `adapters/custom_adapter.py:1172` | `usage.input_tokens → agenthub.input_tokens` |
| `usage.output_tokens` | `custom_adapter.py:1173` | `usage.output_tokens → agenthub.output_tokens` |
| llm 调用识别（`n_turns` 的来源） | `custom_adapter.py:1172-1173` 写 per-call 用量 | 无独立字段：角色由「映射后出现了 `usage.input_tokens`/`output_tokens`」推断，故该条目同时是计数的依据 |
| `error.type` | `registry.py:121/131/139`、`agent_runner.py:3316`、`dag_executor.py:228` | `error.type → agenthub.error` |
| `tool.arguments` | `tools/registry.py:114`（`agenthub.args_summary` = `str(args)[:200]`） | `tool.arguments → agenthub.args_summary`，**且套件需 opt-in 采集** |

读不到的（宿主声明了常量但没有写入点）：`agenthub.model`、`agenthub.cache_read_tokens` → `model` 与 `n_cache_read_tokens` 会报 `provider_not_covered`，成本折算因此只能走 `default` 价档或报不可计算。

## 两个必须在切换时一起处理的分歧

1. **`n_total_tokens` 的口径变化**。旧实现：把所有 span 上的 `agenthub.total_tokens` 相加（宿主只在 agent-runner 根 span 写一次）。新实现：优先取「输入+输出」之和并按 LLM 调用 span 累加。两边都是真实数值，但**数值会不同**，且新口径更接近逐次调用之和。因此切换后的 run 与历史 run 会被 `evidence.mapping_version` 标为不同口径，不可直接比较 —— 这是设计意图，不是回归。
2. **计数不再来自 span 名称**。旧实现用 `"tool.call" in name` / `"turn" in name` 子串匹配数轮次与工具调用。新实现只看标准字段：宿主 span 恰好也叫 `tool.call`，但**名字不再算证据**。所以映射条目不是可选装饰 —— 少了 `usage.input_tokens` 这条，LLM span 就没有任何字段能推断出角色，`n_turns` 会报 `provider_not_covered` 而不是给出数字。宿主的最小可用表项：

```python
# backend/app/eval_integration/config.py:130 的 EvalRunner(...) 增加 trace_mapping=
from agent_eval.trace.mapping import default_mapping

HOST_AEVAL_MAPPING = default_mapping({
    "tool.name": "agenthub.tool_name",
    "tool.success": "agenthub.success",
    "tool.arguments": "agenthub.args_summary",   # 仅在套件 opt-in 采集时才会被读
    "error.type": "agenthub.error",
    "usage.input_tokens": "agenthub.input_tokens",
    "usage.output_tokens": "agenthub.output_tokens",
    "usage.total_tokens": "agenthub.total_tokens",
    "session.id": "agenthub.conversation_id",
    "agent.name": "agenthub.agent_id",
})
```

（`artifact.*` 三项在宿主侧目前无源可接，先不声明 —— 读不到会诚实报缺失，不影响其余字段。）

## 未执行的部分

**运行时 dry-run 没有做**：本仓库内无法启动宿主（需其后端服务、Phoenix 与既有 run 数据），逐条比对的结论全部来自上述源码写入点。要在宿主侧落地的验收动作是：用同一份既有 suite 在切换前后各跑一次，比对 `trial.metrics` 键集合与 `trial.evidence_gaps`，确认

- 除 `n_total_tokens` 的口径差之外，`evidence_gaps` 中**不出现**本表右列标注为「写」的字段；
- `unrecognized_attributes` 里出现的名字，均为本表已判定「宿主确实不写」或宿主有意不接入的项。

若出现第三类（宿主写了、清单里却没覆盖到），说明还有未被本次核对发现的写入点，应补映射条目而**不是**调低阈值。
