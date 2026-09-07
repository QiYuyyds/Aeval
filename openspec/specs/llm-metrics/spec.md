# llm-metrics Specification

## Purpose
规定 LLM 指标如何取信证据并产出分值（测量上下文、按声明读取轨迹、轨迹指标默认仅诊断），以及 pytest 套件门禁所判定的量与失败条件，使 CI 绿灯确实意味着 agent 表现达标且评测本身可信。

## Requirements

### Requirement: 门禁判定单次成功率的实测口径

`--eval-suite` 配合 `--eval-threshold` 的门禁 SHALL 以修正后的 `pass@1`（有效 trial 中的成功比例）作为判定量，其分母 MUST 排除 `invalid` 与 `pending`。

#### Scenario: 蒙对一次不再放行

- **WHEN** 某 task 的 3 个 trial 只有 1 次成功而阈值为 0.7
- **THEN** 门禁判该 task 未达标（此前会因 `pass@1 = 1.0` 被放行）

#### Scenario: 全 pending 不放行

- **WHEN** 某 task 的全部 trial 都在等待人工评分
- **THEN** 门禁判为未达标并给出「证据不足」的原因，而不是静默通过

### Requirement: 门禁在评测不可信时失败

当本次运行的 `invalid` trial 占比超过门禁允许上限时，pytest 会话 MUST 以非零退出码结束，并输出该失败源自评测侧而非 agent 表现。

#### Scenario: 判分器大面积异常

- **WHEN** 一次套件运行中判分器异常率超过门禁上限
- **THEN** 会话失败并提示检查评测配置，而不是按 agent 表现给出结论

### Requirement: 指标测量接收证据感知的测量上下文

指标测量入口 MUST 接收一个测量上下文对象，其中包含任务输入、从该 trial 的 `TrialEvidence` 派生的观测（带来源分级与采集时刻）以及套件侧对该指标的声明；旧的五字符串签名（`input / actual_output / expected_output / context / retrieval_context`）MUST 被移除，不保留双路径。指标声明了取信级别之后，上下文 MUST 只交付该级别范围内的观测——与评分器的取信声明同一套语义。

#### Scenario: 指标读取已声明的轨迹观测

- **WHEN** 一个指标声明取信 `transcript`（runner 级）并测量一个有完整轨迹的 trial
- **THEN** 测量上下文中的轨迹观测包含该 trial 的消息序列，且每条观测带 `observed_by` 与采集时刻
- **AND** 指标基于这些观测计算出的分值随结论落盘

#### Scenario: 未声明的通道不交付

- **WHEN** 一个指标未声明 `harness_state` 通道，而该 trial 存在 harness 读数
- **THEN** 测量上下文中不出现 harness 读数
- **AND** 指标若尝试声明一个不存在的取信级别，装配期报错并列出可选级别

#### Scenario: 旧签名不被接受

- **WHEN** 一个按旧五字符串签名实现的指标被注册进运行
- **THEN** 装配期报错并说明新签名形状，不得静默降级为只传字符串

### Requirement: judge 指标按声明读取轨迹

LLM judge 类指标 MUST 在声明包含轨迹通道时，把完整轨迹（而非仅最终输出文本）注入 judge 提示词；未声明时 judge 提示词 MUST 与今天等价（仅最终输出）。轨迹内容经既有脱敏机制后才进入提示词。

#### Scenario: judge 看到完整轨迹

- **WHEN** 一个 judge 指标声明取信 `transcript` 并测量一个多轮 trial
- **THEN** judge 提示词包含逐条消息的轨迹内容
- **AND** judge 结论附带的解释能引用轨迹中的具体事件

#### Scenario: 未声明轨迹的 judge 行为不变

- **WHEN** 一个 judge 指标未声明任何轨迹通道
- **THEN** judge 提示词只含最终输出（与 0.2.0 等价），不出现轨迹内容

### Requirement: 轨迹指标默认仅诊断

指标目录中的轨迹类指标 MUST 默认标记为诊断量（diagnostic）：出现在报告与能力清单中，但 MUST NOT 进入通过率分子分母、MUST NOT 参与判分。升为判分量（judging）MUST 由套件显式声明；升格后该指标按同一套取信声明与门机制运作。

#### Scenario: 诊断指标不进分母

- **WHEN** 一个套件启用了轨迹指标但未将其升格为判分量
- **THEN** 该指标的分值出现在报告的诊断块中
- **AND** 通过率、pass^k 与各 task 分母与未启用该指标时完全一致

#### Scenario: 显式升格后参与判分

- **WHEN** 套件把某个轨迹指标显式声明为判分量
- **THEN** 该指标经 grader 适配进入判分流程，结论携带其证据声明
- **AND** 汇总结构中该指标从诊断块移入判分块
