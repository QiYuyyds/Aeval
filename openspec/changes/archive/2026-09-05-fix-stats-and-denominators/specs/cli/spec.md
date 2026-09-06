## Purpose

规定 `eval-suite` 命令行向人呈现统计结论的口径，以及在什么条件下以非零退出码表达「不可放行」。

## ADDED Requirements

### Requirement: show 输出与统计口径一致

`eval-suite show` MUST 展示每个 task 与全局的 `valid` / `invalid` / `pending` 计数、`pass@1` 的 95% 置信区间，并对任何外推值给出显式标注。

#### Scenario: 阅读者能分辨实测与外推

- **WHEN** 一次 run 的 `pass@5` 来自 3 次实测的外推
- **THEN** 输出中该数值带外推标注，而 `pass@1..pass@3` 不带

#### Scenario: 无有效样本时不打印零

- **WHEN** 某 task 的有效 trial 数为 0
- **THEN** 输出显示为证据不足，而不是 `0.0%`

### Requirement: compare 在差异不显著时拒绝给方向性结论

`eval-suite compare` 在两次运行某项指标的置信区间重叠时 MUST 标注该差异不显著，并 MUST NOT 输出「退化」或「提升」的方向性结论；当两次运行的统计口径版本不同时 MUST 标注为不可比。

#### Scenario: 噪声不再被读成退化

- **WHEN** run A 的 `pass@1` 为 0.75、run B 为 0.80，且两者区间重叠
- **THEN** 输出标注差异不显著，该 task 不进入退化清单

#### Scenario: 跨口径对比

- **WHEN** 被比较的两个 run 由不同统计口径版本的 Aeval 产生
- **THEN** 输出显式标注两者不可比

### Requirement: 评测自身不可信时退出码非零

`eval-suite run` MUST 在有效样本不足以支撑结论时以非零退出码结束——包括 `invalid` trial 占比超过阈值，或任一 task 的关键统计量为 `insufficient_data`。该判定与「存在未通过任务」并列，两者都构成不可放行。

#### Scenario: 上游故障导致的空结果

- **WHEN** 一次 run 中 60% 的 trial 因判分器异常被判为 `invalid`
- **THEN** 命令以非零退出码结束，并在汇总中说明这是评测可信度问题而非 agent 表现问题
