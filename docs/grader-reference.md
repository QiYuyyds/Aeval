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
| `evidence` | `[harness, runner]` | 本判据**允许消费**的来源级别。默认不含 `subject`（被评方自报）；收紧为 `[harness]` 即「只认评测侧独立取证」，不能为空 |
| `allow_subject` | false | 逃生开关：显式打开后自报证据可单独支撑通过，该选择随 run 落盘且结论被标成弱证据 |
| `judgment_moment` | `at_end` | 环境状态类判据所依据的时刻：`at_end` / `not_at_end` / `any_time`；所用时刻随结论可见 |
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
| `evidence_level_mismatch` | 结论依据了本判据**未声明**的来源级别（如声明只认 `harness` 却用 `runner` 级观测打了分） |
| `subject_only_evidence` | 结论只由被评方自报证据支撑，且套件没写 `allow_subject` |

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

## 证据分级取信 — 谁观测到的决定这条结论值多少

一次 trial 交付的每条读数都带 `observed_by`：`harness`（评测侧在环境停止前独立取证）> `runner`（接入适配层交付）> `subject`（被评 agent 自己写出的内容）。评分器只能消费它**声明**过的级别，两条默认规则由框架统一执行：

| 规则 | 触发 | 结果 |
|------|------|------|
| 自报不得单独定案 | 通过结论只由 `subject` 级支撑且未写 `allow_subject` | `invalid` / `subject_only_evidence` |
| 未声明即读不到 | 结论依据了 `evidence` 里没有的级别 | `invalid` / `evidence_level_mismatch` |

两条的文案互相区分，因为该修的东西不同：一条要补取证通道，一条要改声明。被改写的原结论保留在 `details.rejected_score` / `rejected_explanation` 里 —— 无效不等于什么都没发生。

```yaml
# 对抗/外部场景: 只认评测侧独立取证
- type: state
  name: state_check
  evidence: [harness]
  judgment_moment: at_end
  config:
    expectations: [{ type: file_exists, path: "output.py" }]

# 逃生舱: 允许自报单独支撑通过 (会留下弱证据标记)
- type: state
  name: state_check
  allow_subject: true
  evidence: [harness, runner, subject]
```

**结论与汇总都披露分量**：`grader_results[].evidence_levels` 是这条结论依据的级别，`trial.weakest_evidence` 是支撑它的最弱一级，`TaskSummary.evidence_levels` / `RunSummary.evidence_levels` 是 valid trial 按最弱一级的计数，`subject_only_trials` 单列弱证据通过数。同样一个 1.0，「全靠 agent 自述」与「评测侧自己看过环境」在报告里必须看得出来。

**环境状态类判据要声明判定时刻**（`judgment_moment`，默认 `at_end`）：

| 取值 | 含义 | 只有一次结束态取证时 |
|------|------|---------------------|
| `at_end` | 结束时成立 | 正常判定 |
| `not_at_end` | 结束时不成立（断言已被清掉） | 正常判定（期望取反） |
| `any_time` | 历史上任一时刻成立 | **报证据不足** —— 不得拿结束态冒充全时段观测 |

取证可以在运行中发生，所以「终态」不再只有一个时刻：中途建完又删掉的文件，在默认 `at_end` 下就是不成立 —— 只取最后一次快照会把它判成通过，而发现这种情况正是这类检查存在的理由。反过来，「任一时刻」需要评测侧真的在运行中取过证（≥2 次读数）才判得了。所用时刻随每条结论落盘（`grader_results[].judgment_moment`）。

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

**敏感证据（一套声明，两个字段）**：`capture.tool_arguments`（工具入参/结果）与 `capture.model_content`（模型输入输出正文）默认都关，可按 suite 或 task 逐字段开启；两者共用同一套语义 —— 默认不采、显式 opt-in、开启后强制经过默认脱敏、所用处理的标识随 run 落盘。关闭时对应槽位是 `{"missing": true, "reason": "capture_disabled"}`，任何依赖它的判定报证据不可用而**不判 agent 失败**；两个字段的状态各自独立，`run.evidence.capture_by_task` 与 `capture_content_by_task` 分别记录逐 task 生效值。体积实测：默认口径下单条 trial 的归档证据约 2 KB，开启正文并经默认摘要脱敏后几乎不变（正文被换成定长摘要）；只有换成 `IdentityEvidenceRedactor` 这类保留明文的钩子时才会涨 —— 实测 2,352 字符正文约 9 KB、73,899 字符约 224 KB。详见集成指南 §8。

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
- 判分输入对同一份归档证据**确定**：同一批字节在任何进程里重评都构造出逐字节相同的提示词（自 `implementation_version` 3 起；此前工具清单按集合迭代序拼接，跨进程会变）
- 示例 JSON 里的预填值（`{"quality": 0.0}`）是框架自己写进提示词的一个**显式锚**，它对结论有没有影响现在**可测**：见下方「判分呈现探针」（`run_presentation_probes`）。默认呈现逐字节未变，`implementation_version` 因此仍是 3

## state_check（type: state）— 环境状态检查

```yaml
- type: state
  name: state_check
  evidence: [harness, runner]   # 默认: 不采信被评方自报
  judgment_moment: at_end       # 默认: 判结束态
  config:
    threshold: 1.0
    expectations:
      - { type: file_exists,   path: "output.py" }
      - { type: file_contains, path: "output.py", value: "def main" }
      - { type: file_regex,    path: "main.py", value: "^import " }
      - { type: db_record,     table: "users", match: {"id": 1} }
      - { type: no_conflict_markers, path: "merged.py" }
```

这条判据以前读的是 `trial.outcome` —— 也就是被评方自己序列化上交的那个字典：agent 想让 `files` 里出现什么，判据就判什么通过。现在它按级别取信：

- 先问**评测侧取证**（环境管理器的 `probe()` 在 teardown 之前读到的清单 / dump），其次问**适配层交付**的终态；
- 被评方自报（`subject`）默认不进入判定，只有 `allow_subject: true` 才行，且那条通过会被标成弱证据；
- 一条期望被哪一级满足，就记在 `evidence_levels` 里；`details.expectations[]` 逐条给出 `supported_by` 与 `consulted`，可以复核到底是「评测侧看到了」还是「它自己说的」；
- 被允许的那几级**一条读数都没有**时 → `invalid` / `evidence_unavailable`，解释里点名缺哪一级（而不是拿空清单判「环境里确实没有」，也不是打一个看起来正常的 0 分）。

期望类型读的是状态字典里的 `files`（`{路径: 内容}`）与 `db_records`（记录列表）两类键；状态读数由接入方/探针自己组织，框架不规定环境长什么样。`expectations` 为空 → `invalid` / `no_criteria_configured`。

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

从 EvalRunner 注入的指标注册表分发（`answer_relevancy` / `faithfulness` / `context_recall` / `context_precision` 等）。未注册的指标名 → `invalid` / `unknown_grader`（错误文案列出已注册指标名）；指标因缺输入材料无法计算（如 faithfulness 无 `context`）→ `invalid` / `no_criteria_configured`；两者都**不得**折成 agent 的 0 分。

0.3.0 起：

- 指标经宽签名测量（`measure(ctx: MeasurementContext)`），判据引用指标即**升格**为判分量 —— 结论的 `details` 携带该指标的角色（`metric_role`）与取信声明（`metric_evidence_declaration`）
- 轨迹类指标默认注册为 `diagnostic`（仅诊断块，不进任何分母）；判据里显式引用即升格进判分流程，并从诊断块移入判分块
- 未升格的指标经 task 级 `diagnostic_metrics` 声明仅诊断运行（语法见 yaml-format）

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

## 门判据与乘性合成（0.3.0）

判据可声明为门（`gate: { factor }`）并把 task / suite 的 `reward_basis` 设为 `multiplicative`（语法与校验规则见 yaml-format）。门结论随 run 落盘、报告与 API 可见，三个专用字段：

| 字段 | 含义 |
|------|------|
| `gate_factor` | 声明的因子（0-1）；`None` = 非门判据（历史 run 亦为 None） |
| `gate_applied` | 因子是否已乘入总分（仅 multiplicative 且门判为失败时 true） |
| `gate_reason` | 生效/未生效原因：门失败乘入 / 门通过未乘 / 证据不足未生效 / additive 未启用 |

门的判定走判据自己的取信声明（`evidence` / `allow_subject` / `judgment_moment`）—— 不新造第二套证据机制；subject 级自报证据不得触发门（门判据声明 `allow_subject` 直接被装配期拒绝）。报告的「门 (gate) 结果」一节汇总每个门的因子与生效次数。

塌缩后的 **trial 总分**落盘在 `synthesized_score`（汇总 `avg_score` 即对它取均值，CLI 下钻与 `/runs/{id}/trials` 读它）。API 中既有的 `trials[].score` 仍是 grader 简均（为兼容保留语义不变），所以在门塌缩的 run 上两者本就应当不同——看总分读前者。

两条只能靠真跑才暴露的写法，`eval-suite validate` 不会拦：

- **`name` 是注册表键，不是标签**。判据 `name` 必须命中已注册的评分器名（`code_based` / `transcript` / `metric` / `state_check` …）；写了自造名 → 每个 trial 判 `invalid` / `unknown_grader`，门根本没评到。同理，同一内置评分器在一个 task 里只能出现一次（结论按 `name` 建键，第二条会覆盖第一条）——要多条确定性检查，就把它们写进同一个 `code_based` 的 `checks` 列表，或改用不同类型的评分器。
- **门的检查通道要能判别**。`code_based` 的 `target: transcript` 转储的是**整段对话含任务 prompt**：若禁值本身出现在 prompt 里，`not_contains` 会无条件失败，把被评方没做错的行为判成泄漏（要对 agent 自己的消息取判，只能用 `regex` 加角色锚点）。而 `target: outcome` 读的是 subject_state 通道，缺省取信声明（harness+runner）不含 subject，门又不得开 `allow_subject` —— 结果是读到空文本而**假通过**。写门时先确认它读的那条通道在自己的 `evidence` 声明下真的有内容。

## 多评分者判据（0.3.0）

判据配置 ≥2 个 `judges` 定义后，每个定义对同一批 trial 独立评分；结论的 `rater_scores` / `rater_ratings` 按 trial 对齐，run 汇总的 `agreement` 块报告 Cohen's κ（两评分者无缺失）或 Krippendorff's α（≥2 评分者或含缺失），并附一致/分歧计数。评分者不足或对齐样本过少 → 值为 `None` + 原因（insufficient_data 语义），不伪造数值。

配 κ/α 时有两个实践前提：**对齐样本 ≥ 5**（`MIN_ALIGNED_RATINGS_FOR_AGREEMENT`；对齐样本 = 有 ≥2 个评分者都给出判定的 trial 数，所以 3 次 trial 的两评分者面板只会拿到 `insufficient_data`），以及**各评分者的阈值要跨过指标实际分数带**——阈值都落在分数带同一侧时评分者永远判定一致，κ 退化成 1.0，量不出分歧。

## 新判定时刻：`after_last_event`（0.3.0）

`state_check` 的 `judgment_moment` 新增取值 `after_last_event`：只依据**最后一个注入事件之后**的环境读数判定 —— 「建完文件又删掉」这类被事件中断的时序不再需要只看终态。取值集合变为 `at_end`（默认）/ `not_at_end` / `any_time` / `after_last_event`，新增值非破坏，历史结论读回不变。

两条证据不足边界（都不静默退化）：

- 声明了 `after_last_event` 但该 trial **没有注入任何事件** → 证据不足，缺失原因 `no_event_injected`；
- 有事件但最后事件之后没有环境读数 → 证据不足，缺失原因 `no_state_reading_after_event`。

## 轮级过程量只进诊断块（0.3.0）

多轮 trial 的轮级过程量（声明/消费轮数、注入事件数、人工介入数、会话结束原因 `end_reason`）随 trial 落盘为 `session_diagnostics`，只呈现在报告的诊断块（CLI `show --verbose` 展开处），**不进通过率、pass^k、任何分母** —— pass@k 的分母是 trial 数，不是轮数。

## 模拟器读数的来源级别（0.3.0）

用户模拟器产出的每一条话术、注入的环境事件、人工介入消息都以 `observed_by: harness` 带 `observed_at` 时刻进入 transcript 证据（通道分别为 `simulated_user` / `environment_event` / `human_message`），与普通消息可区分。来源记 harness 而非 runner 的理由：**轮次的供给方是评测侧框架**（框架经模拟器协议供给、经会话句柄交付），不是被评方或接入适配层自报 —— 模拟用户说「我确认修好了」不该被当成被评系统完成了什么。判据取信模拟话术不需要 `allow_subject` 放行。

## 判分呈现探针 —— 同一个 judge 对无关呈现敏不敏感（0.3.0）

κ 高不等于准：两个 judge 若共享同一个偏置，它们**一致地错**，而评分者间信度对此完全无感。呈现探针问的是第三类量 —— **同一个 judge 在不该影响结论的呈现变化下，结论稳不稳**。

它是**库层入口**，无 YAML / CLI / REST / 看板表面、不落库：探针的配置形状正是第一次测量要 inform 的东西，在量过一次之前把它写进 YAML 等于承诺一个没验证过的形状。

```python
from agent_eval.graders.model_based import ModelBasedGrader
from agent_eval.graders.presentation_probes import (
    presentation_probe_cost_quote,
    run_presentation_probes,
)

quote = presentation_probe_cost_quote(n_trials=len(trials), sample=0.5)
# → {'variants_per_trial': 4, 'trials_sampled': 3, 'judge_calls': 12}
report = run_presentation_probes(
    ModelBasedGrader(llm_fn=judge),      # 与生产同一把 judge、同一份判分口径
    task=task,
    trials=trials,
    sample=0.5,                          # 必填：内核不给默认抽样比例
    seed=20260922,                       # 必填：抽样本身要可复现
)
for name, op in report.by_operator.items():
    print(name, op.status, op.flipped_trials, op.reason)
```

探针按定义是 judge 调用的乘法（默认两个算子 = 每个抽中 trial 4 次调用：基线 1 + 锚定变体 2 + 逆序变体 1），所以**报价给在调用点**，不藏进配置。跑之前先报价、跑完之后记实耗。

### 算子 = 一句「保持什么不变」+ 一份变体构造

一个呈现算子必须声明它保持的不变量；**说不清保持什么的改动在构造期就被拒绝**（`ValueError`），不留到运行时产出一个没人读得懂的数。协议里**没有**第三方注册面（无 entry-point 组）—— 泛化性由第二个算子证明，不由扩展点承诺。判读表（七个候选，纳入两个）：

| 候选算子 | 保持的不变量 | 判定 |
|---|---|---|
| `anchor_value` | 模板示例值只是格式说明，不该进分数 | **纳入**，首个 |
| `dimension_order` | 各维度独立打分后取平均；**求和次序造成的末位浮点差异不计为呈现敏感** | **纳入**，第二个（带此限定） |
| `irrelevant_prefix` | 无关内容不该改变结论 | 不纳入：构造「无关但等长」的前缀本身就是新变量 |
| `head_trunc` / `tail_trunc` | —— 轨迹的时间序**就是**语义 | 不纳入：破坏不变量，测出来无法归因到呈现 |
| `label_polarity` | 说不清保持什么 | **拒绝成为算子** |
| `paraphrase` | 引入了改写器这个新变量 | 不纳入 |
| `candidate_order` | 无宿主（框架内没有成对比较判据） | 无处安放 |

`dimension_order` 那句限定不是脚注：`_parse_scores` 按 `for dim in dimensions` 建字典，于是 `sum(scores.values())` 的求和序就是呈现序，而浮点加法不可结合。框架在同一份配置下今天是确定性的、没有待修缺陷 —— **为一个测试工具去改生产算术是反的**，所以求和方式原样保留，改由算子声明与报告输出携带那句限定（`tests/test_presentation_probes.py::TestProbeOffChangesNothing::test_summation_order_follows_the_presentation_not_the_argument`）。

### 测结论翻转，不测分数漂移

进分母的是**通过/失败**：只有结论跨过 `threshold` 才算翻，与统计口径同源。分数移动量作为另一维度单独呈现（`max_score_shift` / `mean_score_shift`）—— 它决定「要不要修」的紧迫度，不决定「敏不敏感」的答案。0.78 → 0.72 而阈值 0.7 不算翻转。

呈现不变性与两类既有信度量各占一个类型、各报各的数，**不得合成一个综合信度分**：`AgreementReport`（两个评分者之间）、单评分者多采样的 `confidence`（自一致）、`PresentationInvarianceReport`（同一评分者的不同呈现）。三态措辞固定，不共用一个空值：

- `sensitive` —— 检出结论翻转。翻了几判是直接观察到的事实，样本不足也不能把它读成「没翻」；
- `not_detected` —— 测过了且未检出，**这是一条结论**，且只在算子声明的那个不变量内成立；
- `not_computable` —— 没测出来：judge 不可用（含无凭证）、无可读读数，或对齐样本 < `MIN_ALIGNED_RATINGS_FOR_AGREEMENT`。把「未检出」与「不可计算」混成一个空值，等于把没测过伪装成已排除。

### 探针不产出结论（硬规则）

探针的读数不是判定条目：不产生 `GraderResult` / `TrialResult`、不进 `grade_attempts`、不移动 `current` 指针、不进任何分母或门禁，也不给 `CALIBER_AXES` 添第六根轴（探针不产出 verdict，没有翻判要归因）。一旦混进判定序列，`verdict_drift` 就会把一次呈现扰动读成「judge 换代翻了 N 个 trial」，伪造出审计结论。守护在 `tests/test_presentation_probes.py`，并用一次测试侧的泄漏模拟证明它看得见泄漏（把「注回缺陷再确认变红」的常规手法放进测试而不是生产代码，因为那条「缺陷」恰好是 spec 明令 MUST NOT 的行为）。

### 这套数字证明什么、不证明什么

机制双向钉死用两个构造性替身：判据按定义对锚定值反应的替身**必须**被报出敏感；只读证据正文的替身**必须**不被误报。两者合起来只证明机制有效，**不证明真实 judge 敏感或不敏感**。真实锚定敏感度要等一把可用的 judge 凭证（宿主四把候选截至 2026-09-22 全为 401/402）；有凭证后直接重跑 `examples/presentation-probes/measure_presentation_sensitivity.py`，不需要新的设计决定。

零凭证下已经量到的一条实现事实：在 judge 真会给出的一位小数分值上（长度 ≤ 5 的全部组合，即判据的实际维度数量级）与两位小数的三元组全部排列上，逆序求和与正序**逐位相同** —— 所以 `dimension_order` 这一轴今天构造不出由算术引起的结论翻转；限定语仍随报告输出，因为下一个人无法自行推断这件事，他只能相信报告说的话。