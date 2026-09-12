## 1. 会话与事件的协议与模型（唯一结构性步骤，集中在此）

- [x] 1.1 `TrialSession` 增加三个钩子：取下一条用户消息、注入环境事件、注入人工介入消息；**`AgentRunner` / `EnvironmentManager` / `Grader` 签名一律不动**（设计 D1）
- [x] 1.2 `UserSimulator` 扩展协议入 `core/contract.py`：预写话术与目标驱动两种模式共用一个协议；模拟器输入视图 MUST 不含期望输出与答案键（沿用既有信息屏障，另加裁剪测试）
- [x] 1.3 任务模型新增 `conversation`（`turns` 预写序列 / `goal` 目标驱动）；`prompt` 保持必填、语义收窄为「首轮用户输入」，全链路不引入判空分支（D4）
- [x] 1.4 `JudgmentMoment` 新增「最后一个注入事件之后成立」值；新增值非破坏，历史结论读回不变
- [x] 1.5 `core/suite.py` 校验：`turns` 与 `goal` 互斥并存报错并给字段路径；不含会话声明的既有套件加载结果与本变更前逐字节一致
- [x] 1.6 单测：预写话术跑完整会话零模型调用且每条用户消息带 harness 来源与时刻；缺 LLM 配置的目标驱动模拟器返回带原因的不可用而不崩溃；模拟器输入视图断言看不到答案键

_以下两条为 2026-09-10 复审新增（spec: extension-contracts「模拟器知道自己在为哪个任务工作」、suite-format「只声明事件而未声明轮次供给方」）_

- [x] 1.7 `SimulatorContext.task_id` 与 `description` 目前**结构上恒为空**：`runner.py:868-879` 只把 `task.conversation` 交给会话，而 `ConversationSpec`（`types.py:312`）没有 `id`/`description` 字段，于是 `contract.py:367-368` 的 `getattr(self._conversation, "id", "")` 永远取到 `""`。改为构造 `TrialSession` 时显式传入 task 标识与描述并在装配 `SimulatorContext` 时使用；单测断言两字段非空且等于该 task（按域切人设的模拟器实现依赖它们）
- [x] 1.8 `ConversationSpec` 校验（`types.py:343`）新增拒绝：`events` 非空而 `turns` 与 `goal` 均为空时加载失败，错误信息点名 `conversation.events` 并说明「无轮次供给方即永不注入」；同时确认 `TrialSession.diagnostics()`（`contract.py:480-482`）在 task 声明了会话维度时不整体缺席 —— 现状 `runner.py:1190-1192` 让这种套件加载通过、run 正常出分而事件一条没发生，属 ④ 的「机制从未执行」类

## 2. 扩展点发现与装配（前置件，与 1 无依赖、可并行开工）

- [x] 2.1 新增 entry-point 组发现自定义 grader / environment / simulator，注册进运行时唯一的一份注册表（不保留「命令行一套、接口一套」的并行装配，D5）
- [x] 2.2 惰性导入：只在套件确实引用某名字时才导入其宿主包；导入异常降级为「该扩展点不可用」告警并继续装配其余，套件引用到它时按未注册给 `unknown_grader`
- [x] 2.3 同名冲突显式报错并列出两个来源包，MUST NOT 静默覆盖
- [x] 2.4 `cli run` 装配时真正注入发现到的 grader 与环境（修掉 `cli.py:408` 今天的降级入口）；`api` 能力清单与 CLI 读同一份注册结果
- [x] 2.5 引用未注册名字时的失败信息列出当前可用名字，并指出本次是否曾尝试加载某外部包
- [x] 2.6 单测：一个测试包通过 entry point 注册的判据可被命令行套件直接使用；损坏元数据只告警不阻断启动；CLI 与 REST 列出的名字集合相同

## 3. 会话执行循环

- [x] 3.1 一次多轮 trial 仍是**一次** `run()` 调用：框架经 session 供给轮次，全过程证据归同一 trial（D2）
- [x] 3.2 「声明轮数未消费完即会话结束」判 `invalid` 并给配置侧原因，错误文案点名适配器与消费到第几轮；MUST NOT 按已发生轮次给出看似完整的通过结论
- [x] 3.3 多轮下的取消、`remaining_ms`、步/token/成本预算边界逐条过（预算在会话内跨轮累计）
- [x] 3.4 轮级过程量（每轮 token、是否自纠、事件后状态）只进 ④ 的诊断块，不进通过率/pass^k/任何分母
- [x] 3.5 端到端：同一多轮任务跑 N 个 trial，`statistics_version` 仍为 2、分母为 N 而非 N×轮数；与不含会话的对照套件在通过率上逐字段可比
- [x] 3.6 单测 + MockRunner 多轮脚本：正常多轮、早退适配器、中途取消三种形状
- [x] 3.7 轮数上限改到**索取之前**判定：现状 `contract.py:391-395` 在 `await simulator.next_message(...)` 之后才查 `max_turns`，触顶那次会先生成一句话术再丢弃（真流量下即一次白付的模型调用），且该句若带 `end=True` 其收尾意图一并丢失；单测断言触顶那一次的模拟器调用计数不再增加（spec: extension-contracts「轮数上限在生成之前生效」）—— 2026-09-10 复审新增

## 4. 事件证据与离线重放

- [x] 4.1 注入事件与人工介入消息带时刻进 transcript 证据，来源记 harness，报告与结论可与普通消息区分
- [x] 4.2 `_merge_state` / `state_check` 支持「事件之后」窗口；声明该时刻而无注入事件时 MUST 报证据不足，MUST NOT 静默退化为「结束时」判定
- [x] 4.3 用户侧输入序列（首轮 + 话术/模拟产物 + 事件 + 人工介入）随 trial 证据落盘，构成可重放脚本
- [x] 4.4 库层重评消费已存用户输入：一次基于证据的重评分中被评系统调用次数为零，被评方输出不因重放而改写
- [x] 4.5 单测：事件注入的时序确定性（离线 e2e）；重放路径断言 agent 调用计数为 0
- [ ] 4.6 补序列化后端的读回腿：`TrialEvidence.user_inputs` 是本轮新字段，而现有断言全部只在内存后端成立 —— `tests/test_conversation.py:141` 的 `make_runner` 默认 `storage=MemoryStorage()`，组 4.3/4.4 的两条结论都没走过 SQLite 的落盘→读出路径。措辞要准确：该后端整块 `model_dump` 存、`TrialEvidence.model_validate` 取（`storage/sqlite.py:285-312`），字段本身大概率不丢，缺的是**「读回之后仍然算数」这条链从未被证**——读出后还要被重放路径与手写投影消费（④ 的 gate 字段就丢在手写投影上）。把「通道序 + 时刻 + 来源逐条一致」与「重放零被评调用」两条断言参数化到 Memory 与 SQLite 两个后端（spec: orchestration「内存后端不得替序列化后端作证」）—— 2026-09-10 复审新增

## 5. 环境初始态与身份（不引入串行化）

- [x] 5.1 task 可声明初始态/fixture 要求，由 `EnvironmentManager.setup(task)` 经既有 task 句柄读取（**签名不变**，D6）
- [x] 5.2 `EvidenceBoundary` 记录环境标识与版本；无环境参与时显式记「无环境」，历史行读回为空且不报错
- [x] 5.3 `compare` 在两侧环境身份不一致时给 `not_comparable_reason` 并拒绝方向性结论，而不是把环境差异归给 agent
- [x] 5.4 声明了环境检查判据却未装配任何环境 → 证据不足并指明缺环境装配，不折成被评方失败
- [x] 5.5 生命周期与并发不变测试：per-trial 建/拆、trial 仍并发、`verify_clean` 检出泄漏仍是「告警 + restore」不判失败
- [x] 5.6 单测：同一 fixture 声明跑两次得到相同的环境身份记录；换初始态声明后与历史 run 判为不可比
- [ ] 5.7 把 5.2/5.3 的两条结论在序列化后端上证一遍：`tests/test_environment_identity.py:91` 只装配 `MemoryStorage()`，因此 `EvidenceBoundary.environment_identity` / `environment_version` 的落盘-读回、以及读回后仍触发 `not_comparable_reason` 这条链从未在 SQLite 路径上断言。机制上说清楚以免白找：该后端整块 JSON 存、`RunResult(**data)` 整体重建（`storage/sqlite.py:151-152`），字段本身大概率不丢，要证的是「读回之后仍参与比较判定」这条链（④ 的 gate 字段正是丢在手写投影上）。同时断言变更前落盘的历史行在两个后端读回同为「未记录」（既不为 `none` 也不报错）（spec: storage「环境身份经序列化后端读回后仍能挡住误比」）—— 2026-09-10 复审新增

## 6. 目标驱动模拟器（LLM 侧）

- [x] 6.1 经既有 `LLMFn` 回调生成下一句，缺配置返回带原因的不可用结论（与 ④ 的 judge 缺配置语义同一条路）
- [x] 6.2 「目标达成 / 何时收尾」的判定权归框架：用既有判据的通过信号收尾（design 开放问题 2 的当前倾向），实现时以一轮真流量数据复核
- [x] 6.3 模拟器每句话术连同时刻与所用提示词进证据，供事后复核；提示词过既有脱敏钩子
- [x] 6.4 单测用确定性 stub 回调：话术生成、收尾判定、缺配置三条路径

## 7. 文档与两处陈旧修复

- [x] 7.1 `docs/yaml-format.md`：`conversation` 的两种模式、互斥规则、事件注入与「事件之后」判定的写法与校验规则表
- [x] 7.2 `docs/integration-guide.md`：`UserSimulator` 怎么写、自定义判据/环境如何通过 entry point 被命令行发现（含一个最小可跑包示例，直接服务「刚 clone 就能跑通」）
- [x] 7.3 `docs/grader-reference.md`：新判定时刻取值、轮级量为何只进诊断块、模拟器读数的来源级别与理由
- [x] 7.4 `docs/getting-started.md` 追加 0.4.0 段：**本轮无协议断裂**、`prompt` 语义收窄、轮数不进分母、跨 trial 环境复用与被评方断点续跑显式未做
- [x] 7.5 修 `examples/achat/http_agent_runner.py` 遗留的 0.2.0 `run(task)` 签名，改到 ③ 契约并跑通一次宿主冒烟
- [x] 7.6 修 `openspec/config.yaml` 的陈旧元数据（仍写 v0.1.0、仍称 `specs/` 为空）
- [x] 7.7 README（中英）Features 一行：多轮/长时程任务与运行中事件介入

## 8. 验证门（离线）

- [x] 8.1 `ruff check packages/agent-eval` 通过；lint 范围经 8.7 扩到 `examples/`（`ruff check examples --config packages/agent-eval/pyproject.toml`，CI 双命令）
- [x] 8.2 `cd packages/agent-eval && PYTHONPATH=src pytest tests/ -q` 全绿（含会话/事件/发现/环境身份/模拟器新测试）
- [x] 8.3 `tests/test_import_isolation.py` 与 `tests/test_vocabulary_isolation.py` 仍通过（新 entry-point 组不得引入 `app.*`）
- [x] 8.4 离线 `eval-suite run examples/minimal/suite.yaml` 正常产出，且分母与 0.3.0 逐字段一致（不含会话的套件零漂移）
- [x] 8.5 一个测试包注册的自定义判据经 `eval-suite run` 命令行跑通（证明 2.4 的墙真拆掉了）
- [x] 8.6 `openspec validate add-user-simulator-and-run-events --strict` 通过
- [x] 8.7 把 lint 范围从 `ruff check packages/agent-eval`（8.1 的 scope）扩到 `examples/`：`examples/achat/run_live_acceptance.py:43`（`EvalSuite` 未用）与 `:162`（`os` 未用）两处 F401 落在门覆盖不到的验收脚本里 —— 于是 8.1 是绿的而工作树并不干净；扩范围后把 8.1 的措辞一并改掉 —— 2026-09-10 复审新增（已完成：4 处可自动修复项经 `ruff --fix` 修复（2×I001 排序 + 2×F401，均确认真未使用）；CI lint 步骤扩为双命令，examples 用包内 pyproject 作显式 config —— 不与包内 lint 合并成一条命令，因为跨目录传 `--config` 时 isort 的 first-party 判定与包内运行不同，会对 `tests/` 误报 I001）

## 9. 宿主真流量验收（归档前置；④ 的教训直接沿用，不允许只靠离线绿就归档）

> ④ 的活跑在 690+ 单测与 `validate --strict` 全绿的前提下抓出 6 个框架缺陷 + 2 个套件写法陷阱，
> 全部落在「mock 与真集成的接缝」和「写侧有覆盖、读侧无人看」两类。⑤ 的会话循环、事件时刻、
> 发现机制三样同样只有真流量能证。

- [x] 9.1 宿主新增多轮验收套件：2–3 轮 × 2 trial、预写话术为主、一次运行中事件注入 + 一个「事件之后」判据、一个经 entry point 发现的宿主自定义判据；宿主适配器改到 ③ 契约后 `eval-suite validate` 通过（套件：examples/achat/conversation-suite.yaml + conversation-goal-suite.yaml + eval-ext；validate 双双通过）
- [x] 9.2 成本预估先行并取得用户授权：真实 agent 调用数 = trial × 轮数，另加目标驱动模拟器的 LLM 调用；不跑就如实留未勾选，MUST NOT 为勾清单擅自触发真调用（授权已取得：活跑 1 ≈ 6 次 agent 调用/0 次 LLM，活跑 2 ≤ 3 次 agent + ≤ 3 次 LLM；多候选凭证均 402/401 后未再重试）
- [x] 9.3 活跑第 1 轮（预写话术 + 事件注入，零模拟器 LLM）：记录 run id；验证轮数不进分母、模拟用户读数记 harness 且带时刻、事件后判据真生效、发现机制在宿主安装下真可达（不是靠 Python 手工装配）—— **run_ac44b1c37dfc**（宿主 AChat 真流量）：denominator total=2 valid=1（轮数不进分母 ✓）；trial#1 用户侧输入序列 = user_prompt → simulated_user → environment_event → simulated_user，全部 observed_by=harness 且带时刻（✓）；state_check 以 after_last_event 时刻依据事件后读数出结论（verdict=valid，未静默退化为 at_end ✓）；achat_session_gate 经 entry-point 组发现并 pass=True「会话完整： 4 条用户侧输入 (含 1 条事件)， 全部为 harness 来源」（发现可达 ✓）；trace 桥未启用走降级通道（trace_id_unavailable，不影响统计）。后续复跑因宿主 LLM 供应商（LongCat）配额耗尽（402）全部首段超时，属外部阻塞非框架缺陷
- [ ] 9.4 活跑第 2 轮（目标驱动模拟器）：验证收尾判定归框架这一选择真能收尾；复核「达成/未达成」的轮数是否落在合理区间，若普遍拖长则回到 6.2 修正 —— **机制已真生效但达成收尾路径未走通，如实未勾选**：run_eeec14818ab1 / run_49f2d4f9ab41 中模拟器缺配置路径真生效（verdict=invalid、invalid_reason=simulator_unavailable、end_reason/带原因文案落盘进诊断块，trial 不崩溃不占分母 ✓）；但全部候选 LLM 凭证失效（LongCat 402 Payment Required × 2、DeepSeek 401 × 3），话术无法产出，「goal_achieved 收尾 + 轮数合理区间复核」待宿主恢复凭证后补验
- [x] 9.5 report/API 两条腿读回两个 run：诊断块显示轮级量、`not_comparable_reason` 在环境身份变化时如实报不可比 —— CLI `show run_ac44b1c37dfc`：valid=1 invalid=1、pass@1=0% 带 Wilson 区间、avg=0.6667（✓）；API（standalone /v1）两条腿：GET /v1/runs/{id} 两个 run 均 200（statistics_version=2、evidence/regrade 字段齐）、POST /v1/compare 200；轮级诊断块经存储读回验证（session_diagnostics 含 end_reason=simulator_unavailable 全文案）；run 间比较按声明差异如实给 not_comparable_reason（「逐 task 的工具入参采集声明不同」先于环境身份判触发；环境身份差异报不可比已由单测固化）
- [x] 9.6 结果写回本清单（勾选 + run id + 判读）；发现缺陷先修复再归档 —— 本组完成是 `openspec archive add-user-simulator-and-run-events` 的前置条件（判定：活跑暴露的问题均为宿主侧外部因素 —— LongCat 配额 402、DeepSeek key 401、agent 端到端变慢 —— 未发现框架缺陷；唯一未闭合项 9.4 的达成收尾路径待宿主凭证恢复后补验，补验入口：`python examples/achat/run_live_acceptance.py`（宿主 venv，凭证恢复后自动走通）+ 证据库 aeval-acceptance-040.db）
