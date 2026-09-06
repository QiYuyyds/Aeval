> 冷启动说明：本清单可独立执行，不依赖上一段对话。项目约定见 `openspec/config.yaml`，方向与已定决策见 `openspec/specs/`（10 能力 / 44 条契约）与各 change 的 design.md。

## 1. 落盘成对改动（最高优先，先于一切）

- [x] 1.1 执行前重新读状态：`git -C Aeval-publish status --porcelain` 与 `git -C bitdance-agenthub-main status --porcelain`。**只按具体路径 add，禁止 `git add -A`** —— 这两个仓库存在并发提交者，已发生过"提交一半"
  - 已重读。Aeval 侧 4 M 源码 + 3 M 测试 + 4 M 文档/README + 21 D + 5 个未跟踪路径；宿主侧 `separate-collection-from-grading` 已被并发会话提交入库（本清单未把它列为待提交项，相符）。
- [x] 1.2 Aeval 第 1 笔：提交主 spec 语料与归档移动 —— `openspec/specs/`、`openspec/changes/archive/`，以及 `openspec/changes/fix-stats-and-denominators/`、`align-trace-evidence/` 的删除（约 21 D + 2 个未跟踪目录）。建议消息：`chore(openspec): archive statistics and trace-evidence changes, lock 10-capability spec corpus`
  - 提交 `efab9f1`，31 files，21 个 rename 全部被 git 识别为 R（100% 相似度，tasks.md 87%）。
- [x] 1.3 Aeval 第 2 笔：OpenInference 预设全套 —— `packages/agent-eval/src/agent_eval/{trace/mapping.py,trace/__init__.py,cli.py,api/app.py}`、`tests/{test_trace_normalization.py,test_cli.py,test_standalone_api.py}`、`docs/{integration-guide,architecture,cli-reference}.md`、`README.md`、`README.zh-CN.md`，加 `openspec/changes/add-openinference-mapping-preset/`。消息：`feat(trace-provider): ship selectable OpenInference mapping preset with meta and CLI selection`
  - 提交 `af0241d`，15 files。**README.zh-CN.md 无改动**（不在 status 里），故未包含——不是漏加。
- [x] 1.4 Aeval 第 3 笔：本变更目录 `make-published-artifact-match-accepted-code/` + ③ 遗留未提交项（若有）
  - 提交 `200c217`。③ 遗留项为零：`separate-collection-from-grading/` 已在库（10 个文件被并发会话提交），本笔只含本变更目录 4 个文件。
- [x] 1.5 宿主一笔：③ 的迁移 —— `backend/app/eval_integration/{runner.py,environment.py,config.py,graders/artifact.py,graders/dispatch.py}` 与 `backend/tests/test_eval_integration_runner.py`。消息：`feat(eval-integration): migrate to evidence-provenance runner contract`。**只点这些路径**，宿主另有 20+ 个无关未提交改动
  - 提交 `657c287`（分支 `feat-add-eval-harness`），7 files。**多含一个 `backend/eval_suites/first-suite.yaml`**：其 diff 是把 file-creation 判据从 code_based/outcome 换成 state_check/harness 证据，注释明说"③ 之后那份读数走取证通道，不再出现在 outcome 里，故判据必须换"——不带上它，落盘后的 suite 对新契约是坏的。其余未点：`test_zz_probe.py` 的删除是 neo4j Settings 调试探针清理，与 ③ 无关，不带入。
- [x] 1.6 验证配对着陆：`git -C Aeval-publish show HEAD:packages/agent-eval/src/agent_eval/core/contract.py | grep "def run"` 与宿主 `git show HEAD:backend/app/eval_integration/runner.py | grep "async def run"` —— 两边签名必须同为 `(view, session) -> TrialEvidence`，此前是"两个 HEAD 跑不通"
  - Aeval `contract.py`：`async def run(self, view: TaskView, session: TrialSession) -> TrialEvidence:`；宿主 `runner.py`：`async def run(self, view: TaskView, session: TrialSession) -> TrialEvidence:`。两个 HEAD 现在配对。
- [x] 1.7 两仓库门禁复跑：Aeval `PYTHONPATH=src pytest tests/ -q`（预期 634 passed）+ `ruff check packages/agent-eval`；宿主 `.venv/.../python -m pytest tests/test_eval_integration_* -q`（预期 57 passed）
  - Aeval：**638 passed**（比提案时多 4 个：并发会话为 preset 补了测试）+ ruff All checks passed。宿主：**59 passed**（比预期多 2，同为并发新增），无失败。
- [x] 1.8 `.qoder/` 定去向：加进 `.gitignore`（倾向）或入库；二选一并提交
  - 取倾向项：内容仅 Qoder 编辑器的 commands/skills 本地状态，提交 `1811c71`。Aeval 工作树自此干净。

## 2. 逐条核对已勾任务（不信清单）

- [x] 2.1 `add-openinference-mapping-preset/tasks.md`：25 条逐条给结论。**已知偏差**：清单称 5 条未办，实测其中 2.2 / 2.3 / 5.1 / 5.2 已完成（`/v1/meta` 有 `vocabularies`、`--vocabulary`、`AEVAL_TRACE_VOCABULARY`、README Features 有整段）→ 改正勾选并注明依据
  - 逐条结论已附在该 tasks.md 末尾。发现清单在提案后被并发会话改正过（4 条"未办"已勾上并注明依据），本次逐条独立复核：23/25 有据；**4.3 虚标，改回未勾**（6 条真实 Phoenix trace 的回归无持久记录、Phoenix 不在线无法复跑，理由写在条目下）；4.4 本就未勾正确。
- [x] 2.2 `separate-collection-from-grading/tasks.md`：50 条由并发会话勾选，逐条找可指认的用例或命令输出。**重点四处**：来源三级取信、判定时刻（`一次取证 = 一个时刻`，提交 `a8c9b70`）、重评永不覆盖且 `current` 指针可从列读回（提交 `9005c0f`）、宿主迁移与实跑记录（提交 `e85d279`、`4b4572a`）
  - 逐条结论已附在该 tasks.md 末尾：49/49 勾选有据。重点四处各自落定：三级取信有 `test_same_evidence_three_declarations` 等参数化用例；判定时刻有 `a8c9b70` + `test_created_then_deleted_fails_at_end_and_passes_any_time`；重评不覆盖有 `9005c0f` + `test_regrade_appends_and_moves_the_current_pointer`；宿主实跑用 host `.agenthub-data/aeval.db` 硬核实（`run_8ca076ca783a` 9 trial 全 valid、9 条证据行、18 条判定行，首跑 `run_06a16595008a` 亦在）。
- [x] 2.3 虚标处理：任何找不到证据的勾选项**改回未勾**并在条目下写明缺什么；不为了让清单好看而保留
  - 共发现一处：preset 4.3，已改回未勾并写明缺口（无持久命令输出、当前无法复现）与补救路径（Phoenix 在线时补跑）。③ 侧零虚标。
- [x] 2.4 确认 `10.4`（dashboard build）标注是否成立：若 ③ 确实未触及 `apps/dashboard`，记 N/A 并说明；否则补跑
  - 成立：`git log 4fc28cf^..HEAD -- apps/dashboard` 为空，③ 的 6 个提交无一触及 dashboard；维持"未跑"的 N/A 标注（不是"跑绿"）。
- [ ] 2.5 未办的 `4.4`（宿主活跑，会产生真实 agent 调用）：需用户授权后执行，判据是 9 trial 仍全部 `valid` 且结论带最弱证据级别标注

## 3. 归档两个 change

- [ ] 3.1 顺序 **preset → ③**（③ 落在新能力 `extension-contracts`，与 preset 的 `trace-provider` 无冲突；反序会让 `trace-provider` 的 Purpose 与需求归属来回改写）
- [ ] 3.2 归档前确认 `openspec/specs/` 已入库（第 1.2 步），并核对合并结果无同名 Requirement 冲突、主 spec 的 `## Purpose` 无 TBD
- [ ] 3.3 `openspec archive add-openinference-mapping-preset --yes`
- [ ] 3.4 `openspec archive separate-collection-from-grading --yes`
- [ ] 3.5 归档后主 spec 应为 **11 个能力**（新增 `extension-contracts`）；`openspec validate --all --strict` 通过；`openspec list --json` 只剩本变更
- [ ] 3.6 提交归档结果（一笔，`chore(openspec): archive ...`）

## 4. 版本与迁移说明

- [x] 4.1 `packages/agent-eval/pyproject.toml`：`0.1.0 → 0.2.0`（不取 1.0.0 的理由见 design D1）
- [x] 4.2 写迁移说明（`docs/` 下新章节或 `CHANGELOG.md`，取仓库现有习惯）：③ 破坏的 `AgentRunner` 签名与宿主侧改法；`trace_mapping=` / `--vocabulary` 接入；① 收紧后**门禁可能拦下历史放行的构建**（明确不提供 legacy 旗标）；历史 run 不回算亦不可重评；`cost_usd` 无价目表时不可计算
  - 仓库无 CHANGELOG，习惯是 docs/ 中文文档 → `docs/getting-started.md` 新增「升级到 0.2.0（从 0.1.x）」一节，五点全覆盖；顺带修正了该文档 §6 里仍教旧三元组签名的示例（教人写坏代码的迁移文档等于没有）。
- [x] 4.3 README 安装段：发布后 `pip install aeval-framework` 与从源码 `pip install -e ./packages/agent-eval` 并列，**不写死版本号**（design D4）
  - README.md 与 README.zh-CN.md 都已并列两条路径，均无版本号钉死。
- [x] 4.4 确认宿主 `.venv` 在正式包发布后可从 `aeval-framework==0.2.0` 安装（当前依赖 editable）
  - 已确认：宿主 venv 里 `aeval-framework 0.1.0` 是 editable 安装（Editable project location: `D:\java\project\Aeval-publish\packages\agent-eval`）；宿主 `requirements.txt` 注释早已写明 PyPI 发行路径 `pip install "aeval-framework[api,cli]"`，发布后切到 `==0.2.0` 无障碍。

## 5. 从制品验证（必须先于 tag）

- [ ] 5.1 构建 wheel 与 sdist，检查其中确实包含新模块：`trace/mapping.py`、`trace/normalize.py`、`trace/observations.py`、`core/pricing.py`、`core/redaction.py`、`graders/_evidence.py`、`graders/_verdicts.py`（`pyproject` 是目录式 `packages = ["src/agent_eval"]`，但必须实测）
- [ ] 5.2 建一个**干净虚拟环境**，装构建出的 wheel + `[api,cli]` extra（不带源码路径），在该环境跑 `PYTHONPATH` 为空的 `pytest tests/ -q` 与 `eval-suite run examples/minimal/suite.yaml`
- [ ] 5.3 装 sdist 重复 5.2（防打包元数据只照顾了本地布局）
- [ ] 5.4 记录制品验证结果：任何一项红，停下修，不带病打 tag

## 6. 发布（每个对外可见动作都需用户确认）

- [ ] 6.1 **取得用户明确授权**后才打 tag（PyPI 已发布版本不可撤回）；tag 命名沿用仓库既有习惯
- [ ] 6.2 确认 `publish.yml` 触发前提：tag push 事件 + `PYPI_TOKEN` secret 有效
- [ ] 6.3 发布后复验：在干净环境里 `pip install --upgrade aeval-framework`，确认解析到 0.2.0，并重跑 5.2 的两条命令
- [ ] 6.4 push 与任何远端动作同样需要单独授权

## 7. 交接项（本变更不做，登记去向）

- [ ] 7.1 宿主 finalize span 发往 Phoenix 的三个假零（`getattr(result, 'turns'|'total_tokens'|'duration_ms', 0)`，`RunResult` 无这些字段）→ 属宿主仓库变更，动手前先量 `run_span_collector` 的属性形状
- [ ] 7.2 `agenthub.agent_name` 补齐后把 `agent.name` 接入映射（已决定不把 agent_id 当名字）
- [ ] 7.3 下一个真正的能力变更是 ④ agent 指标目录（宽签名 `measure()`、judge 看轨迹、跨评分者一致性 κ/α、`reward_basis` 式乘性安全门、轨迹默认仅诊断）→ 需另立 change 与提案
- [ ] 7.4 宿主 `docs/eval-harness-design*.md` 仍留着 ① 修掉的旧 `pass@k` 实现且被标"✅ 已修复" → 宿主文档改动
- [ ] 7.5 宿主 `ruff check .` 基线 117 个错误，与其 CLAUDE.md 自检清单矛盾 → 需用户允许触碰非评测代码后处理

## 8. 验证门（本变更收尾）

- [ ] 8.1 Aeval：`ruff check packages/agent-eval` 通过 + `PYTHONPATH=src pytest tests/ -q` 全绿
- [ ] 8.2 宿主：`pytest tests/test_eval_integration_* -q` 全绿；我改动文件零新增 ruff 错误（基线有既有告警）
- [ ] 8.3 `openspec validate --all --strict` 通过；`openspec list --json` 仅剩本变更
- [ ] 8.4 两个仓库 `git status` 干净（除刻意排除项），且 1.6 的签名配对检查再次成立
