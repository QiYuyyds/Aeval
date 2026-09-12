# Changelog

Aeval 的版本语义变更记录。格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)；
每个条目对应的完整动机与规格见 `openspec/changes/archive/` 下的各变更提案。

## [Unreleased]

> 本地 `pyproject.toml` 曾钉在 0.3.0（仅含变更④）但**未发布到 PyPI**；变更⑤落地后下一次发布将直接为 **0.4.0**（新增能力、非破坏性 minor）。

### 新增（变更④ add-agent-metric-catalog，目标 0.3.0）

- **证据感知的指标协议**：`Metric.measure()` 改收 `MeasurementContext`（任务输入 + 从 `TrialEvidence` 派生的观测 + 声明过的证据边界）。**破坏性**：旧五字符串签名移除，自定义指标作者需迁移（不保留 legacy 旗标）。
- **轨迹感知 judge**：声明包含 transcript/steps 通道的 judge 指标，提示词注入完整轨迹而非仅末条输出；未声明时行为与 0.2.x 逐字节等价。
- **跨评分者一致性**：同一判据配置 ≥2 个独立 judge 时报告 Cohen's κ（两评分者）与 Krippendorff's α（≥2 评分者、容忍缺失）；单 judge 多采样的 `confidence` 明确标注为自一致，与信度分开。
- **乘性安全门**：判据可声明为门（`gate.factor`），乘性合成时门失败把总分按因子塌缩，不被平均稀释（默认合成方式仍为加性，门是显式 opt-in）。
- **轨迹指标默认仅诊断**：轨迹类指标默认出现在报告与 `/v1/meta` 目录但不进任何分母；升为判分角色须在套件显式声明。

### 新增（变更⑤ add-user-simulator-and-run-events，目标 0.4.0）

- **多轮会话评测**：任务可声明 `conversation`（`turns` 预写话术 / `goal` 目标驱动，二者互斥）；`prompt` 保持必填、语义收窄为「首轮用户输入」。轮次循环挂在框架产出的 `TrialSession` 上，`AgentRunner` / `EnvironmentManager` / `Grader` 签名零改动；一个完整对话仍是一个 trial，`statistics_version` 维持 2、分母口径不变。
- **`UserSimulator` 扩展点**：预写话术（确定性零模型调用）与目标驱动（经既有 `LLMFn`，缺配置返回带原因的不可用）共用一个协议；模拟用户读数记 `observed_by: harness`。
- **运行中事件与人工介入**：`inject_event(...)` / `human_message(...)` 带时刻以 harness 来源进 transcript 证据；`JudgmentMoment` 新增「最后一个注入事件之后」取值（非破坏）。
- **扩展点发现**：新增 `agent_eval.graders` / `agent_eval.environments` / `agent_eval.simulators` entry-point 组，CLI 与 REST 共用同一份注册表（修复「命令行是降级入口」的陈年问题）。
- **环境初始态与身份**：task 可声明环境 fixture（经既有 `setup(task)` 句柄读取）；`EvidenceBoundary` 记录环境标识与版本，跨 run 比较在环境身份不同时报 `not_comparable_reason`，历史行读回为「未记录」。
- **可重放脚本**：用户侧输入序列（首轮 + 话术 + 事件 + 人工介入）随证据落盘，库层重评分被评系统调用次数为零。

### 新增（变更⑥ add-baseline-gate-and-power-analysis，随下一次 minor 发布）

- **基线相对回归门**：`eval-suite run --baseline <run_id>` 与 pytest 插件 `--eval-baseline <run_id>`。复用 compare 的同源可比判定（统计口径一致且证据边界一致，含环境身份），以套件实测 `pass@1` 的 95% 区间判门：区间不重叠且方向向下才算「显著变差」（退出码非 0）；不可比 / 不可判同样失败并给原因（宁可红不可哑）；区间重叠或优于基线则放行并如实呈现两区间。逐 task 升降以诊断块呈现，不参与门判定。可比性判定从 API 路由下沉核心层（`core/comparison.py`），CLI / API / 插件三处同源。
- **样本量规划（`eval-suite power`）**：`--delta` 问法（基线 `--p` 缺省 0.5 最保守，Wilson 95% 区间半宽反解）与 `--delta --from-run <run_id>` 问法（实测 p 走 Wilson 反解，实测分数 σ 走双样本正态近似回答「分辨差异 d 需要 N」）。全闭式公式零新依赖；输出自陈公式、假设与「正态近似偏乐观」局限；run 无有效样本时报证据不足退出非零，不以 0/1 代算。
- **生产 trace 回放通路（文档 + 离线示例）**：[接入指南 §14](docs/integration-guide.md) 写明 trace 导出 → `trace_mining` 建任务 → 人工补判据 → 套件化 → `eval-suite run --baseline` 定时回归的全通路与边界（不做在线服务）；`examples/trace-replay/` 提供全程离线的最小演示。
- 不传新参数时 `run` / pytest 插件行为与 0.3.x 逐字节一致；`statistics_version` 不变（功效分析是报告量与门禁行为，不改分母口径）。

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

[Unreleased]: https://github.com/QiYuyyds/Aeval/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/QiYuyyds/Aeval/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/QiYuyyds/Aeval/releases/tag/v0.1.0
