> 冷启动说明：本清单可独立执行，不依赖上一段对话。项目约定见 `openspec/config.yaml`，方向与已定决策见 `openspec/specs/`（10 能力 / 44 条契约）与各 change 的 design.md。

## 1. 落盘成对改动（最高优先，先于一切）

- [ ] 1.1 执行前重新读状态：`git -C Aeval-publish status --porcelain` 与 `git -C bitdance-agenthub-main status --porcelain`。**只按具体路径 add，禁止 `git add -A`** —— 这两个仓库存在并发提交者，已发生过"提交一半"
- [ ] 1.2 Aeval 第 1 笔：提交主 spec 语料与归档移动 —— `openspec/specs/`、`openspec/changes/archive/`，以及 `openspec/changes/fix-stats-and-denominators/`、`align-trace-evidence/` 的删除（约 21 D + 2 个未跟踪目录）。建议消息：`chore(openspec): archive statistics and trace-evidence changes, lock 10-capability spec corpus`
- [ ] 1.3 Aeval 第 2 笔：OpenInference 预设全套 —— `packages/agent-eval/src/agent_eval/{trace/mapping.py,trace/__init__.py,cli.py,api/app.py}`、`tests/{test_trace_normalization.py,test_cli.py,test_standalone_api.py}`、`docs/{integration-guide,architecture,cli-reference}.md`、`README.md`、`README.zh-CN.md`，加 `openspec/changes/add-openinference-mapping-preset/`。消息：`feat(trace-provider): ship selectable OpenInference mapping preset with meta and CLI selection`
- [ ] 1.4 Aeval 第 3 笔：本变更目录 `make-published-artifact-match-accepted-code/` + ③ 遗留未提交项（若有）
- [ ] 1.5 宿主一笔：③ 的迁移 —— `backend/app/eval_integration/{runner.py,environment.py,config.py,graders/artifact.py,graders/dispatch.py}` 与 `backend/tests/test_eval_integration_runner.py`。消息：`feat(eval-integration): migrate to evidence-provenance runner contract`。**只点这些路径**，宿主另有 20+ 个无关未提交改动
- [ ] 1.6 验证配对着陆：`git -C Aeval-publish show HEAD:packages/agent-eval/src/agent_eval/core/contract.py | grep "def run"` 与宿主 `git show HEAD:backend/app/eval_integration/runner.py | grep "async def run"` —— 两边签名必须同为 `(view, session) -> TrialEvidence`，此前是"两个 HEAD 跑不通"
- [ ] 1.7 两仓库门禁复跑：Aeval `PYTHONPATH=src pytest tests/ -q`（预期 634 passed）+ `ruff check packages/agent-eval`；宿主 `.venv/.../python -m pytest tests/test_eval_integration_* -q`（预期 57 passed）
- [ ] 1.8 `.qoder/` 定去向：加进 `.gitignore`（倾向）或入库；二选一并提交

## 2. 逐条核对已勾任务（不信清单）

- [ ] 2.1 `add-openinference-mapping-preset/tasks.md`：25 条逐条给结论。**已知偏差**：清单称 5 条未办，实测其中 2.2 / 2.3 / 5.1 / 5.2 已完成（`/v1/meta` 有 `vocabularies`、`--vocabulary`、`AEVAL_TRACE_VOCABULARY`、README Features 有整段）→ 改正勾选并注明依据
- [ ] 2.2 `separate-collection-from-grading/tasks.md`：50 条由并发会话勾选，逐条找可指认的用例或命令输出。**重点四处**：来源三级取信、判定时刻（`一次取证 = 一个时刻`，提交 `a8c9b70`）、重评永不覆盖且 `current` 指针可从列读回（提交 `9005c0f`）、宿主迁移与实跑记录（提交 `e85d279`、`4b4572a`）
- [ ] 2.3 虚标处理：任何找不到证据的勾选项**改回未勾**并在条目下写明缺什么；不为了让清单好看而保留
- [ ] 2.4 确认 `10.4`（dashboard build）标注是否成立：若 ③ 确实未触及 `apps/dashboard`，记 N/A 并说明；否则补跑
- [ ] 2.5 未办的 `4.4`（宿主活跑，会产生真实 agent 调用）：需用户授权后执行，判据是 9 trial 仍全部 `valid` 且结论带最弱证据级别标注

## 3. 归档两个 change

- [ ] 3.1 顺序 **preset → ③**（③ 落在新能力 `extension-contracts`，与 preset 的 `trace-provider` 无冲突；反序会让 `trace-provider` 的 Purpose 与需求归属来回改写）
- [ ] 3.2 归档前确认 `openspec/specs/` 已入库（第 1.2 步），并核对合并结果无同名 Requirement 冲突、主 spec 的 `## Purpose` 无 TBD
- [ ] 3.3 `openspec archive add-openinference-mapping-preset --yes`
- [ ] 3.4 `openspec archive separate-collection-from-grading --yes`
- [ ] 3.5 归档后主 spec 应为 **11 个能力**（新增 `extension-contracts`）；`openspec validate --all --strict` 通过；`openspec list --json` 只剩本变更
- [ ] 3.6 提交归档结果（一笔，`chore(openspec): archive ...`）

## 4. 版本与迁移说明

- [ ] 4.1 `packages/agent-eval/pyproject.toml`：`0.1.0 → 0.2.0`（不取 1.0.0 的理由见 design D1）
- [ ] 4.2 写迁移说明（`docs/` 下新章节或 `CHANGELOG.md`，取仓库现有习惯）：③ 破坏的 `AgentRunner` 签名与宿主侧改法；`trace_mapping=` / `--vocabulary` 接入；① 收紧后**门禁可能拦下历史放行的构建**（明确不提供 legacy 旗标）；历史 run 不回算亦不可重评；`cost_usd` 无价目表时不可计算
- [ ] 4.3 README 安装段：发布后 `pip install aeval-framework` 与从源码 `pip install -e ./packages/agent-eval` 并列，**不写死版本号**（design D4）
- [ ] 4.4 确认宿主 `.venv` 在正式包发布后可从 `aeval-framework==0.2.0` 安装（当前依赖 editable）

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
