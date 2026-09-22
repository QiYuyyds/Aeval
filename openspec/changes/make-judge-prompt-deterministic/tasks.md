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
- [ ] 4.4 1.1 与 4.1 **同批提交**，不得拆开：中间态会出现"判分输入已变而版本未变"，正是本变更要消灭的那类不可归因 —— 待提交（与 7.3 同一次 commit）

## 5. 回归与静态门

- [x] 5.1 排查现有 `model_based` 单测是否断言过工具清单的某个具体次序 —— 该断言本身是本缺陷的产物，改为断言"稳定且有序"，不迁就旧断言 —— 全仓 grep `使用的工具` / `tools_used` / `_build_prompt`：仅 `test_builtin_graders.py:168` 断言过 rubric 与 transcript 正文，**没有任何旧断言钉过工具次序**，故无需迁就、也无处可改；"稳定且有序"这条新断言落在 `test_prompt_determinism.py`
- [x] 5.2 `cd packages/agent-eval && pytest tests/ -q` 全绿 —— **863 passed in 107.77s**（新增 10 条计入其中），无 skip、无 failure
- [x] 5.3 `ruff check packages/agent-eval` 双范围干净 —— 仓库根 `ruff check packages/agent-eval` 与包内 `ruff check src tests` 均 All checks passed
- [x] 5.4 确认未夹带任何判分**内容**改动（提示词措辞、锚定模板预填值、系统提示词、截断策略）—— 它们各自可能是问题，但不属于可复现性，混入会让 6.1 的翻转数无法归因 —— diff 仅三处：`tools_used` 构造式、版本常量、`channel_levels` docstring；f-string 模板与 `"You are an evaluation expert."` 逐字节未动

## 6. 实测翻转数量（本变更的验收证据）

> 已完成的**离线**测量（下列数字均由此产生，方法可复现）：驱动脚本 `_verify_judge_prompt_determinism.py`（仓库根、未纳入版本控制）。
> v2 侧不手写仿制品，而是 `git show HEAD:...graders/model_based.py` 取修复前原文按独立模块加载；
> 套件 = MockAgent 交付含 5 种工具调用（各两次）的 transcript × 6 trial；judge = 固定替身判据
> 「清单里第一个列出的工具名 <'f' 才给 completeness 1.0」；跑完换回真实 v3 grader 走 `regrade_run` + `verdict_drift`。
> 三个独立进程各跑一次的结果一致：
>
> | 量 | 值 |
> |---|---|
> | 判分输入字节发生变化的 trial | **6/6** |
> | `verdict_drift.flipped_trials` / `flip_rate` | **6 / 1.0** |
> | `verdict_drift.distinct_calibers` | 1（mapping/spec/statistics/judge_models 四轴两次 attempt 全等） |
> | `grader_versions` 之差 | 仅 `model_based: "2" → "3"`（其余 8 个评分器版本不变） |
> | v2 提示词里第一个列出的工具 | 进程 1/2 = `web_search`，进程 3 = `fs_read`（v3 恒为 `calculator`）← 修复前不可复现的直接读数 |
> | 重评后 run 的 `statistics_version` | `"2"` 不变 |

- [ ] 6.1 选一个含多工具调用的历史 run 执行重评：优先 `examples/achat` 的活跑归档；若其 trial 数不足以体现翻转，补一个 MockRunner 构造的多工具套件（离线、零凭证）。两者不冲突 —— **优先分支未能执行**：宿主归档 `bitdance-agenthub-main/.agenthub-data/aeval.db`（2.9 MB，09-07 13:32）的读取被权限分类器拦下，且活跑重评需 judge 凭证（`AEVAL_JUDGE_*` 在宿主 `.env` 为空；`.env.local` 的 LongCat key 上次报 402）。MockRunner 分支已跑完并出数（见上表）。未勾选原因：6.1 的首选分支从未真正跑过，其 trial 数是否"足以体现翻转"仍是未知，不能拿替代测量的 6/6 顶替
- [ ] 6.2 用 `verdict_drift` 量出评分器 v2→v3 翻转的 verdict 数量，并确认翻转**全部**归因到评分器版本这一维度（不与 judge 模型变化、证据边界变化混在一起）—— 离线路径上已量出 6 并逐项验过归因（judge_models / mapping / spec / statistics 四轴全等，唯 `model_based` 版本不同；`evidence_levels` 两次均为 `["runner"]`）。未勾选原因：数字来自替身 judge，真实 judge 的翻转数未量；且 `verdict_drift.distinct_calibers` 本身**不含** `grader_versions` 轴，"全部归因"这件事只能靠逐 attempt 对照（已做），不能只引用那一个字段
- [ ] 6.3 若翻转数为 0：说明该 run 的 trial 未触发多工具分支，需另选/另造一个确实含两种以上工具的 run 重测 —— 0 翻转不能作为"修复无影响"的证据，也不能作为验收通过 —— 本条针对的反而是"翻转不足"：离线为 6/6，非 0。未勾选原因：与 6.1/6.2 同源，真实归档上的数字仍缺
- [ ] 6.4 结果写回本清单（勾选 + run id + 翻转数 + 判读）；5.x 与 6.x 全部完成是 `openspec archive make-judge-prompt-deterministic` 的前置条件 —— 判读：**修复确实会改变重评结论**，凡"判据读到工具清单且 trial 含 ≥2 种工具"的历史 trial，其 v2 判分输入本来就随进程漂移（上表倒数第三行是直接证据），v3 之后固定。离线 run id：`run_d46a69f5c262` / `run_b912db7b4c59` / `run_31eeef4f3652`（MemoryStorage，未落库，仅供追溯方法）。**6.x 未闭合，归档前置条件未满足**

## 7. 文档与发布说明

- [x] 7.1 `docs/grader-reference.md` 的 `model_based` 条目补一句：判分输入对同一份归档证据确定，跨进程重评逐字节一致
- [x] 7.2 `CHANGELOG.md` 的 0.4.0 条目点名：**重评同一批字节可能得到不同分数，且这是修复**；同时说明 `statistics_version` 不变、历史 run 不需迁移
- [ ] 7.3 提交信息遵循 Conventional Commits，scope 取能力名：`fix(graders): ...` —— 待提交
- [x] 7.4 在 `research/agent-eval-landscape/04-aeval-coverage-matrix.md` 记一笔：判分输入可复现性已闭合（该矩阵快照为 09-07，另有 7 行已过期，回填属独立工作，不在本变更范围）—— 追加「快照后的增量」段，挂在第 4 行（重放审计）的前提上
