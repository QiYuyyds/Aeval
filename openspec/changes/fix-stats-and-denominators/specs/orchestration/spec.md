## Purpose

规定一次 trial 的结论如何分类：哪些情况属于 agent 未通过评测，哪些情况属于评测本身没有产生有效结论，并要求后者的原因可被查询与计数。

## ADDED Requirements

### Requirement: 评测侧失败归类为无效而非零分

当 trial 的评分过程未能产生有效判定时，该 trial SHALL 归类为 `invalid`，且 MUST NOT 计入任何通过率的分子或分母。未注册的 grader、评分器抛出异常、评分器超时、LLM 判定调用失败、LLM 判定输出无法解析，均属评测侧失败；任何评测侧失败 MUST NOT 被折算为 0 分或半分。

#### Scenario: 判分器崩溃

- **WHEN** 某 trial 的一个 grader 抛出异常
- **THEN** 该 grader 结论为 `invalid` 并携带原因文本，该 trial 不计入通过率
- **AND** 整场 run 继续正常完成而不崩溃

#### Scenario: 解析失败不再给半分

- **WHEN** LLM 判定返回的内容无法解析出任何维度分数
- **THEN** 结论为 `invalid`，而不是各维度 0.5

#### Scenario: 部分维度缺席

- **WHEN** LLM 判定只返回了配置维度中的一部分
- **THEN** 缺席维度按 `invalid` 处理，且维度平均分的分母仍为配置的全集维度数

#### Scenario: 依赖未满足保持既有语义

- **WHEN** 某 grader 因前置依赖未通过而被跳过
- **THEN** 其结论仍按既有语义记 0 分并说明依赖原因，不归类为 `invalid`

### Requirement: 超时归类为无效

trial 因超过单次执行时限而结束时，该 trial SHALL 归类为 `invalid` 并记录原因为超时，MUST NOT 与「评分判定为不通过」共用同一结论。

#### Scenario: 超时不等于失败

- **WHEN** 一个 trial 超时
- **THEN** 它计入 `invalid_trials` 且原因为 timeout
- **AND** 它不出现在该 task 的失败 trial 清单中

### Requirement: 未配置的判据不得自动得满分

当评分器已在 task 上配置但不含任何判据时，该评分结论 SHALL 判定为 `invalid` 并说明未配置判据，MUST NOT 返回满分或判为通过。

#### Scenario: 空的确定性检查列表

- **WHEN** 一个 `code` 类评分器的 `checks` 列表为空
- **THEN** 结论为 `invalid`（原因为未配置判据），而不是 1.0 自动通过

#### Scenario: 缺少期望轨迹

- **WHEN** 步骤级评分器没有配置 `expected_trace`
- **THEN** 结论为 `invalid`，且该 task 的通过率分母相应排除这次试验

#### Scenario: 空状态期望列表

- **WHEN** 环境状态检查评分器的 `expectations` 列表为空
- **THEN** 结论为 `invalid`，并出现在配置的告警清单中，提示该 grader 形同未挂载

### Requirement: 无效结论必须可追溯

每个 `invalid` 结论 MUST 保留其证据与原因文本并可通过 run 查询接口取回；系统 MUST NOT 因判定无效而丢弃该 trial 已采集的 transcript、过程指标或产物。

#### Scenario: 事后排查判分器故障

- **WHEN** 一次 run 结束后有人查询某个 invalid trial
- **THEN** 返回结果包含异常原因、涉及的 grader 名称与该 trial 的原始证据

### Requirement: pending 与 invalid 是不同状态

等待人工评分的 trial SHALL 保持 `pending` 语义而不被改写为 `invalid`；人工评分回传后，该 trial MUST 重新参与汇总计算并转为 `valid` 或 `invalid`。

#### Scenario: 人工评分回传后重算

- **WHEN** 一个 pending trial 收到人工分数
- **THEN** 所属 task 的通过率与分母计数被重算，该 trial 从 pending 计数中移出
