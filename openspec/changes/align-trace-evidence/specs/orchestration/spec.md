## Purpose

规定一次 trial 因何结束如何被记录与归类：终止原因的取值集合、预算触顶的处置、以及终止原因如何参与汇总而不被折叠进通过率数字。

## ADDED Requirements

### Requirement: 每次 trial 记录终止原因

每个 trial MUST 记录 `termination_reason`，取值范围为 `agent_completed`、`step_budget_exceeded`、`token_budget_exceeded`、`cost_budget_exceeded`、`timeout`、`agent_error`、`cancelled`。该值 MUST 由框架在编排过程中判定；被评测方提交的文本或自报状态 MUST NOT 单独决定该值。

#### Scenario: 正常结束

- **WHEN** agent 自行宣告任务完成且未触任何上限
- **THEN** 终止原因为 `agent_completed`，进入正常评分

#### Scenario: 被评测方自称完成但已触顶

- **WHEN** 被评测方返回「已完成」而该 trial 的步数已达上限
- **THEN** 终止原因为 `step_budget_exceeded`，而非采信自报的完成

### Requirement: 预算触顶立即停止并单独归类

步数、token、成本任一上限被达到时，框架 MUST 立即停止该 trial 并记为对应的预算原因。预算触顶属于任务约束未达成，MUST 与「评分判定为不通过」以及框架强制时限造成的 `timeout` 三者分别可辨。

#### Scenario: token 上限触顶

- **WHEN** 某 trial 累计 token 达到 `token_budget`
- **THEN** 该 trial 被停止且原因为 `token_budget_exceeded`，不计入通过，但计入预算触顶计数

#### Scenario: 超时与触顶不混淆

- **WHEN** 同一套件里既有超时结束的 trial 也有 token 触顶的 trial
- **THEN** 汇总中两者的原因计数不同，且超时者按评测不可信的无效通道归类、触顶者按任务约束未达成归类

### Requirement: agent 报错的归类不由框架猜测

当 trial 因被评测系统报错而结束时，`agent_error` 属于「agent 自身缺陷」还是「外部依赖不可达」MUST 由接入方显式声明；未声明时框架 MUST 将其归为结论不可信并标注需要人工判定，MUST NOT 默认折算为通过或不通过。

#### Scenario: 接入方未区分的报错

- **WHEN** runner 只抛出通用异常而未声明错误类别
- **THEN** 该 trial 记为需要人工判定的无效结论，并在汇总中单列

### Requirement: 终止原因分布进入汇总

run 与 task 两级汇总 MUST 给出各终止原因的计数；任何原因的占比 MUST NOT 被折叠进通过率或平均分，以避免「分数下降」掩盖「评测根本没跑完」。

#### Scenario: 大面积取消可见

- **WHEN** 一次 run 中 40% 的 trial 原因为 `cancelled`
- **THEN** 汇总显式呈现该占比，通过率旁同时给出有效样本数
