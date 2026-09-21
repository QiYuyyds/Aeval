# cli Specification

## Purpose
规定 `eval-suite` 命令行向人呈现统计结论的口径，以及在什么条件下以非零退出码表达「不可放行」。

## Requirements

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

### Requirement: run 的基线相对门在显著变差时以非零退出码失败

`eval-suite run` SHALL 接受 `--baseline <run_id>`：本次 run 完成后与基线 run 做基线比较（statistics 的显著变差语义）。显著变差 → 非零退出码并在输出中给出两 run 的 `pass@1` 与区间；不可比 → 非零退出码并给 `not_comparable_reason`（宁可红不可哑）；区间重叠或新值更高 → 退出码 0 且输出明确写"与基线差异不显著"。不传 `--baseline` 时行为与不传前逐字节一致。

#### Scenario: 显著变差置非零退出码

- **WHEN** 新 run 与基线可比且 `pass@1` 区间不重叠、方向向下
- **THEN** 退出码非零，输出包含两侧区间与"显著变差"结论

#### Scenario: 差异不显著时退出码为零且如实呈现

- **WHEN** 两 run 可比但区间重叠
- **THEN** 退出码 0，输出呈现两区间并注明"差异落在噪声内"

#### Scenario: 不可比宁可红不可哑

- **WHEN** 基线 run 与本次 run 的统计口径或证据边界不同
- **THEN** 退出码非零，输出 `not_comparable_reason`——不静默放行也不静默失败

### Requirement: pytest 插件的基线门与 CLI 同语义

pytest 插件 SHALL 接受 `--eval-baseline <run_id>`：套件评测完成后按同一显著变差语义置 `session.testsfailed`；不可比同样失败并给原因。与既有 `--eval-threshold`（绝对阈值）可同用——任一门失败即失败，输出区分是哪个门触发。

#### Scenario: 基线门触发 testsfailed

- **WHEN** `--eval-baseline` 指向的 run 与本次评测结果构成显著变差
- **THEN** `session.testsfailed` 被置位，terminal summary 报告基线比较结论

#### Scenario: 与绝对阈值门并存

- **WHEN** 同时传 `--eval-threshold` 与 `--eval-baseline` 且只有阈值门触发
- **THEN** 退出码非零且输出指明触发的是阈值门而非基线门

### Requirement: power 子命令输出样本量规划

`eval-suite power` SHALL 支持 `--delta`（假设基线 p）与 `--from-run <run_id>`（实测基线）两种问法，按 statistics 的功效分析口径输出 N、所用公式、假设与局限；输出呈现给 CI 与人读，不要求机器可解析的稳定性承诺。

#### Scenario: delta 问法

- **WHEN** `eval-suite power --delta 0.03 --p 0.7`
- **THEN** 输出达到 ±3% 半宽所需的有效 trial 数及公式假设

#### Scenario: 无有效样本的 from-run 如实报告

- **WHEN** `--from-run` 指向的 run 没有任何有效 trial
- **THEN** 报告证据不足并退出非零，不以 0 或 1 代算

### Requirement: 命令行运行的套件其扩展点必须真正生效

从命令行运行一份套件时，框架 MUST 把发现到的环境管理、自定义评分器与用户侧模拟器注入其所使用的运行时，使这些扩展点在该次运行中确实参与判定 —— 命令行 MUST NOT 成为一个「能解析套件却拿不到环境与自定义判据证据」的降级入口。声明了环境检查判据而本次运行没有装配任何环境时，MUST 报证据不足并指明缺的是环境装配，MUST NOT 按「文件不存在」判被评方失败。

#### Scenario: 命令行跑一份带环境的套件

- **WHEN** 用户用命令行运行一份引用已安装包提供的自定义判据与环境的套件
- **THEN** 该判据被执行、环境被真实搭建，结论携带各自的证据声明
- **AND** 用户无需编写任何构造运行时的代码

#### Scenario: 有状态判据却没有环境

- **WHEN** 套件声明了环境状态检查判据，而运行环境里没有任何环境管理被装配
- **THEN** 该判据报证据不足并说明未装配环境
- **AND** 该 trial 不被折成 agent 表现失败

### Requirement: 可用扩展点清单同时面向命令行与接口

命令行 MUST 能列出本次运行真正可用的扩展点及其来源（内置的与被发现的，各标注其注册来源包），接口侧的能力清单 MUST 报告同一份注册结果；两处 MUST NOT 出现一个看得见、另一个用不上的分歧。引用了清单上不存在之名字的套件，其失败信息 MUST 列出当前可用名字。

#### Scenario: 清单与运行一致

- **WHEN** 同一份安装分别被命令行清单与接口能力清单查询
- **THEN** 两处给出的自定义判据与环境名字集合相同，且每个名字都能在真实运行中被解析

#### Scenario: 名字打错时给得出可选项

- **WHEN** 套件的判据引用了一个未注册的名字
- **THEN** 失败信息列出当前可用的名字，并指出该次运行是否曾尝试加载某个外部包
