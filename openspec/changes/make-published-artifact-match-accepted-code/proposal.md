# 让发布出去的包与我们验收过的代码是同一份

> 日期：2026-09-06

## Why

这一天的验收成果目前**只存在于两个仓库的工作树里**，而对外发布的那份是坏的：

- PyPI 上 `aeval-framework` 是 **0.1.0**，README 又让开发者 `pip install aeval-framework` —— 一个新 clone 的人按文档装上去，拿到的是"读不到任何 trace 属性 + 成本对明细桶双重计费 + `pass@1` 语义错误"的那一版。仓库 `pyproject.toml` 仍写 `version = "0.1.0"`。
- **两个 HEAD 合起来跑不通**：Aeval 的 `AgentRunner.run()` 已经是 ③ 破坏后的 `(view, session) -> TrialEvidence`（提交 `4fc28cf`），而宿主的迁移（`backend/app/eval_integration/` 下 6 个文件）**尚未提交**，宿主 HEAD 里还是旧签名。
- **①② 换来的 10 个能力 / 44 条契约主 spec 完全不在 git 里**（`openspec/specs/` 是 untracked），归档产生的移动也未提交。一次 `git checkout .` 或 reset 就会抹掉。

目标用户的定位已经确定为「下载下来评自己 agent 的开源开发者」（2026-09-05 决定），所以"发布的制品与验收过的一致"不再是整洁问题，而是这个定位的直接兑现条件。

## What Changes

1. **落盘**：把两个仓库的成对改动提交（Aeval 的 spec 语料 + 归档移动 + OpenInference 预设；宿主的 ③ 迁移）。
2. **核对**：`add-openinference-mapping-preset` 与 `separate-collection-from-grading` 的任务勾选由不同会话写入，**逐条实跑验证**而不是采信清单（已发现一处：preset 的 5 条"未办"里 4 条其实已完成）。
3. **归档**：核对通过后归档这两个 change，顺序 `preset → ③`（③ 落在新能力 `extension-contracts`，与 preset 的 `trace-provider` 无冲突）。
4. **版本与发布**：`0.1.0 → 0.2.0`，写迁移说明（含破坏的接入契约、收紧的门禁语义、`trace_mapping=` 接入方式），发布前**从构建出的 wheel/sdist 装进干净环境**跑一次离线示例与测试，通过后才打 tag。

**不含**：④ agent 指标目录（那是下一个独立变更，需要自己的契约设计）、覆盖面（⑤）、差异化项（⑥）。见文末"交接项"。

## Capabilities

**无 spec 级行为变更。** 本变更做的是提交、核对、归档、版本与发布验证——改变的是"哪些内容在版本控制与制品仓库里"，不改变软件的可观察行为，因此按项目约定设 `skip_specs: true`，不为通过校验而编造 Requirement。

被归档进主 spec 的那 44 条契约属于 ①②③/preset 四个 change 本身，本变更只是把它们移动到 `openspec/specs/`。

## Impact

- 仓库：`Aeval-publish`（HEAD `4b4572a`，工作树含 4 个源码文件 + 3 个测试文件 + 4 个文档/README + 未跟踪的 `openspec/specs/`、`openspec/changes/archive/`、`openspec/changes/add-openinference-mapping-preset/`、`separate-collection-from-grading/`、`make-published-artifact-match-accepted-code/`）
- 跨仓库：`bitdance-agenthub-main` 有 6 个文件承载 ③ 的宿主迁移，未提交（框架破坏契约后必须成对落盘）
- 版本：`packages/agent-eval/pyproject.toml` 的 `version`；发布由 `.github/workflows/publish.yml` 在 **push tag** 时触发，依赖 `PYPI_TOKEN` secret
- 不可逆动作：PyPI 版本一旦发布不可撤回 —— 因此 tag/发布步骤**必须逐次经用户确认**，本变更内所有对外可见操作都是如此
- 门禁现状（写提案时实测）：Aeval `pytest tests/ -q` **634 passed**、`ruff check` 干净；宿主 eval 集成测试 **57 passed**；`openspec validate --all --strict` 通过
- 交接项（不在本变更做，写在 tasks 末尾）：宿主 finalize span 发往 Phoenix 的三个假零（D7）、`.qoder/` 是否入库、④ 的提案
