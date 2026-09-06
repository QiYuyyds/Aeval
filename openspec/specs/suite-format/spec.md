# suite-format Specification

## Purpose
规定套件文件格式中与证据和预算相关的可声明内容：任务标签与难度、最优步数与各类预算上限、以及工具入参采集开关，并定义它们的校验与默认值。

## Requirements

### Requirement: 任务可声明标签、难度与预算上限

task MUST 支持声明 `tags`、`category`、`difficulty`、`optimal_steps`、`step_budget`、`token_budget`、`cost_budget`。套件加载时 MUST 校验取值合法性，并在错误信息中给出具体字段路径。

#### Scenario: 非法预算值

- **WHEN** 某 task 的 `step_budget` 为 0 或负数
- **THEN** 加载失败并指出该 task 的该字段，错误信息含文件路径与字段路径

#### Scenario: 全部新字段可缺省

- **WHEN** 加载一份不含任何新增字段的既有套件
- **THEN** 加载成功，且所有行为与本变更前一致

### Requirement: 工具入参采集按套件显式 opt-in

suite 与 task MUST 支持声明是否采集工具调用入参与结果，默认 MUST 为关闭，且该声明 MUST 出现在 run 结果中以便复核当时的证据边界。task 级声明 MUST 能覆盖 suite 级声明。

#### Scenario: 默认关闭

- **WHEN** 套件未声明采集开关
- **THEN** run 记录中标注入参未采集，任何需要入参的指标报证据不足

#### Scenario: 单任务收紧

- **WHEN** 套件开启采集而某个 task 声明关闭
- **THEN** 该 task 的入参不被采集，且 run 记录反映该差异

### Requirement: 预算声明只用于终止与归类

预算类字段 MUST 仅用于决定 trial 的终止与归类，MUST NOT 隐式改变通过判定的计分方式；效率与成本类结论与通过率保持为并列呈现的独立量。

#### Scenario: 超预算不扣分于其他维度

- **WHEN** 某 trial 步数超过 `optimal_steps` 但任务判定为成功
- **THEN** 步数效率作为独立诊断量呈现，该 trial 的通过结论不受其影响
