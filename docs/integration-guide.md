# 接入指南

Aeval 与你的 Agent 系统之间只依赖少量小协议。**必选的只有一个**：`AgentRunner`；其余（TraceProvider / Storage / EnvironmentManager / Grader）都有默认实现，按需替换。

## 1. AgentRunner（必选）

框架通过 `run(task)` 与 Agent 交互，不需要知道 Agent 内部实现：

```python
from agent_eval.core.types import EvalTask

class MyAgentRunner:
    async def run(self, task: EvalTask) -> tuple[str, list[dict], dict]:
        # 1. 按 task.env 准备环境（种子文件等）
        # 2. 把 task.prompt 发给你的 Agent，等待完成
        # 3. 收集结果
        return trace_id, transcript, outcome
        #      ^str     ^对话记录     ^环境最终状态
```

约定：

- **transcript**: `[{"role": "user"|"assistant"|..., "content": ...}, ...]` — `code_based` 的 `target: transcript` 在此搜索
- **outcome**: 任意 JSON dict（如 `{"files": {...}}`）— `target: outcome` 在此搜索（深序列化后匹配）
- **trace_id**: OTel trace id；没有 trace 体系就传 `""`
- **瞬时故障**：网络超时/抖动请显式抛 `TransientError`（`agent_eval.core.contract`）— 框架做指数退避重试（默认最多 2 次）；其余异常直接判该 trial 失败，不重试

### 报错分类契约（`agent_error`）

「agent 报错了」本身不告诉你这次评测该记通过、未通过还是不采信。框架只接受**接入方声明**的分类，不猜：

| 你抛的异常 | 归类 | 对该 trial 的后果 |
|-----------|------|------------------|
| `TransientError` | 评测侧抖动 | 指数退避重试；重试耗尽记 `invalid` + `external_dependency_unavailable`（不占分母） |
| `AgentDefect` | 被评测系统自身的缺陷 | `verdict=valid` 且 `success=False`：计为未通过并占分母（这是关于 agent 的结论），终止原因仍记 `agent_error` |
| `ExternalDependencyError` | 上游服务/凭据/网络不可达 | `verdict=invalid`，不占分子也不占分母 |
| 其他任意异常 | 未声明类别 | `invalid_reason=unclassified_agent_error` → **需人工判定**，既不折算通过也不折算未通过 |

未分类的报错会原样出现在终止原因分布里（`agent_error` 单列计数），但聚合数字不会替它下结论。接入稳定后把出口异常收敛到上面三类，是让批量数据可信的最省事的一步。

超过 `per_trial_timeout` 的挂起按 `timeout` 归类：那是「评测没跑完」，走 invalid 通道，与 agent 表现无关。

### HTTP Agent 适配

Agent 以 HTTP 服务暴露时，参考 `examples/achat/http_agent_runner.py`：提交 prompt → 轮询至完成 → 组装三元组。AChat 仓库的 `AChatAgentRunner` 是完整生产实现（含 workspace 沙箱与事件总线完成检测）。

## 2. Runner 注册与选择

`eval-suite run --runner <name>`（或环境变量 `AEVAL_RUNNER`）按名称解析 AgentRunner：

- `mock` — 内置 MockAgentRunner（默认）
- 自定义 — 在你的包里声明 entry-point，name → **零参工厂**：

```toml
[project.entry-points."agent_eval.runners"]
my-agent = "my_pkg.runner:create_runner"
```

```python
def create_runner() -> MyAgentRunner:
    return MyAgentRunner(api_base=os.environ["MY_AGENT_URL"])
```

在纯 Python 侧使用时则完全不需要注册——直接把实例传给 `EvalRunner`。

## 3. TraceProvider 与属性翻译表（可选）

默认 `MockTraceProvider`（返回空 span）。接 Phoenix：

```python
from agent_eval.trace import PhoenixProvider

runner = EvalRunner(..., trace_provider=PhoenixProvider(endpoint="http://localhost:6006"))
```

span 数据用于：过程指标提取（`tracked_metrics`）、`tool_calls` / `step_level` / `transcript` 评分器、Trial 下钻与 Phoenix 外链。需要 `pip install arize-phoenix`（懒加载，未装不报错，除非真的调用）。

### 归一化边界：框架只读标准观测

`TraceProvider` 交回的 span 先经一次属性翻译，得到词汇无关的**标准观测**（工具名、调用成败、token 四分解、时延、会话标识、agent 名称与版本…），内置评分器与指标只消费标准观测，**不再看 span 名称，也不认识任何宿主的私有属性名**。翻译只做一次，因此加一个宿主 = 加一条表项，不改框架源码也不改评分器。

内置条目对齐 OTel GenAI 语义约定。该规范已迁出主仓库、除 `error.type` 外全部处于 Development 稳定性且尚无 tagged release，所以版本钉死在 `agent_eval.trace.mapping.OTEL_GENAI_SPEC_VERSION`（当前 `genai-94f432d`）并随 run 落盘：

```python
from agent_eval.trace.mapping import default_mapping

mapping = default_mapping({
    # 标准字段 → 你埋点里实际写的属性名（可给多个，按顺序取首个命中）
    "tool.name":   "myco.tool.name",
    "usage.input_tokens": ["myco.llm.prompt_tokens", "gen_ai.usage.input_tokens"],
    "artifact.type": "myco.artifact.kind",   # 规范未定义产物属性, 只能这样接入
    "artifact.content": "myco.artifact.body",
})
runner = EvalRunner(..., trace_mapping=mapping)
```

规范本身没定义属性名的字段（`tool.success`、`usage.total_tokens`、`span.role`、产物三件套）默认**没有条目**：不声明就读不到，读不到就报缺失，框架不会拿相近的名字凑数。

### span 角色怎么定

按此顺序，**绝不按 span 名称猜**：

1. `span.role`（经映射读，值认 `tool` / `llm` / `artifact` 及常见别名）
2. `gen_ai.operation.name`（`execute_tool` → tool，`chat`/`text_completion`/`generate_content` → llm…）
3. 都缺时按「哪些标准字段出现了」推断

工具调用的成/败另有一条独立顺序：宿主显式布尔（`tool.success`）> `error.type` 存在即失败 > span `status.code`（`ERROR`/`OK`）> 报缺失。

### 读不到会怎样

- **缺失 ≠ 0**：没读到的指标键**不出现在** `metrics` 里，同时在该 trial 的 `evidence_gaps` 带上字段与原因（`provider_unavailable` / `provider_not_covered` / `unrecognized_attribute` / `no_such_call` / `capture_disabled`）。trace 里确实一条工具 span 都没有才是 `n_toolcalls = 0`。
- **不认识的属性名**进 `unrecognized_attributes` 清单并 `logger.warning`，是更新映射表的信号，而不是被静默丢弃。
- **provider 不可用/未安装**归为 `source_status="unavailable"`，不崩溃，也不用空列表冒充「没有调用」。
- **入参与结果**默认不读：只有套件 `capture_tool_arguments: true` 且 trace 里真有该属性时才采集，且必经脱敏（见 §8）。

`GET /v1/meta` 会报告当前 `spec_version` / `mapping_version` 与 `tool_arguments_captured_by_default: false`；每个 run 的 `evidence` 记录里存着当时生效的版本，跨版本的两个 run 会被标注为不可直接比较。

### 第三方 grader 里取观测

自定义评分器的 `grade(trial, spans, task, context)` 签名不变；`context.observations` 就是本次已归一化的标准观测（runner 已翻译，无需重做），`spans` 保留原文供自述证据用。

## 4. Storage（可选）

默认 `MemoryStorage`（进程内，不落盘）。CLI 的 `run` 默认使用 SQLite：

```python
from agent_eval.storage.sqlite import SqliteStorage

storage = SqliteStorage("./aeval.db")
await storage.initialize()
runner = EvalRunner(..., storage=storage)
```

`Storage` 协议核心方法：`save_run / get_run / list_runs / delete_run / save_suite / get_suite / list_suites`。PostgreSQL 后端在路线图上。

## 5. EnvironmentManager（可选）

默认 `NoOpEnvironment`（无操作）。要做环境隔离与泄漏检测时实现：

```python
class MyEnvironment:
    async def setup(self, task) -> None: ...          # 每 trial 开始（种子文件等）
    async def teardown(self, task) -> None: ...       # 每 trial 结束
    async def snapshot(self) -> dict: ...             # 环境基线快照
    async def verify_clean(self, baseline) -> dict:   # trial 后比对
        return {"clean": True, "differences": []}
    async def restore(self, baseline) -> None: ...    # 泄漏时恢复基线
```

`verify_clean` 报告不干净时：框架自动 `restore` 并记录告警，**不**判 trial 失败（失败只由评分决定）。

## 6. 自定义 Grader（可选）

```python
from agent_eval.core.types import GraderResult, GraderType

class CodeStyleGrader:
    name = "code_style"          # suite 里 name 字段引用这个名字

    async def grade(self, trial, spans, task, context=None) -> GraderResult:
        ...
        return GraderResult(
            grader_name=self.name,
            grader_type=GraderType.CODE,
            score=0.9, passed=True, explanation="style ok",
        )

runner = EvalRunner(..., graders=[CodeStyleGrader()])  # 与内置合并，同名覆盖内置
```

评分器之间可以声明依赖（拓扑排序执行）：`GraderConfig(dependencies=["code_based"])` — 依赖未通过时依赖方跳过并记 `score=0.0` + `verdict=valid`（这是关于 agent 的结论）。评测侧故障（缺数据、外部服务不可用）请显式返回 `verdict=invalid` + `invalid_reason`，让它不占通过率分母，详见 `docs/grader-reference.md` §判定态。

## 7. LLM 依赖的注入

`model_based`（LLM-as-Judge）与 `metric` 类评分器需要 LLM 回调：

```python
async def my_llm(system: str, user: str) -> str: ...

runner = EvalRunner(..., llm_fn=my_llm)   # 注入未自行配置的 judge/指标
```

也可以走指标注册表：`metrics_registry={...}`（见 `agent_eval.metrics`）。缺配置时框架返回 `invalid` / `judge_unavailable` 结论并注明原因 —— 该 trial 不占通过率分母，也不会 crash 整个 run。

## 8. 脱敏钩子（采集开启时必经）

工具入参与结果可能含凭据与用户数据。框架的立场是：默认不采集；一旦采集，**必须**经脱敏处理才进入证据存储、API 响应与 Dashboard，未脱敏的原始值不落盘。

默认实现 `HashingEvidenceRedactor` 以定长摘要替换字符串（`redacted:sha256:<16>`），保留结构与数值标量而不是删字段——「期望参数 vs 实际参数」的比对因此仍可在脱敏形式下进行，且同一输入恒得同一输出（可复现）。

需要换实现时，实现同一 Protocol 并整体注入（**替换，不叠加**）：

```python
from agent_eval.core.redaction import EvidenceRedactor

class PhiIiRedactor:
    identifier = "myco.phi-ii"     # 写进 run 的 evidence.redactor_identifier
    version = "3"                  # 写进 evidence.redactor_version

    def redact(self, value, *, field: str = ""):
        return my_phi_ii.transform(value, scratchpad=field)   # 必须确定性

runner = EvalRunner(..., redactor=PhiIiRedactor())
```

`EvidenceRedactor` 是 `runtime_checkable` 的 Protocol：上面这种鸭子类型实现即可满足；想让你的实现被 `isinstance` 与类型检查认出来，就显式 `class PhiIiRedactor(EvidenceRedactor)` 继承它。

比对场景需要逐字看参数时，`IdentityEvidenceRedactor` 是显式逃生舱：它什么都不改，明文会落盘，但 `run.evidence.redactor_identifier == "aeval.identity-none"` 让审计一眼看得见是谁关掉的脱敏（默认值是 `aeval.sha256-summary`）。

## 9. 单价表与成本轴

`cost_usd` 与通过率是**并列的两轴**，不折进任何复合分数。框架不内置任何默认价目——价格变动远快于套件修订，内置价只会产出「看起来很精确的假成本」。未配置即报不可计算（证据缺口 `cost_usd: price_table_not_configured`），不以 0 冒充零成本。

```python
from agent_eval.core.pricing import PriceTable, TokenPrices

runner = EvalRunner(
    ...,
    price_table=PriceTable(
        default=TokenPrices(input_per_mtok=2.5, output_per_mtok=10.0),
        by_model={"gpt-4o": TokenPrices(
            input_per_mtok=2.5,
            output_per_mtok=10.0,
            reasoning_per_mtok=40.0,   # 缺价则按输出价折算
            cache_read_per_mtok=1.25,  # 缺价则按输入价折算
        )},
    ),
)
```

- 四路 token 分别计量、分别折算，不合并为单一 token 总数；`n_total_tokens` 只是「输入+输出」的只读别名，不用于计费推导。
- 观测到的模型未在 `by_model` 单列且没有 `default` 档 → `model_price_not_covered`，该 trial 成本不可计算。
- 也接受外部配置字典：`price_table={"default": {"input_per_mtok": 2.5}, "by_model": {...}}`（runner 内 `PriceTable.from_mapping` 折算；空配置 = 不计算）。
- 汇总里 `resources.passed` / `resources.failed` 分开给均值，`cost_unknown_trials` 单列成本算不出的 trial 数及首要原因；`cost_trend()` 生成跨 run 序列时会**排除**没有成本轴的 run 并标注排除原因，而不是按零计入。
- 目前仅 `currency="usd"`；CLI 不带单价表，`eval-suite run` 的结果一律报成本不可计算。

## 10. 挂载 API（可选）

把评测 API 挂进你已有的 FastAPI 应用：

```python
from agent_eval.api.app import create_app

app.mount("/api/eval", create_app(runner=my_runner))   # 路径随你
```

或独立部署（`/v1` 前缀 + `X-Aeval-Version` 头 + `/v1/meta`）：

```python
from agent_eval.api.standalone import create_standalone_app

uvicorn.run(create_standalone_app(runner=my_runner), host="127.0.0.1", port=8000)
# 或直接: eval-suite serve --port 8000
```

API 兼容承诺：同一大版本内 URL 与响应结构向后兼容；破坏性变更升 `/v2` 并保留并行期。
