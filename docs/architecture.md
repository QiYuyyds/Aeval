# 架构

Aeval 是一个由 OTel trace 驱动的 Agent 评测框架：取证按 OTel GenAI 语义约定的词汇读入，宿主的私有属性名经翻译表接入。本文描述它的模块划分、执行数据流与扩展点设计。

## 1. 定位

- **开发时工具**：开发者在本地或 CI 里运行评测，验证 Agent 改动是否引入退化；非生产环境持续运行
- **单向依赖**：`agent_eval` 不依赖任何宿主应用（`import app.*` 被测试固化为禁令）；宿主 → 框架单向依赖
- **词汇中立**：框架源码里不出现宿主私有属性名（同样由 AST 扫描固化为禁令）——宿主的埋点词汇只是运行时映射条目，加一个宿主不改框架代码
- **自部署**：无用户系统 / 认证 / 权限；多租户不在范围内

## 2. 模块布局

```
agent_eval/
├── core/           # 编排与统计
│   ├── types.py      # 数据模型: EvalSuite/EvalTask/GraderConfig/TaskView → 证据层(ObservedBy/Observation/TrialEvidence/GradeAttempt) → TrialResult/TaskSummary/RunSummary/RunResult
│   ├── contract.py   # 协议: AgentRunner(必选, run(view, session)→证据)/TraceProvider/Grader/Storage/EnvironmentManager(含取证探针) + TrialSession + 报错分类异常/EvalContext
│   ├── suite.py      # YAML 加载 + 严格校验 (校验器在 Pydantic 模型上)
│   ├── metrics.py    # 估计量 (组合无偏 / 外推标注) + 有效性分母 + Wilson & bootstrap 区间 + p50/p95/worst_of_n + 过程指标与成本轴派生
│   ├── pricing.py    # 单价表 (外部配置, 无内置价目) 与四路 token 折算
│   ├── redaction.py  # 证据脱敏钩子 Protocol + 默认摘要实现
│   └── runner.py     # EvalRunner — 核心编排器
├── graders/        # 9 个内置评分器 + 注册表 (只读标准观测; 按声明的证据级别取信)
├── metrics/        # LLM 质量指标 (RAG 四件套/LLM judge/批量/报告/pytest 插件)
├── dataset/        # 数据集构建 (5 类数据源/质量检查/覆盖度/semver 升版)
├── storage/        # Memory + SQLite (runs/suites/人工评分请求 + trial_evidence/grade_attempts 两张派生表)
├── trace/          # 归一化边界
│   ├── mapping.py    #   属性翻译表 (内置条目对齐 OTel GenAI + 版本常量) + default_mapping()
│   ├── normalize.py  #   span → 标准观测 (唯一读原始属性名的地方) + collect_observations()
│   ├── observations.py # 标准观测记录 / Missing 与缺失原因枚举
│   └── phoenix.py    #   Phoenix TraceProvider (懒加载)
├── api/            # FastAPI 工厂: create_app (寄宿挂载) + create_standalone_app (/v1 独立)
│   └── routes/       # suites/tasks/runs(含 SSE 流)/compare/graders/datasets/metrics
├── examples/       # MockAgentRunner / MockTraceProvider (可按任意词汇产出 span)
└── cli.py          # eval-suite 命令行 (typer)
```

## 3. 执行数据流

**归一化边界**：span 只在进入框架的那一刻被翻译一次（`trace/normalize.py`），此后指标提取、内置评分器、汇总与呈现消费的都是词汇无关的标准观测。框架源码里不存在任何宿主的私有属性名——它们只能是运行时注入的映射条目。读不到的字段是带原因的「缺失」，与真实零值不混同（`Missing` 不许当布尔用）。

```
suite.yaml ──load──▶ EvalSuite
                        │
                        ▼
               ┌─── EvalRunner.run_suite ───┐
               │  (per task, N trials)       │
               │                             │
   snapshot ──▶│ ① 采集相                    │── TransientError? ──▶ 指数退避重试 (用尽记 invalid)
               │   setup → run(TaskView,     │── 超时?            ──▶ invalid(trial_timeout), 不占分母
               │          TrialSession)      │── 报错未分类        ──▶ invalid(需人工判定)
               │    ├ session.emit(...)      │   ← 适配层随做随推
               │    ├ session.harness_probe()│   ← 运行中独立取证 (带时刻)
               │    └ trace spans → 归一化   ──▶ 标准观测 + 缺失清单 + 未识别属性名
               │ ② 结束前取证 end_state       │   ← 框架发起: 至少一个结束态读数
               │    证据按 trial 落盘 ────────┼──▶ trial_evidence 表
               │    teardown                 │
               │    verify_clean(harness 读数)│── 泄漏 → restore + 告警(不判失败)
               │ ③ 评分相 (先停被评方再判分)   │
               │    graders 拓扑序, 只读声明过的级别
               │    两条默认规则: 越级 / 仅自报 → invalid
               │    判定口径落盘 ─────────────┼──▶ grade_attempts 表 (current 指针, 永不覆盖)
               └─────────────────────────────┘
                        │
                        ▼
   RunSummary (pass@k / pass^k / 一致性 / 饱和度 / 终止原因分布 / 资源与成本轴 / 证据强度分布)
                        │
                        ▼
   RunResult.evidence (采集开关 · 规范与映射版本 · 脱敏处理标识 · allow_subject 放行清单)
                        │
                        ▼
              Storage (Memory / SQLite)  ──▶  API / CLI / Dashboard

   ── 延迟评分 (与采集解耦) ──────────────────────────────────────
   regrade_run(run_id)   ─▶ 读 trial_evidence ─▶ 用当前判据/翻译表/judge 重判
                            （全程不调用 AgentRunner; 新结论追加进 grade_attempts）
   verdict_drift(run_id) ─▶ 原结论 vs current 的翻判比例
```

关键行为约定：

- **三相顺序**：取证 MUST 在 teardown 之前（停止阶段常会清理工作目录，之后再采只能读到被清理后的状态）；评分 MUST 在停止之后（先停被评方再判分，避免边采边改）；证据先落盘、判分是它的可重放派生
- **并发模型**：trial 并发默认 1（`asyncio.Semaphore` 可调）；评分器内部另有并发上限
- **重试**：只有 `TransientError` 重试（实现方显式包装）；重试用尽记 `invalid` + `external_dependency_unavailable`——基建问题不是 agent 能力问题
- **终止原因由框架判定**，不信被评测方自报：`agent_completed` / `step_budget_exceeded` / `token_budget_exceeded` / `cost_budget_exceeded` / `timeout` / `agent_error` / `cancelled`，归类分野见 §4.5
- **预算触顶即停**：该 trial 不再进入评分（拿不完整的证据得出关于 agent 的结论比不结论更糟），但已采集的 transcript / 指标 / 产物保留
- **报错分类**：`AgentDefect` 计未通过并占分母；`ExternalDependencyError` 记 invalid；未声明类别的其他异常记 `unclassified_agent_error` **需人工判定**，框架不替它折算通过与否
- **取消是协作式的**：`POST /runs/{run_id}/cancel` 置标志位，**进行中的 trial 跑完**（强杀留下的半途状态比不取消更难解释），此后未启动的 trial 逐个记 `cancelled` 留痕，已完成部分保留可查
- **泄漏检测**：`verify_clean` 收到本次 trial 的评测侧取证读数并据其比对，不再依赖被评方自报状态；报告不干净 → 自动 restore + 告警；trial 成败只由评分决定
- **评分依赖**：grader 按 `dependencies` 拓扑排序执行；依赖未通过 → 跳过并给 0 分解释，判定仍为 `valid`（这是关于 agent 的结论）
- **评测侧故障**：grader 异常/超时、未注册 grader、judge 不可用、判分无法解析、判据未配置、取证通道不可用、证据越级、仅自报支撑的通过 → 记 `invalid` 并保留原因，不折成 0 分、不占分母、不 crash run
- **grader 缓存**：同 run 内按内容寻址缓存评分结果（可关），key 含**取信声明**（`evidence` / `allow_subject` / `judgment_moment`）与取证读数的内容 —— 同一份 transcript 在三种声明下结论不同，共用缓存会让第一个看到的声明决定后面所有结论。**按 run 分桶**——删除 run 时其派生结果（可能含采集到的证据内容）一并失效
- **重评分**：`regrade_run` 只读归档证据重放判定，不触碰被评系统；证据不齐即**整体拒绝**（`IncompleteEvidence` 点名缺哪几条），本变更前落盘的 run 一律 `RegradeUnavailable`。本期只有库层入口，HTTP/CLI 未暴露

## 4. 统计语义

统计口径版本 `STATISTICS_VERSION = "2"`（`core/types.py`）。每个 `RunResult` 记录产生它时所用的版本；**历史 run 不回算**——旧数据里 grader 异常已经落成 0 分，现场信息不足以重判，重算只会产出另一个错的东西。

### 4.1 通过率估计量

设一个 task 的 **有效** trial 数为 n、其中成功 c：

| 量 | `k ≤ n`（实测） | `k > n`（外推） |
|----|----------------|----------------|
| `pass@k`（能力） | `1 - C(n-c, k) / C(n, k)` | `1 - (1-p)^k`，p = c/n |
| `pass^k`（可靠性） | `C(c, k) / C(n, k)` | `p^k` |

- `k ≤ n` 用有限样本无偏估计，因此 `pass@1 == c / n`：3 次里蒙对 1 次是 **0.333**，不是旧口径的 1.0
- `pass^k` 按组合数计算，与 trial 完成顺序无关（不截取前 k 个判定）
- 外推值 MUST 携带 `extrapolated` 标记、所用的 `p_point` 与 p 的 Wilson 95% 下界；报告界面不得以外推值冒充实测值

### 4.2 不确定性与分布摘要

| 对象 | 附带量 |
|------|--------|
| 每个通过率 | Wilson 95% 置信区间（`p_lower_bound` / `p_upper_bound`） |
| 每个连续分数 | bootstrap 95% 区间（≥1000 次重采样，seed 可注入以复现）、均值、标准差、`worst_of_n` |
| 过程指标 | avg / min / max / **p50 / p95** |

小样本不再被读成饱和：2/2 全通过的 `pass@1` 是 1.0，但其 95% 区间为 `[0.342, 1.0]`。

### 4.3 三态判定与分母

每个 trial 与每条 grader 结论都带 `verdict ∈ {valid, invalid, pending}`（默认 `valid` 以保持第三方 grader 协议兼容）。**只有 `valid` 进入通过率的分子与分母**：

- `invalid` = 评测侧故障，原因取封闭枚举：`grader_error` / `grader_timeout` / `unknown_grader` / `judge_unavailable` / `verdict_unparseable` / `no_criteria_configured` / `trial_timeout` / `evidence_unavailable` / `unclassified_agent_error` / `external_dependency_unavailable` / `trial_cancelled`。这类故障说明评测本身没跑通，不是关于 agent 的证据
- `pending` = 等待人工评分回传，走独立通道，回传后重算汇总
- 优先级 pending > invalid > valid；有 grader 结论时按 grader 推导，故人工评分回传后判定可自愈
- **边界（明确保留）**：grader 依赖未满足仍记 `passed=False` + `score=0.0` + `verdict=valid` —— 前置条件没达成是关于 agent 的结论
- trial 超时归 `trial_timeout`，且已采集的 transcript / 指标 / 产物不丢弃

分母为 0 时聚合量为 `insufficient_data`（`None`），**不是 `0.0`**；每个通过率与平均分都同时报告 `valid_trials` / `invalid_trials` / `pending_trials`。

### 4.4 饱和度与一致性

- **饱和度**：基于**实测区间**的 `pass@1`（`method == "measured"` 且非外推），且有效 trial 数 ≥ `MIN_VALID_TRIALS_FOR_SATURATION`（默认 5）的任务才参与判定；其余任务列入 `insufficient_sample_tasks`。合格任务过半 `pass@1 ≥ 0.95` 才判饱和，无合格任务时 `saturation_ratio` 为 `None`
- **一致性**：采用与 trial 成功判定**同一条加权分序列**（配置了 grader 权重时同为加权分）；有效样本不足 2 时 `consistent` / `score_std_dev` 为 `None`，单点不记「完美一致」

### 4.5 终止原因与归类分野

`termination_reason` 由框架判定，**不采信被评测方自报的完成状态**。它与 `verdict` 是两件事：前者回答「这次 trial 因何结束」，后者回答「这个结论能不能进分母」。分野必须清楚，否则「agent 做不到」与「评测没跑完」会混成同一个数字：

| 终止原因 | 归类 | 进分母？ |
|---------|------|---------|
| `agent_completed` | 由评分决定通过与否 | 是 |
| `step_budget_exceeded` / `token_budget_exceeded` / `cost_budget_exceeded` | **任务约束未达成** → 计未通过，但单列计数，与评分判定的不通过可辨 | 是 |
| `timeout` | **评测没跑完** → `invalid` + `trial_timeout` | 否 |
| `cancelled` | 评测没跑完 → `invalid` + `trial_cancelled` | 否 |
| `agent_error` | 取决于接入方声明的类别：`AgentDefect` 占分母计未通过；`ExternalDependencyError` 不占；未声明 → `unclassified_agent_error` 需人工判定 | 视声明 |

task 与 run 两级汇总都输出 `termination_reasons` 分布计数（不折叠进通过率）。本变更之前落盘的 trial 没有该字段，读为 `unknown` 并单独计数——不臆断为「正常完成」。

预算触顶时该 trial **不进入评分**：拿不完整的证据得出关于 agent 的结论，比不结论更糟。声明了预算但当时判不了（没配单价表、provider 没上报用量），会在 trial 的 `evidence_gaps` 里留下 `budget.*` 及原因——「没超限」这个结论同样需要证据。

### 4.6 成本与资源：与通过率并列的轴

`cost_usd` 不与通过率折成任何复合分数，也不参与通过判定；它是并排呈现的第二轴。

- **四路分解**：输入 / 输出 / 推理 / 缓存读取分别计量，分别按各自单价折算（推理缺价按输出价、缓存缺价按输入价回落）。`n_total_tokens` 只是「输入+输出」的只读派生别名，不用于计费推导。
- **无内置价目**：单价表是外部配置（`PriceTable` 或 dict）。未配置 → `price_table_not_configured`；模型未单列且无兜底档 → `model_price_not_covered`；某路有量无价 → `price_missing_for_class:<路>`。三种都报「成本不可计算」，**不以 0 冒充零成本**。
- **缺字段即证据缺口**：算不出来的指标键不出现在 `metrics` 里，而是一项项落在 `evidence_gaps`（`field` + `reason` + `detail`），使结论自陈「没看到什么、为什么没看到」。trace 里确实没有工具 span 才是 `n_toolcalls = 0`；provider 没答上来则是缺失。
- **两类分开汇总**：`summary.resources` 给出 `passed` / `failed` 各自的平均 token 与成本及 `costed_trials`，「失败比成功更贵」这类事实不被全局均值抹平；`cost_unknown_trials` 与 `cost_unknown_reason` 单列算不出成本的 trial。
- **跨 run 趋势**：`cost_trend()` 只把有成本轴的 run 连成序列，其余进 `excluded` 并标原因（`history_run_without_resource_axis` / `no_summary` / 具体的缺失原因），`comparable` 需 ≥2 个可计入 run。

### 4.7 证据分级与延迟评分

统计口径（§4.1–4.6）回答「数字怎么算」，这一节回答「数字凭什么可信」。一次 trial 交付的每条读数都带 `observed_by`：

| 级别 | 谁观测到的 | 默认可信度 |
|------|-----------|-----------|
| `harness` | 评测侧在 teardown 之前独立取证（探针 / dump / 文件清单） | 最高；判据可被要求只认这一级 |
| `runner` | 接入适配层交付（transcript、trace_id、自报终态、trace 埋点观测） | 默认可信 —— 那是接入方自己写的代码，不是 agent 的产出 |
| `subject` | 被评 agent 自己写出的内容 | 不得单独支撑「通过」 |

两条默认规则由框架统一执行，落到每条 grader 结论上（`invalid` 的两个新原因）：结论依据了未声明的级别 → `evidence_level_mismatch`；通过只由自报证据支撑且未写 `allow_subject` → `subject_only_evidence`。分级不立默认规则就只是元数据。

- **判定时刻**：环境状态判据 MUST 声明依据 `at_end` / `not_at_end` / `any_time` 哪一个（默认结束时）。只有结束态一次取证时，`any_time` 报证据不足 —— 拿结束态冒充全时段观测等于把结果检查换成过程检查。
- **分量可见**：`trial.weakest_evidence` 与汇总里的 `evidence_levels` 分布让「全靠自报的 1.0」与「评测侧取证的 1.0」在同一个报告里区分得出来；`subject_only_trials` 单列弱证据通过数。
- **采集与评分分离**：证据先按 trial 落 `trial_evidence` 表（run 记录本身不内联证据正文），判分在其后独立进行。每次判定追加一格 `grade_attempts`，带判分实现版本、翻译表与规范修订、判定模型标识、时间与 `current` 指针，**永不覆盖**既有条目。
- **重评分**：`regrade_run(run_id)` 复用归档证据重放判定，MUST NOT 调用被评系统；`verdict_drift(run_id)` 因此能回答「judge 换代让多少 trial 翻判」。统计口径版本（§4）与证据边界一起决定两个 run 能否比较。
- **诚实边界**：`harness` 由框架在自己发起的探针调用上钉死，但接入方在**返回对象**里标什么级别框架无法从数据分辨。本变更让溯源**可声明**，不宣称**可强制** —— 强制需要环境隔离，属框架定位之外。

## 5. API 部署形态

| 形态 | 入口 | 前缀 | 说明 |
|------|------|------|------|
| 寄宿挂载 | `create_app()` | 由宿主决定（如 AChat `/api/eval`） | 挂进已有 FastAPI；可注入真实 runner |
| 独立部署 | `create_standalone_app()` | `/v1` | 复用 `create_app` 全部路由挂 `/v1`；`X-Aeval-Version` 头 + `/v1/meta` 能力清单 |

兼容承诺：同一大版本内 URL 与响应结构向后兼容；破坏性变更升 `/v2` 并保留并行期。SSE 流（`GET /runs/{run_id}/stream`）采用快照+增量协议，刷新可恢复。

**结构兼容不豁免口径声明**：同一大版本内数值的语义仍可能变化（如 v0.1.0 → v0.1.1 的统计口径修正），因此两种形态都经元信息接口公布当前口径：寄宿形态 `GET <prefix>/meta`，独立形态 `GET /v1/meta`，响应含 `statistics.version` 与 `confidence_level` / `bootstrap_rounds` / `min_valid_trials_for_saturation` / `gate_invalid_ratio_limit` 四个默认值。每个落盘 run 另在 `statistics_version` 上记录产出它时所用的版本，调用方据此判断两个 run 是否可比。

同一份元信息还公布**证据口径**：`spec_version`（钉住的 OTel GenAI 版本）、`mapping_version`（翻译表自身版本）、`tool_arguments_captured_by_default: false`、`model_content_captured_by_default: false`，以及能力位 `regrade_over_http: false` / `regrade_over_cli: false`（重评分本期只有库层入口）。每个 run 另在 `evidence` 里落盘自己当时的证据边界（两类采集开关及逐 task 差异、`allow_subject` 放行清单、规范与映射版本、脱敏处理标识与版本），run 详情与列表再附一个 `regrade: {available, reason, exposed_over_http: false}`：本变更前落盘的 run 在此明确读出「不可重评」及其原因，而不是被静默当成等价数据。对比接口的可比判定要同时过两道：统计口径相同 **且** 证据边界相同，否则 `not_comparable_reason` 会点名是哪一维不同（缺边界记录的历史 run 一律视为不可直接比较，而不是假定与今天同边界）。

## 6. 扩展点

运行时协议见 `core/contract.py`（接入指南有实现示例），归一化与成本相关的扩展点各自成模块（`trace/mapping.py`、`core/redaction.py`、`core/pricing.py`）：

- `AgentRunner`（必选）— 执行任务并**交付带来源的证据**：`run(view: TaskView, session: TrialSession) -> TrialEvidence`；`emit` 与运行中探针都是可选的递进能力
- `TraceProvider` — 从 trace 后端拉 span 数据
- `AttributeMapping` — span 属性名 → 标准观测的翻译表（`default_mapping(extra)` 注入宿主词汇）；换表即换证据边界，历史 run 因记录了当时版本而不被误判为同口径
- `EvidenceRedactor` — 采集开启时强制应用的脱敏处理（整体替换，不叠加默认实现）；标识与版本随 run 落盘，审计看得见「谁换掉了默认摘要」
- `PriceTable` — 成本折算的单价表（外部配置，无内置价目）；换价表会改变 `cost_usd`，故不配置时宁可不报
- `Grader` — 逐 trial 评分（`EvalContext` 贯穿共享状态；`context.observations` 是已归一化的标准观测，`context.evidence` 是按本判据 `evidence` 声明过滤后的证据视图）。声明 `evidence_levels` 与 `implementation_version` 后，结论可被审计到「依据哪几级、由哪一版判的」
- `Storage` — 持久化 runs/suites/人工评分请求，另有两组**可选**方法：`save_trial_evidence / get_trial_evidence` 与 `save_grade_attempt / list_grade_attempts`（不实现则该批 run 可读、可评但不可重评分）
- `EnvironmentManager` — 每 trial 环境 setup/teardown/快照/泄漏检测/恢复，加**取证探针** `probe(channel)`：由框架在 teardown 之前调用，读数一律记 `harness` 级；没有默认实现，不传即没有环境

LLM 侧依赖统一经 `LLMFn` 回调（`(system, user) → text`）与指标注册表注入；缺配置或判分无法解析时返回带原因的 `invalid` 结论（`judge_unavailable` / `verdict_unparseable`），既不崩溃也不折成 agent 的 0 分。

## 7. Dashboard

`apps/dashboard`（Next.js 16 App Router）：总览 / Suites+YAML 导入 / Run 报告（SSE 实时）/ Trial 下钻（transcript、grader 分解、Phoenix 外链）/ A/B 对比 / 数据集管理 / Task 库 / Settings。经 rewrites 把 `/api/eval/*` 代理到后端（`EVAL_BACKEND_URL`）。

前端不重算统计量：报告页直接取后端 `TaskSummary`，声明口径版本与 `valid / invalid / pending` 分母，外推值带 `*` 且 hover 可查置信区间；无有效样本时呈现「证据不足」而非 0。对比页在两个 run 口径不同时标注不可比，差值落在置信区间内时以中性样式呈现，不给方向性结论。

## 8. 测试策略

- 纯单元：core 统计 / suite 校验 / graders / storage（`cd packages/agent-eval && python -m pytest --cov=agent_eval.core.metrics --cov-report=term-missing`，core 统计模块覆盖率下限 90%）
- MockRunner 端到端：超时隔离 / 瞬态重试 / 依赖跳过 / 泄漏检测 / 终止原因与预算触顶的完整行为矩阵
- **双词汇参数化**：`MockTraceProvider` 按传入的翻译表产出 span，因此同一条逻辑 trace 可以按标准词汇或宿主词汇表达；grader / metrics / e2e 用例在两种词汇下各跑一遍。这是为了防止「mock 与框架约定同一套私有名字」的自证循环——翻译表没做对时，用例必须失败
- **私有字面量静态检查**：AST 扫描 `agent_eval` 包源码，出现宿主私有属性名字面量即失败（`tests/test_vocabulary_isolation.py`）
- API：CliRunner / TestClient / 挂载回归（宿主侧）
- 导入隔离：AST 扫描 `agent_eval` 拒绝 `app.*`（`tests/test_import_isolation.py`）
- 全程离线：无外部服务与凭据依赖
