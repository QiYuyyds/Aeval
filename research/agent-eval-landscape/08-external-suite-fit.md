# 08 · 外部评测集适配判断：该承载哪些、什么条件下承载

> 快照日期：**2026-09-11**（晚于本语料其余各篇的 09-07，代码依据也不同，见文末"与 04 的差异"）。
> 基准出处见 [01](./01-benchmark-landscape.md) 与 [`docs/open-source-agent-eval-survey.md`](../../docs/open-source-agent-eval-survey.md)；空白依据见 [05](./05-gap-analysis.md)；本文不重做调研，只回答一个问题：**这些开源评测集里，哪些该进 Aeval、在什么条件下进。**

## 一、准入判据：不是"格式能不能映射"

题面映射永远能成——`EvalTask.prompt` + `env: dict[str, Any]` 透传（`core/types.py:490`）装得下任何基准的题目。所以"能映射"没有区分度。真正的判据只有一条：

> **该基准的判定读数，能否落在被评 agent 写不动的通道上。**

因为 Aeval 的全部差异化都挂在 `observed_by: harness|runner|subject` 这三级上（[04](./04-aeval-coverage-matrix.md) 矩阵 #3"独有"）。一个适配若只把外部分数搬进来、证据其实来自被评方自报，Aeval 就退化成统计外壳——**比不用它更糟，因为它会让人以为溯源已经做了**。

这条判据不是抽象洁癖。伯克利团队 2025 年自动化审计 8 个主流基准（SWE-bench / WebArena / OSWorld / GAIA / Terminal-Bench）**全部可被攻破**：SWE-bench 容器里放一个 `conftest.py` 用 pytest 钩子把所有测试结果改写为"通过"，500 题满分而零个 bug 被修；WebArena 可用 `file://` 直接读本地配置里的标准答案（见 survey 第六节）。**这些基准的共同破口是判定器与被评 agent 同处一个可写面**——正是上面那条判据的反面。

```
外部评测集的四层           Aeval 承接槽位                                    现状
──────────────────────── ──────────────────────────────────────────────── ──────────
题面  question/issue  →   EvalTask.prompt / env                            ✓ 就位
                          TaskView 只裁 id/description/prompt/env            （答案键在类型里
                          （core/types.py:640）                                就不存在）
初始态  db/docker/vm  →   EnvironmentManager.setup(task)                   △ 协议在、实现为零
                          + EnvironmentDeclaration.fixture (⑤, :360)
判定  verifier/tests  →   Grader（9 个内置 + agent_eval.graders 组）        △ 只有走 probe()
                          读数须来自 EnvironmentManager.probe()             才算 harness 级
统计  resolve rate    →   pass@k / pass^k / Wilson / 三态 verdict           ✓ 领先，基准本身没有
```

**推论**：适配工作的难点从不在转换器，而在判定读数的来源级别。下面三层的划分依据就是这个，不是"实现难度"。

## 二、三层结论

### 第一层 · 现在就适合，缺的只是一个转换器

| 评测集 | 判定读数落点 | 承接槽位 | 条件 / 注意 |
|---|---|---|---|
| **GAIA / BrowseComp / HLE**（唯一短答案型） | gold answer 在框架侧比对 | `code` grader（contains/regex） | 需要一个**答案归一化薄判据**（大小写/单位/列表序）；多模态附件经 `env` 透传由 runner 自读 |
| **BFCL v1/v2**（AST 比对，不真执行） | 调用序列 vs 期望序列 | `tool_calls` grader | `capture_tool_arguments` **默认关**（`types.py:674`），须 per-suite 显式开且受脱敏约束 |
| **MT-Bench 式固定多轮** | judge 打分 | `model` grader + `conversation.turns`（`types.py:312`，预写话术、零模型调用、确定性） | rubric 仍是自由文本整体发 judge（P3-B 未开工）→ 单 judge 分按**诊断量**用，不进分母 |

场景画像：**"自家 agent 产品每次改 prompt / 换模型，在公开题集的子集上到底有没有变好"**。这是 Aeval 的主场——公开基准只给一个 pass@1 点估计，Aeval 给区间、给 pass^k 一致性、给 invalid 不占分母。

### 第二层 · 能做，但各自要先补一层东西

| 评测集 | 要先补的那一层 | 备注 |
|---|---|---|
| **τ-bench / τ²-bench**（**性价比最高**） | 模拟器工具通道 | 它是这堆交互基准里**唯一零容器**的：环境是进程内 typed DB，`get_db_hash()` / `check_db` 正好做成 `probe()` 的 harness 级读数——不需要 Docker/VM/真网站。⑤ 的 `UserSimulator` + `conversation` 又刚好是它的地基。形不似处：dual-control 里**模拟用户手里有工具**，而 `SimulatorReply` 只有 `text/end`（`contract.py:107`），无动作通道 → 用户侧工具调用要么由 runner 侧代跑并 `emit`，要么注册自带工具能力的 `simulator` 名 |
| **SaaS-Bench / WebArena / OSWorld**（终态核验型） | 环境槽位隔离（见硬约束 1） | 判定形态与 `state_check` grader + `judgment_moment`（`types.py:168`，含 `after_last_event`）完全对得上，题面也装得下；阻塞点纯在环境并发 |
| **SWE-bench / Terminal-Bench** | **agent 写不到的执行通道** | 见下段：前置条件不是套件格式，而是 [06](./06-optimization-roadmap.md) P2-C 第 3 件的 `DockerEnvironmentManager` 参照实现 |

**SWE-bench 类的两条路，只有一条该走**：

```
(a) runner 内调 SWE-bench harness，把 patch + 测试结果 emit 回来
     → 拿到数字，同时把 conftest 作弊面原样收下
     → 且它记 runner 级，而默认取信级别 (harness+runner) 读得到它 = 静默放行
(b) 测试在第二个容器/只读挂载里跑，结果经 probe() 回来
     → harness 级，被评方写不动 = 这套证据模型的用武之地
     → 但需要 P2-C 的参照实现（框架核心仍不依赖 Docker，见反范围清单）
```

**结论：编码类基准的适配排在 P2-C 之后，不是之前。**

### 第三层 · 不适合，理由各异（说明理由比说明结论有用）

| 评测集 | 不适合的理由 |
|---|---|
| **Chatbot Arena / 人类偏好对战** | 要成对比较 + Elo/Bradley-Terry 聚合，Aeval 无此判据形态（P3-B 的 swap 是判据内部机制，不是对战聚合）；"众包人类"与"自部署评自家 agent"不同路 |
| **lm-evaluation-harness / HELM 的纯模型基准** | 那是模型评测不是 agent 评测（[02](./02-harness-tooling-landscape.md) 自标"不面向 agent"）；Aeval 不采 logprob |
| **VisualAgentBench / 具身（Minecraft、OmniGibson）** | `Observation.value` 是 JSON dict，**无二进制/图像资产通道**；`artifact` grader 查文件存在与内容，查不了视觉相似 |
| **SWE-Lancer / Vending-Bench 的经济计分轴** | 能承载题目、承载不了"赚到美元"——Aeval 的成本轴是**推理开销**（`core/pricing.py`）不是收入，口径要自建；反范围清单明确不做托管榜单 |

## 三、三条硬约束（代码验证，会决定排期）

1. **环境是单实例、trial 是并发的。** `runner.py:760` 一个 `Semaphore(concurrency)`；`:863` 每次 trial 对**同一个** `self.environment` 调 `setup(task)`；`:1034` 调 `snapshot()`——而 `snapshot()` 签名**不接受 task 或 trial 参数**（`contract.py:777`）。并发 >1 时 A 的基线与 B 的初始态在同一对象上互相覆盖。**最坏之处在于它不报错，只会安静给出一个看似正常的分数。** → 交互型基准今天要么 `--concurrency 1`（成本 ×N），要么适配器自己做 (task_id, trial_index) 槽位池：后者是适配器能解决的事、不需要框架改动，但**必须写进适配设计**。
2. **没有 trial 级 checkpoint。** `grep checkpoint|resume` 在 `core/` 与 `cli.py` 全空。P1-A（= ⑤）三件事里落了第 1（`UserSimulator`）与第 2（`inject_event` / `human_message`），**第 3 件 checkpoint 未做**。对 OSWorld 2.0 那种小时/天级任务是硬伤——崩溃即整个 trial 作废，长时程基准的适配成本被这一条放大。
3. **`SourceType` 无外部来源值。** 只有 manual / trace_mining / llm_generated / adversarial / regression 五个（`dataset/models.py:31`）。外部基准条目进来后 `source_ref` 能写 `gaia/Level-1-xx`，但 `source_type` 只能标成 `manual`——**溯源口径在导入这一步就断了一次**。这是 P4-D 里最便宜的一步，也是承接任何外部内容之前必须先补的一块。

顺带：`EnvironmentManager` / `Grader` / `UserSimulator` / `AgentRunner` 四个扩展点全部经 entry-point 组发现（`core/discovery.py:31-33`、`cli.py:46`），**"写一个外部基准适配器"在机制上是通的**——第三方包发出去、装进同一 venv、套件按名引用即可，不需要 fork 框架。

## 四、为什么值得适配（以及不值得的时机）

若目标只是**拿一个榜单数字**，基准自带 harness 一条命令更便宜，Aeval 加不了分。值得的只有这五个，都很具体：

```
① pass^k / 区间      基准报单点；Aeval 能答"这 62% 稳不稳定"
② 跨版本可比          STATISTICS_VERSION + compare —— 基准口径一变分数即废
③ 判定器隔离          probe 通道 = 正是那 8 个基准的破口（本文第一节）
④ 成本/时延一等轴      基准普遍不报，选型的一半就是它
⑤ regrade             换判据/换 judge 不重跑 agent —— 500 题 × N 次规模下这条就是钱
```

## 五、对排期的含义

本文**不改动 [06](./06-optimization-roadmap.md) 的优先级**，只给它补"选材"：

| 已定提案 | 08 追加的选材/前置关系 |
|---|---|
| **P4-D**（分发） | 第一步应是最小的 `SourceType.EXTERNAL` + 转换器，而非任务包；**任何外部内容承载之前** |
| **P1-A**（⑤，进行中） | 已解锁第一层与 τ² 类；**未解锁**长时程（硬约束 2 的 checkpoint 是它没做完的第三件） |
| **P2-C**（安全内容） | 其 `DockerEnvironmentManager` 参照实现同时是 **SWE-bench/Terminal-Bench 类适配的前置**——这一层用途 06 未记，值得回写 |
| **P5-E**（功效分析） | 服务"从 500 题里选多大的子集才够分辨"这个具体问题；它决定第一层适配在成本上是否成立 |

## 六、开放问题

1. **目标分叉**：(a) 让 Aeval 成为"跑公开基准的统计外壳"（要 importer + 子集选择 + 分发 = P4-D），还是 (b) 用公开基准的题目回归自家产品（要 runner 适配器 + 功效分析 = P5-E）？两条路的第一块砖完全不同，当前代码对 (b) 的支持明显更实。
2. **τ² 的 dual-control 值不值得开一个协议问题**：模拟用户只产话术、不产动作，是 ⑤ 的有意简化还是欠设计？若是后者，那可能是下一个 change 的核心命题，**不该由适配层绕过**。

## 与 04 的差异（读 04 时请一并看这条）

[04-aeval-coverage-matrix.md](./04-aeval-coverage-matrix.md) 的 **#11（用户模拟器）/ #12（事件注入）/ #23（运行中介入）** 三行仍写"缺失"——那是 09-07 快照，**⑤ 已在 09-10 实现这三项**（`contract.py` 的 `UserSimulator` / `inject_event` / `human_message`；`types.py` 的 `ConversationSpec`）。那三行现已过期。注意⑤**尚未提交**（`openspec/changes/add-user-simulator-and-run-events`，51/58），所以本文的判断依据是工作树代码，不是 HEAD。
