# 证据对齐：OTel GenAI 词汇与 trial 终止原因

> 日期：2026-09-04

## Why

框架声称与宿主解耦（`tests/test_import_isolation.py` 用 AST 扫描禁止 `import app.*`），但**解耦只做到了 import 层，没做到数据词汇层**：`agenthub.*` 这套宿主私有属性名硬编码在 `core/metrics.py`、`graders/tool_calls.py`、`graders/step_level.py`、`graders/transcript.py`、`graders/artifact_check.py` 五处，而 OTel GenAI 的标准属性名（`gen_ai.*`）在整个代码库出现次数为零。span 的识别靠 `"tool.call" in name or "tool_call" in name` 子串猜。

后果不是「指标偏乐观」这种程度问题，而是**读不到**：任何按标准埋点的 agent 跑进 Aeval，token 数与工具调用数都会是 0，而 0 会被下游当成真实观测继续参与打分与聚合。README 现在把这件事写成「span 覆盖不全导致分数偏乐观」，诊断错位了。

同时这四个缺口挡住了 2026 主流 agent 指标的大部分：输入/输出 token 不分家就算不出成本、读不到工具入参就算不出参数正确性、没有 `termination_reason` 就分不清「预算触顶 / 超时 / 能力失败 / 基建故障」，而这三者恰是可信评测的分母前提。

## What Changes

- 引入一张**版本钉定的属性翻译表**，把 span 归一化为标准观测（工具名、工具成败、输入/输出/推理/缓存 token、会话标识、agent 名称与版本）。宿主私有词汇不再是框架内常量，而是翻译表里的一条可替换映射；`gen_ai.*` 作为默认映射随版本一起声明。
- 归一化字段的**「缺失」与「值为 0」在语义上分离**：缺失附带原因（属性不存在 / 规范版本不认识 / provider 未覆盖），并作为 `invalid` 证据参与统计口径（消费方为 `fix-stats-and-denominators` 定义的三态）。
- 暴露所用规范版本与翻译表版本，使历史 run 可以判断是否处于同一口径。
- 每次 trial MUST 记录 `termination_reason`；步数/token/成本预算触顶时立即停止该 trial，并与超时、agent 报错、被取消分类区分。汇总中 MUST 给出终止原因分布计数。
- **成本成为与通过率并列的轴**：输入/输出 token 分别计量、按可配置单价表折算 `cost_usd`、给 p50/p95；MUST NOT 折进任何单一复合分数。
- 工具类评分器改为只消费归一化观测；工具选择指标由单一 recall 扩为 precision/recall/F1；步数效率作为诊断量输出。
- suite 新增可声明字段：任务标签/类别/难度、最优步数与各类预算上限、以及**工具入参采集开关**。
- 工具入参与结果**默认不采集**（与 OTel 规范自身的 Opt-In 立场一致）：仅在 suite 显式 opt-in 且 provider 提供了该属性时可用；未开启时相关判定报「证据不可用」而非判失败；开启时 MUST 经默认启用的可替换脱敏钩子处理后方进入证据存储与展示层。
- 新增字段随 run 落盘；缺失这些字段的历史 run MUST 仍可读，并报告为 `unknown` 而非失败或 0。

## Capabilities

均为词表内既有能力的名称，`openspec/specs/` 下尚无文件，故按新能力锁定。

### New Capabilities

- `trace-provider`: span 到标准观测的翻译表、版本钉定与缺失原因报告
- `graders`: 评分器消费的归一化证据、入参 opt-in 与脱敏边界、证据缺失的表达
- `suite-format`: 任务级标签/难度/预算/采集开关声明与校验
- `orchestration`: 终止原因记录、预算触顶处置与终止原因分布汇总
- `statistics`: 成本与 token 分解、工具选择三元组、步数效率等派生指标
- `storage`: 新增证据字段的持久化与历史 run 的前向可读

### Modified Capabilities

（无。）

## Impact

- 代码：`core/metrics.py`（`extract_metrics` 全面改写为消费归一化观测）、新增翻译表模块于 `trace/`、`trace/phoenix.py`、`graders/tool_calls.py`、`graders/step_level.py`、`graders/transcript.py`、`graders/artifact_check.py`、`core/types.py`（`EvalTask` 新字段、`TrialResult.termination_reason`）、`core/suite.py`（校验）、`core/runner.py`（预算与终止原因）、`storage/sqlite.py`、`examples/mock_runner.py`
- **必须一并修正的自证循环**：`examples/mock_runner.py` 当前造的假 span 用的就是 `agenthub.*`，因此现有测试结构上不可能发现与标准脱钩。本变更要求 mock 同时产出「标准词汇」与「宿主词汇」两种 trace，并各自有一套通过的用例——否则测试套件会继续为私有一词背书。
- 测试：`tests/test_metrics.py`、`tests/test_builtin_graders.py`、`tests/test_graders.py`、`tests/test_runner.py`、`tests/test_suite.py`、`tests/test_eval_dataset_sources.py`、`tests/test_e2e.py`
- 文档：`docs/architecture.md` §3/§4、`docs/integration-guide.md`（TraceProvider 与属性映射实现示例）、`docs/yaml-format.md`（新字段）、`docs/grader-reference.md`
- 依赖：不引入 OTel SDK 作为硬依赖（翻译表是纯数据结构）；单价表为配置，无内置默认价目
- 兼容性：不改 `AgentRunner` 的返回契约（结构性破坏在后续「采集与评分分离」变更中处理）；`n_total_tokens` 保留为输入+输出派生别名以免 Dashboard 与历史 suite 立刻断裂
- 与前置变更的接缝：本变更多处产生 `invalid`/`unknown` 信号，依赖 `fix-stats-and-denominators` 先落地以承接其分母语义
