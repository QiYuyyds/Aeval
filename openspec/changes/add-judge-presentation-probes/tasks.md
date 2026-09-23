## 1. 探针算子协议

- [x] 1.1 定义算子协议：每个算子 MUST 携带一句**保持的不变量**声明与一份变体构造（输入同一份判分配置，输出 N 个逐字节可复现的变体）。无不变量声明的算子在构造期即被拒绝，不留到运行时 —— `graders/presentation_probes.py`：`PresentationProbeOperator`（ABC）的 `__init__` 校验 `name`/`invariant`，缺声明直接 `ValueError`；`variants(presentation)` 为抽象方法，未实现者在实例化时即失败。测试 `TestOperatorProtocol::test_operator_without_an_invariant_is_rejected_at_construction`
- [x] 1.2 确认协议**不含**第三方注册表面（无 entry-point 组）—— Non-Goals 明确排除，加了就是提前承诺扩展形状 —— `discovery.EXTENSION_KINDS` 仍是 `graders`/`environments`/`simulators` 三项，`presentation_probes` 模块零 `register*` / `discover*` / `*ENTRY_POINT_GROUP` 符号；由 `test_protocol_exposes_no_third_party_registration_surface` 钉住
- [x] 1.3 把 design D2 那张判读表落成文档：七个候选算子、纳入两个、拒绝一个、三个因无宿主或破坏不变量出局。这张表是设计的核心产物，不能只活在 design 里 —— 全文落 `docs/grader-reference.md` 新增节「判分呈现探针 —— 同一个 judge 对无关呈现敏不敏感（0.4.0）」的表格（归档后 design.md 会移入 `openspec/changes/archive/`，表格留在 docs 里）；`graders/model_based.py` 节里加了一句指向它，并注明那个预填值是框架自己写进去的显式锚

## 2. 两个算子

- [x] 2.1 `anchor_value`：把 `graders/model_based.py:165` 的模板预填值参数化（默认值使提示词与今天逐字节相同），变体为 `0.0` / `1.0` / 不给值 —— `_build_prompt(..., anchor_value=DEFAULT_JUDGE_ANCHOR_VALUE)`，默认 `"0.0"`；`None` 渲染成 `{"quality": ...}`（不给值）。"不给值"刻意不读成配置项：**没有**新增 `config["anchor_value"]`，那会是一个没验证过的 YAML 表面（D7）
- [x] 2.2 `dimension_order`：变体为配置顺序与逆序。其不变量声明 MUST 含准确措辞 —— "各维度独立打分后取平均；**求和次序造成的末位浮点差异不计为呈现敏感**"（`_parse_scores` 按 `for dim in dimensions` 建字典，`sum(scores.values())` 不可结合，见 design D2）。声明与报告输出都要带这句限定，不得只写在 design 里 —— `DIMENSION_ORDER_INVARIANT` = `各维度独立打分后取平均；` + `SUMMATION_ORDER_QUALIFIER`；`DimensionOrderProbe.__init__` 在限定语缺失时抛错（`test_declaration_must_survive_a_subclass_editing_it`）。限定语出现在三处：算子声明、`PresentationOperatorReport.invariant`、以及 `reason` 文案本身（`core/metrics.py` 两条 status 分支都把 invariant 拼进判读句）
- [x] 2.2b 确认**没有**为了让该算子干净而去改 `avg_score` 的求和方式 —— D2 已否决那条出路（框架今天在这件事上是确定性的，不存在待修缺陷；为探针改框架算术方向是反的）。若有人提议顺手修，指回 D2 —— `avg_score` 表达式原样搬进 `score_dimensions()`（同一表达式、同一分母语义），`grade()` 与探针共用它；`test_summation_order_follows_the_presentation_not_the_argument` 钉住"求和序 = 呈现序"这一事实本身。**顺带量到的结果（D2 未预料）**：逆序求和的末位差异在 judge 真会给的值域上不存在 —— 一位小数、长度 ≤5 的全部 177,144 个向量与两位小数三元组的全部 999,900 个排列，正序与逆序逐位相同（`test_reverse_order_summation_moves_nothing_on_the_values_judges_emit`）。所以这条轴今天**构造不出**由算术引起的结论翻转；限定语仍然留着，因为下一个人无法自行推断这件事
- [x] 2.3 两个算子的变体构造各自加一条逐字节可复现测试（同一输入两次构造结果相等），且纳入 P0 那条跨进程一致性测试的覆盖面 —— 进程内：`TestVariantConstructionIsByteReproducible::test_same_config_constructs_the_same_rendering`（两个算子各参数化一次，比较渲染字节）；跨进程：`tests/test_prompt_determinism.py` 的 `_build_in_child` 新增 `probe_variants` 分支 + `test_probe_variant_rendering_is_byte_identical_across_two_processes`（两个独立随机 hash 种子的子进程构造出逐字节相同的五份呈现）
- [x] 2.4 确认默认关闭时 `model_based` 的判分输入与 P0 落地后**逐字节相同** —— 探针不得改变不开探针时的任何行为 —— 把 P0 -era 提示词整份冻成字面量 `P0_FROZEN_PROMPT`，两条测试分别打生产辅助函数与**真实 `grade()` 路径**（用记录型 llm_fn 抓实际发出的字符串）：`test_default_prompt_equals_the_frozen_p0_bytes` / `test_the_production_grade_path_sends_exactly_the_frozen_prompt`。`implementation_version` 因此**保持 3** 不动（判分输入没变，提版会把不该归因的东西归因一次）

## 3. 呈现不变性报告

- [x] 3.1 新建报告类型（**不改** `AgreementReport`），直接复用 `cohen_kappa` / `krippendorff_alpha` —— `core/types.py` 新增 `PresentationInvarianceReport` + `PresentationOperatorReport`；`AgreementReport` 一字未动（`git diff` types.py 为 `94 0`：纯新增）。`core/metrics.py::presentation_operator_report()` 直接调那两个函数：两呈现无缺失 → κ，≥3 呈现或含缺失 → α（nominal）
- [x] 3.2 翻转定义与统计口径同源：结论跨过 `threshold` 才算翻。分数移动量作为另一维度单独呈现，不与翻转混计 —— 结论由 `judge_presentation()` 用生产的 `threshold` 与 `score_dimensions()` 算出；`DriftOnlyJudge`（0.78 → 0.72，阈值 0.7）报 `not_detected` 且 `flipped_trials=0`、`max_score_shift≈0.06` —— 正是 statistics spec 第三个场景（`test_flip_is_declared_only_when_the_conclusion_crosses_the_threshold`）
- [x] 3.3 对齐样本不足时复用既有 `MIN_ALIGNED_RATINGS_FOR_AGREEMENT` 语义，报"样本过少 + 原因"，不伪造数值 —— 8 trial × sample 0.5 = 4 对齐 < 5 → `not_computable` + `"不可计算: 对齐样本过少 (4 < 5)"`，`value`/`measure` 均为 None（`test_aligned_sample_floor_is_reused_and_says_so`）
- [x] 3.4 `sample` 与 `seed` 均为必填、内核不给默认（D5）。报告里落 seed，使抽样本身可复现 —— 两者都是 keyword-only 且无默认值（`inspect.signature` 断言 + 漏传时 `TypeError`）；`sample` 越界抛 ValueError 并点名 D5 的理由；`draw_trials` 同种子同批、换种子换批（`test_sample_and_seed_have_no_kernel_default` / `test_sampling_is_reproducible_by_seed_and_actually_samples`）
- [x] 3.5 成本报价在调用点显式给出（份数 × 抽中 trial 数），不藏进配置 —— `presentation_probe_cost_quote(n_trials, sample)` → `{variants_per_trial: 4, trials_sampled, judge_calls}`；报告自带这三个字段；入口脚本 `examples/presentation-probes/` 先印报价再开跑。跨算子按呈现身份去重（基线被两个算子共享），所以份数是 4 而不是 3+2=5 —— 与 D3 的"基线 1 + 锚定变体 2 + 逆序变体 1"逐字对上
- [x] 3.6 报告把「未检出敏感」与「不可计算（无凭证/样本不足）」分成两种不同措辞的输出，不得共用一个空值 —— 前者是结论，后者是没测 —— `status ∈ {sensitive, not_detected, not_computable}` 三态各配固定文案（分别以「检出呈现敏感」/「未检出呈现敏感」/「不可计算」开头）；`UnavailableJudge`（抛 401）→ `not_computable` 且 reason 里点名首个失败原因，与 `BodyOnlyJudge` 的 `not_detected` 走两条分支（`test_unavailable_judge_is_reported_as_not_measured`）

## 4. 双向替身验证（先写测试，后实现）

- [x] 4.1 先写**构造敏感**替身：判据按定义对锚定值反应。断言探针 MUST 报出敏感 —— 此时应红 —— `AnchorReactiveJudge`（分数回归到示例值，读不出锚时退到 0.5）。**已观察到红**：首轮实现前的 collection 失败为 `ModuleNotFoundError: No module named 'agent_eval.graders.presentation_probes'`
- [x] 4.2 再写**构造不敏感**替身：判据只读证据正文。断言探针 MUST NOT 误报 —— 这条防的是"总能报出点什么"的探针 —— `BodyOnlyJudge`（只读 `- 输出:` 行）；两个算子都必须 `not_detected`、`flipped_trials=0`、κ/α=1.0，且 reason 必须以「未检出」开头
- [x] 4.3 实现到 4.1 / 4.2 双绿。两条测试都必须在不依赖任何 API 凭证的前提下跑通 —— 双绿，全程 `llm_fn` 替身、零凭证、零网络。`29 passed`（`tests/test_presentation_probes.py`）
- [x] 4.4 `dimension_order` 复用同一对替身，验证协议承载第二个算子时不需要改探针本体（这是 D3 要两个算子的全部理由，必须被实际观察到）—— `run_presentation_probes(operators=[DimensionOrderProbe()])` 在**不改动 `run_presentation_probes` 一行**的前提下跑通三份测试：单算子报告（份数 2）、锚定敏感不被抹到次序轴（该替身在这条轴上 `not_detected`）、单维度配置下构造不出第二份呈现且报价随之降到 3。第三点尤其重要：它证明份数是从配置**算**出来的，不是写死的常数
- [x] 4.5 在测试与文档里都写明：**这组数字证明机制有效，不证明真实 judge 敏感或不敏感**。最容易被误读的地方不能只靠 design 兜 —— 三处都写了：报告对象的 `interpretation_note`（随每次运行呈现，`test_report_states_its_own_interpretation_limit` 断言其存在与措辞）、测试文件 docstring、`docs/grader-reference.md` 的「这套数字证明什么、不证明什么」节 + `examples/presentation-probes/README.md` 的对照表

## 5. 硬规则：探针不产出结论

- [x] 5.1 探针调用 MUST NOT 产生 `TrialResult` / `GraderResult` 结论，MUST NOT 进入 `grade_attempts`，MUST NOT 移动 `current` 指针 —— 探针的读数类型是 `PresentationJudgment`（dataclass，注释里点名它不是判定条目）；`a_report_contains_no_verdict_bearing_object` 递归走完报告对象图断言零个 `GraderResult`/`TrialResult` 且无 `grader_results`/`verdict`/`invalid_reason`/`success` 字段；`test_the_entry_point_cannot_reach_anything_that_records` 是结构上门：入口签名里没有 `storage`/`runner`/`run_id`，探针拿不到能写判定的东西
- [x] 5.2 一条测试断言：同一 trial 开探针与关探针，判定条目数、通过率分母、`statistics_version` 完全一致 —— 真跑一遍 `EvalRunner` + `MemoryStorage`（MockAgentRunner + 真 `model_based` 判据），快照 attempt 条目 id 集、`current` 指针（含 attempt_id，否则同 trial 被追加时看不出来）、逐 trial `model_dump()`、逐 task `(valid_trials, total_trials, pass_at_k, avg_score)`、κ/α 的 `agreement` 块、`statistics_version`，然后**对真实 run 的 trial** 开探针再快照一次：逐项相等（`test_probe_changes_neither_entries_denominators_or_statistics`）
- [x] 5.3 确认 `core/runner.py` 的 `CALIBER_AXES` **未新增**第六根轴 —— 探针不产出 verdict，没有翻判要归因（proposal 的 Impact 已记此点，防止有人"顺手补上"）—— `CALIBER_AXES` 仍是五根（断言逐字相等），另加一条正向防"顺手补上"：轴名里出现 `probe|presentation|bias|anchor` 即红
- [x] 5.4 反向验证：临时让探针结果进 `grade_attempts`，确认 5.2 那条测试真的变红（同 P0 的 2.3 / 8.6 手法——测不到缺陷的守卫测试比没有测试更糟）—— **手法换了，理由记下**：P0 那次注回的是"一个已修的缺陷"，这里的"缺陷"恰好是 spec 明令 MUST NOT 的行为（探针产出结论），哪怕只注一瞬间也要往生产路径里写结论代码 —— 权限分类器也拦下了那次临时注入。于是把它升成常驻测试 `test_the_guard_actually_watches_the_leak_path`：在测试侧模拟两次泄漏（读数就地写进被评 trial；结论进 `grade_attempts`），断言 5.2 看着的每一项都动 —— `trials` 快照变、`attempts` 多一条、`current` 指针里恰好只有 `("t1", 0)` 搬家。**这条测试顺带暴露了快照自身的一个盲区**：`(task_id, trial_index)` 集合在同一条 trial 被追加判定时不变，所以"没移动指针"这件事只有把 `attempt_id` 收进快照才看得见
- [x] 5.5 确认探针不影响跨 run 可比判定：`EvidenceBoundary` 与 `not_comparable_reason` 在开探针时不产生新差异维度 —— 被探针读过的 run 与一个全新 run 走 `runs_comparable()` → `comparable=True`、`reason=None`、两份 `evidence` 边界逐字段相等（`test_run_read_by_a_probe_stays_comparable_to_a_fresh_run`）

## 6. 回归与静态门

- [x] 6.1 `cd packages/agent-eval && pytest tests/ -q` 全绿 —— **898 passed in 39.65s**，0 failed、0 skipped（P0 记的是 868；本变更 +29 条探针测试 +1 条跨进程呈现测试）
- [x] 6.2 `ruff check packages/agent-eval` 双范围干净 —— 仓库根 `ruff check packages/agent-eval` 与包内 `ruff check src tests` 均 All checks passed（过程中修掉一处 `UP012`：`.encode("utf-8")`）
- [x] 6.3 确认 `storage/` 未被改动（不新增表、不碰既有表 —— 该模块无迁移机制，任何表结构改动都不可逆）—— `git status` 里 `storage/` 零改动；本变更落库面为 0（探针不落库、不持久化）
- [x] 6.4 确认 `suite.py` / YAML 校验、`cli.py`、`api/` 均未被改动（无表面）—— 改动面只有六项：`core/{metrics,types}.py`、`graders/{model_based,presentation_probes}.py`（后者新建）、`tests/{test_presentation_probes.py（新建）,test_prompt_determinism.py}`，外加 `docs/`、`research/`、`examples/presentation-probes/`（新建）
- [x] 6.5 确认未夹带任何判分**内容**改动：锚定模板默认值、系统提示词、无截断策略一律保持原样 —— `model_based.py` diff 为 `95 8`，八处删除全是等价搬家（默认值改引用常量、`self._llm_fn or ...` 抽成 `resolve_llm_fn()`、`avg_score` 抽成 `score_dimensions()`、f-string 里那个字面量换成同名默认值的参数）；`DEFAULT_JUDGE_ANCHOR_VALUE="0.0"`、`SYSTEM_PROMPT="You are an evaluation expert."`、`DEFAULT_THRESHOLD=0.7` 三个值原样，模板正文与 `implementation_version` 未动，截断策略未新增

## 7. 记录与遗留缺口

> 以下数字全部由 `examples/presentation-probes/measure_presentation_sensitivity.py`（零凭证，可重跑）产出，方法与 `tests/test_presentation_probes.py` 同源。
> 判据是**构造性替身**，不是真实模型 —— 每张表右列写的就是它能被引用成什么。
>
> | 替身 | 算子 | status | 统计量 | 翻转 | 分数位移 max | judge 调用 | 能被引用成 |
> |---|---|---|---|---|---|---|---|
> | `anchor_reactive` | `anchor_value` | **sensitive** | α = **−0.4167**（3 份呈现） | **6/6**（flip_rate 1.0） | 1.0 | 24 = 4 份 × 6 trial | 探针**能发现**敏感 |
> | `anchor_reactive` | `dimension_order` | not_detected | κ = 1.0 | 0/6 | 0.0 | 同上 | 逐算子各报各的：锚定敏感没有被抹到次序轴 |
> | `body_only` | `anchor_value` | not_detected | α = 1.0 | 0/6 | 0.0 | 24 | 探针**不虚报**敏感 |
> | `body_only` | `dimension_order` | not_detected | κ = 1.0 | 0/6 | 0.0 | 24 | 同上 |
>
> α 为负不是 bug 而是这件事的正确形状：三份呈现里 `锚定 1.0` 那一份**恒**判通过、另两份**恒**判不通过 —— 分歧是系统性的、由呈现决定的，比"随机不一致"更值得修，所以报负 α 而不是把它当成噪声摊平。翻转数（6/6）不依赖统计量够不够，样本不足时也不会被读成"没翻"。

- [x] 7.1 记录替身测出的两份数字（构造敏感 / 构造不敏感），并逐条标注其证明范围，沿用 P0 的 6.2 那句话的形状：「不是真实 LLM 的翻转率……测不出来，不是没测」—— 见上表与其右列的"能被引用成"。同一句话还印在三个地方：报告对象的 `interpretation_note`、测试文件 docstring、`docs/grader-reference.md`。**另记一条实现事实**（它收窄了 proposal 第 13 行为 `dimension_order` 举的动机）：等权平均对维度排列是**对称**的，所以"首因效应"在任何逐维打分上得到的排列下都不会移动合成分；加上 2.2b 量到的"逆序求和逐位相同"，这一轴在二元结论上今天**双向不可翻**。要让它真能测出首因效应，需要逐维读数或加权聚合 —— 那是第一次真测量之后要不要改的东西，不在本变更范围内动分母口径
- [x] 7.2 已知缺口如实写进记录且**不假装闭合**：真实锚定敏感度需一把可用的 judge 凭证，宿主四把候选现全为 401/402 —— 缺口未闭合。`--live` 分支今天**未被执行**（本仓库的验证不执行真实 API 调用，零凭证下的失败路径由 `test_unavailable_judge_is_reported_as_not_measured` 用一个抛 401 的替身钉住：报告必须是 `not_computable` 且 reason 里带出失败原因，而不是冒充"未检出"）。入口脚本在无 `OPENAI_API_KEY` 时也会印出这句话而不是静默出数
- [x] 7.3 入库一个零凭证下可跑、有凭证后可直接重跑真实测量的入口脚本（P0 的 D7 教训：验收记录不得引用只存在于本机的代码）—— `examples/presentation-probes/{measure_presentation_sensitivity.py,README.md}`（与 `examples/prompt-determinism/` 同形：目录 + README + 脚本，脚本自己把 `packages/agent-eval/src` 加进 `sys.path`）。默认模式**自校验**（敏感没报出来、或不敏感被误报 → 退出码 1），`--live` / `--model` / `--trials-json` / `--sample` / `--seed` 为真测量留好了口子。已实测：默认模式 24 次调用、`机制自校验: 通过`、退出码 0；`--trials-json` 用 7 份导出 trial 跑出 28 次调用。零凭证下不读宿主库、不越界路径（P0 的 6.1 在那上面栽过一次）
- [x] 7.4 更新 `research/agent-eval-landscape/04-aeval-coverage-matrix.md` 第 8 行「Judge 偏差缓解 —— 缺失」：改为「探针机制已具备、幅度未测」，并注明仍**不具备**成对比较位置偏置的缓解（无宿主）—— 第 8 行评级 `缺失 → 部分（探针机制已具备、幅度未测）`，代码证据列改指 `graders/presentation_probes.py` 与 `core/metrics.py`；视觉摘要那根条从 1 格升到 2 格；「快照后的增量」加了一条 2026-09-22 记录，写明"不给领先，因为测得到 ≠ 测过了；不给已缓解，因为成对比较那一类仍无宿主"
- [x] 7.5 提交信息遵循 Conventional Commits，scope 取能力名：`feat(graders): ...` / `feat(statistics): ...` —— 按能力拆两个代码提交：`c61def1 feat(statistics): ...`（`core/types.py` + `core/metrics.py`，新类型先落，依赖顺序干净）与 `538b84e feat(graders): ...`（协议 + 两个算子 + 测试 + `examples/presentation-probes/`）。**报告类型没有再往下拆**：Migration Plan 第 1 条就写明拆开只会得到一个"支持单算子的协议"，看不出形状对不对。提案与文档批为第三个提交

## 8. 归档收口

- [x] 8.1 `openspec validate add-judge-presentation-probes --strict` 通过（两个能力的 delta：`graders` + `statistics`）—— `Change 'add-judge-presentation-probes' is valid`
- [x] 8.2 归档前复查：`graders` 与 `statistics` 的既有 Requirement 条目本文均未被改动（两条都是 ADDED）—— 两份 delta 的**唯一**操作头都是 `## ADDED Requirements`；`git diff --stat -- openspec/specs` 为空（一行未动）。归档前的基数记在这里供 8.3 对照：`graders` 10 条 Requirement / 21 个场景，`statistics` 15 条 / 31 个场景
- [ ] 8.3 归档：`openspec archive add-judge-presentation-probes`，确认两条新 Requirement 分别进入 `openspec/specs/graders/spec.md` 与 `openspec/specs/statistics/spec.md`
- [ ] 8.4 归档后在记录里点名下一步的三个候选（都应在第一次真测量之后再设计）：修锚、探针的 YAML/CLI 表面、跨 run 敏感性趋势
