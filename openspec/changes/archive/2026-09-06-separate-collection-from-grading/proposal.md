# 分离证据采集与评分

> 日期：2026-09-05

## Why

现在一次 trial 的**全部证据都来自被评的一方**：`AgentRunner.run(task)` 返回 `(trace_id, transcript, outcome)`，其中 `outcome` 是宿主自己序列化的 dict，而 `state_check` 直接信任它——agent 想让 `files` 里出现什么，判据就判什么通过。`spans` 同理：token 与工具序列由被评方产生，评分层无从分辨"这是谁观测到的"。今天两次验收跑都印证了这条通路有多脆：29 次历史 trial 只有 5 次走到评分，而那 5 次的所有过程指标都是 0.0。

这不是"再加几个 grader"能绕过的：**三元组在类型上就没有地方表达"谁、在什么时候、通过哪条通道观测到的"**，因而也无法表达 phase 分离（teardown 之前由评测侧独立取证）与延迟评分（采集与判分解耦，可事后重评）。用户已批准在 v0→v1 之间破坏这个契约——宿主侧只有一个实现点（`AChatAgentRunner`）加框架自带 mock，v0.x 无外部部署方，破坏成本是一次性能付掉的。

这个变更是后续三件事的共同地基：coding-agent 赛道要的「评测侧独立取证」、指标目录要的「judge 换版本后重打分历史 run」、差异化项要的「对抗自检只重跑评分器不重跑 agent」。

## What Changes

- **BREAKING** `AgentRunner.run(task)` 返回结构化的 **trial evidence** 而非三元组：每类观测携带 `observed_by ∈ {harness, runner, subject}`，环境终态拆成「评测侧独立取证」与「被评方自报」两个互不替换的通道。
- **BREAKING** `EnvironmentManager` 增加**取证探针**：由框架在 teardown **之前**、在被评方不可写的通道上执行/采集；被评方自报的状态只能作为线索，不能单独判通过。
- 评分器必须**声明**它允许消费哪一级来源；未声明即不可信。默认规则：`subject` 级证据不能单独使 trial 通过。
- **采集与评分解耦**：证据先落盘成 run 的 evidence archive，判分在其后独立进行；支持对既有 run **重评分而不重跑 agent**，且每次评分记录其 grader/映射/规范版本，重评结果与原结论并列保存（不覆盖历史结论）。
- LLM 输入/输出正文与工具入参共用**同一套**按套件 opt-in + 默认脱敏机制（不新增第二种隐私开关）。
- **grader 崩溃、judge 不可用、判据未配置** 等已有 `invalid` 语义获得真正的出路：证据在手 → 可以只重跑评分。
- 明确不做：容器/沙箱、网络隔离、CLI 与 HTTP 的重评分入口（各自另立变更）。本变更让溯源**可声明**，不宣称溯源**可强制**——后者需要真正的环境隔离，属于覆盖面变更。

## Capabilities

### New Capabilities

- `extension-contracts`: 扩展协议本身的契约——接入方必须提供什么、证据的来源分级、以及协议层的兼容承诺（`openspec/specs/` 此前未固化过这一能力）

### Modified Capabilities

- `orchestration`: trial 生命周期改为「采集 → 停止 → 评分」三相；新增延迟评分与重评分的可观察行为；评分版本随每次判定落盘
- `graders`: 评分器只能读它声明过的来源级别；被评方自报证据不可单独判通过
- `storage`: 每次 trial 的证据存档、重评分历史与原结论并列保留、敏感证据的留存与彻底删除
- `suite-format`: 正文与工具入参共用一个采集声明（默认不采、按套件/任务 opt-in）

## Impact

- 代码：`core/contract.py`（`AgentRunner` / `EnvironmentManager` / `Grader` 三处协议）、`core/types.py`（`TrialResult` 承载证据与来源分级、每次判定的版本记录）、`core/runner.py`（三相编排、探针调度、重评分入口）、`graders/`（9 个内置实现逐个声明可消费来源；`state_check` 是最大改动：它现在读的正是被评方自报的 dict）、`storage/sqlite.py` 与 `storage/memory.py`（证据存档与重评分历史）、`examples/mock_runner.py`（第二个实现点，顺带成为新契约的参考样例）
- 宿主：`bitdance-agenthub` `backend/app/eval_integration/runner.py` 的 `AChatAgentRunner` 是**唯一外部实现点**；`graders/artifact.py`、`graders/dispatch.py` 两个宿主自有评分器需同步声明来源；宿主环境管理器需实现取证探针（AChat 有 workspace 文件与 DB，可先只做文件清单 + `sqlite/psql` dump 两条）
- 数据兼容：已归档的历史 run **无法重评分**——当时只存了三元组的产物，没有保留证据边界与来源标记，重评所需的信息在现场就已丢失。它们仍可读、但会被标为"不可重评"，且与新 run 不同口径（沿用 ① 的统计口径版本机制）
- 体积：正文采集打开后，单 trial 证据可达数十 KB 级（今天实测 `input.value` 均值 2,352 字符、最大 73,899），SQLite 归档需要留存策略
- 风险边界：本变更让「谁观测到的」进入类型系统，但**诚实性仍取决于接入方实现的探针**——若宿主把 `harness` 级证据也自己编造，框架无法从数据上分辨。真正的强制需要后续的环境隔离
