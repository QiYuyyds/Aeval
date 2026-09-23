# Changelog

Aeval 的版本语义变更记录。格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)；
每个条目对应的完整动机与规格见 `openspec/changes/archive/` 下的各变更提案。

## [Unreleased]

## [0.3.0] — 2026-09-23

> **版本标签映射说明**：下面各段标题原先写的「目标 0.3.0 / 0.4.0 / 0.5.0」，是在「0.3.0、0.4.0、0.5.0 会各自单独发布」的假设下分别记下的。该假设未成立——0.3.0 此前从未成为 tag 也从未上传 PyPI（实测索引最新稳定为 0.2.0），④⑤⑥⑦ 与 P0、⑧ 六个变更一直是同一坨未发布内容。版本号是**发布序列**不是**计划序列**，0.2.0 之后的下一个发行物只能是 0.3.0，故本批六个变更**同批以 0.3.0 发行**。**归档记录（`openspec/changes/archive/`）与各段本文一律不改写**，收敛只发生在这一层。

### 新增（变更④ add-agent-metric-catalog — 目标即实际 0.3.0）

- **证据感知的指标协议**：`Metric.measure()` 改收 `MeasurementContext`（任务输入 + 从 `TrialEvidence` 派生的观测 + 声明过的证据边界）。**破坏性**：旧五字符串签名移除，自定义指标作者需迁移（**不保留 legacy 旗标**——旧签名在装配期即报错，绕不过去）。**迁移路径见 [`docs/getting-started.md`](docs/getting-started.md) 「升级到 0.3.0（从 0.2.x）」**，逐参数对照与新旧写法见 [`docs/integration-guide.md`](docs/integration-guide.md) §11；只写套件、不写自定义指标的升级即用。
- **轨迹感知 judge**：声明包含 transcript/steps 通道的 judge 指标，提示词注入完整轨迹而非仅末条输出；未声明时行为与 0.2.x 逐字节等价。
- **跨评分者一致性**：同一判据配置 ≥2 个独立 judge 时报告 Cohen's κ（两评分者）与 Krippendorff's α（≥2 评分者、容忍缺失）；单 judge 多采样的 `confidence` 明确标注为自一致，与信度分开。
- **乘性安全门**：判据可声明为门（`gate.factor`），乘性合成时门失败把总分按因子塌缩，不被平均稀释（默认合成方式仍为加性，门是显式 opt-in）。
- **轨迹指标默认仅诊断**：轨迹类指标默认出现在报告与 `/v1/meta` 目录但不进任何分母；升为判分角色须在套件显式声明。

### 新增（变更⑤ add-user-simulator-and-run-events — 原标 0.4.0，实际随 0.3.0 发行）

- **多轮会话评测**：任务可声明 `conversation`（`turns` 预写话术 / `goal` 目标驱动，二者互斥）；`prompt` 保持必填、语义收窄为「首轮用户输入」。轮次循环挂在框架产出的 `TrialSession` 上，`AgentRunner` / `EnvironmentManager` / `Grader` 签名零改动；一个完整对话仍是一个 trial，`statistics_version` 维持 2、分母口径不变。
- **`UserSimulator` 扩展点**：预写话术（确定性零模型调用）与目标驱动（经既有 `LLMFn`，缺配置返回带原因的不可用）共用一个协议；模拟用户读数记 `observed_by: harness`。
- **运行中事件与人工介入**：`inject_event(...)` / `human_message(...)` 带时刻以 harness 来源进 transcript 证据；`JudgmentMoment` 新增「最后一个注入事件之后」取值（非破坏）。
- **扩展点发现**：新增 `agent_eval.graders` / `agent_eval.environments` / `agent_eval.simulators` entry-point 组，CLI 与 REST 共用同一份注册表（修复「命令行是降级入口」的陈年问题）。
- **环境初始态与身份**：task 可声明环境 fixture（经既有 `setup(task)` 句柄读取）；`EvidenceBoundary` 记录环境标识与版本，跨 run 比较在环境身份不同时报 `not_comparable_reason`，历史行读回为「未记录」。
- **可重放脚本**：用户侧输入序列（首轮 + 话术 + 事件 + 人工介入）随证据落盘，库层重评分被评系统调用次数为零。

### 新增（变更⑥ add-baseline-gate-and-power-analysis — 原标 0.4.0 / 0.5.0，实际随 0.3.0 发行）

- **基线相对回归门**：`eval-suite run --baseline <run_id>` 与 pytest 插件 `--eval-baseline <run_id>`。复用 compare 的同源可比判定（统计口径一致且证据边界一致，含环境身份），以套件实测 `pass@1` 的 95% 区间判门：区间不重叠且方向向下才算「显著变差」（退出码非 0）；不可比 / 不可判同样失败并给原因（宁可红不可哑）；区间重叠或优于基线则放行并如实呈现两区间。逐 task 升降以诊断块呈现，不参与门判定。可比性判定从 API 路由下沉核心层（`core/comparison.py`），CLI / API / 插件三处同源。
- **样本量规划（`eval-suite power`）**：`--delta` 问法（基线 `--p` 缺省 0.5 最保守，Wilson 95% 区间半宽反解）与 `--delta --from-run <run_id>` 问法（实测 p 走 Wilson 反解，实测分数 σ 走双样本正态近似回答「分辨差异 d 需要 N」）。全闭式公式零新依赖；输出自陈公式、假设与「正态近似偏乐观」局限；run 无有效样本时报证据不足退出非零，不以 0/1 代算。
- **生产 trace 回放通路（文档 + 离线示例）**：[接入指南 §14](docs/integration-guide.md) 写明 trace 导出 → `trace_mining` 建任务 → 人工补判据 → 套件化 → `eval-suite run --baseline` 定时回归的全通路与边界（不做在线服务）；`examples/trace-replay/` 提供全程离线的最小演示。
- 不传新参数时 `run` / pytest 插件行为与 0.3.x 逐字节一致；`statistics_version` 不变（功效分析是报告量与门禁行为，不改分母口径）。

### 新增（变更⑦ add-suite-packaging-and-hygiene — 原标 0.5.0，实际随 0.3.0 发行）

- **套件包（pack）与来源解析**：`eval-suite run` / `validate` 的来源参数从"本地 YAML 路径"扩展为四类——单文件（现状原样）、pack 目录、pack 压缩包（`.tar.gz` / `.zip`，解包校验用后即清）、git URL（浅 clone 到临时目录后定位，`file://` 全链路离线可测；git 缺失给明确报错）。pack 由 `suite.yaml` + 引用资产 + `manifest.json`（pack 名 / 套件 semver / 逐文件 sha256）构成：任一文件被改动即拒绝加载并点名文件，manifest 缺失/非法同样拒绝；pack 内资产引用相对 pack 根解析，越界/绝对路径引用即拒（新模块 `core/packaging.py`，标准库实现零新依赖）。
- **内置 starter pack 随 wheel 分发**：`agent_eval.packs.starter`（确定性 code/artifact 判据，零 LLM 零网络零凭据）打包进 wheel 作 package data；`eval-suite run demo` 直接运行它——pip 用户装完即跑，不必 clone 仓库（与本地同名路径冲突时本地优先并提示；未显式传 `--runner` 时强制内置 mock）。仓库侧 `packs/starter/` 与包内同源，测试钉住两侧逐字节一致。
- **污染卫生**：suite 新增规范字段 `canary_guid`（UUID 格式校验，随 run 输出与运行记录呈现；框架不做运行时强制）与任务标记 `holdout: true`——默认不跑（`run` 排除并报告跳过数），`--include-holdout` 显式放行；过滤在 `EvalRunner.run_suite` 入口完成，REST 与宿主挂载自动同享；全 holdout 套件报错拒绝运行（不产出空 run）；跳过数不进 RunSummary（统计口径零变更，`statistics_version` 不动）。`docs/release-checklist.md` 新增公开发布前检查单（canary 生成与用途、holdout 拆分、许可证与数据来源声明、pack 发布方式）。
- **破坏性**：无。单文件路径加载、无 holdout 套件的运行、未声明 `canary_guid` 的套件三条现状路径行为逐字节一致（各有回归测试钉住）；唯一新增 pip 依赖为零，git 为可选外部命令。

### 新增（变更⑧ add-judge-presentation-probes — 随 0.3.0 发行；本段为发布时补写）

- **判分器可被呈现探针检视，且探针不产出结论**：`graders/presentation_probes.py` 允许就同一份归档证据、以「只改变不该影响结论的呈现细节」的方式重问**同一个 judge**，并报告结论是否随呈现变化。每个算子 MUST 声明它**保持的不变量**，说不清保持什么的改动（改写被评正文、翻转判据极性）在构造期即被拒绝成为算子——否则测出的差异无法归因到「呈现」。首版两个算子：`anchor_value`（只替换判分模板的预填示例值 `0.0` / `1.0` / 不给值，其余逐字节相同）与 `dimension_order`（配置顺序与逆序）。探针默认关闭，`sample` 与 `seed` 均必填且内核不给默认（无分布前拍默认样本量等于拍脑袋定阈值），成本在调用点显式报价（份数 × 抽中 trial 数，跨算子按呈现身份去重故为 4 份）。**硬边界**：探针读数不是判定条目——不产出 verdict、不进 `grade_attempts`、不移动 `current` 指针、不进任何分母、不成门禁；同一 trial 开探针与关探针，其判定条目数、通过率分母与 `statistics_version` 逐项相等。
- **呈现不变性是独立报告类型，不与跨评分者 κ/α 合成一个数**：同一判据的「一致性」是三个不同问题——两个评分者之间是否一致（**信度**）、同一评分者重复采样是否一致（**自一致**，④ 已把它与信度分开）、同一评分者在不该影响结论的呈现变化下是否一致（**呈现不变性**，本次新增）。三者 MUST 各自独立报告、MUST NOT 合并为综合信度分、MUST NOT 用一类的数值顶替另一类（两个共享同一偏置的 judge 会报「高度一致」而它们**一致地错**，κ 高不掩盖呈现敏感）。实现上新类型 `PresentationInvarianceReport` / `PresentationOperatorReport`（`core/types.py`）复用 `cohen_kappa` / `krippendorff_alpha` 的数学，`AgreementReport` 一字未动，两类量分块不混排。翻转定义与统计口径同源：**结论跨过 `threshold` 才算翻**，分数漂移单独呈现、不与翻转混计。
- **三态 status 把「未检出」与「没测」分开**：`status ∈ {sensitive, not_detected, not_computable}`，三态各配固定文案（分别以「检出呈现敏感」/「未检出呈现敏感」/「不可计算」开头），不得共用一个空值。`not_detected` 是一条结论；`not_computable` 是没测出来（无凭证 / 无可读读数 / 对齐样本不足——后者复用既有 `MIN_ALIGNED_RATINGS_FOR_AGREEMENT` 语义报「样本过少 + 原因」，不伪造数值）。逐算子合成时「没测出来」不得被「未检出」掩盖。
- **`dimension_order` 这一格的 `not_detected` 比标签听起来弱（发布须知）**：等权平均对维度排列是**对称**的，一个「跟随位置的偏置」在任何逐维打分排列下都不会移动合成分；再叠加实测到的「逆序求和在 judge 真会给出的值域上逐位相同」，该轴今天在二元结论上**双向不可翻**。所以它报 `not_detected` 只等于「这条轴没构造出可归因于呈现的结论差异」，**不等于**「judge 对列举顺序不敏感」。要让它真能测出首因效应，缺的是逐维读数或加权聚合这一层。该限定同时写进算子的不变量声明与报告输出（`SUMMATION_ORDER_QUALIFIER`），不只活在归档文档里。
- **真实敏感度未测——机制已具备、幅度待凭证**：宿主四把候选 judge 凭证今天全为 401/402，`--live` 分支未被执行。**本次验收没有验证真实 judge 敏不敏感，也未得出「judge 不敏感」的结论**：双向构造性替身（构造敏感的必须报出 `sensitive`、构造不敏感的不得误报）证明的只是探针机制有效。这句话随报告对象的 `interpretation_note` 每次运行呈现，并写在 `docs/grader-reference.md` 与 `examples/presentation-probes/README.md`。有凭证后重跑同一入口 `examples/presentation-probes/measure_presentation_sensitivity.py --live` 即可，无需新的设计决定。
- **明确没有的表面（范围决定，不是延期）**：未新增 `CALIBER_AXES` 第六根轴（探针不产出 verdict，没有翻判要归因）；无 YAML / CLI / REST / 看板入口（沿用 ④ 对 `regrade` 的处理与理由——探针的配置形状正等这次测量来 inform，且这些表面背同大版本兼容承诺）；不落库（`storage/` 零改动，该模块无迁移机制）；不做呈现算子的第三方扩展点（无 entry-point 组）。库层入口 `run_presentation_probes(...)` 是唯一调用面。
- **默认行为零变更**：不开探针时 `model_based` 的判分输入与 P0 落地后**逐字节相同**（预填值 `DEFAULT_JUDGE_ANCHOR_VALUE="0.0"`、系统提示词、阈值 `0.7` 与模板正文均原样），故 `implementation_version` **保持 3 不动**；被探针读过的 run 与全新 run 走 `runs_comparable()` 仍 `comparable=True`。无新增运行时依赖。

### 修复（make-judge-prompt-deterministic — 原标 0.3.0 / 0.4.0，实际随 0.3.0 发行）

- **判分提示词必须可复现**：`model_based` 把工具清单以 `list(set(...))` 拼进 judge 提示词，而字符串 hash 按进程随机化——同一批归档字节在不同进程里重评会构造出**不同的提示词**，变更④ 承诺的"对同一批字节重评"因此不成立。现改为按内容排序，判分输入成为（归档证据, rubric, dimensions, 判分配置）的纯函数，跨进程重评逐字节一致（守护测试真起两个子进程，不给它们设 `PYTHONHASHSEED`）。
- **重评同一批字节可能得到不同分数，且这是修复**：`ModelBasedGrader.implementation_version` `"2"` → `"3"`，含两种以上工具调用且判据读到工具清单的历史 trial，其 verdict 可能翻转。翻转不静默处理——版本随判定落盘，`verdict_drift` 能把它们单独归因到评分器版本这一维度，而不与"换了 judge 模型""证据边界变了"混成同一个数字。
- **翻判归因看得见判分实现版本**：`verdict_drift` 的口径聚合原先只有属性翻译表版本 / 规范修订 / 统计口径版本 / 判定模型四根轴，**不含评分器实现版本**——于是本变更自己那次 v2→v3 重评报出 `distinct_calibers = 1`（字面意思"口径相同"，实际有一根轴变了，且 `= 1` 会把翻判暗示成无害）。现加入第五根轴，且**只计入本次判定实际产出结论的评分器**（未参与的那一位改版不得污染归因）；输出追加两个字段——`differing_caliber_axes` 点名变化落在哪根轴，`unattributable_flips` 在"全轴同值却仍翻判"时显式报警。
  **发布须知**：`distinct_calibers` 名称与类型不变，但它的**值**会比以前大（同一批重评数据从 1 变 2）。这不是回归，是原字段看不见那根轴的纠错——若你手上有引用旧数字的记录，按新语义复核，不要以为自己造的 case 出了问题。
- **验收证据入库**：本变更的复现入口从仓库根的临时脚本改为受版本控制的 `examples/prompt-determinism/`（`measure_version_flip.py` 离线量换代翻转与归因轴、`replay_archived_prompts.py` 在真实归档上量判分输入变化，两者都不需 LLM 凭证、都拒绝被传入修复后的 rev）；顺带收掉变更⑤ 同类遗留——其归档记录引用的 `examples/achat/run_live_acceptance.py` 修复（推理模型 `max_tokens` 300→1500 + 空 `content` 显式报错）此前只是本机未提交改动。
- `statistics_version` **维持 2**：本变更只修判分输入的确定性，不动分母口径，历史 run **无需迁移**、不回算。

## [0.2.0] — 2026-09-06

### 变更

- **统计口径修正（变更② fix-stats-and-denominators）**：`pass@1` 改为有效 trial 上的无偏成功比例（`c/n`，旧值含义是「n 次内至少对一次」）；`k > n` 的 `pass@k`/`pass^k` 为外推值并强制携带 `extrapolated` 标记；每个通过率带 Wilson 95% 区间、每个连续分数带 bootstrap 区间；grader 崩溃/未配置判据/judge 不可用归类 `invalid` 并移出分母（`STATISTICS_VERSION = "2"`，历史 run 不回算）。
- **采集与评分分离（变更③ separate-collection-from-grading）**：`AgentRunner.run(view, session) -> TrialEvidence`（**破坏性**，无兼容层——绕得过的来源分级等于没有分级）；证据按 trial 先落盘、判定是其可重放派生；`grade_attempts` 追加不覆盖 + `current` 指针；`regrade` 本期为库层入口。
- **证据对齐（变更① align-trace-evidence）**：span 属性经版本钉定的翻译表归一化，`trial` 终止原因由框架判定（超时/预算触顶/错误/取消各自归桶），四路 token 分解与 `cost_usd` 成本轴与通过率并列。
- **OpenInference 预设**：翻译表内置第二套公共约定预设（`otel-genai` 默认 / `openinference`），按名选择、钉定 spec 修订号，未知词汇在装配期失败而非静默回退。
- **发布流程**（变更 make-published-artifact-match-accepted-code）：PyPI 制品与仓库验收代码对齐（0.1.0 时期制品落后代码的教训固化进流程）。

## [0.1.0] — 2026-08-30

### 新增

- Suite as YAML（严格校验：semver、唯一 task id）与 9 种内置评分器（code / LLM-as-judge / state_check / tool_calls / transcript / artifact / human / step_level / metric）。
- `pass@k` / `pass^k` / 一致性 / 饱和度聚合；`invalid` 三态判定；trial 并发、重试、预算与取消。
- SQLite / Memory 存储；FastAPI REST + SSE（`/v1` 独立部署与寄宿挂载）；`eval-suite` CLI（run / validate / list / show / compare / serve）。
- 数据集构建（trace 挖矿等 4 类数据源）与 RAG 质量指标（answer relevancy / faithfulness / context recall / precision）。
- Next.js Dashboard（总览 / 套件管理 / 实时 run 报告 / trial 下钻 / A/B 对比）。

[Unreleased]: https://github.com/QiYuyyds/Aeval/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/QiYuyyds/Aeval/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/QiYuyyds/Aeval/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/QiYuyyds/Aeval/releases/tag/v0.1.0
