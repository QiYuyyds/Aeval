# Grader 参考

每个任务（`EvalTask.graders`）配置一个或多个评分器。通用字段：

| 字段 | 默认 | 说明 |
|------|------|------|
| `type` | 必填 | `code` / `model` / `state` / `tool_calls` / `transcript` / `artifact` / `metric` / `custom` |
| `name` | 必填 | 评分器名（正则 `^[a-zA-Z][a-zA-Z0-9_-]*$`），路由到具体实现 |
| `weight` | 1.0 | 加权评分权重（`WEIGHTED` / `HYBRID` 策略） |
| `required` | false | `HYBRID` 策略下必须通过，否则任务失败 |
| `sample_count` | 1 | LLM Judge 多采样次数（1-10，算 confidence） |
| `dependencies` | [] | 依赖的其他评分器名（拓扑排序；依赖未通过 → 本评分器跳过） |
| `config` | {} | 类型特定配置（见下） |

## 判定态（verdict）— grader 结论的三态

每条 grader 结论与每个 trial 都带 `verdict ∈ {valid, invalid, pending}`（默认 `valid`）：

| verdict | 含义 | 是否占通过率分母 |
|---------|------|----------------|
| `valid` | 关于 agent 的有效证据（**包括得 0 分**） | 是 |
| `invalid` | 评测侧故障 —— 评测本身没跑通，不是关于 agent 的证据 | 否 |
| `pending` | 等待人工评分回传 | 否（回传后重算） |

`invalid` 的原因取**封闭枚举** `InvalidReason`：

| 原因 | 触发场景 |
|------|----------|
| `grader_error` | grader 抛异常 |
| `grader_timeout` | grader 超过 `timeout_s` |
| `unknown_grader` | 配置了未注册的 grader 或指标名 |
| `judge_unavailable` | LLM judge 未配置或调用失败 |
| `verdict_unparseable` | judge 输出无法解析为判定（不再有 0.5 兜底） |
| `no_criteria_configured` | grader 挂上去了但没有任何判据：`code_based.checks` / `state_check.expectations` / `tool_calls.required_tools`+`forbidden_tools` / `artifact_check.expected_type`+`content_regex` / `step_level.expected_trace` / `model_based.dimensions` / `metric.metric_name`，或指标缺输入材料（如 faithfulness 无 context） |
| `trial_timeout` | trial 整体超时（已采集的 transcript / 指标 / 产物仍保留） |
| `evidence_unavailable` | 判定所依赖的过程证据读不到（取证通道不可用、字段未映射、入参未采集） |
| `unclassified_agent_error` | agent 报错但接入方未声明类别 → 需人工判定，不折算通过与否 |
| `external_dependency_unavailable` | 接入方声明的外部依赖不可达（上游服务/凭据/网络） |
| `trial_cancelled` | 取消生效后该 trial 未运行（评测没跑完） |

规则：

- 只有 `valid` 进入 `pass@k` / `pass^k` / 平均分的分子与分母；分母为 0 时聚合量为 `insufficient_data`（`null`），**不是 `0.0`**
- 判定优先级 `pending > invalid > valid`；trial 配了 grader 时其判定由各 grader 结论推导，人工评分回传后自动离开 `pending`
- **依赖未满足是例外**：前置 grader 未通过 → 本 grader 记 `score=0.0` + `passed=False` + `verdict=valid`，因为「前置条件没达成」是关于 agent 的结论
- 判据缺席不再自动满分：`no_criteria_configured` 既不判通过也不判 agent 失败

**自定义 grader 如何返回 invalid**（`GraderResult` 的两个字段）：

```python
from agent_eval.core.types import GraderResult, GraderType, InvalidReason, TrialVerdict


class MyGrader:
    name = "my_grader"

    async def grade(self, trial, spans, task, context=None):
        ref = load_external_reference(task)
        if ref is None:                      # 评测侧缺料, 不是 agent 的错
            return GraderResult(
                grader_name=self.name,
                grader_type=GraderType.CUSTOM,
                score=0.0,                   # 值不参与聚合 (已被排除)
                passed=False,
                explanation="参考数据未准备好",
                verdict=TrialVerdict.INVALID,
                invalid_reason=InvalidReason.NO_CRITERIA_CONFIGURED,
            )
        return GraderResult(
            grader_name=self.name, grader_type=GraderType.CUSTOM,
            score=1.0 if ref.match(trial) else 0.0, passed=..., explanation=...,
        )                                # 正常返回即 verdict=valid (含 0 分)
```

抛异常也可以——runner 会兜底记 `grader_error`，但显式返回能保留更准确的原因与解释。

## 证据边界 — 过程类 grader 共用的判定语义

内置的过程类评分器（`tool_calls` / `step_level` / `transcript` / `artifact_check`）只读**归一化观测**：span 经属性翻译表转成标准观测后交给它们，评分器不认识任何宿主的私有属性名，也不看 span 名称。于是每条结论都必须能回答「我看到了哪些证据、哪些没看到、为什么」。

**两种「零」不是一回事**：

| 情形 | 结论 | 对分母的影响 |
|------|------|-------------|
| 取证通道没答上来（provider 未安装/不可用）、字段未被映射接入、入参未 opt-in | `verdict=invalid` + `evidence_unavailable`，解释里点名缺的字段与原因 | 既不占分子也**不占分母**（评测没看到，不能说 agent 没做到） |
| trace 读到了，里面确实一条工具调用都没有 | `score=0.0` + `verdict=valid` | 占分母，计为未通过（这是关于 agent 的结论） |

`tool_calls` / `step_level` / `transcript` / `artifact_check` 都按这条线走：计数为 `Missing` → 证据不可用；计数为真 0 → 真实失败。`transcript` 还会把只测得出来的分量取平均（测不出的分量不参与、不按 0 计入），全部分量都测不出时才整条结论转为 `evidence_unavailable`。

每条结论的 `details.evidence` 是自陈的证据账本：

```json
{
  "tool_calls_observed": 3,
  "llm_calls_observed": {"missing": true, "reason": "provider_not_covered", "detail": ""},
  "artifacts_observed": 0,
  "source_status": "ok",
  "missing_fields": {"usage.reasoning_tokens": "provider_not_covered"},
  "unrecognized_attributes": ["myco.brand_specific_thing"],
  "spec_version": "genai-94f432d",
  "mapping_version": "1"
}
```

**对汇总的影响**：证据不可用的 trial 走 ① 的 invalid 通道，因此它会把分母缩小而不是把通过率压低——这是有意的，但分母缩小本身有风险，所以 pytest 插件与 CLI 门禁在 invalid 占比超过 `invalid_ratio_limit`（默认 `0.2`）时直接把该 run 判为不可信。大批量出现 `evidence_unavailable` 通常意味着映射表缺条目或没装 trace 后端，属于接入问题，应当去修证据通道而不是调阈值。`unrecognized_attributes` 非空正是「该更新映射了」的信号。

**工具入参**：默认不采集（suite/task 的 `capture_tool_arguments`）。关闭时入参槽位是 `{"missing": true, "reason": "capture_disabled"}`，任何需要入参的判定报证据不可用；开启后入参必经脱敏钩子（默认摘要化，保留结构与可判定性）才落盘与呈现。详见集成指南 §8。

## code_based（type: code）— 确定性检查

```yaml
- type: code
  name: code_based
  config:
    threshold: 1.0        # 必须通过的比例
    checks:
      - { type: contains,      target: transcript, value: "80" }
      - { type: not_contains,  target: outcome,    value: "password" }
      - { type: regex,         target: transcript, value: "\\d{2,5}" }
      - { type: exact,         target: outcome,    value: "ok" }
```

- `target`: `transcript`（对话记录全文）| `outcome`（环境最终状态 JSON）| `spans`
- `threshold` < 1.0 时按通过比例给部分分
- `checks` 为空 → 判定 `invalid` / `no_criteria_configured`（旧版此处自动给 1.0 满分并判通过）

## model_based（type: model）— LLM-as-Judge

```yaml
- type: model
  name: model_based
  sample_count: 3          # 多采样 → confidence
  config:
    rubric: "回答必须准确且引用了文件内容"
    dimensions: ["correctness", "completeness"]
    threshold: 0.7
```

需 LLM 回调（`EvalRunner(llm_fn=...)` 或指标注册表注入）。判定语义：

- 未配置 LLM 或调用失败 → `invalid` / `judge_unavailable`（旧版返回带原因的 0 分并计入分母）
- 输出无法解析为判定 → `invalid` / `verdict_unparseable`；**各维度 0.5 兜底已删除**
- `dimensions` 为空 → `invalid` / `no_criteria_configured`
- 平均分按**配置的全集维度数**为分母：judge 漏答某个维度按 0 计入，不会因缺席而抬高分数

## state_check（type: state）— 环境状态检查

```yaml
- type: state
  name: state_check
  config:
    expectations:
      - { type: file_exists,   path: "output.py" }
      - { type: file_contains, path: "output.py", value: "def main" }
      - { type: db_record,     table: "users", match: {"id": 1} }
```

`expectations` 为空 → `invalid` / `no_criteria_configured`。

## tool_calls（type: tool_calls）— 工具调用验证

```yaml
- type: tool_calls
  name: tool_calls
  config:
    required_tools: ["fs_read", "fs_write"]   # 期望调用集合
    forbidden_tools: ["bash"]                  # 禁止调用
    threshold: 1.0                             # 得分门禁
```

实际调用集合取自归一化观测（需要真实 TraceProvider + 正确的映射条目），不再按 span 名称子串匹配。评分是工具选择的 **F1**：

- 配了 `required_tools` → `score = F1(expected, actual)`；`details` 同时给 `recall`（既有消费字段，保留）/ `precision` / `f1`
- 只配 `forbidden_tools` → 无违规即 1.0
- 命中禁止工具 → 直接 0.0
- 多余调用被精确率反映：调齐了期望工具外加三个无关工具，召回率 1 而精确率 < 1，结论不再只由召回率给出
- 读不到工具调用计数（取证通道缺失）→ `invalid` / `evidence_unavailable`；trace 里确实没有工具 span → 真实 0 分且 `valid`

## transcript（type: transcript）— 转录分析

```yaml
- type: transcript
  name: transcript
  config:
    max_turns: 20        # 超出扣分
    max_tokens: 10000
    step_efficiency_threshold: null   # 显式声明才参与门禁
```

轮次数、token 量与调用冗余度全部来自归一化观测（token 为输入/输出之和，二者都读不到时才认 provider 直报的总数）。**测不出的分量不参与平均分**（也不按 0 计入），并在 `details.unavailable` 与解释里列名；全部分量都测不出时整条结论转 `evidence_unavailable`。

task 声明了 `optimal_steps` 时额外输出 `step_efficiency`（最优步数 / 实际步数）作为**诊断量**：默认不影响通过判定，只有配了 `step_efficiency_threshold` 才作为一个 0/1 分量参与门禁。

## artifact_check（type: artifact）— 产物检查

```yaml
- type: artifact
  name: artifact_check
  config:
    expected_type: "code_file"     # 期望产物类型（outcome.artifacts[].type）
    content_regex: "^#.*"          # 可选：产物内容正则
    threshold: 1.0
```

产物优先取被评测方自报的 `trial.outcome["artifacts"]`，其次才取归一化的产物观测。注意 OTel GenAI 规范**没有定义产物属性名**，因此 `artifact.type` / `artifact.id` / `artifact.content` 三个字段默认没有映射条目：宿主不经 `default_mapping({...})` 声明就读不到，此时只有 outcome 自报路径可用（框架不会拿相近的属性名凑数，也不会因此判 agent 失败——产物证据缺失按 §证据边界处理）。

## human（type: custom, name: human）— 人工评分

```yaml
- type: custom
  name: human
  config:
    instructions: "按正确性与清晰度打分"
```

**pending 语义**：评分请求落库（`human_score_requests` 表），该 trial 记为 pending（不计入通过率）；经 `POST /runs/{run_id}/human-scores` 回传分数后汇总重算。配合 Dashboard 或 API 做人工复核闭环。

## step_level（type: custom, name: step_level）— 步骤级评估

```yaml
- type: custom
  name: step_level
  config:
    expected_trace: ["fs_read", "fs_write", "bash"]   # 按索引对照的工具调用序列
```

从归一化的工具调用观测取实际序列（宿主换埋点词汇只需加映射条目，不改这里），逐位对照，报告**第一个错误步骤**，得分为 `正确步数 / 总步数`。名字读不到的调用**保留空槽**（`null`）而不是被跳过——索引对齐是这条 grader 的全部意义，该步按错误计入且在结果里可见原因。

## metric（type: metric）— LLM 质量指标分发

```yaml
- type: metric
  name: answer_relevancy      # 或 name: metric + config.metric_name
  config:
    thresholds: { "0.8": 1.0 }   # 可选，逐指标阈值覆盖
```

从 EvalRunner 注入的指标注册表分发（`answer_relevancy` / `faithfulness` / `context_recall` / `context_precision` 等）。未注册的指标名 → `invalid` / `unknown_grader`；指标因缺输入材料无法计算（如 faithfulness 无 `context`）→ `invalid` / `no_criteria_configured`；两者都**不得**折成 agent 的 0 分。

## 评分聚合策略（task 级）

```yaml
score_strategy: hybrid      # all_pass | weighted | hybrid（默认）
score_threshold: 0.7
```

- `all_pass`：所有 grader 通过才通过
- `weighted`：加权平均分 ≥ threshold
- `hybrid`：`required` 评分器必须全部通过 **且** 加权平均 ≥ threshold

## 查看注册表

```bash
curl http://localhost:8000/api/eval/graders     # 或独立部署 /v1/graders
```

返回全部可用评分器（name / type / description）。
