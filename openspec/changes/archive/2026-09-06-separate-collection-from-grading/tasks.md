## 1. 证据模型与会话类型（纯新增，无行为变更）

- [x] 1.1 定义 `observed_by` 三级枚举与观测记录基类：每条读数带来源级别 + 采集时刻
- [x] 1.2 定义 `TrialEvidence`：transcript / steps / subject_state / harness_state（读数序列）/ artifacts / budget / gaps，gaps 复用 ② 的 `EvidenceGap` 与 `AbsentReason`，不另造第二套缺失表示
- [x] 1.3 定义 `TrialSession`：`emit()`、`await harness_probe()`、时限与取消信号；**不实现探针的接入方必须仍能只靠返回值完成接入**
- [x] 1.4 `TrialResult` 改为承载证据；新增字段全部给默认值，使第 2、3 步可分步落地
- [x] 1.5 单测：同一份观测分别以三级来源表达时，类型与序列化结果可区分；缺失与空值仍按 ② 的语义区分

## 2. 破坏接入契约（唯一不可逆步骤，集中在此）

- [x] 2.1 `core/contract.py`：`AgentRunner.run(view, session) -> TrialEvidence`；删除三元组路径（**不留兼容层**，与 ①「不留 legacy 旗标」同一原则）
- [x] 2.2 定义任务视图（仅暴露执行所需的输入与环境参数），并从协议上保证评分器配置与答案键不被递给被评方
- [x] 2.3 `Grader` 协议增加来源级别声明入口；未声明即什么都读不到
- [x] 2.4 `examples/mock_runner.py` 迁到新契约，并作为参考实现：一条路径只用返回值、一条路径用 `emit` + 运行中探针，两条都出测试
- [x] 2.5 全量测试在此步允许红：先只改协议与 mock，评分器与编排留在第 3、4 步跟上

## 3. 三相编排与取证探针

- [x] 3.1 `core/runner.py`：生命周期改为 `setup → run（运行中可探针）→ 结束前探针 → teardown → grade`；确保「至少有一个结束态读数」
- [x] 3.2 `EnvironmentManager` 增加探针入口，默认实现返回**明确的缺失**而不是空读数（空=确实没有，缺失=没取到）
- [x] 3.3 harness 读数落为 `(时刻, 读数)` 序列；实现「结束时 / 结束时不成立 / 任一时刻」三种取值器
- [x] 3.4 泄漏检测（`verify_clean`）改为可读 harness 读数，不再依赖被评方自报状态
- [x] 3.5 端到端用例：中途创建后删除的文件，在「结束时」判为不成立、在「任一时刻」判为成立；探针缺失时报证据不足而非通过

## 4. 评分器分级取信与两条默认规则

- [x] 4.1 `graders/` 九个内置实现逐个声明可消费来源；`state_check` 改为优先 harness、其次 runner，`subject_state` 不单独支撑通过
- [x] 4.2 实现「只有 subject 级证据支撑的通过 → `invalid`」与「读到未声明级别 → `invalid`」两条规则，原因文案可区分
- [x] 4.3 套件侧逃生开关 `allow_subject`：生效、随 run 落盘、在结论上可见
- [x] 4.4 结论披露其依赖的**最弱证据级别**，并贯通到汇总结构
- [x] 4.5 环境状态类判据的判定时刻进入配置与结果，默认「结束时」且时刻随结论可见
- [x] 4.6 参数化单测：同一份证据在「只 harness / 默认 / 允许 subject」三种声明下产生三种不同结论

## 5. 证据归档与库层重评分

- [x] 5.1 每次 trial 的证据独立持久化（SQLite 与 Memory 两个实现），可完整取回且保留来源与缺失原因
- [x] 5.2 判定历史表：每条结论带 grader 实现版本、属性映射与规范修订、判定模型标识、时间；`current` 指针；**永不覆盖**既有结论
- [x] 5.3 `EvalRunner` 暴露库层重评分入口；证据不完整时拒绝重评并说明缺什么，不得用残缺证据出新结论
- [x] 5.4 重评分不得触发任何对被评系统的调用（用 mock 断言调用次数为 0）
- [x] 5.5 明确「同一 trial 多个结论」在汇总里的算法：以 `current` 参与统计，历史条目只用于审计与漂移度量
- [x] 5.6 本期不做 HTTP/CLI 入口；在 `rest-api` / `cli` 的现有能力清单里如实标注重评分尚未对外暴露（避免留下"能调但没写"的暗坑）

## 6. 敏感证据采集开关统一

- [x] 6.1 把 ② 的工具入参开关与新的模型正文开关合并为一套声明语义（默认关、可按套件/任务开启、开启后强制默认脱敏、处理标识落盘）
- [x] 6.2 未开启时相关判据报「证据不可用」，不判失败
- [x] 6.3 用例：只开正文不开入参（及反向）时，两者状态各自独立且可见
- [x] 6.4 按 run 删除时同时清除证据、判定条目与派生缓存，删除后查询无残留

## 7. 宿主迁移（AChat）

- [x] 7.1 `AChatAgentRunner.run` 改为返回证据 + 接受会话句柄；`subject_state` 与 `harness_state` 分开填：`fs_listdir` 走评测侧读数通道，agent 自述走 `subject`
- [x] 7.2 环境实现探针（文件清单 + DB dump 两条即可），并确认运行中探针与 `WorkspaceCoordinator` 无冲突（已核无锁，仍需实测）—— 实测方式：`test_probe_follows_the_current_trial_not_the_caller` 把「probe 读 current trial、交叠时后 begin 覆盖前者」写成断言，并据此保留 `concurrency=1`
- [x] 7.3 宿主两个自有评分器补来源声明
- [x] 7.4 重跑 `run_first_suite.py`：确认 9 trial 仍全部 valid；**预期差异要逐条解释**——尤其 `file-creation` 若因证据级别重划而改变结论，要说明它原来依赖的是哪一级 —— 实跑通过：`run_8ca076ca783a`（agent `ag_yTc8OAQE5Uzi`）9 trial 全 valid、pass@1=100%，`file-creation` 的 `weakest_evidence=harness`、`moment=at_end`，另两条 task 是 `runner`；逐条差异与口径变化记在 `host-migration/README.md`。**首跑（`run_06a16595008a`）三条全挂，查证据链后确认是两个框架缺陷**：结束态只取序列里最后一条读数（db_dump 顶掉了 workspace 清单，明明存在的 hello.md 被判不存在）、`list_grade_attempts` 从 blob 读 `is_current`（18 条全报当前）—— 均已修并有回归用例（`fix(orchestration)!` / `fix(storage)`）
- [x] 7.5 更新宿主 `docs/eval-harness-design*.md`：那份文件里仍写着 ① 修掉的旧 `pass@k` 实现，顺带纠正

## 8. 历史数据与口径声明

- [x] 8.1 变更前落盘的 run 标记为「可读取、不可重评」，并在 API/CLI/报告里与统计口径版本一起显示
- [x] 8.2 确认这类 run 不会被误送进重评分路径（有明确错误而非静默产出错结论）

## 9. 文档

- [x] 9.1 `docs/integration-guide.md`：新接入契约的最小实现、`emit` 与运行中探针何时才需要、来源三级各自含义与**"harness 是可声明不是可强制"的诚实边界**
- [x] 9.2 `docs/grader-reference.md`：来源声明、判定时刻、`allow_subject` 与弱证据标注
- [x] 9.3 `docs/architecture.md` §3/§4：三相生命周期图与延迟评分数据流
- [x] 9.4 README（中英）：Features 增「采集与评分分离，可对既有 run 重评分」；Known Limitations 增「溯源可声明、不可强制」
- [x] 9.5 `docs/yaml-format.md`：新增字段与校验规则一览

## 10. 验证门

- [x] 10.1 `ruff check packages/agent-eval` 通过
- [x] 10.2 `cd packages/agent-eval && PYTHONPATH=src pytest tests/ -q` 全绿
- [x] 10.3 `tests/test_import_isolation.py` 与 `tests/test_vocabulary_isolation.py` 仍通过（协议改造不得把宿主形状写进内核）
- [ ] 10.4 `pnpm --filter eval-dashboard build` 通过（若本期触及呈现层）—— 本期未改 apps/dashboard，未跑
- [x] 10.5 离线 `eval-suite run examples/minimal/suite.yaml` 正常产出，且结论带最弱证据级别标注
- [x] 10.6 `openspec validate separate-collection-from-grading --strict` 通过
- [x] 10.7 体积实测：开启正文采集后单 trial 归档字节数（已知均值 2,352 字符、最大 73,899），据此回答 design 的留存策略待答项

---

## 逐条核对结论（2026-09-06，由 make-published-artifact-match-accepted-code 任务 2.2 执行）

49 条勾选逐条复核（10.4 见下），**无一条虚标**，均有可指认的用例/提交/库记录：

- 1.1 ✓ `ObservedBy` 三级枚举（types.py:111）+ `Observation.observed_by`；测试 `test_same_reading_is_distinguishable_across_levels`、`test_levels_are_ordered_by_strength`。
- 1.2 ✓ `TrialEvidence`（types.py:639）双环境通道分离；`test_two_state_channels_coexist_and_contradict`；gaps 复用 `EvidenceGap`。
- 1.3 ✓ `TrialSession`（contract.py:116，emit/harness_probe/deadline/cancelled 全备）；`test_simple_path_needs_no_session` 证只用返回值仍可接入。
- 1.4 ✓ `TrialResult` 新字段全部带默认值；`test_trial_result_defaults_keep_old_payloads_loadable`。
- 1.5 ✓ `test_subject_level_survives_serialisation`（序列化可区分）、`test_absent_reading_differs_from_empty_reading`（缺失≠空）。
- 2.1 ✓ `contract.py` 的 `run(view, session) -> TrialEvidence`（本变更任务 1.6 已验证两个 HEAD 配对）；`grep tuple\[str contract.py` = 0，无兼容层残留。
- 2.2 ✓ `test_task_view_carries_no_graders_or_answer_keys`、`test_agent_runner_receives_only_the_view`。
- 2.3 ✓ `GraderConfig.evidence_levels`（types.py）+ `test_grader_config_defaults_to_trusted_levels` / `can_narrow_to_harness_only` / `rejects_empty_declaration`。
- 2.4 ✓ `src/agent_eval/examples/mock_runner.py` 实存；三条路径各有用例（`test_simple_path_needs_no_session` / `test_session_path_pushes_and_probes_mid_run` / `test_emit_only_path_lands_in_the_same_channels`）。
- 2.5 过渡步骤（当时允许红），终态由第 10 节全绿覆盖。
- 3.1 ✓ `test_lifecycle_order_is_setup_run_probe_teardown_grade`、`test_end_state_reading_exists_without_probe_implementation`。
- 3.2 ✓ 默认环境探针返回带原因缺失：`test_broken_probe_never_becomes_a_pass`、`test_end_state_reading_exists_without_probe_implementation`。
- 3.3 ✓ (时刻,读数) 序列与三种取值器：`test_end_state_merges_every_probe_channel` / `test_end_state_keeps_the_last_reading_of_each_channel` / `test_created_then_deleted_only_passes_any_time` / `test_not_at_end_reads_the_same_end_state_inverted`。
- 3.4 ✓ `test_leak_check_reads_harness_readings_not_self_report`。
- 3.5 ✓ `test_created_then_deleted_fails_at_end_and_passes_any_time`、`test_any_time_without_mid_run_probe_is_insufficient`、`test_not_at_end_asserts_absence_at_end`。
- 4.1 ✓ 九个内置 grader 全部声明 evidence_levels（artifact_check/code_based/human/metric/model_based/state_check/step_level/tool_calls/transcript）；state_check 优先序 `(HARNESS, RUNNER, SUBJECT)`（state_check.py:62）。
- 4.2 ✓ `test_subject_only_pass_without_escape_switch_is_invalid`、`test_grader_reading_undeclared_level_is_rejected_with_distinct_reason`（原因文案可区分）。
- 4.3 ✓ `allow_subject`（types.py:303、_evidence.py:40-44）+ `test_allow_subject_is_recorded_on_the_run`。
- 4.4 ✓ `test_conclusions_disclose_their_weakest_level`；汇总 `RunSummary.evidence_levels` / `subject_only_trials`（cli.py `_evidence_line` 展示；离线实跑输出 `Evidence: runner=6`）。
- 4.5 ✓ `judgment_moment` 进配置（types.py:308，默认 at_end）与结果（types.py:943）；yaml-format.md 校验表 :83。
- 4.6 ✓ 参数化 `test_same_evidence_three_declarations`。
- 5.1 ✓ `@pytest.mark.parametrize("backend", ["memory", "sqlite"])` + `test_evidence_archived_per_trial_and_retrievable`；活跑 9 trial 落 9 条 `trial_evidence` 行（host 库实测）。
- 5.2 ✓ `test_each_verdict_records_its_caliber`、`test_regrade_appends_and_moves_the_current_pointer`；`fix(storage)` 提交 `9005c0f` 让 `current` 指针从列读回。
- 5.3 ✓ `test_regrade_refuses_when_evidence_is_incomplete`、`test_regrade_requires_the_grader_definitions`；入口 `EvalRunner.regrade_run` + `regrade_state()`（runner.py:201）。
- 5.4 ✓ `test_regrade_never_touches_the_evaluated_system`（mock 断言调用为 0）。
- 5.5 ✓ `test_summary_counts_only_the_current_verdict`。
- 5.6 ✓ `/v1/meta` 能力清单声明 `regrade_over_http: False` / `regrade_over_cli: False` + `not_exposed.regrade` 说明（api/app.py:134-141）。
- 6.1 ✓ 两开关统一为 `CapturePolicy`：`test_capture_policy_defaults_closed` / `test_legacy_bool_means_tool_arguments_only` / `test_capture_policy_inherits_field_by_field`；脱敏标识落盘 `redactor_identifier/version`（types.py:567-568）；core/redaction.py 实存。
- 6.2 ✓ `test_uncaptured_content_yields_evidence_unavailable_not_failure`。
- 6.3 ✓ `test_the_two_switches_stay_independent_and_visible`。
- 6.4 ✓ `test_deleting_a_run_leaves_no_evidence_or_attempt`。
- 7.1 ✓ 宿主 `runner.py`：`session.harness_probe(PROBE_WORKSPACE_FILES)` → `harness_state`（:253/:295），agent 末条回复 → `subject_state`（:320）；整体落盘于本变更任务 1.5 的提交 `657c287`。
- 7.2 ✓ 宿主环境 `probe(channel)` 两条通道；`test_probe_follows_the_current_trial_not_the_caller`（宿主 runner 测试 :615）实存并通过（本次宿主门禁 59 passed 含它）。
- 7.3 ✓ 宿主 `graders/{artifact,dispatch}.py` 补 `evidence_levels` + `implementation_version`（dispatch.py:51/:87/:110，artifact.py:27/:49/:65/:75）。
- 7.4 ✓ **库记录硬核实**：host `.agenthub-data/aeval.db` 中 `run_8ca076ca783a`（9 trial 全 valid、9 条证据行、18 条判定行）与首跑 `run_06a16595008a` 均实存；验收明细在 `host-migration/README.md` 与 Aeval 提交 `e85d279`/`4b4572a`；两个框架缺陷的回归用例即 `fix(orchestration)!`（`a8c9b70`）与 `fix(storage)`（`9005c0f`）。
- 7.5 ✓ 宿主 `docs/eval-harness-design.md` §4.3 已是①修正口径（"口径 (change ① 修正)。旧草图…会把 3 次里蒙对 1 次读成 100%"），版本历史记 v0.16。`eval-harness-design-review.md` 里的旧代码段是**刻意保留**的历史审查快照（"缺陷 1 ✅ 已修复"标注属于当时记录），host-migration/README.md 有说明——不构成本条的虚标，但 see make-published-artifact-match-accepted-code 交接项 7.4 的处理决定。
- 8.1 ✓ `regrade_state()`（"可读"与"可重评"分开判断）+ CLI `Regrade: no — <原因>` 行（cli.py:145-146）+ `test_legacy_run_is_readable_but_not_regradeable`。
- 8.2 ✓ `test_legacy_trials_do_not_gain_verdicts_by_default`。
- 9.1 ✓ integration-guide:212 起新契约图与最小实现。
- 9.2 ✓ docs/grader-reference.md（allow_subject :14、判定时刻、弱证据 :109）。
- 9.3 ✓ architecture.md 三相顺序（:83）与延迟评分（:75、§4.7 :166）。
- 9.4 ✓ README.md:86 / README.zh-CN.md:85「采集与评分分离」；可声明不可强制：zh :84、en :85。
- 9.5 ✓ docs/yaml-format.md（evidence/allow_subject/judgment_moment 与校验规则 :49-84）。
- 10.1 ✓ 本次复跑 ruff 通过。10.2 ✓ 本次复跑 638 passed。10.3 ✓ 两个隔离测试在 638 内。
- 10.4 N/A 确认成立：`git log 4fc28cf^..HEAD -- apps/dashboard` 为空，③ 确实未触及 dashboard，"未跑"标注如实。
- 10.5 ✓ 本次复跑 `eval-suite run examples/minimal/suite.yaml`：6 trial 全 valid，输出带 `Evidence: runner=6` 与 `Regrade: available`。
- 10.6 ✓ 本次复跑 `openspec validate separate-collection-from-grading --strict` 通过。
- 10.7 ✓ design.md:172 留存策略待答项已由实测数据回答（默认口径约 2.1 KB/行；明文钩子 9.1 KB–223.7 KB）。

结论：**49/49 勾选有据，10.4 的 N/A 标注成立，无虚标**。
