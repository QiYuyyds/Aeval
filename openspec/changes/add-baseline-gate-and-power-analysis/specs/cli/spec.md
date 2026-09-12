# cli 变更（delta）

## ADDED Requirements

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
