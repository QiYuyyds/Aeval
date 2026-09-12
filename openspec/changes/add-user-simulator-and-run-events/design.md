## Context

只写影响做法的事实。动机见 `proposal.md` — Why；行为契约见 `specs/`。

- ③ 之后契约是 `run(view, session) -> TrialEvidence`（`core/contract.py:273`），**一次调用对应一个 trial**；`TrialSession`（`:116`）由框架构造、适配器消费，已有 `emit()` / `harness_probe()` / `cancelled` / `remaining_ms`。轨迹通道 `TrialEvidence.transcript` 已是列表（`core/types.py:770`），`session_id` 已归一化但无人使用。
- 环境侧：`EnvironmentManager` 六个方法齐全（`contract.py:469`），生命周期严格 per-trial（`runner.py:817` 建、`:1039` 拆），且 trial 是 `asyncio.gather` 并发跑的（`:774`）—— 跨 trial 持久在结构上不可能。`EvidenceBoundary`（`types.py:664`）记采集/规范/映射/脱敏，**没有环境身份**。
- 判据侧：进程内自定义 grader 早已可用（`EvalRunner(graders=[...])`，未知名 → `invalid/unknown_grader`），但 `cli.py:408` 装配时 `graders=` 与 `environment=` 都不传，entry-point 只发现 `agent_eval.runners` 一组 —— **命令行是降级入口**。`code_based` 锁死 contains/not_contains/regex/exact 四种。
- ④ 刚交付两样可直接复用的东西：诊断块（「分值+分布，不进任何分母」）与门的取信声明机制。轮级结论有地方放了。
- 已定的三条口径（用户 2026-09-07 拍板）：一个完整对话=一个 trial；可执行判据用原生发现+进程内；环境只做到「声明初始态 + 落盘身份」，不引入串行化。
- 研究文档 P1-A 预期「`AgentRunner` 契约若需感知多轮则断裂 → 升 0.4.0」。下面的 D1 是对该预期的实测反驳。

## Goals / Non-Goals

**Goals:**

- 让「对话/长时程」成为可评测的任务形状，且**宿主已实现的三个协议（`AgentRunner` / `EnvironmentManager` / `Grader`）签名一个都不改**。
- 轮次与事件全部成为带来源与时刻的证据，因而可复核、可离线重放、可被 ③/④ 的取信声明约束。
- 把「扩展点只能靠 Python 手工装配」这道墙拆掉 —— 它同时是新模拟器的落地前提。
- 分母口径零变化：`statistics_version` 维持 2。

**Non-Goals:**

- 不做跨 trial 复用的有状态环境（要串行化，见 D6）；不做 Docker/容器参照实现（属 P2-C）。
- 不做被评方状态的断点续跑（见 D7）。
- 不做多智能体编排、不做在线评测、不做子进程沙箱（反范围清单 + D5）。
- 不引入任何新运行时依赖。

## Decisions

### D1：轮次循环挂在 `TrialSession` 上，不挂 `AgentRunner`

会话推进的钩子（取下一条用户消息、注入事件、注入人工消息）加在**框架自己构造的** `TrialSession` 上；适配器仍是被调用一次的那个 `run()`，它向 session 要下一轮而非被反复调用。于是：宿主实现零改动（单轮适配器不调这些钩子即可），⑤ 成为一次**非破坏性**的 0.4.0，而不是继 ①③④ 之后又一次断裂。不选「给 `AgentRunner` 加 `run_turns()`」：那会把破坏半径重新扩大到每个宿主适配器，且 ③ 刚立的「协议演进不保留双路径」意味着不能新旧两条并存。不选「让适配器自行循环、框架不管」：框架就再也无法核对「声明了 3 轮是否真走了 3 轮」，静默降级回来了 —— 所以配套要求是「未按会话行事 → `invalid`（配置故障）」。

### D2：一个完整对话 = 一个 trial，轮级只进诊断块

分母单位仍是 trial，`statistics_version` 保持 2，①②③④ 建立的所有历史 run 继续可比。轮级度量（每轮 token、是否自纠、事件后状态）作为 ④ 的诊断量呈现。不选「每轮一个评分单元」：那是分母口径变更，① 的全部努力就是让分母诚实且可声明，为一个新任务形状把它推翻不划算；真要时另开变更并显式递增。不选「两种都支持按套件声明」：双轨分母正是 ② 之前「缺失与零混同」那一坑的翻版。

### D3：模拟用户读数记 `observed_by: harness`，不开新来源级别

模拟器是评测侧代码的产物，与运行中探针同级（研究文档的当前倾向，此处采纳）。不选「新增 `simulated` 级别」：③ 的三级枚举被 ④ 的通道级取信声明、`weakest_evidence` 披露、门的 subject 排除规则三处引用，加一级要同时改判定与呈现语义，收益只是分类学上的干净。

### D4：`prompt` 保持必填，语义收窄为「首轮用户输入」

会话维度只增不改：`conversation.turns` / `conversation.goal` 是可选新增。不选「`prompt` 改为可选、首轮可写进 conversation」：那会让 `TaskView.prompt`、`MeasurementContext`、judge 提示词装配、宿主适配器全部出现判空分支，正是 ④ 设计阶段刻意避开的「可选参数超集使缺失与未传不可区分」。

### D5：可执行判据走 entry-point 发现 + 进程内，零依赖、零沙箱

新增 `agent_eval.graders`（及 environment / simulator 对应组），CLI 与 REST 共用同一份发现结果；用户代码在自己进程内跑，故障由既有 `grader_timeout` + `GRADER_ERROR → invalid` 兜住。备选都留下更贵的代价：借 `harbor-rewardkit`（决策 2 曾提过）等于把它的版本节奏变成我们的版本节奏，而本轮真正缺的只是「发现与注入」；子进程沙箱要把 `TrialEvidence` 跨界传输，等于造第三套证据数据模型。定位依据是自部署 dev-time 工具 + 用户评自己的 agent（威胁模型本就软）。

### D6：环境保持 per-trial 与并发，只加「初始态声明 + 身份落盘」

fixture 声明由 `EnvironmentManager.setup(task)` 从既有 task 句柄读取（**签名不变**），环境标识与版本写进 `EvidenceBoundary`，使跨 run 比较能声明「是否同一环境」，不可比时报 `not_comparable_reason` 而不是硬比。不选「跨 trial 复用环境」：`runner.py` 的 trial 是 `gather` 并发的，复用要么引入「串行 task」这条新语义轴（预算、超时、取消、`verify_clean` 的告警语义全要重定义），要么破坏并发；两者都不该塞进一个以对话为主的变更里。真需要时与 P2-C 的容器参照实现一起做更划算。

### D7：只做「用户侧可重放」，不做被评方断点续跑

事件流与用户输入带时刻落盘，配合 ③ 的库层重评即可「不再调用被评系统而核对对话输入」。真正意义的 mid-trial resume 需要序列化被评方与环境状态 —— 研究文档的反范围与「不做全量状态序列化」的自述都挡在这条路上，且它省下的只是 agent 侧时间，收益/复杂度不划算。记为已推迟，不是已放弃。

### D8：轮级与事件类判据复用 ④ 的取信声明，不新造机制

「事件之后成立」是往既有 `JudgmentMoment` 枚举里加一个值（新增值非破坏），并复用 `_merge_state` 的时刻窗口语义；诊断类轮级量复用 ④ 的诊断块。④ 的教训在这一点上直接适用：门曾经差点长出第二套证据声明。

## Risks / Trade-offs

- **多轮真流量验收比 ④ 更贵**（轮数 × agent 调用）→ 缓解：验收固定用小形状（2–3 轮 × 2 trial），沿用 ④ 组 8 的做法——**先报调用数、拿到授权才跑**，不跑就如实留未勾选。
- **目标驱动模拟器破坏可复现性** → 缓解：CI 与离线测试一律只跑预写话术（零模型调用）；模拟器每句话术连同时刻进证据，可事后复核；缺配置时返回带原因的不可用而不是崩溃。
- **「轮数未消费完 → invalid」可能把整批 trial 打成无效，看着像评测坏了** → 缓解：错误文案点名是哪个适配器、消费到第几轮；既有 `invalid_ratio_limit` 门禁会把这种 run 显式判为不可信，而不是给一个静默的假绿。
- **惰性导入第三方代码 = 导入即执行其模块代码** → 缓解：只在套件确实引用某名字时才导入（非启动期全量导入）；导入异常降级为「该扩展点不可用」告警 + `unknown_grader`，并保留来源包名供人排查。
- **`EvidenceBoundary` 新增环境身份会让既有 run 与新 run 判为不可比** → 缓解：历史行读回为空并显式标注「未记录环境」，`compare` 给 `not_comparable_reason`（该行为已有 spec 支撑），不报错、不猜测。

## Migration Plan

无协议断裂，迁移面只剩两处陈旧：`examples/achat/http_agent_runner.py:57` 仍在实现 0.2.0 的 `run(task) -> 三元组`，本变更内改到 ③ 契约（README 承诺破坏性变更要更新受影响实现，这次补账）；`openspec/config.yaml` 仍写 v0.1.0 且声称 `specs/` 为空，一并修正。回滚：按组逐笔 revert；D1 是唯一结构性选择，评审定稿前不动实现。

## Open Questions

- 预写话术用完而被评方仍在追问时，是否允许「以最后一条话术重复一次」作为默认？先按「会话结束」实现，真跑对话型 agent 后再定，不影响 specs 与任务拆分。
- 目标驱动模拟器的「目标达成」判定权归模拟器还是框架？当前倾向归框架（用既有判据的通过信号收尾），留到实现时用一轮真流量数据定。
