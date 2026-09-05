## Context

动机见 `proposal.md` — Why，行为契约见 `specs/`。此处只记录约束如何决定方案。

现状约束：

- `Grader` 是 `core/contract.py` 里的 `Protocol`，由宿主与第三方实现。它们返回的 `GraderResult` 无法预知本变更新增的判定字段——**任何新字段都必须有能保持旧实现行为不变的默认值**。
- `Storage` 侧 SQLite 以 `run.model_dump()` 整包 JSON 落盘，因此新增字段是免费的；但历史行缺字段，读取时必须能表达「不知道」。
- 框架定位是开发时工具且自部署，所以不能引入服务化组件（如外部的统计服务或 bootstrap 计算池）。
- `pass@k` 的当前语义已写进 `docs/architecture.md` §4、`README` 与 Dashboard 图例，且 `pytest --eval-suite` 门禁正在消费它。改动会同时改变四处呈现的同一个数字。
- `tests/` 目前用 `examples/mock_runner.py` 造 trace；`k > n` 外推的既有单测断言了具体数值（如 1/3 成功外推 `pass@5 ≈ 0.868`）。

## Goals / Non-Goals

**Goals:**

- 一次改动把「数字怎么算」与「数字凭什么算」两件事同时定下来：估计量、分母、有效性三者互相自洽。
- 有效性判定必须是**可枚举的结论类别**，而不是散落在各 grader 里的 0 分/半分兜底。
- 让下游（CLI、门禁、API、Dashboard）不需要理解统计细节就能安全地呈现——所需信息全部在汇总结构里。

**Non-Goals:**

- 不做完整的 A/B 显著性检验（permutation / Mann-Whitney）。本变更只承担「不把噪声说成退化」的下限：区间重叠即不给方向性结论。
- 不引入 `disqualified`（乘性安全门）第四态。本变更的分类通道预留它但不启用，留给安全门变更。
- 不改 `transcript` 评分器把成本折进能力分这件事——那是成本并列轴那条决策的归属，属于指标目录变更。
- 不改 `AgentRunner` / `Grader` 两个协议的现有必选签名，不做采集与评分分离。
- 不动 PostgreSQL 后端。

## Decisions

### D1：`k ≤ n` 用组合无偏估计，`k > n` 保留二项外推但强制标注

选 `1 - C(n-c,k)/C(n,k)` 与 `C(c,k)/C(n,k)`，与 τ²-bench、Inspect AI、Anthropic 三方定义一致。`k > n` 沿用现有二项外推，但返回值升级为带 `(value, extrapolated, p_point, p_lower_bound)` 的结构。

- 备选 A「删掉外推只报 `k ≤ n`」：数字最保守，但与 `max_trials=3` 的常态套件一起把 `pass@10` 变成不可用，且与 README 已承诺的行为冲突。
- 备选 B「外推改用 Wilson 下界代入 `1-(1-p)^k`」：系统性保守，但与 `k ≤ n` 区间的口径不同构，读者会把两种保守程度不同的数字并排看。
- 注意 MLflow 那篇实务协议文章把 `pass^k` 定义为「k 次中至少一次成功」，与上述三方相反，**不采纳其定义**（其 seed 数、bootstrap、方差分解部分仍可参考）。

### D2：有效性作为 `GraderResult` 上的显式判定字段，默认 `valid`

新增 `verdict ∈ {valid, invalid, pending}`，默认 `valid`。invalid 的来源是一个封闭枚举：`grader_error` / `grader_timeout` / `unknown_grader` / `judge_unavailable` / `verdict_unparseable` / `no_criteria_configured` / `trial_timeout`。

- 之所以不选「抛异常由编排层捕获推断」：异常只能说明「出错了」，丢失是谁出的错、错在哪一步，也无法与「依赖未满足」这类**正确的**结论区分。
- 默认 `valid` 是兼容性的关键：第三方 grader 不改动任何一行代码，其结论仍按原样计入分母。老行为只会在框架内置 grader 上被改变。

### D3：`invalid` 与「依赖未满足」的边界写死

依赖未满足仍记 `passed = False`、`score = 0.0`、`verdict = valid`——那是关于 agent 的**结论**（前置条件没达成），不是评测器故障。不划清这条线，改造会把大量合法 0 分误翻成 invalid，反而掏空分母。

同理，「未配置判据」现在从 `score = 1.0 auto-pass` 改为 `invalid`：那是**根本没测**，既不该算满分也不该算失败。这类缺陷与折 0 分是同一个病根的两个方向。

### D4：显著性用「区间不重叠」的保守规则

`compare` 与 Dashboard 只做一个判断：两侧置信区间是否重叠。重叠即标注不显著、不给方向性结论、不上方向色。

- 备选「引入 Mann-Whitney / permutation test」：功效更好，但会把本变更拖成统计检验专项，且与已知的 Phase 3 项目重叠。当前规则只会「少下结论」，不会「下错结论」，符合可信性优先的方向。

### D5：一致性口径统一到加权分

`consistent` / `score_std_dev` 改用与 `_compute_trial_success` 相同的加权分序列。

- 备选「保留简单平均并在文档注明两者不同口径」：否。同一个 trial 的「是否成功」与「是否稳定」如果是两套分数，报表里就可能出现「每次都不过但极稳定」与「分数抖动但都过」这类互相矛盾的呈现，而这恰是本变更要消灭的东西。
- 副作用要如实报告：`std < 0.2` 这个既有阈值在 n=3 上统计功效很低，统一口径后不一致率数字会变化。阈值本身不在本变更调整（避免同时动两个变量），但 `insufficient_data` 规则会挡住 n 过小的任务被误判。

### D6：历史 run 不回算，改为记录口径版本

`RunResult` 增加统计口径版本常量字段；跨版本比较由 `compare`、API `/v1/meta`、Dashboard 三处标注「不可比」。

- 备选「启动时批量重算历史 summary」：否。它会静默改写已经发布出去的结论，且历史 per-trial 数据里根本没有 invalid 判定所需的现场信息（当时 grader 异常已经落成 0 分），重算出来的仍是错的东西。
- `/v1` 响应结构保持向后兼容（只增字段），因此不升 `/v2`；语义变化靠版本声明与文档承担，而不是靠 URL。

### D7：本变更内确定的数值默认值

门禁 `invalid` 占比上限 `0.2`；饱和判定的最小有效样本数 `5`；bootstrap 重采样次数 `1000`；置信水平 `0.95`。全部可配置，且都进入 `/v1/meta` 或 run 记录，避免同一 suite 在不同配置下产出不可比的数字。

## Risks / Trade-offs

- **修正后大量既有 suite 的分数会明显回落**（`pass@1` 从虚高回到真值），用户第一反应会是「agent 退化了」 → 缓解：CLI 与 Dashboard 显示口径版本与变化说明；`docs/` 与 README 的 Known Limitations 明确写「这是口径修正不是能力变化」；`examples/` 的期望数字一并更新。
- **小样本下组合估计本身方差极大**，n=3 的 `pass@1` 区间可能宽到没有决策价值 → 缓解：区间强制呈现，让「这套题太小」变成可见事实而不是隐藏风险；`insufficient_data` 规则阻止极端情况给出结论。
- **三态改造触及内置 grader 与 metric 分发两条路径，容易改漏**（`graders/` 9 个实现 + `MetricGrader` + `MetricGraderAdapter` 共三处兜底逻辑） → 缓解：以 specs/orchestration 的 scenario 逐条写参数化单测，而不是逐个文件手工检查。
- **默认 `verdict = valid` 意味着外部 grader 的基建故障仍会被折进分母** → 接受此权衡：不改协议就不可能知道外部 grader 的失败；文档需引导实现方改用 `invalid`，并将其列为升级到下一 major 的动机。
- **门禁收紧会打断现有 CI**（原本放行的构建开始失败） → 这是本变更的目的而非副作用，且项目处于 v0.x、无外部部署方，不提供 legacy 旗标：一个 0.x 库为不存在的用户保留假绿灯没有价值。改为在 `docs/getting-started.md` 与升级说明中明示「门禁语义已修正，历史上放行的构建可能开始失败」，并把该提示作为 CLI 在检测口径版本变化时的输出内容。

## Migration Plan

1. 先落 `core/metrics.py` 的估计量与区间函数并配齐单测——此时无行为变更，可独立合并。
2. 落数据模型字段（默认值保证行为等价），仍然无行为变更。
3. 切内置 grader 与编排层的 verdict 分类，同一提交内更新 `tests/` 断言。
4. 切汇总口径（饱和度、一致性、分母过滤），此时数字开始变化。
5. 切 CLI / 门禁 / API / Dashboard 呈现，加 `legacy` 过渡旗标。
6. 更新 `docs/` 与 README；`examples/` 重算期望值。

**回滚**：第 3、4 步是行为分水岭，各自独立可回滚——统计函数与模型字段向后兼容，回滚只需还原 verdict 分类与汇总口径两处。SQLite 无需迁移脚本（新增字段读时缺省）。

## Open Questions

- `p50 / p95` 是否对全部过程指标一律输出，还是只对时延与 token 输出、其余保留 avg/min/max（不影响结构与契约，实现时按输出噪声决定）。
- Dashboard 外推标识的具体视觉形式（角标 vs 灰底 vs 单独列），待与现有表格样式对齐。
