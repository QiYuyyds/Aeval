## Context

动机见 `proposal.md` — Why。这里只写塑造方案的事实与约束。

今天的验收跑把这个变更从"设计洁癖"变成了"必要修复"，三件事已被测量：

- **证据从未抵达评分层**：provider 交付空属性时，0/1000 条真实 span 带属性；29 次历史 trial 只有 5 次走到评分，而那 5 次过程指标全为 0.0。评分层当时无法分辨"agent 没做"与"我没看到"——这正是缺少来源与通道区分的表现。
- **`invalid` 三态已经就位**（①），但**没有出路**：证据是一次性的，trial 一旦判完就再也无法重评，所以"grader 崩溃 → 这次评测作废"目前只能作废。
- **`state_check` 读的是被评方自报的 dict**：`graders/state_check.py` 检查 `trial.outcome["files"]`，而这个 dict 由 `AChatAgentRunner` 自己序列化上交。

约束：

- 框架定位是自部署的开发时工具，`pip install` 即用，**不得**引入容器、守护进程或强制的外部服务。
- 单向依赖由 `tests/test_import_isolation.py` 用 AST 固化；数据词汇层同理，`tests/test_vocabulary_isolation.py` 禁止宿主私有属性名出现在框架源码。设计必须继续走"运行时注入"而不是把 AChat 的形状写进内核。
- ②已经把"缺失 ≠ 0"做成了归一化观测 + `EvidenceGap` + `AbsentReason`，本变更在**它之上**加来源分级，不重做那套。
- 统计口径版本机制（`STATISTICS_VERSION`，历史 run 不回算）已确立，本沿用。
- 外部实现点只有一个类，框架自带 mock 是第二个。

## Goals / Non-Goals

**Goals:**

- 让"这条结论依据哪个观测"成为**类型系统里的问题**，而不是文档里的约定。
- 采集一次，判分多次：judge/grader 换代后能对同一批证据重评，从而让 ④ 的校准与 ⑥ 的对抗自检成为可能。
- 给出**破坏的边界**：哪些字段消失、历史数据处于什么状态、如何回滚。

**Non-Goals:**

- 不做容器/沙箱/egress 隔离。本变更让来源**可声明**，不宣称**可强制**（见 Risks）。
- 不做 CLI 与 HTTP 的重评分入口——先定库层契约，暴露面另立变更（它牵涉 API 兼容承诺，值得单独审）。
- 不做多轮/用户模拟器，不做新指标，不改 `ScoreStrategy`。
- 不引入证据归档的二进制格式或外部对象存储。

## Decisions

### D1：证据以带来源的观测记录表达，而不是加一个 `trusted: bool`

每个观测携带 `observed_by`，三级：

```
harness   评测侧在 teardown 前独立取证（探针、dump、文件清单）   ← 最可信
runner    接入适配层交付（transcript、trace_id、自报终态）        ← 半可信：是用户的代码，不是 agent 的产出
subject   被评 agent 自己写出的内容                              ← 最低：不能单独判通过
```

- **不选**两级（可信/不可信）：`runner` 与 `subject` 现实里必须分开——AChat 的 `AChatAgentRunner` 是用户自己写的可信适配层，把它的输出和 agent 的自吹同等对待会误伤，也会让人干脆绕过分级。
- 环境终态因此拆成两个互不替换的通道：`harness_state` 与 `subject_reported_state`。
- 不引入"数值签名/哈希链"之类的完整性机制：那需要可信第三方，超出框架定位（见 Risks 的诚实边界）。

### D2：`AgentRunner.run()` 接受会话句柄，可推送、可被运行中取证

```python
async def run(self, view: TaskView, session: TrialSession) -> TrialEvidence

TrialSession:
    emit(observation)         # 适配层随做随推，不必等跑完
    await harness_probe()     # 框架请求当场取证；读数带 observed_by=harness
    deadline / cancelled      # 框架把时限与取消交给适配层观察
```

一次调用仍对应一个 trial，因此**最简接入只是"跑完返回证据"** —— `emit` 与 `harness_probe` 可以完全不碰，复杂度是递进的而不是翻倍。这是选 B 而不是 A 的前提条件。

宿主侧已确认可行，不需要改并发模型：`WorkspaceCoordinator` 无锁（`begin` / `clear` 为同步方法），工作区列举本就是独立的只读调用（`collect_workspace_listing` → `fs_listdir`），所以"运行中取证"在 AChat 上不引入新的串行化或竞态。

- **不选** A（`run(view) -> evidence` 事后一次性返回）：A 下框架只能在 `run()` 返回**之后**取证，长跑型与后台进程型 agent 的中途状态永远拿不到，`harness` 级被限缩成"结束时快照"一种。
- **不选** C（保留旧签名 + 加可选钩子）：两条路径意味着分级可被绕过，等于没有分级。
- `harness_probe` 在接入方不支持并发探针时 MUST 明确报缺失，MUST NOT 返回空读数 —— 空读数是"确实没有"，缺失是"没取到"，这条区别 ② 已经立好。

### D2c：环境状态取证成为带时刻的序列，评分器必须声明判定时刻

既然能在运行中取证，"环境终态"就不再只有一个时刻。harness 取证读数因此是 `(时刻, 读数)` 的序列，而检查环境状态的评分器 MUST 声明判的是哪一个：结束时存在、结束时不存在、还是曾经存在过。

- 不选"永远取最后一个快照"：那会让"建完文件又删掉"判通过，而发现这种情况正是这类检查存在的理由。
- 不选"任一时刻满足即通过"：那是把结果检查悄悄换成过程检查。
- 默认 MUST 是"结束时"，且**所用时刻本身要随结论落盘可见**，让人看得出这条结论是按哪个时刻判的。

### D2b：任务视图去掉答案键

`run` 收到的是**去掉答案键的任务视图**（不含 `graders[*].config` 里的 `expected_*`、`value`、`checks`）。② 已确认 `expected_output` 就躺在 grader config 里、随对象递给被评方；本变更把这条路彻底堵住，而不是靠文档提醒"别读"。已核 `AChatAgentRunner` 只用 `task.prompt` 与 `task.env`，正是视图允许的两项。

### D3：`EnvironmentManager` 提供取证探针，可在运行中与停止前触发

```
setup → run ─┬─ 运行中探针（会话句柄按需触发）→ 带时刻的 harness 读数
             └─ 返回 → 结束前探针 → stop/teardown → grade
                                ↑                        ↑
                     至少保证有一个结束态     先停被评方再采集，避免边采边改
```

- 探针由**框架调用**、由接入方实现具体动作（每条读数自带 `observed_by="harness"` 与采集时刻）；被评方无法通过返回内容影响它。
- 运行中探针是**增量能力**：不实现它的接入方只得到结束态，与 A 形状等价，因此不破坏递进性。
- `state_check` 因此第一次有了真能用的语义：文件是否存在、DB 里有没有那条记录，可以来自 harness 读数（并按 D2c 声明时刻）。`subject_state` 里的 `files` 仍可读，但按 D4 默认不得单独支撑"通过"。
- 不选"框架自己实现容器内执行"：那会把 Aeval 变成环境平台，违背定位。我们提供通道与时机，执行能力留给 ⑤。

### D4：评分器声明可消费来源；未声明 = 什么都读不到

```
GraderConfig 新增 evidence: ["harness", "runner"]     # 默认（门 2 已批）
                          ["harness"]                 # 评外部/对抗场景时收紧
```

默认规则两条：

1. **`subject` 级证据不能单独使 trial 通过**——必须至少有一条 harness 或 runner 级证据支撑。要违反它，必须在套件里**显式**写出 `allow_subject: true`，且该选择随 run 落盘、在报告里可见。
2. 评分器被喂了它没声明的来源时，报 `invalid`（原因：证据级别不符），而不是照原样打分。

理由：这是整个变更的实际生效点。不立默认规则，分级就只是元数据。第 2 条让它变成可测的行为而不是口头承诺。

### D5：延迟评分——证据先落盘，判分可重做，且**永不覆盖**原结论

```
collect(run) → evidence archive            （贵、不可重复）
grade(run)   → verdict history             （便宜、可反复）
   每次判定记录: grader 版本 / 映射版本 / 规范修订 / judge 模型与标识 / 时间
   重评产生新版本条目，原结论保留 → 能回答「judge 换代让多少 trial 翻判」
```

- 选"并列保留 + 显式指定 current"而非"原地覆盖"：覆盖会让已经发出去的结论消失，且永远无法审计 judge 漂移——而那正是 ④ 要量的东西。
- 不选"重评即重跑 agent"：那既不必要（证据在手）又贵，而且把 ⑥ 的对抗自检变成不可做的。

### D6：正文采集与工具入参共用一个开关机制

```
capture:
  tool_arguments: false      # ② 已定
  model_content:  false      # 本变更新增：input.value / output.value 一类正文
redactor: <默认启用, 可替换>
```

两者同一套语义：默认不采、套件/任务级 opt-in、开启后强制过默认脱敏、处理标识写入 run。

- 理由不是省事，是**避免第二套隐私机制漂移**：两套规则迟早出现"一个开了一个没开"的组合。
- 规模已被测量（`input.value` 均值 2,352 字符、最大 73,899），因此默认不采同时是体积决策。

### D7：不给旧契约留兼容层；历史 run 标记为"不可重评"

- 不选 deprecated 双路径：v0.x、无外部部署方、1 个实现点。① 已确立同一原则（不给收紧的门禁留 legacy 旗标）。
- 历史 run 的诚实处置：**能读、不能重评、与新 run 标为不同口径**。当时没有保留证据边界，重评所需信息在现场就丢了——硬要重算只会产出另一个错的东西（与 ①「历史 run 不回算」同一论证）。

### D8：归档用现有 SQLite 的整包 JSON，加两张派生表

run 已经按 `model_dump()` 整包存；本变更加 `trial_evidence`（按 trial 一行）与 `grade_attempts`（按判定一行）。

- 不选对象存储/独立 blob 仓：超出定位，且自部署场景下 SQLite 是可用的最大公约数。
- 正文采集打开时的体积是主要代价 → D7 风险条给了缓解手段，留存策略列为 Open Question（可延后，不改变契约）。

## Risks / Trade-offs

- **「harness 级」仍是自我声明**：接入方若把自报数据标成 harness，框架从数据上无法分辨 → 接受并**明写**。本变更的收益是让撒谎需要显式撒谎（套件里写 `allow_subject`、实现里伪造探针），并且这个选择在 run 里可见；真正的强制需要后续环境隔离。README 与文档必须按这个措辞，不得写成"已防作弊"。
- **重评产生多个结论，"哪个是真的"会变成新问题** → 缓解：`grade_attempts` 带 current 指针 + 每次判定记录版本；默认展示 current，但历史结论永不被隐藏。
- **证据归档让 SQLite 体积快速膨胀**（打开正文后单 trial 可达数十 KB 到数百 KB）→ 缓解：默认不采、探针可单独声明留存、`storage` 侧要求按 run 彻底删除；具体留存默认值列为待答。
- **默认 `subject` 不可单独判通过会立刻打断现有宿主套件**：核对宿主代码后修正了原先的判断 —— `first-suite.yaml` 的 `file-creation` 并不是「只证明 agent 提过文件名」：它用 `code_based target: outcome` 匹配的文本来自 `AChatAgentRunner._collect_outcome_files`，即经 `fs_listdir` + `fs_read` 读到的**真实 workspace 内容与路径**，属可信但非独立的 `runner` 级证据。③ 对它的真实影响是：这份读数改由取证通道交付并升为 `harness` 级，于是 `target: outcome` 不再看得到它，套件必须把该判据换成 `state_check`（`evidence: [harness]`）。口径也确实变了，所以要在升级说明里明写，否则宿主 CI 会像突然退化。
- **改动面大**：协议、类型、编排、9 个内置评分器、2 个宿主评分器、2 个存储实现、mock，加测试 → 缓解：按 Migration 顺序分步落地，每步都有独立可回滚的验证点；mock 先改成新契约，用它驱动内置评分器。
- **`run()` 收到任务视图后，依赖 `task.env` 之外字段的宿主实现会失效** → 已核对：`AChatAgentRunner` 只用 `task.prompt` 与 `task.env`，正是任务视图允许的两项。

## Migration Plan

分步，每步都能独立验证与回滚：

1. 定义 `TrialEvidence` 与来源枚举；`EvalContext` 与归一化观测复用现有结构（纯新增，无行为变更）。
2. `AgentRunner` / `Grader` 协议 + 任务视图；`examples/mock_runner.py` 先迁到新契约，作为参考实现与测试夹具。
3. 三相编排 + `collect_probes`；`EnvironmentManager` 协议扩展（默认实现返回空探针集，行为与今天一致）。
4. 内置评分器逐个声明来源；`state_check` 改为优先 harness；确立 D4 的两条默认规则。
5. 证据归档与 `grade_attempts`；接入 `runner.regrade(run_id)`（**库层**，本变更不做 HTTP/CLI）。
6. 宿主侧：`AChatAgentRunner` 返回证据、环境实现文件清单探针、两个宿主评分器补声明；跑 `run_first_suite.py` 对比升级前后。
7. 文档与 README：写清"harness 是声明不是强制"、`allow_subject` 的含义、历史 run 不可重评。

**回滚**：1–3 向后兼容（只加不改），可单独回退；4 与 6 是语义分水岭（评分器开始拒绝来源），回退需同步撤销文档与宿主声明；5 的归档是新增表，回滚只丢重评能力、不丢 run。

## Open Questions

- ~~证据归档的默认留存策略~~ —— **已由实测回答 (任务 10.7)**：SQLite 里单条 trial 的 `trial_evidence` 行在默认口径 (两类正文都不采) 约 **2.1 KB**；开启正文与入参并走默认摘要脱敏后**仍然约 2.1 KB**（正文被换成定长摘要）；只有换成保留明文的钩子（`IdentityEvidenceRedactor` 一类）才涨 —— 2,352 字符正文实测 **9.1 KB**，73,899 字符 **223.7 KB**。run 记录本身不含证据正文（另表存一份，不双写）。因此本期结论：**全量保留直到显式按 run 删除**，不引入按天过期或「只留 invalid」那类裁剪 —— 体积风险由采集开关与脱敏钩子的选择决定，而不是由留存策略决定。若将来要支持明文留存，再单立变更。
- `steps` 是否需要与公共轨迹交换格式（ATIF 一类）对齐以便复用现成产出。本变更先按内部结构落，接入公共格式待评估其对手段与结果的影响后另立变更。
