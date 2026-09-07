# ④ agent 指标目录：让指标读证据、可互证、可设安全门

> 日期：2026-09-06

## Why

③ 之后，证据是框架的一等公民（`TrialEvidence` 三通道 + 来源分级 + 判定时刻），但指标层还停在 ③ 之前的世界：

- `Metric.measure()` 的签名是五个字符串（`input / actual_output / expected_output / context / retrieval_context`）——指标**拿不到证据对象**：工具调用序列、步骤读数、带时刻的环境状态全部不可见。凡是"过程质量"类指标（绕路、自纠、工具效率）在当前协议下无法表达。
- LLM judge **只看最终输出文本**。agent 怎么走到这个答案的——试错几次、调了什么工具、有没有自相矛盾——judge 一概不知。而评分器侧（`graders/`）经 ③ 已经能按声明的证据级别取信，指标侧没有对等机制。
- 现有的 `confidence = 1 - uncertainty`（多采样极差的一半）是**同一 judge 的自一致**，不是评分者间信度。两个不同配置的 judge 打分分歧多大，框架没有任何度量——judge 换代（换模型、换提示词）翻了多少判，今天只能靠 diff 两次 run 的结论肉眼估。
- 分数合成是加性/均值的：一个硬性安全维度失败，可以被其他多个"表现良好"的维度在平均里稀释掉。RL 侧 reward basis 的教训是：安全门必须乘进总分，而不是参与平均。
- 接入 ③ 的证据后，可计算的轨迹类指标会突然变多。它们若默认进入通过率分母，等于悄悄改了所有历史口径。

时机成立：0.2.0 已发布可对照，trace 通路在宿主活跑（`run_fd7ef8c369b6`）上是活的，③ 的库层重评让"judge 换代翻判"第一次可量化。

## What Changes

1. **`measure()` 换宽签名**：指标测量改收一个测量上下文（任务输入 + 从 `TrialEvidence` 派生的观测 + 声明过的证据边界），旧的五字符串签名移除——按扩展协议"协议演进不保留双路径"的既定原则执行，不留兼容层。
2. **judge 按声明读取轨迹**：LLM judge 类指标像 ③ 的评分器一样声明取信级别；声明包含轨迹通道（transcript/steps）的，judge 提示词注入完整轨迹而非仅末条输出。未声明就只看输出，行为与今天等价。
3. **跨评分者一致性 κ/α**：同一判据配置 ≥2 个独立 judge（不同模型/提示词）时，statistics 报告 Cohen's κ（两评分者）与 Krippendorff's α（≥2 评分者、容忍缺失）；单 judge 多采样的 `confidence` 保留但文档明确标注其为自一致，与信度是两类量。
4. **reward_basis 式乘性安全门**：套件可把判据声明为门（gate）并选择乘性合成——门的因子乘进总分（门失败 → 总分按乘子塌缩），而不是把门的失败平均掉。默认合成方式维持现状（加性），门是显式 opt-in，门结果随结论落盘。
5. **轨迹指标默认仅诊断**：指标目录里的轨迹类指标默认 `diagnostic`——出现在报告与 `/v1/meta` 目录里，但不进通过率分子分母、不参与判分；升为 `judging` 必须在套件里显式声明，且升格后走第 2、4 条的声明与门机制。statistics 既有"诊断量不进分母"的口径由此推广到整个指标目录。

## Capabilities

### New Capabilities

（无——词表固定，④ 全部落进既有能力的需求扩展。）

### Modified Capabilities

- `llm-metrics`：`measure()` 宽签名契约；judge 按声明读取轨迹；轨迹指标默认仅诊断、升格须显式。
- `graders`：reward_basis 乘性安全门的合成语义与落盘要求。
- `statistics`：多评分者一致性 κ/α 的报告口径；诊断量与判分量在汇总结构中的分离。

## Impact

- **代码**：`metrics/base.py`（`Metric` ABC 签名破坏性变更，`MetricGraderAdapter` 随改）、`metrics/llm_judge.py`（轨迹注入）、`metrics/` 目录（轨迹类指标新实现）、`graders/`（门合成 + judge 轨迹消费）、`core/metrics.py` 与 `core/types.py`（κ/α、诊断/判分分离、门结果字段）、`cli.py` / `api/app.py`（目录展示）。
- **破坏性**：`Metric` 协议签名断裂（第 1 条），自定义指标作者必须迁移——随 0.3.0 的迁移说明发布，不留 legacy 旗标（与 ①③ 同一原则）。
- **版本**：0.3.0；汇总结构新增字段（诊断指标块、κ/α、门结果），同一大版本内向后兼容，statistics_version 是否递增在 design 定。
- **依赖**：无新增运行时依赖（κ/α 手写公式，样本量小不值得引 numpy/pingouin）。
- **验证门**：`pytest tests/ -q` + `ruff check packages/agent-eval`；κ/α 用已知小样本对照表测；门合成与诊断分离用 MockRunner 端到端。
