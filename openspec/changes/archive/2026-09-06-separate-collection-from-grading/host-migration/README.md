# 宿主迁移记录 (change ③ 第 7 节)

本目录不再放待应用的代码 —— 第 7 节的改动已**直接落在 `bitdance-agenthub-main`**（会话初期对该目录的写入被权限拦下，后来按用户指示可写）。这里只留落地清单、验证结果与仍未做完的那一项。

## 已落地

| 文件 | 改动 | 任务 |
|------|------|------|
| `backend/app/eval_integration/runner.py` | `run(view: TaskView, session: TrialSession) -> TrialEvidence`；workspace 文件改由 `await session.harness_probe("workspace_files")` 取证交付；适配层元数据（会话/run/种子/产物）记 `runner` 级；agent 最后一条回复单独记 `subject` 级；transcript 仍记 `runner` 级（它是被评判的产出物，不是关于环境的主张） | 7.1 |
| `backend/app/eval_integration/environment.py` | 新增 `probe(channel)`，两条通道 `workspace_files`（清单+内容，经 fs API）与 `db_dump`（本地库 messages/artifacts 行）；无会话 / 未知通道 / 读失败一律返回**带原因的缺失读数**而非空列表；`verify_clean(baseline, harness_readings)` 优先用取证清单并在差异里标 `source: harness_probe` | 7.2 |
| `backend/app/eval_integration/graders/{artifact,dispatch}.py` | 补 `evidence_levels` + `implementation_version`，三条/两条出口自陈 `evidence_levels=[runner]` | 7.3 |
| `backend/eval_suites/first-suite.yaml` | 升 **1.2.0**：`file-creation` 由 `code_based target: outcome` 换成 `state_check` + `evidence: [harness]` + `judgment_moment: at_end`，并在 `metadata.evidence_change` 写明口径变化 | 7.1 的前置 |
| `backend/tests/test_eval_integration_runner.py` | 迁到新契约（`RunnerUnderTest` 保持单参调用形状，实际按 `(TaskView, TrialSession)` 走真契约，探针可注入）；新增三通道分离、探针缺失读数、未知通道、**probe 跟随 current trial** 四个用例 | 7.2 实测 |
| `docs/eval-harness-design.md` | §5.1 契约重写、§6 草图三相化、§4.3 `pass_at_k`/`pass_power_k` 换成①的修正口径、§2.4 措辞、风险映射表补三行、版本历史加 v0.16 | 7.5 |

`docs/eval-harness-design-review.md` **未改**：那里提到 pass@k 与三元组的段落是当时的审查记录（含「缺陷 1 已修复」标注），属历史快照而非现行契约说明。

## 验证结果

```
backend> ./.venv/Scripts/python.exe -m pytest tests/test_eval_integration_*.py tests/test_eval_mount.py tests/test_eval_rules_async_write.py -q
67 passed

backend> ./.venv/Scripts/python.exe -m ruff check app/eval_integration tests/test_eval_integration_runner.py
3 errors —— 全部先前就有: client.py 的 I001/F401 (未改文件) 与 runner.py 的 UP041
(HEAD 版 runner.py 同一处已报 UP041; 本次反而少了 1 个 I001)
```

宿主仓库整体基线本来就不干净（`ruff check .` 有百余条，且工作树里另有他人未提交的 `config.py` / `ARCHITECTURE.md` / `test_api_infra.py` / 前端 settings 等改动），所以只按触及文件比对前后，不动别人的在途改动。

顺带记录一处**与本变更无关的既有失败**（在同一次宽选择里观察到，且换一次运行就会换一条）：`tests/test_api_documents.py` 单独跑就 8/8 全挂（连 404 用例都挂）、`tests/test_rag_config_api.py` 两个 fixture error、`tests/test_rag_eval_system.py::test_delete_nonexistent_dataset` 随顺序浮动。它们不 import `eval_integration` 的改动路径，属该工作树里的既存问题。

## 还差的一项：7.4 真实链路验收

需要（当前都不具备）：

1. AChat 后端在 `127.0.0.1:8000` 运行 —— 探测结果 `000`（未起）
2. Phoenix 在 `:6006` 运行 —— 探测结果 `000`（未起）
3. 一个可用的 `EVAL_AGENT_ID`（历史 run 里的 id 已被删除，不能复用）

命令（在 `backend/` 下）：

```bash
EVAL_AGENT_ID=<id> ./.venv/Scripts/python.exe scripts/run_first_suite.py
```

跑完要逐条解释的预期差异（已按核对过的代码事实写清，不沿用 design 原先的说法）：

| 现象 | 原因 |
|------|------|
| `file-creation` 判据换成 `state_check`，结论可能由通过变未通过 | 判定基础从「适配层经 fs API 读到的 workspace 文本字符串匹配」换成「取证清单里文件是否真在 + 内容是否真含该行」。**注意**：design 原写「原先只证明 agent 提过文件名 = 假绿」，核对后不成立 —— 原先读的也是真实文件内容，只是级别是 `runner` 而非 `harness`；③ 的真实变化是这条证据升级为评测侧独立取证 |
| `Evidence:` 行出现 `harness=3` | 三个 task 的 trial 都拿到结束前取证读数；`file-creation` 的通过结论依据 `harness` 级 |
| 每条 trial 的 `harness_state` 有 2 次读数（`workspace_files` + `end_state`） | 适配层在 run 收尾主动取一次，框架在 teardown 前再取一次；「任一时刻」类判据因此有窗口可判 |
| 结论里可能出现 `invalid(evidence_unavailable)` | 若 Phoenix/后端未就绪导致取证读不到 —— 报「没取到」而不是判 agent 失败（这正是③要的行为） |
| `run.evidence` 多出 `capture_model_content` / `subject_allowed` | 统一采集声明与 allow_subject 放行清单落盘；老 run 读回为默认值，不影响可读性 |
| 历史 run（含 `run_b5a14807878d`、9-trial 验收 run）`Regrade: no` | 变更前只存三元组形态产物，来源分级在现场就丢了；可读、不可重评、与新 run 不同口径 |
| 每次判定都会在 `grade_attempts` 里留一条当场结论，重评后追加且 `is_current` 移动 | 5.2 的判定历史；`EvalRunner.verdict_drift(run_id)` 可直接算翻判比例 |

## 未提交内容

- 宿主仓库：改动留在工作树，**未提交**（那里有他人并发提交的历史，且本会话的框架侧提交已单独获准）。要提交的话建议按 `feat(eval-integration): consume Aeval evidence-provenance contract` 一条 + `docs: ...` 一条，`git add` 只点上面那张表里的路径。
- Aeval 仓库：`openspec/changes/separate-collection-from-grading/design.md` 的两处更正与 `host-migration/` 本身尚未提交。
