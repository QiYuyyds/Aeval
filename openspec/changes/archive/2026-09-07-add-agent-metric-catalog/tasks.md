## 1. 测量上下文与宽签名（唯一不可逆步骤，集中在此）

- [x] 1.1 定义 `MeasurementContext`：任务输入（task id / prompt / 期望输出）、按声明过滤后的观测序列（复用 `Observation`，带来源分级与时刻）、指标自身的声明引用；放在 `core/types.py`
- [x] 1.2 指标取信声明结构：通道级 `evidence_levels` 声明（transcript/steps/harness_state/subject_state），缺省 = 仅最终输出；装配期校验复用 ③ 的校验器（未知通道/空声明/重复报错并列可选值）
- [x] 1.3 `Metric.measure(ctx: MeasurementContext)` 替换旧五字符串签名，删除旧路径（不留兼容层、不留继承桥）；`MetricGraderAdapter` 同步改造，`to_grader()` 对外形状不变
- [x] 1.4 迁移全部内置指标到新签名（answer_relevancy / faithfulness / context_precision / context_recall / llm_judge / prompt_metric / batch_evaluation / synthetic_data / pytest_plugin 触达处逐个过）
- [x] 1.5 上下文观测过滤实现：未声明通道不交付（harness 读数不出现）；声明了不存在/空/重复级别装配期报错
- [x] 1.6 单测：旧签名注册报错并说明新签名形状；默认声明下 `MeasurementContext` 交付内容与 0.2.0 的五参逐字段等价；声明 transcript 后观测含消息序列且每条带 `observed_by`

## 2. judge 轨迹注入

- [x] 2.1 `metrics/llm_judge.py`：声明含 transcript/steps 时，把轨迹（经脱敏钩子后）注入 judge 提示词；未声明时提示词与 0.2.0 逐字节等价
- [x] 2.2 judge 结论的 `explanation` 可引用轨迹事件（提示词要求引用具体消息；不强制）
- [x] 2.3 单测：声明轨迹的 judge 收到完整轨迹且脱敏生效；未声明的 judge 提示词不含轨迹内容（用 fake llm_fn 断言收到的 prompt）

## 3. 诊断 / 判分角色轴

- [x] 3.1 指标注册与 `MetricResult` 增加 `role: diagnostic | judging`；轨迹类指标注册缺省 `diagnostic`，判分路径只接受 judging（或经套件升格）
- [x] 3.2 套件升格语法：判据里显式引用指标为判分量（经 `to_grader()` 走判分流程，带取信声明）；YAML 校验规则与错误文案
- [x] 3.3 汇总新增诊断块（分值 + 分布摘要），不进通过率 / pass^k / 分母 / 判分聚合；CLI 与报告默认折叠，`--verbose` 展开
- [x] 3.4 历史回读兼容：无诊断块字段的旧 run 汇总读为空、κ/α 读 `None`、不报错（测试固化）
- [x] 3.5 端到端：启用若干诊断指标的 run 与未启用的 run，通过率/pass^k/分母逐字段一致；升格一个指标后它出现在判分块

## 4. reward_basis 乘性安全门

- [x] 4.1 套件判据 `gate: {factor}` 声明 + task/run 级 `reward_basis: additive|multiplicative`（缺省 additive）；YAML 校验：因子 ∈ [0,1]、乘性时至少存在一个非门判据、门判据必须有取信声明
- [x] 4.2 合成实现：multiplicative 时总分 = 基础分 × ∏(生效门因子)；门未生效（证据级别不满足/证据缺失）不乘因子并在结论标注原因
- [x] 4.3 `GraderResult` 增加门结果字段（因子、生效与否、原因、所依据级别），随 run 落盘、报告与 API 可见
- [x] 4.4 单测：门失败塌缩总分；证据不足门不生效不冒充；subject 级证据不得触发门；additive 套件分数与 0.2.0 逐位一致
- [x] 4.5 与 ③ 语义的交互测试：门的取信声明走同一套 evidence_levels / allow_subject / 判定时刻机制（门是判据，不新造第二套）

## 5. 跨评分者一致性 κ/α

- [x] 5.1 手写 Cohen's κ（二值/序数、未加权）与 Krippendorff's α（nominal/ordinal、容忍缺失），纯函数 + 已知小样本对照表测试（含文献经典数值）
- [x] 5.2 判据级多评分者配置：≥2 个 judge 定义（不同模型/提示词）跑同一批 trial，评分按 trial 对齐
- [x] 5.3 `RunSummary` 新增 agreement 字段：κ（两评分者）/ α（≥2 或含缺失）；评分者不足或样本过少 → `None` + 复用 `insufficient_data` 原因语义
- [x] 5.4 self-consistency 标注：单评分者多采样的 `confidence` 在报告与 API 呈现时标注 self-consistency，与 agreement 分块呈现，不混排
- [x] 5.5 单测：两评分者已知样本 κ 吻合对照表；含缺失评分的 α 吻合；单评分者场景 κ/α 为 None 且带原因

## 6. 文档与迁移说明

- [x] 6.1 `docs/integration-guide.md`：指标作者迁移指南（旧五参 → MeasurementContext 字段对照表、声明写法、升格写法）
- [x] 6.2 `docs/grader-reference.md` / `docs/yaml-format.md`：gate 与 reward_basis 的 YAML 语法、校验规则表、诊断/判分块说明
- [x] 6.3 迁移说明（getting-started 升级章节追加 0.3.0 段）：签名断裂、无 legacy 旗标、confidence 语义澄清（自一致 ≠ 信度）、诊断块不改变历史口径
- [x] 6.4 README（中英）Features 一行：证据感知指标 + 跨评分者一致性

## 7. 验证门

- [x] 7.1 `ruff check packages/agent-eval` 通过
- [x] 7.2 `cd packages/agent-eval && PYTHONPATH=src pytest tests/ -q` 全绿（含新指标/judge/门/κ/α/兼容性测试）
- [x] 7.3 `tests/test_import_isolation.py` 与 `tests/test_vocabulary_isolation.py` 仍通过
- [x] 7.4 离线 `eval-suite run examples/minimal/suite.yaml` 正常产出，报告出现诊断块且分母与 0.2.0 一致
- [x] 7.5 `openspec validate add-agent-metric-catalog --strict` 通过

## 8. 宿主真流量验收（归档前置；本组是 ④ 的唯一真验收）

> 补记（2026-09-06）：原清单漏写活跑项——①②③ 的致命缺陷全部是单测绿、活跑暴露
> （provider 不交付属性 / token 分桶 / 进程 locality），④ 的四类新机制（judge 看轨迹、
> 诊断轴、κ/α、乘性门）不验真流量就不许归档，同理。

- [x] 8.1 宿主新增验收套件：真用 `type: metric` 判据（judge 声明读轨迹）+ `reward_basis: multiplicative` 安全门（真流量上可能失败的门，如产物禁含密钥类内容）+ ≥2 个独立 judge 定义产 κ/α 数据；`eval-suite validate` 通过
  - 完成（宿主提交 `2fd94ca`）：`metric-acceptance-suite.yaml`（全量：`process_quality` 轨迹指标 + lenient/strict 双 rater 阈值 0.3/0.9 产 κ/α + factor=0.0 `not_contains` 泄密门 + answer_relevancy 仅诊断）VALID；配套宿主自有轨迹指标 `app/eval_integration/metrics.py::ProcessQualityMetric`（`evidence_levels=("transcript",)`，注册进 runner registry——内置指标无一读轨迹，轨迹注入必须宿主自建才有真流量路径）；`run_first_suite.py` 参数化套件路径。第 1 轮门优先变体 `metric-acceptance-gateonly.yaml` 亦 VALID。
- [x] 8.2 宿主 venv 临时切回 editable（本变更已提交的 0.3.0 树；PyPI 上没有 0.3.0）——验收后随发版切回 `==0.3.0`
  - 已执行：uninstall PyPI 0.2.0 → `pip install -e` 指向本仓库已提交的 0.3.0 树，`pip show` 确认。
- [x] 8.3 取得用户授权：真实 agent 调用 + judge 的真实 LLM 调用（多 judge 使 judge 调用量 ≥2×，成本预估先行）
  - 完成（2026-09-07）：宿主栈恢复（postgres/phoenix/neo4j/milvus Up、后端 :8000 响应、`EVAL_AGENT_ID=ag_yTc8OAQE5Uzi` 活的 coder、宿主 venv = 本仓库 0.3.0 editable 树）。成本预估先行并按调用数报清：第 1 轮 3 次 agent / 0 次 judge，第 2 轮 3 次 agent + 9 次 judge（每 trial 双评分者 + answer_relevancy 诊断各 1 次）。用户授权**两轮都跑**，judge 凭证**内联传入不落盘**。
  - 但 judge 凭证实测不可用：`backend/.env` 的 `AEVAL_JUDGE_* / EVAL_LLM_* / OPENAI_API_KEY` 三档后备全空；`.env.local:110` 的 `JUDGE_LLM_API_KEY`（LongCat，26 字符→解析后 32）smoke test 返回 **HTTP 402 `Insufficient token quota`** —— 配额耗尽，不是配置缺失。agent 侧不受影响（走 SQLite `model_profiles` 默认 profile，非 .env）。
- [x] 8.4 真流量活跑：记录 run id；验证诊断块不进分母、门因子塌缩、κ/α 在真实 trace 上与单测语义一致；report/API 可读
  - 第 1 轮（门优先，无 judge）**已完成** —— `run_0b4bbb66f8ec`（status=completed，111.7s，valid=3 / invalid=0 / pending=0）：
    - 门塌缩成立：基础判据 `transcript` 真跑分 0.9048/0.9352/0.9354 且 `passed=True`（`observed_by=runner`，turns/tokens/redundancy 由真实 Phoenix span 算出），门 `code_based` `gate_applied=True` factor=0.0 → 3/3 trial 总分 = 0，汇总 avg_score=0.0 / pass@1=0.0%。
    - 归因正确：塌缩没有把 trial 折成 invalid（valid=3），即「被评方失败」而不是「评测侧缺料」；统计版本 2、分母 3 与未启用门的口径一致。
    - report/API 两条腿均可读：`eval-suite show` 与 `GET /api/eval/runs/{id}`（宿主自签 token 打活的 :8000，HTTP 200）都能拿到该 run。
  - 活跑暴露两处**单测与 validate 都抓不到的套件写法缺陷**（已在宿主侧修，见 8.5 附记）：判据 `name` 是注册表键而非标签（`doc_content`/`no_secret_leak` → `unknown_grader` → 3/3 trial invalid，门根本没评到）；`not_contains target transcript` 的门把**任务 prompt 自带的凭据**算成 agent 泄漏（转储含 user 消息），而 `target: outcome` 走 subject_state、缺省取信声明读不到东西会假通过。
  - 第 2 轮（全量 judge）**已完成** —— `run_cc6123e09fdd`（status=completed，322.6s，valid=3 / invalid=0，pass@1=33.3%，avg_score=0.3333）。judge 凭证按用户授权内联复用 agent 的默认 `model_profiles` profile（LongCat，与 agent 同源）；`.env.local` 那份已 402 耗尽。
    - 门的两半分支都在真流量上取到：trial1 agent **没有**复述凭据 → `gate_applied=False`「门通过: 因子未乘入」→ `synthesized_score=1.0`、`success=True`；trial0/2 真泄漏 → `gate_applied=True` factor 0.0 → 总分 0。报告门汇总一行同时呈现两种：`code_based: factor=0.0 生效 2 次 / 未生效 1 次 (未生效原因示例: 门通过: 因子未乘入)`。
    - 宽签名 + 轨迹注入生效：`process_quality`（`evidence_levels=("transcript",)`）每 trial 收到 `observed_count=2` 条带来源前缀的轨迹观测，judge 提示词含逐条轨迹（宿主指标自己按 `[来源|通道|通道名]` 前缀拼装）。
    - 多评分者 κ/α：**对齐与选择逻辑在真实 trace 上与单测一致**，数值未取到 —— `agreement = {measure: None, value: None, reason: "insufficient_data: 对齐样本过少 (3 < 5)", raters: 2, aligned_samples: 3}`。`MIN_ALIGNED_RATINGS_FOR_AGREEMENT = 5` 而套件 `max_trials: 3`，走的是不伪造数值的 None+原因路径。
    - 诊断块不进分母：`answer_relevancy` 诊断得 n=2 / avg=0.875 / min 0.8 / max 0.95 / **errors=1**（一次 60s 超时按 error 计，不冒充 0 分），而分母与判分聚合仍按 valid=3、avg=0.3333 计 —— 诊断量完全没进任何分母。
    - report/API 可读：`eval-suite show`（折叠 → 展开 `--verbose`）、`render_run_report` markdown、API `GET /runs/{id}` 三条腿都读到该 run，且 API 现在带 `synthesized_score` 与门三字段。
  - 第 3 轮（补齐数值 κ，`max_trials: 5` + strict 阈值 0.9→0.98）—— `run_5cef385f413d`（606.3s，valid=4 / **invalid=1**，pass@1=25%，avg=0.25）：
    - **真分歧取到了**：trial0 与 trial3 是 `lenient=pass / strict=fail`，trial2/4 一致 pass/pass；门同样两半都有（trial4 门通过 → `synthesized_score=1.0`，其余塌缩）。阈值跨过实测分数带（process_quality 只出 0.95/1.0）才可能分歧，这一点已写进 grader-reference。
    - **数值 κ 仍未发布**：trial1 的 `lenient` 一格抛异常（异常消息为空）→ 该单元不算对齐 → `aligned_samples=4 < 5` → `agreement.value=None` + 同样的是 insufficient_data 原因。机制没坏：把这 4 格真实判定直接喂给 `cohen_kappa` 得 **0.0**（agree 3 / disagree 2），补成 5 格时 `agreement_report` 正常出 `measure=cohen_kappa, value=0.0` —— 挡住的只是那条 ≥5 的发布下限。
  - 第 4 轮 = 对 `run_5cef385f413d` 走 **③ 的库层重评**（`regrade_run`，证据已归档 ⇒ **0 次 agent 调用**，只重判 5 trial）：
    - **κ 在真流量上发布成功**：`agreement = {measure: "cohen_kappa", value: 0.0, aligned_samples: 5, agree: 2, disagree: 3, reason: null}` —— 不是全一致退化成 1.0 的退化样本，而是含 3 个真实分歧的 5 格对齐。
    - 重评没触碰被评系统（agent 调用 0），历史并列保留（该 run `grade_attempts` 从 50 增至 60 行，原结论未被覆盖）。诊断块这次 `n=1 / errors=4`（answer_relevancy 对 judge 延迟敏感），仍按 error 计不冒充 0 分、不进任何分母。
    - 该 run 存盘的 `valid=4 / invalid=1` 是**这次重评跑在粘滞修复之前**的结果（trial1 两个判据都已 valid，trial 级仍挂 `grader_error`）—— 这正是第 6 个缺陷的实测样本，另起重评即可收敛为 valid=5。
- [x] 8.5 结果写回本清单（勾选 + run id + 判读）；发现缺陷先修复再归档——本组完成是 `openspec archive add-agent-metric-catalog` 的前置条件
  - 缺陷修复（**框架侧 6 处**，全部由活跑暴露、单测与 `validate` 均未抓到）：
    1. `core/runner.py` 未注册判据分支绕过 `_annotate_gate` → 声明了门的判据连门字段都不落盘，安全门能从记录里静默消失（违反 spec「每个门的因子、生效结果 MUST 随结论落盘」）。已补路由 + `test_unregistered_gate_still_records_gate_declaration`。
    2. `api/routes/runs.py` 手写投影漏 `gate_factor` / `gate_applied` / `gate_reason` → 门结果 API 不可见（违反 4.3）。已补投影 + `test_gate_result_visible_in_api`。
    3. trial 总分不落盘：呈现层只能读 `avg_score()` 的 grader 简均（门塌缩的 run 上显示 0.4524，而汇总是 0.0）→ 新增 `TrialResult.synthesized_score`（`total_score()` 读侧回退，历史 run 读为 `None`）+ API 新增同名键（既有 `score` 语义不变）+ CLI 下钻与 task history 改读总分。测试：`test_failed_gate_collapses_total_score` 扩展、`test_total_score_falls_back_for_legacy_runs`。
    4. `cli.py show` 读回存盘 run 时不呈现诊断块与 agreement（3.3 要求「CLI 与报告默认折叠、`--verbose` 展开」）→ 抽出 `_print_diagnostics_and_agreement` 供 `run`/`show` 共用 + `show --verbose`；顺带修 agreement 在不可计算时渲染成 `None = insufficient_data (insufficient_data: …)` 的重复标签。测试：`test_show_renders_diagnostic_and_agreement_blocks`。
    5. `_multi_rater` 的评分者故障原因会丢（第 3 轮实测：结论只写下「评分者 'lenient' 评分失败: 」，冒号后为空 —— 异常 `str()` 为空）；且失败的不是首个定义时**整条原因根本进不了结论**（返回的是 `results[0]` 的副本，后一格只留 `None`）。已改为空消息回退 `repr(e)` 并把各评分者的故障消息汇进 `details["rater_errors"]`。测试：`test_rater_failure_reason_reaches_conclusion`。
    6. `core/metrics.py:classify_trial` 在**有 grader 结论时仍让 trial 级 INVALID 标志优先**（第 105 行；与它自己的文档「重算时以 grader 为准」相反）→ 库层重评永远修不好一次评测侧故障：trial1 重评后两个判据都 valid，trial 级却仍挂 `invalid / grader_error`，被永久挡在分母外。修法：重评前重置**由判定产生**的无效原因（`grader_error` / `grader_timeout` / `unknown_grader`），采集相的原因（超时/外部依赖/取消/预算）不动 —— 那些 trial 本就没有 grader 结论，重评循环已经跳过。测试：`test_regrade_repairs_a_trial_the_last_grading_pass_marked_invalid`（把修复用的集合在内存里清空后该测试确实失败，已核实）。
    - 验证门：`PYTHONPATH=src pytest tests/ -q` **696 passed**、`ruff check packages/agent-eval` 通过、`openspec validate add-agent-metric-catalog --strict` 通过。文档：`docs/getting-started.md` 0.3.0 段第 5 条、`docs/grader-reference.md` 门小节补总分口径与两条实测陷阱、多评分者小节补 κ/α 的两个实践前提。
  - 宿主侧：两套件判据改名（`name` 是注册表键）并把门检查重写为「只对 agent 消息取判」的负向前瞻正则；判别力离线核实过（干净回复→门通过、复述凭据→塌缩），真流量两半分支均已取到。
  - 原「数值 κ 未发布」缺口 **已由第 4 轮库层重评关闭**（`cohen_kappa=0.0`，5 格对齐含 3 个真分歧）。留一处纯外观余项：`run_5cef385f413d` 存盘的分母仍是 `valid=4 / invalid=1`，因为那次重评跑在缺陷 6 修复之前；在修复后再重评一次即可收敛为 `valid=5`（成本 ≈ 15–20 次 judge、0 次 agent），不影响任何结论。
  - 核对为非缺陷的两处：诊断块里 `role: "judging"` 是指标**注册表角色**（该块本身即说明本次按诊断运行），非自相矛盾；超时诊断的 `score=0.0` 伴 `error` 非空是 ③ 前既有约定（`MetricResult.score` 字段文档写明「error 非空时无意义」），且汇总按 `errors=1` 排除在 `n` 之外，未污染分布。
