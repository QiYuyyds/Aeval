## Purpose

规定 pytest 套件门禁所判定的量与失败条件，使 CI 绿灯确实意味着 agent 表现达标且评测本身可信。

## ADDED Requirements

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
