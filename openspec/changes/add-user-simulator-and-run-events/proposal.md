# ⑤ 覆盖面：让任务可以是对话、环境可以有初始态、判据可以是用户的代码

> 日期：2026-09-07 ｜ 对应研究文档 [06-optimization-roadmap.md](../../../research/agent-eval-landscape/06-optimization-roadmap.md) 的 **P1-A**（里程碑 0.4.0 旗舰）

## Why

Aeval 的任务模型锁死在「一条静态 prompt」。③ 把 `run()` 换成了 `run(view, session)`，`session.emit()` 让轨迹通道已经是 N 长的 —— 但没有任何东西决定**第 2 轮用户说什么**。于是框架只能评解题型 agent，而行业主战场已经是对话与长时程任务（τ²-bench / GAIA2 / OSWorld 2.0）。

时机成立：④ 交付了门与诊断/判分角色轴，多轮带来的「轮级结论」第一次有地方放（诊断块）而**不必动分母口径**；③ 交付的 `TrialSession` 恰好是框架自己产出的对象 —— 轮次钩子加在它上面，宿主实现的协议一个字都不用改。

调研还量到第二道墙，且它会挡住 P1-A 自己的交付：**自定义判据与环境今天只能靠 Python 手工构造 `EvalRunner` 才挂得上**（`cli.py:408` 装配时既不传 `graders=` 也不传 `environment=`，entry-point 只发现 `agent_eval.runners` 一个组）。按既定决策「开箱可用是硬要求」，如果新加的 `UserSimulator` 扩展点沿用同样的装配方式，它落地即不可用。所以发现机制属于本变更的前置件，不是顺带修。

## What Changes

1. **轮次循环挂在 `TrialSession` 上，`AgentRunner` 契约不变**：会话由框架驱动，适配器通过 session 取下一条用户消息。**BREAKING 的规避是设计目标**；配套要求是「不静默降级」——多轮任务遇到未按会话行事的适配器，判 `invalid`（配置故障），绝不按单轮出分。
2. **`UserSimulator` 扩展点**：两种模式，脚本化话术（确定性、零 LLM，CI 用）与目标驱动模拟（经既有 `LLMFn` 注入，缺配置返回带原因的不可用而不是崩溃）。模拟用户的读数记 `observed_by: harness` —— 它是评测侧代码的产物，与探针同级（沿用研究文档的当前倾向）。
3. **套件新增 `conversation` 维度**（向后兼容）：`prompt` 语义收窄为「首轮用户输入」并保持必填，后续轮次与目标声明在 `conversation` 下 —— 零 schema 断裂、不引入可空分支。
4. **运行中事件注入**：`inject_event(...)`（环境事件）与 `human_message(...)`（运行中介入，对齐 Inspect 的 Intervention 语义）；事件带采集时刻进入 transcript 证据；判定时刻枚举新增「事件后状态」值（新增值，非破坏）。
5. **扩展点发现与可达**（前置件）：新增 entry-point 组发现自定义 grader / environment / simulator，CLI 与 REST 装配时真正注入，能力清单与 CLI 读同一份注册结果。
6. **环境可声明初始态 + 环境身份落盘**：fixture 声明走既有 `setup(task)` 句柄读取（签名不变）；`EvidenceBoundary` 记录环境标识与版本，使「同一结论出自同一个环境」可核对。生命周期仍 per-trial、仍并发 —— 不引入串行化这条新语义轴（本轮明确不做）。
7. **事件流随证据落盘，使会话可离线重放**（用户侧确定性）。agent 侧断点续跑需要状态序列化，与研究文档的反范围倾向冲突，显式推迟（理由见 design D6）。
8. **两处陈年缺陷随手修**：`examples/achat/http_agent_runner.py` 仍在实现已退役的 0.2.0 `run(task)` 签名（README 承诺破坏性变更要更新受影响实现）；`openspec/config.yaml` 仍写 v0.1.0 且声称 `specs/` 为空。

## Capabilities

### New Capabilities

（无 —— 词表已锁定为 11 个能力，⑤ 全部落进既有权重路径，不新增域层级。）

### Modified Capabilities

- `extension-contracts`：新增 `UserSimulator` 协议与 `TrialSession` 的轮次/事件钩子；扩展点发现注册表；「协议演进不保留双路径」约束本轮的规避方式（见设计 D1）。
- `suite-format`：`conversation` 任务维度与事件脚本声明；`prompt` 语义收窄为首轮输入。
- `orchestration`：会话驱动的执行循环、多轮 trial 的取消与预算边界、事件流的落盘与离线重放。
- `graders`：判定时刻枚举新增「事件后状态」；自定义判据按名声明与发现后的可用性。
- `storage`：证据边界须记录环境身份，使跨 run 比较能声明「同一环境」。
- `cli`：`eval-suite run` 装配时注入发现到的自定义判据/环境/模拟器，并与 REST 一致可见。

## Impact

- **代码**：`core/contract.py`（`UserSimulator`、`TrialSession` 钩子）、`core/types.py`（`JudgmentMoment` 新增值、`EvidenceBoundary` 环境字段、任务模型 `conversation`）、`core/suite.py`（校验）、`core/runner.py`（会话循环、发现装配、事件落盘）、`cli.py`（装配注入）、`api/`（注册表可见）、`graders/_evidence.py` 与 `state_check.py`（事件后状态）、`examples/`。
- **破坏性**：**无协议断裂**（宿主实现的 `AgentRunner` / `EnvironmentManager` / `Grader` 签名全部不变；新钩子挂在框架产出的 `TrialSession` 上；`prompt` 仍必填）。唯一的语义收窄是「多轮任务的适配器必须按会话行事，否则 invalid」，它只影响新写的多轮套件。
- **版本**：0.4.0（新增能力，非破坏性 minor）。`statistics_version` 维持 **2** —— 一个完整对话仍是一个 trial，分母口径不变（研究文档 design 开放问题 2 的既定倾向）。轮级结论只进 ④ 的诊断块。
- **依赖**：无新增运行时依赖（模拟用户经既有 `LLMFn` 回调；不引入 langchain / inspect / harbor-rewardkit）。
- **验证门**：`cd packages/agent-eval && PYTHONPATH=src pytest tests/ -q` + `ruff check packages/agent-eval`；脚本化会话的确定性 e2e（无网络）；事件注入时序的离线回放测试；宿主真流量验收沿用 ④ 的组结构（**多轮真流量不验不许归档**）。
