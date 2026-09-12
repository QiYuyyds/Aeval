# Agent 评测前沿调研（快照：2026-09-07）

本文件夹是对 **2025–2026 agent 评测（eval harness）领域前沿**的一次系统性调研，以及它与 **Aeval** 当前实现的对照分析。它是研究快照，不是产品文档——内容带日期、会过时；产品文档见 [`docs/`](../../docs/)。

> **相关文档**：`docs/open-source-agent-eval-survey.md` 是另一份面向读者的分类导论（基准/框架分类地图）。分工：导论在那边，**Aeval 对照矩阵、空白分析与路线图在本文件夹**；两处更新时互链，勿各自维护同一批数字。
>
> **状态更新（2026-09-12）**：变更④（指标目录）已归档、随 0.3.0 版本号本地落库；变更⑤ `add-user-simulator-and-run-events`（即下文路线图的 P1-A）已在实现中——覆盖矩阵里的 #11（用户模拟器）/ #12（事件注入）即将翻转。

## 调研方法

- **行业侧**：对 2025–2026 的 agent benchmark、eval harness/平台、方法学论文与工程实践做了多轮网络调研（约 60 个独立来源，全部列在 [07-sources.md](./07-sources.md)）。
- **代码侧**：通读了 Aeval 的 README / `docs/architecture.md` / YAML 套件格式 / 进行中的变更 `add-agent-metric-catalog`（变更④），并抽查了 `llm_judge.py`、`pytest_plugin.py`、`model_based.py`、`dataset/sources/` 等实现细节，所有"缺失"判断都经过代码验证（grep 确认 canary / swap / position_bias / docker / checkpoint / user_simulat 等关键词在源码中不存在）。

## 文档地图

| 文档 | 内容 | 适合谁读 |
|------|------|---------|
| [01-benchmark-landscape.md](./01-benchmark-landscape.md) | 2025–26 主流 agent benchmark 全景：测什么、新在哪 | 想了解"行业在评什么" |
| [02-harness-tooling-landscape.md](./02-harness-tooling-landscape.md) | eval harness 与平台全景 + 与 Aeval 的横向对比 | 想了解"别人用什么评" |
| [03-methodology-themes.md](./03-methodology-themes.md) | 12 个方法学主题：judge 偏差、pass^k、轨迹评分、沙箱、污染、安全…… | 想了解"怎么评才对" |
| [04-aeval-coverage-matrix.md](./04-aeval-coverage-matrix.md) | 行业主题 × Aeval 现状的覆盖对照矩阵（含代码证据） | 想知道 Aeval 站在哪 |
| [05-gap-analysis.md](./05-gap-analysis.md) | 五块结构性空白：交互模型 / judge 方法学 / 安全内容 / 分发生态 / 工程闭环 | 想知道缺什么 |
| [06-optimization-roadmap.md](./06-optimization-roadmap.md) | 优化方向、候选 change 提案、优先级与排期建议、反范围清单 | 想决定接下来做什么 |
| [07-sources.md](./07-sources.md) | 全部来源按类汇总 | 想核对出处 |
| [08-external-suite-fit.md](./08-external-suite-fit.md) | 外部评测集适配判断：准入判据、三层选材结论、三条硬约束（快照 2026-09-11，代码依据为⑤工作树） | 想决定"承载哪些基准、什么条件下承载" |

## 核心结论（TL;DR）

1. **Aeval 押对并被行业验证的方向**：trace→grader 已成为行业模板（OpenAI evals 平台即 datasets→traces→graders→runs）、YAML 声明式套件（promptfoo/Harbor/Inspect 共同验证）、pass^k（2025 年才被社区推广，Aeval 已内建）、OTel GenAI 作为 trace 底座、成本/时延作一等轴。
2. **Aeval 独有、多数主流开源 harness 没有的**：证据溯源（`observed_by`）、三态判定与分母纪律、采集/评分分离 + regrade/verdict_drift 审计、诚实成本轴（算不出即报"不可算"）。
3. **五块结构性空白**（详见 [05-gap-analysis.md](./05-gap-analysis.md)）：
   - **交互模型**只有"单轮静态 prompt"——缺用户模拟器（τ²-bench 双控）、动态事件注入（GAIA2/ARE）、长时程 checkpoint（Inspect）；
   - **judge 方法学**在"测量"一致性（变更④ κ/α）但没在"缓解"偏差——缺 swap/顺序随机化、结构化 rubric 清单评分器、人工金标校准闭环；
   - **安全评测**有门机制但无内容——缺金丝雀/外泄探针、注入鲁棒性 graders、沙箱参照实现；
   - **分发生态**——套件是本地 YAML 文件而非可分享制品，缺打包分发、内置任务包、污染卫生（canary/holdout）；
   - **工程闭环**——pytest 门禁只有绝对阈值，缺两车道 CI、基线相对回归门、样本量规划、生产 trace 回放闭环。
4. **优先级建议**（详见 [06-optimization-roadmap.md](./06-optimization-roadmap.md)）：交互模型层最急（越晚做破坏性越大）> 安全内容层（复用率最高）> rubric+swap（应接变更④）> 套件分发（采纳率杠杆）> 基线回归门+样本量规划（最便宜且独有）。

## 与 OpenSpec 工作流的关系

本调研是想法层面的输入。若要将某块空白立为正式变更，走 `openspec-propose` 流程；[06-optimization-roadmap.md](./06-optimization-roadmap.md) 里每个候选提案已按该流程需要的信息（scope / 能力归属 / 破坏性 / 验证门）做了粗排。
