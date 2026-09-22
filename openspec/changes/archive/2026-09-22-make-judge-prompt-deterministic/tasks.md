## 1. 修复判分输入的确定序

- [x] 1.1 `graders/model_based.py:158`：`list(set(...))` → `sorted(set(...))`，使工具清单在判分输入中的次序由内容决定而非进程决定（落地为 `sorted({...})`，语义同）
- [x] 1.2 通读 `_build_prompt` 其余插值项，确认除 1.1 外无其它集合派生内容（rubric / dimensions / transcript[0] / transcript[-1] 均为列表或标量），有则一并修 —— 判读结论：其余四项分别是标量、标量、按配置序 join 的列表，**无第二处**
- [x] 1.3 检查 `metrics/` 轨迹注入路径（`render_trajectory_block`）：审计结论为无集合派生内容进提示词，用一条断言测试固化该结论，而非仅靠人工判读 —— `tests/test_prompt_determinism.py::test_trajectory_block_is_byte_identical_across_two_processes`（跨子进程逐字节）+ `::test_trajectory_block_only_holds_ordered_readings`（行数==可渲染读数数，多一行派生清单即红）
- [x] 1.4 全仓 grep `list(set(` / `sorted(set(` 于 `graders/` `metrics/` `trace/`，确认 design D5 审计表未漏项；若发现新点位，回填 D5 表再决定纳不纳入本变更 —— 三目录内 `list(set(` 仅 1.1 一处；D5 表已回填 3 行（`tool_calls.py:99-100,110-114` 落盘清单源自配置列表、`_evidence.py:123-125,144-146` 同受 D4 不变量约束、`normalize.py:129-134,158,230,258` 已 sorted）。**范围外新发现两处同类**（`core/metrics.py:876` 众数 tie-break、`core/metrics.py:358-361` κ 求和次序），已记入 D5 表并**未修**，理由见该表末段

## 2. 跨进程守护测试

- [x] 2.1 新增测试：内存构造一份含**两种以上工具调用**的证据对象 + 固定 rubric/dimensions，在两个独立子进程中各构建一次判分输入，断言逐字节相等（不依赖数据库、不共享临时文件）—— 5 种工具各调用两次，`subprocess.run([sys.executable, "-c", ...])`，比较 UTF-8 字节
- [x] 2.2 确认该测试**不以 `PYTHONHASHSEED=0` 启动子进程** —— 那会掩盖缺陷而非验证它；测试须在默认随机种子下通过 —— 子进程不注入任何 hash 种子（`hash_seed` 在测量里报为 `random`）
- [x] 2.3 反向验证：临时把 1.1 的修复还原，确认 2.1 的测试真的变红（一次性的，验证完还原）。测不到缺陷的守护测试比没有测试更糟 —— 还原后连跑 3 次，`test_model_based_prompt_is_byte_identical_across_two_processes` 与 `::test_tool_listing_is_rendered_in_sorted_order` **3/3 变红**，已还原修复。副产品：据此**删掉了两条**先前写的进程内重复构建测试（它们在缺陷还原后仍绿，正是 D2 点名的"永远绿"）
- [x] 2.4 若子进程测试在 CI 上耗时明显，加 `@pytest.mark.slow` 归入慢道，不降级为进程内断言 —— 实测整个 `test_prompt_determinism.py`（含 4 次子进程启动）2.6 s，全套 107 s 内占比 <3%，未达"耗时明显"，故**未加** slow 标记（仓库现无 slow 慢道，凭空造一个标记属本变更外）。未降级为进程内断言

## 3. 固化 evidence_levels 的强度唯一性（design D4）

- [x] 3.1 新增断言测试：`ObservedBy` 各级的 `strength` 两两不同（`EVIDENCE_STRENGTH` 值集合大小 == 级别数）—— 另加一条「强度表恰好覆盖全部级别」与一条 `channel_levels` 降序实测
- [x] 3.2 在 `channel_levels` 的 docstring 补一行说明其确定性依赖该唯一性 —— 只写"为什么安全"这个非显然约束，不写实现步骤
- [x] 3.3 确认未给 `sorted` 添加二级 key 兜底（D4 明确否决该方案：并列强度应先在本能力层面辩论，不该被静默打破）—— `_evidence.py` 的 diff 只有 docstring，`sorted(present, key=lambda level: -level.strength)` 原样

## 4. 版本提升与归因

- [x] 4.1 `ModelBasedGrader.implementation_version` `"2"` → `"3"`
- [x] 4.2 确认 `statistics_version` 维持 2 —— 本变更不动分母口径，误提升会让跨 run 可比判定无端失效 —— `core/types.py:102` 仍为 `"2"`，重评后的 run 读回仍是 `"2"`
- [x] 4.3 确认 `implementation_version` 随判定落盘的路径未变（④ 既有机制），新值能在 verdict 与 `grader_versions` 口径里读到 —— verdict 侧 `details["grader_version"]`（`model_based.py:121`）、attempt 侧 `GradeAttempt.grader_versions`（`runner.py:705-708`）；6.x 的测量里同一 trial 两次 attempt 报出 `model_based: "2"` 与 `"3"`
- [x] 4.4 1.1 与 4.1 **同批提交**，不得拆开：中间态会出现"判分输入已变而版本未变"，正是本变更要消灭的那类不可归因 —— 1.1 与 4.1 同落 `1b01aa0 fix(graders): ...`，未拆

## 5. 回归与静态门

- [x] 5.1 排查现有 `model_based` 单测是否断言过工具清单的某个具体次序 —— 该断言本身是本缺陷的产物，改为断言"稳定且有序"，不迁就旧断言 —— 全仓 grep `使用的工具` / `tools_used` / `_build_prompt`：仅 `test_builtin_graders.py:168` 断言过 rubric 与 transcript 正文，**没有任何旧断言钉过工具次序**，故无需迁就、也无处可改；"稳定且有序"这条新断言落在 `test_prompt_determinism.py`
- [x] 5.2 `cd packages/agent-eval && pytest tests/ -q` 全绿 —— **863 passed in 107.77s**（新增 10 条计入其中），无 skip、无 failure
- [x] 5.3 `ruff check packages/agent-eval` 双范围干净 —— 仓库根 `ruff check packages/agent-eval` 与包内 `ruff check src tests` 均 All checks passed
- [x] 5.4 确认未夹带任何判分**内容**改动（提示词措辞、锚定模板预填值、系统提示词、截断策略）—— 它们各自可能是问题，但不属于可复现性，混入会让 6.1 的翻转数无法归因 —— diff 仅三处：`tools_used` 构造式、版本常量、`channel_levels` docstring；f-string 模板与 `"You are an evaluation expert."` 逐字节未动

## 6. 实测翻转数量（本变更的验收证据）

> 已完成的**离线**测量（下列数字均由此产生，方法可复现）：入库入口 `examples/prompt-determinism/measure_version_flip.py --rev 1b01aa0^`（tasks 9.3 把它从仓库根未跟踪脚本改成了受版本控制的复现入口）。
> v2 侧不手写仿制品，而是 `git show 1b01aa0^:...graders/model_based.py` 取修复前原文按独立模块加载；
> 套件 = MockAgent 交付含 5 种工具调用（各两次）的 transcript × 6 trial；judge = 固定替身判据
> 「清单里第一个列出的工具名 <'f' 才给 completeness 1.0」；跑完换回真实 v3 grader 走 `regrade_run` + `verdict_drift`。
> **共跑过 16 个独立进程**（原测量 9 次 + 8.7 重跑 7 次，命令与套件完全相同，只有进程不同）：`flipped_trials` 出现 **6 十二次、0 四次** —— 换代前那份清单的次序本身就随进程浮动，"翻了几判"在旧实现下不是定值（判据要求第一个列出的工具名 `<'f'`，四个 0 全是 `calculator` 恰好被排到第一的那几次）。
>
> | 量 | 值 |
> |---|---|
> | 判分输入字节发生变化的 trial | **6/6** |
> | `verdict_drift.flipped_trials` / `flip_rate` | **6 / 1.0**（16 个进程里 12 次为此值，另 4 次为 `0 / 0.0`） |
> | `verdict_drift.distinct_calibers` | **2**（mapping/spec/statistics/judge_models 四轴两次 attempt 全等，差在第五根轴）。**这是纠错不是改数**：并入 `grader_versions` 轴之前同一批数据报的是 `1`，而 `1` 的字面含义"口径相同"是错的 |
> | `verdict_drift.differing_caliber_axes` | **`['grader_versions']`** —— 16 次里每一次都如此，包括那 4 次 `flipped_trials = 0` 的（轴变了，结论恰好没变） |
> | `grader_versions` 之差 | 仅 `model_based: "2" → "3"`（其余 8 个评分器版本不变，且不参与本 trial 判定故不进归因轴） |
> | v2 提示词里第一个列出的工具 | 16 个进程里出现 `web_search` / `fs_read` / `fs_write` / `sql_query` / `calculator` 五种（v3 恒为 `calculator`）← 修复前不可复现的直接读数 |
> | 重评后 run 的 `statistics_version` | `"2"` 不变 |

- [x] 6.1 选一个含多工具调用的历史 run 执行重评：优先 `examples/achat` 的活跑归档；若其 trial 数不足以体现翻转，补一个 MockRunner 构造的多工具套件（离线、零凭证）。两者不冲突 —— 首选分支**已用真实归档试过并出局**：`.live-run-output.txt` 记的两次活跑（`run_481a28198c86` / `run_eeec14818ab1`）共 3 trial 全为 invalid，且 `examples/achat/*.yaml` 里根本没有 `type: model` 判据（只有 code/state/custom）。宿主自有 `eval_suites/t1-core.yaml` 确实挂了两个 `model_based` 判据，于是按用户授权做了一次**只读、零调用**的归档重放（入口现为 `examples/prompt-determinism/replay_archived_prompts.py`，v2 侧从 `1b01aa0^` 取原文加载）：读到的 `achat-metric-acceptance` 三次 run（`run_0b4bbb66f8ec` / `run_cc6123e09fdd` / `run_5cef385f413d`，共 11 trial）里 `model_based_verdicts = 0`。局限如实记下：该库**未能全量枚举** —— 重放脚本第二次调用同一越界路径时被权限分类器拦下（"previously automode-blocked"），故只能说"读到的这些 run 里没有可翻的 model_based 结论"，不能说整个宿主库都没有。出局条件由 6.1 自己写明（trial 数不足以体现翻转），故按同一句授权补 MockRunner 多工具套件
- [x] 6.2 用 `verdict_drift` 量出评分器 v2→v3 翻转的 verdict 数量，并确认翻转**全部**归因到评分器版本这一维度（不与 judge 模型变化、证据边界变化混在一起）—— **6/6 翻转**（`flipped_trials=6`, `flip_rate=1.0`, `attempts=12`）。归因逐 attempt 对照过：judge_models / mapping_version / spec_version / statistics_version **四轴两次全等**，`evidence_levels` 两次均为 `["runner"]`，唯一差异是 `grader_versions["model_based"]: "2" → "3"`（其余 8 个评分器版本不变）。当时 `verdict_drift.distinct_calibers` 的口径元组**不含** `grader_versions`，所以"归因唯一"这件事只能靠这样逐 attempt 人工比出来 —— 那句"必须逐 attempt 比"本身就是缺陷报告，已由 8.x（design D6）修掉：现在框架自己输出 `differing_caliber_axes = ['grader_versions']`。**局限**：数字出自确定性的呈现序敏感替身 judge（判据：清单第一个列出的工具名 <'f' 才给 completeness 1.0），不是真实 LLM 的翻转率 —— 宿主四把候选凭证现全为 402/401（`.live-run-output.txt:21-28`），真实翻转数当前**测不出来**，不是没测
- [x] 6.3 若翻转数为 0：说明该 run 的 trial 未触发多工具分支，需另选/另造一个确实含两种以上工具的 run 重测 —— 0 翻转不能作为"修复无影响"的证据，也不能作为验收通过 —— 触发这条的正是宿主归档（读到的 run 全是 0 条 model_based 结论）。另造的 MockRunner 套件确含 5 种工具调用（各两次），实测量到 **6/6**，且独立于 judge 敏感性还量得"判分输入字节变化的 trial = 6/6"
- [x] 6.4 结果写回本清单（勾选 + run id + 翻转数 + 判读）；5.x 与 6.x 全部完成是 `openspec archive make-judge-prompt-deterministic` 的前置条件 —— 判读：**修复确实改变重评结论**，凡"判据读到工具清单且 trial 含 ≥2 种工具"的历史 trial，其 v2 判分输入本来就随进程漂移（16 个进程里 v2 首个列出的工具出现五种，v3 恒为 `calculator`），v3 之后固定。离线 run id：`run_d46a69f5c262` / `run_b912db7b4c59` / `run_31eeef4f3652`（原测量）与 `run_b8512a5f3141`（8.7 用入库脚本重跑）；均为 MemoryStorage，不落库，仅供追溯方法。**已知缺口一项未变**：真实 judge 下的翻转数需一把能用的 judge 凭证，届时直接重跑 `examples/prompt-determinism/measure_version_flip.py --rev <修复前 rev>` 即可。前置条件按 10.1 修订为 **5.x + 6.x + 8.x + 9.x 全完**（并入项扩大了范围，原"5.x + 6.x 即满足"一句作废）

## 7. 文档与发布说明

- [x] 7.1 `docs/grader-reference.md` 的 `model_based` 条目补一句：判分输入对同一份归档证据确定，跨进程重评逐字节一致
- [x] 7.2 `CHANGELOG.md` 的 0.4.0 条目点名：**重评同一批字节可能得到不同分数，且这是修复**；同时说明 `statistics_version` 不变、历史 run 不需迁移
- [x] 7.3 提交信息遵循 Conventional Commits，scope 取能力名：`fix(graders): ...` —— `1b01aa0` 为实现批，`2e579af` 为提案/文档批
- [x] 7.4 在 `research/agent-eval-landscape/04-aeval-coverage-matrix.md` 记一笔：判分输入可复现性已闭合（该矩阵快照为 09-07，另有 7 行已过期，回填属独立工作，不在本变更范围）—— 追加「快照后的增量」段，挂在第 4 行（重放审计）的前提上

## 8. 翻判归因必须能看见判分实现版本（并入项，design D6）

- [x] 8.1 `core/runner.py:658-666` 的口径聚合元组加入判分实现版本，但**只计入本次判定实际产出结论的评分器** —— 从 `attempt.trial.grader_results[].grader_name` 反推参与者，不要直接用全量的 `attempt.grader_versions` —— 新增 `CALIBER_AXES`（五根轴）与 `_caliber_values(attempt)`；`grader_versions` 轴取 `(参与评分器名, 落盘版本)` 的有序元组，未参与者不进轴
- [x] 8.2 `grader_results` 为空的 attempt（人工评分未回传等中间态）其归因轴取值报告为「不可判定」而非空集。空集会在跨 attempt 比较中伪装成与任何值都相同，把"没数据"读成"没变化" —— `AXIS_UNDETERMINED = "undetermined"` 哨兵；测试 `test_attempt_without_grader_results_is_undetermined_not_equal`
- [x] 8.3 追加"哪根轴不同"的输出：对每根轴检查所有 attempt 是否同值即可，不做两两配对。**不改动** `distinct_calibers` 的名称与类型（同大版本响应结构向后兼容） —— 新增 `differing_caliber_axes`（逐轴全等检查，无两两配对）；`distinct_calibers` 名称与 `int` 类型不变，全仓除 `runner.py` 外无其它消费方（CLI/看板均未读该字段）
- [x] 8.4 全部被考察轴同值而仍有翻判时，输出必须是「存在无法归因到已记录口径轴的翻判」，而不是让 `distinct_calibers = 1` 暗示口径一致 —— 两者是相反的结论 —— 新增 `unattributable_flips`（有翻判且无任何差异轴时为真）；测试 `test_flip_with_identical_calibers_is_reported_as_unattributable` 同时钉住 `distinct_calibers == 1` 与 `unattributable_flips is True` 这个危险组合
- [x] 8.5 三条测试对应 `specs/orchestration/spec.md` 的三个场景：换代是唯一变化 / 未参与的评分器改版不污染归因 / 全轴同值仍翻判时显式报告不可归因 —— `test_flips_name_the_grader_version_axis` / `test_uninvolved_grader_upgrade_does_not_move_any_axis` / `test_flip_with_identical_calibers_is_reported_as_unattributable`（另加 8.2 一条）。门：`pytest tests/ -q` **868 passed**，`ruff` 双范围干净
- [x] 8.6 反向验证：临时把 8.1 的过滤改成全量 `grader_versions`，确认"未参与评分器改版"那条测试真的变红（测不到缺陷的守护测试比没有测试更糟，同 2.3） —— 改成 `tuple(sorted(attempt.grader_versions.items()))` 后**只有** `test_uninvolved_grader_upgrade_does_not_move_any_axis` 变红（其余 21 条仍绿），已还原
- [x] 8.7 按新语义重跑 6.1/6.2 那批数据，确认框架给出的轴名与当时人工逐 attempt 比对的结论一致（应点名判分实现版本，且 `model_based` 之外的八个不出现） —— 重跑 7 个进程 + 入库脚本 1 次：`differing_caliber_axes` **每次都是 `['grader_versions']`**，与当时人工逐 attempt 的结论一致；`changed_grader_names = ['model_based']`（其余八个版本未变）。新语义还多出一件人工比对当时给不出的事实：**4 次 `flipped_trials = 0` 的进程里差异轴照样点名 `grader_versions`** —— 轴变了而结论恰好没变，旧输出把这两种情况都显示成 `1`
- [x] 8.8 复核 6.x 记录中 `distinct_calibers = 1` 一句：新语义下该值会变，按新语义标注并说明是纠错，**不得静默改数** —— 第 6 节表内该行已改为「**2** …… **这是纠错不是改数**：并入前同一批数据报的是 `1`」，旧值与语义差别都留在记录里；6.2 那句"必须逐 attempt 比"同时标注为缺陷报告、已被 8.x 修掉
- [x] 8.9 确认未新增 `bias_operator` 之类的轴（Non-Goals：并入项只接已落盘的轴，为 P1 留出挂载点而不替它做决定） —— 全仓 grep `bias_operator` 于 `src/` **零命中**；`CALIBER_AXES` 五根轴全部来自 `GradeAttempt` 既有落盘字段

## 9. 验收证据入库与遗留收口（design D7）

- [x] 9.1 把 `_verify_judge_prompt_determinism.py` 中可自动化的部分升为 pytest（跨进程逐字节一致已由 2.1 覆盖，此处只补未覆盖的断言），其余一次性测量逻辑不硬塞进测试套件 —— 只有一条真正没被覆盖：**换代只动判分实现版本、版本随判定落盘而 `statistics_version` 不动**的端到端断言 → `test_grader_regeneration_changes_only_the_grader_axis_end_to_end`（delegating grader 换代 + `regrade_run`）。一次性测量逻辑（替身 judge 的翻转数、跨进程分布）留在脚本里不进套件。过程中该测试还抓出写驱动时的一个契约错误：委托型评分器若沿用内层的 `grader_name`，归因轴会去读**另一个**评分器的版本 —— 已在测试里显式自陈名字
- [x] 9.2 `_verify_replay_archived_prompts.py` 依赖宿主库路径与越界读取授权，不能进 CI —— 入库为受版本控制的人工验收入口，位置与命名与 ⑤ 的 `examples/achat/run_live_acceptance.py` 同类同处（design Open Questions 项，执行期定） —— 定为 `examples/prompt-determinism/`（与 `examples/trace-replay/` 同形：目录 + README + 脚本）：`measure_version_flip.py`（离线换代翻转与归因轴）、`replay_archived_prompts.py`（真实归档重放，只读 + 只打印聚合量）、`README.md`。两者的 `--rev` **必填**且拒绝修复后的 rev（退出码 2 并说明"0 差异不能当修复无影响"）—— 原临时脚本默认 `HEAD`，在修复落地后会静默测出一个假 0
- [x] 9.3 6.x 记录改引入库后的路径，保留原始 run id 与数字。记录的价值在"别人能按同样方法得到同样的数"，不在数字本身 —— 第 6 节前言与 6.1/6.4 均改引 `examples/prompt-determinism/...`，四个原始 run id 全部保留，另补 8.7 重跑的 `run_b8512a5f3141`
- [x] 9.4 收掉 ⑤ 的同类遗留：`examples/achat/run_live_acceptance.py`（`max_tokens` 300→1500 + 空 content 显式报错）至今是未提交的工作区改动，而其修复已被 ⑤ 归档记录引用 —— 与本变更第 6 项是同一类缺陷的两次出现，一并入库 —— `4b94428 fix(orchestration): stop the goal simulator's silent empty-content 200s`
- [x] 9.5 提交后确认 `git status` 干净：不存在"归档记录引用了只存在于本机的代码"这类状态 —— 三个临时驱动已删，入口全部入库（`ff2423d`），`git status` 再无未跟踪的验证代码；仓库领先 origin 若干提交，**未 push**（推送需另行确认）

## 10. 归档收口

- [x] 10.1 8.x 与 9.x 全部完成前，6.4 那句「archive 前置条件满足」**作废** —— 并入项扩大了本变更范围，验收证据与归因语义都需重跑 —— 作废已执行：6.4 那句改成「前置条件按 10.1 修订为 5.x + 6.x + 8.x + 9.x 全完」；验收证据按新语义重跑（16 个进程 + `868 passed` 门），记录表里的 `distinct_calibers` / `differing_caliber_axes` 两行即为重跑后的读数
- [x] 10.2 `openspec validate make-judge-prompt-deterministic` 通过（两个能力的 delta：`graders` + `orchestration`） —— `1 items, passed 1, failed 0`
- [x] 10.3 归档：`openspec archive make-judge-prompt-deterministic`，确认两条新 Requirement 分别进入 `openspec/specs/graders/spec.md` 与 `openspec/specs/orchestration/spec.md` —— 归档为 `2026-09-22-make-judge-prompt-deterministic`，`totals: added 2 / modified 0`；`graders` 9→10 条（新条目在 `specs/graders/spec.md:137`「判分输入必须对同一份归档证据确定」），`orchestration` 15→16 条（`specs/orchestration/spec.md:219`「跨判定条目的翻判归因必须指明变化在哪一根口径轴」，三个场景的 WHEN/THEN 全文在内）
- [x] 10.4 归档后复查：`orchestration` 既有的「每次判定记录其评分口径且历史结论不被覆盖」条目本文未被改动（并入项是 ADDED，不是 MODIFIED） —— `git diff --numstat` 对两份 spec 各为 `19 0`（新增 19 行、**删除 0 行**）；纯追加即证明既有条目本文一字未动
