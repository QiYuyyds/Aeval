# 04 · Aeval × 行业前沿：覆盖对照矩阵

> 快照日期：2026-09-07。基准与工具出处见 [01](./01-benchmark-landscape.md)、[02](./02-harness-tooling-landscape.md)、[03](./03-methodology-themes.md)；本文聚焦"Aeval 站在哪"，附代码证据。

## Aeval 是什么（一段话）

OTel trace 驱动的开源 agent 评测框架：YAML 声明套件（严格校验），重复 trial（重试/并发/预算控制），9 种内置 grader 逐 trial 判分，聚合为统计严谨的 pass@k / pass^k / 一致性 / 饱和度；证据带溯源（`observed_by: harness|runner|subject`）与缺失原因清单；采集与评分分离、判定可重放（regrade / verdict_drift）；REST + SSE + Next.js 看板；MIT，自部署，全程离线可测。

## 覆盖矩阵

评级：**领先** = 比主流开源 harness 做得好或独有；**持平** = 行业标配 Aeval 也有；**部分** = 有机制但缺内容/闭环；**缺失** = 无（经代码验证）。

| # | 主题（2025–26 行业状态） | Aeval 现状 | 评级 | 代码证据 |
|---|------------------------|-----------|------|---------|
| 1 | 一致性优先：pass^k、随机性形式化、区间门禁 | 无偏组合估计 + 外推标注 + Wilson/bootstrap + 饱和度防误读 | **领先** | `core/metrics.py`，`STATISTICS_VERSION="2"` |
| 2 | 证据化评分：trace 级评分、步级/里程碑 | step-level grader + 轨迹指标（变更④默认 diagnostic）+ 判定时刻语义 | **领先**（缺里程碑清单表达法） | `graders/step_level.py`、`suite.py` |
| 3 | 证据溯源（谁观测的） | observed_by 三级 + 两条默认规则（越级/仅自报 → invalid）+ 证据边界落盘 | **独有/领先** | `core/types.py`、`core/runner.py` |
| 4 | 采集/评分分离 + 重放审计 | trial_evidence + grade_attempts 追加 + current 指针 + regrade/verdict_drift | **独有/领先** | `storage/sqlite.py`、`core/runner.py` |
| 5 | OTel GenAI 底座（仍 experimental） | 版本钉定翻译表 + otel-genai/openinference 双预设 + fail-fast | **领先**（设计恰中靶心） | `trace/mapping.py`、`trace/normalize.py` |
| 6 | 成本/时延一等轴 | 四路 token + 外部价目 + "不可算≠0" + passed/failed 分列 + 跨 run 趋势 | **领先** | `core/pricing.py`、`core/metrics.py` |
| 7 | 多 judge 信度（κ/α 进工具 UX） | 变更④进行中（35/38）：κ/α + 自一致/信度两类分开 | **持平/进行中** | `openspec/changes/add-agent-metric-catalog` |
| 8 | Judge 偏差缓解（swap/顺序随机化/长度控制） | 呈现探针已具备：同一 judge 就同一份归档证据被 N 份呈现重问，报**结论**翻不翻（`anchor_value` / `dimension_order`，库层入口、只出诊断、不进任何分母与门禁）；swap 仍无宿主 | **部分**（探针机制已具备、**幅度未测**——真实锚定敏感度要一把可用的 judge 凭证；仍**不具备**成对比较位置偏置的缓解，框架内没有成对比较判据） | `graders/presentation_probes.py`、`core/metrics.py`（复用 ④ 的 κ/α） |
| 9 | 结构化 rubric 清单评分器 | rubric 为自由文本整体发 judge | **部分** | `graders/model_based.py:150` |
| 10 | Judge 校准闭环（人工金标 → 一致率 → 用/不用） | human grader 有 REST 回调；无金标集工作流 | **部分** | `graders/human.py` |
| 11 | 用户模拟器 / 双控（τ²-bench 模型） | 单轮静态 `prompt` 字符串 | **缺失** | `docs/yaml-format.md`（任务模型） |
| 12 | 动态/异步事件注入（GAIA2/ARE、Inspect Intervention） | setup→run→teardown 单发；无 trial 中钩子 | **缺失** | `core/contract.py`（EnvironmentManager） |
| 13 | 长时程 checkpoint/续跑（Inspect/Harbor） | 仅 TransientError 重试；中断即作废 | **缺失** | `core/runner.py`（重试路径） |
| 14 | 安全评测内容（注入/外泄/金丝雀，AgentDojo 联合评分） | 门机制 ✓（乘性塌缩）；安全内容零 | **部分**（机制有、子弹无） | grep：canary/inject/exfil 无命中 |
| 15 | 沙箱容器化（container-per-trial = table stakes） | 明确不做、亦无参照实现 | **缺失**（自报定位） | README 已知限制 |
| 16 | 套件打包分发 / 任务包 registry（Harbor/inspect_evals） | 本地 YAML + 2 个 examples | **缺失** | `examples/` |
| 17 | 污染卫生（canary 字段、holdout 拆分） | 无 | **缺失** | grep：canary 无命中 |
| 18 | 两车道 CI + 基线相对回归门 | pytest 插件单车道（绝对阈值 + invalid 上限）；compare 有区间不重叠判定但未接入门禁 | **部分** | `metrics/pytest_plugin.py`、`cli.py compare` |
| 19 | 样本量规划 / 功效分析（"还需多少 trials"） | 无；但区间公式已备齐 | **缺失**（低成本高独特性） | `core/metrics.py`（Wilson/bootstrap） |
| 20 | 生产 trace → 离线回放闭环 | trace_mining 数据源已有；链路未文档化打通 | **部分** | `dataset/sources/trace_mining.py` |
| 21 | 在线评测/漂移告警 | 明确反范围（dev-time 定位） | **缺失（by design）** | `docs/architecture.md` §1 |
| 22 | 多智能体失败分类诊断 | dispatch 机制有；无协调/级联/角色混乱诊断 | **部分** | README（orchestration 未校准） |
| 23 | 运行中人工介入（Intervention） | 无；只有结束后 human grader | **缺失** | `core/contract.py` |
| 24 | Postgres / 规模化存储 | SQLite + Memory；PG 在 Phase 3 | **缺失（已规划）** | `storage/` |

## 覆盖图（视觉摘要）

```
 行业 2025–26 主题                        Aeval 现状
 ═════════════════════════════════════════════════════════════════
 一致性优先 (pass^k/CI 门)        ██████████ 领先（无偏估计+Wilson）
 证据化评分 (trace grading)       ██████████ 领先（observed_by/regrade）
 成本/时延作一等轴               █████████  领先（四路 token+诚实成本）
 OTel GenAI 词汇                 █████████  领先（双预设+钉版本）
 多 judge 一致性 κ/α             ███████░   进行中（变更④）
 结构化 rubric 清单评分           ███░░░░░░░ 只有自由文本 rubric
 judge 偏差缓解（swap/集集成）    ██░░░░░░░░ 探针机制已具备，幅度未测；swap 无宿主
 用户模拟器 / 双控              █░░░░░░░░░ 单轮静态 prompt
 动态事件 / 中途干预             █░░░░░░░░░ setup→run→teardown 单发
 长时程 checkpoint/续跑          ░░░░░░░░░░ 无
 安全评测内容（注入/外泄）        ██░░░░░░░░ 有门机制，无安全内容
 沙箱容器化                      ██░░░░░░░░ 明确不做（已知限制）
 套件分发/任务包生态              █░░░░░░░░░ 只有本地 YAML
 两车道 CI / 基线回归门           ███░░░░░░░ pytest 插件仅绝对阈值
 生产 trace 回放闭环             ████░░░░░░ trace_mining 有，链路未通
```

## 三条战略判断

1. **不要追赶名单，要放大不对称优势。** 矩阵上半部（1–7）是 Aeval 的护城河：溯源、分母纪律、重放审计在对比表里没有一列同时具备。任何新特性都应接到这套证据底座上，而不是绕开它。
2. **五块"缺失"不是并列的，交互模型层是结构性缺口。** 用户模拟器/事件注入会改套件格式与扩展点协议（`AgentRunner`/`EnvironmentManager`/`TaskView`），拖得越晚破坏性越大（与变更①③④"协议演进不留双路径"的原则一致）。
3. **已知限制的行业权重已重排。** README 自报的限制里：沙箱缺位从"定位选择"变成"最被要求的缺失"；compare 无正式检验居中（行业普遍如此，不孤单）；regrade 无 HTTP 面、无人工评审 UI、PG 延后都排不上号——没有人因为这些选不了 Aeval。

详细的空白分析与"做成什么样"见 [05-gap-analysis.md](./05-gap-analysis.md)；优先级与候选提案见 [06-optimization-roadmap.md](./06-optimization-roadmap.md)。

## 快照后的增量

- **2026-09-22 · 第 4 行的隐含前提已闭合**：「采集/评分分离 + 重放审计」此前有一块没写出来的洞——`model_based` 把工具清单以 `list(set(...))` 拼进提示词，而字符串 hash 按进程随机化，所以"对同一批归档字节重评"在不同进程里喂给 judge 的东西本就不相同。变更 `make-judge-prompt-deterministic` 修掉它（判分输入成为归档证据的纯函数 + 真起子进程的逐字节守护测试 + 评分器版本 2→3 使翻转可单独归因）。矩阵上半部的评级不变，但第 4 行的承诺从此真正成立，且「judge 偏差缓解」（第 8 行）第一次有了可测的前提。
- **2026-09-22 · 第 8 行由「缺失」改评「部分」**：变更 `add-judge-presentation-probes` 建的是**测量能力**，不是缓解——判分器现在可以被「只改不该影响结论的呈现细节」的方式重问，并报告结论是否随之翻转（κ/α 复用 ④ 的数学，另立一个报告类型，不与跨评分者信度合成一个数）。**幅度今天没测**：真实锚定敏感度要一把可用的 judge 凭证，宿主四把候选全为 401/402（与 P0 的 tasks 6.2 同一手）；入口已入库（`examples/presentation-probes/`），凭证恢复后直接重跑，不需要新的设计决定。这一行不给「领先」，因为测得到 ≠ 测过了；也不给「已缓解」，因为成对比较位置偏置那一类仍无宿主（框架内没有成对比较判据）。修锚、探针的 YAML/CLI 表面、跨 run 敏感性趋势三件事都排在第一次真测量之后。
- 本文件仍是 **2026-09-07 快照**，另有 7 行已过期（⑤⑥⑦ 落地后：用户模拟器、事件注入、套件分发、基线门等）。逐行回填属独立工作，不在上述变更范围内。
