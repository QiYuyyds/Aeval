## Purpose

规定 REST 汇总响应如何在不破坏既有结构的前提下携带统计口径、不确定性与有效性计数，使 API 消费方能够自行判断结论是否可信。

## ADDED Requirements

### Requirement: 汇总响应新增口径与不确定性字段

run 汇总响应 SHALL 在既有字段之外新增分母计数（`valid_trials` / `invalid_trials` / `pending_trials`）、每个 k 的估计方式（实测或外推）、置信区间、`worst_of_n` 与过程指标的 p50/p95；既有字段的名称与类型 MUST 保持向后兼容。

#### Scenario: 老客户端不受影响

- **WHEN** 一个只读取既有 `pass_at_k` 字段的客户端调用汇总接口
- **THEN** 请求成功且该字段仍存在，其值按修正后的口径计算

#### Scenario: 新客户端读取有效性

- **WHEN** 客户端读取一个存在判分器故障的 run
- **THEN** 响应中该 task 的 `invalid_trials` 大于 0，且对应 k 的条目带有置信区间

### Requirement: 能力清单声明统计口径版本

独立部署形态的 `/v1/meta` MUST 公布当前统计口径版本标识，使调用方能判断两个 run 是否可直接比较；每个已落盘 run MUST 记录产生它时所用的口径版本。「同一大版本内响应结构向后兼容」的既有承诺不豁免该声明义务——结构兼容而数值语义变化时，仍须显式公布。

#### Scenario: 调用方自检可比性

- **WHEN** 客户端准备比较两个历史 run
- **THEN** 它可通过 `/v1/meta` 与各 run 记录的口径版本判断两者是否可比

#### Scenario: 寄宿挂载形态

- **WHEN** 框架以宿主自选前缀挂载而非独立部署
- **THEN** 口径版本仍可通过该形态下的元信息接口取得
