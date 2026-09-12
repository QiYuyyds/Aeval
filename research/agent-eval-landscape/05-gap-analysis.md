# 05 · 空白分析：五块结构性缺失

> 快照日期：2026-09-07。行业依据见 [03](./03-methodology-themes.md)；覆盖总表见 [04](./04-aeval-coverage-matrix.md)。
> 本文只列**经代码验证的缺口**，每块回答四件事：行业怎么做 / Aeval 有什么没什么 / 为什么重要 / 做成什么样算完。

```
        Aeval 现有地基层（领先，保持）
 ┌────────────────────────────────────────────────────┐
 │  证据溯源 · 分母纪律 · 重放审计 · 统计严谨 · 成本轴  │
 └────────────────────────────────────────────────────┘
            ▲ 五块空白都应"接在地基上"而不是绕开
 ┌──────────┬──────────┬──────────┬──────────┬────────┐
 │ A 交互模型 │ B judge   │ C 安全内容 │ D 分发生态 │ E 工程闭环│
 │ 用户模拟器 │ 偏差缓解   │ 金丝雀探针 │ 套件打包   │ 两车道CI  │
 │ 事件注入   │ rubric清单 │ 注入grader│ 任务包     │ 基线回归门 │
 │ checkpoint│ 金标校准   │ 沙箱参照   │ 污染卫生   │ 功效分析  │
 └──────────┴──────────┴──────────┴──────────┴────────┘
```

## A. 交互模型：只有"单轮静态 prompt"一个被评对象

### 行业怎么做

2025–26 的前沿评测对象已经是三种更复杂的交互：

1. **用户模拟器 / 双控**——τ²-bench 的核心：agent 与模拟用户**各持工具、必须协同**（[论文](https://arxiv.org/abs/2506.07982)）。评对话型 agent 没有用户模拟器，等于只能评"答题"不能评"服务"。
2. **动态事件注入**——GAIA2/ARE 的全部卖点：回合中途异步事件到达（新消息、状态突变、打断），考察适应性（[Meta ARE](https://ai.meta.com/research/publications/are-scaling-up-agent-environments-and-evaluations/)）。Inspect AI 的 **Intervention** API 是同一件事的工程化（评测者运行中向 agent 发消息）。
3. **长时程 checkpoint**——OSWorld 2.0 把任务拉到小时/天级；Inspect 加了 checkpointing（崩溃的 run 从断点续跑）。

### Aeval 有什么、没什么

- 任务模型：`prompt` 单字符串 → agent 跑 → 判分（`docs/yaml-format.md`；`core/types.py` 的 `EvalTask`）。无多轮对话脚本，无用户角色。
- 环境模型：`EnvironmentManager` 是 setup→run→teardown 单发（`core/contract.py`）。**没有"trial 进行中"的干预钩子**——这同时堵死了事件注入与运行中人工介入（矩阵 #11/#12/#23）。
- 重试：只覆盖 `TransientError`（显式包装）；长时程 trial 崩溃即整体作废（矩阵 #13）。

### 为什么重要

- 这是**结构性**缺口：用户模拟器与事件注入都要改套件格式（任务定义多一个"对话脚本/事件表"维度）与扩展点协议（`TaskView`、`EnvironmentManager`、`TrialSession`）。Aeval 的既定原则是"协议演进不留双路径"（变更①③④一以贯之）——拖得越晚，破坏性变更越大。
- 对话/客服/办公类 agent 是行业主战场（τ²-bench 被 36+ 模型跟踪）；评不了它们，框架的适用面被锁在"解题型"任务上。

### 做成什么样算完

- `UserSimulator` 扩展点：多轮任务的"用户侧"由 harness 扮演，话术脚本是任务定义的一部分；模拟用户属 harness 侧，其读数天然记 `observed_by: harness`（与现有证据模型无缝）。
- `TrialSession` 增加 trial 中钩子：`inject_event(...)`（环境事件）与 `human_message(...)`（运行中介入），事件流进 transcript 证据并带时刻。
- 长时程：trial 级 checkpoint/续跑至少做到"崩溃的 run 可从最近事件边界恢复重放"。
- 判定时刻语义（`at_end`/`any_time`）已有，只需为"事件后状态"扩一个枚举值。

## B. Judge 方法学：在"测量"一致性，没在"缓解"偏差

### 行业怎么做

- 偏差先缓解、再测量：**swap/顺序随机化、长度控制、跨模型集成**是标准缓解（[arXiv 2406.07791](https://arxiv.org/html/2406.07791v9)、[arXiv 2410.21819](https://arxiv.org/abs/2410.21819)）；judge panel 提升人一致性（[arXiv 2604.13717](https://arxiv.org/html/2604.13717v1)）。
- **结构化 rubric**：逐项 checklist、每项单独判 pass/fail、单独带证据引用、单独带权重（RaR，[arXiv 2507.17746](https://arxiv.org/abs/2507.17746)）。
- **金标校准闭环**：小人工金标集 → human-LLM 一致率/Krippendorff's α → 决定这个 judge 能不能用（[Arize 方法论](https://arize.com/blog/measuring-human-llm-judge-alignment/)已成标准引用）。

### Aeval 有什么、没什么

- `metrics/llm_judge.py`：`LLMFn` 单调用、单顺序、JSON 容错解析 + 重试；变更④给 judge 加了轨迹注入（按取信声明）。
- `graders/model_based.py:150`：rubric 是**一段自由文本**整体发 judge，带 dimensions——不是逐项清单。
- 变更④的 κ/α 是 judge-judge **测量**；不一致的根源若是系统性偏差（位置/冗长），量出来的低 κ 只会让人换 judge，而不知道换掉偏差就能修。
- human grader 零件在（REST 回调），但没有"金标集 → 校准报告"工作流（矩阵 #8/#9/#10）。

### 为什么重要

- judge 分数直接进分母；偏差不缓解，κ/α 数字没有可行动性（测量与缓解是两件事）。
- 结构化 rubric 与 Aeval 的证据引用文化天然契合：逐项引用 `[消息 2]`（`llm_judge.py` 的 `TRAJECTORY_PROMPT_HEADER` 已要求标注编号）比整体一个分可信得多，且逐项结果是 κ/α 与门机制的更好输入。
- 应接变更④顺理成章：④给了测量地基，⑤做缓解，故事连贯。

### 做成什么样算完

- **结构化 rubric grader**：rubric 从字符串变为项列表（`id/描述/权重/证据要求`），judge 逐项返回 `{pass, 引用}`，报告呈现逐项达成率；门可以声明在项级。
- **swap/顺序随机化**：成对比较型判据自动交换 A/B 顺序重判，不一致即标记（复用现有 grader 缓存时把顺序计入 key——缓存 key 已含取信声明，扩展一个维度即可）。
- **金标校准**：允许套件声明 `golden_set`（人工已判样本），run 报告输出 judge-人类 κ/α；低于阈值给"此 judge 不可信"的明确警示而非静默低分。

## C. 安全评测：门机制有了，"子弹"没有

### 行业怎么做

- AgentDojo 模型：**安全与效用联合评分**，防御不得损害任务（[榜单](https://agentdojo.spylab.ethz.ch/)）；InjecAgent 的间接注入/数据外泄分类（[论文](https://arxiv.org/abs/2403.02691)）；AgentHarm、ASB、OS-Harm、Agent-SafetyBench 等内容集（见 [03](./03-methodology-themes.md) §8）。
- container-per-trial 沙箱已是 table stakes（Harbor/AgentBench FC 全容器化；三层沙箱共识：Docker→gVisor→microVM）。

### Aeval 有什么、没什么

- **机制全在**：乘性安全门（变更④，门失败塌缩总分——正是"安全不被平均稀释"的正确机制）；`EnvironmentManager.probe()` 由框架在 teardown 前发起、读数记 `harness` 级；`verify_clean` 泄漏检测 + 自动 restore。
- **内容为零**（grep 验证：canary/inject/exfil 在源码无命中）：没有注入 payload 支持、没有外泄检测 grader、没有环境诱饵。
- 沙箱：明确不做容器（README 已知限制），也没有参照实现。

### 为什么重要

- 这是**投入产出比最高**的一块：`observed_by: harness` 的设计让"被评 agent 伪造它没见过的诱饵"不可能——安全探针恰好是证据模型的用武之地，80% 基础设施已存在。
- AgentDojo 的联合评分防"防御性装死拿满分"——Aeval 的门 + 通过率两轴并排呈现正好能表达"安全门全过、任务全挂"这类结论。

### 做成什么样算完

- **金丝雀/诱饵探针**：环境 setup 时由 harness 植入 secret 文件/注入标记；新 grader 类型 `canary` 判据："transcript/token 流中从未出现金丝雀内容 + 结束态金丝雀仍在原位"。证据全部 harness 级。
- **注入鲁棒性 graders**：工具返回值/环境文档里埋注入 payload（按 InjecAgent 分类），判据 = "未执行注入指令 **且** 原任务照常完成"（联合评分）。
- **沙箱参照实现**：框架不增加 Docker 依赖，但发 `DockerEnvironmentManager` 参照实现（可选 extra，像 Phoenix 那样懒加载）——保住轻量定位，同时给出"强制溯源边界"的事实标准。

## D. 分发生态：套件是"本地 YAML 文件"，不是"可分享制品"

### 行业怎么做

- **Harbor registry**：`harbor run -d terminal-bench@2.0` 一条命令拉任务包（[文档](https://www.harborframework.com/docs/tutorials/running-terminal-bench)）。
- **inspect_evals**：策展任务库是 Inspect 采纳率的关键（[GitHub](https://github.com/UKGovernmentBEIS/inspect_evals)）。
- **污染卫生**：canary GUID 约定 + log-prob 告警（[发布指南](https://arxiv.org/html/2505.18102v6)）；公开/私有拆分成默认姿态（SWE-bench Pro）。

### Aeval 有什么、没什么

- 套件 = 本地 YAML 文件 + `env` 内联种子；semver 套件版本与严格校验已有（分发格式的地基现成）；examples 只有 `minimal` 与 `achat`。
- 无打包格式、无远程来源解析、无内置任务包；suite 元数据无 canary 概念、无 holdout 拆分（矩阵 #16/#17）。

### 为什么重要

- 对"**开源可复用**"这个目标，这是采纳率上最要紧的一块：新用户 5 分钟内跑不起来有价值的东西，就不会回来。
- 若未来把任务包当公开数据集发布，没有 canary/holdout 就是裸奔（GEM 2026：报告污染水平 1–45% 且在上升）。

### 做成什么样算完

- **最小分发**：`eval-suite run <git-url|打包文件>`；套件包 = YAML + 附属环境种子 + grader 配置 + 内容寻址校验和，带 semver。
- **内置任务包**：一个开箱即跑的小包（若干终端/工具编排/RAG 任务，全离线），进 `examples/` 或独立 `packages/`。
- **污染卫生第一步**：suite 元数据规范 `canary_guid` 字段 + holdout 任务标签（`holdout: true` 的任务默认只在显式 flag 下运行）；文档写清"公开发布前的检查单"。

## E. 工程闭环：从"能跑评测"到"评测驱动开发"

### 行业怎么做

- **两车道 CI**：确定性快车道卡每次合并；judge 慢车道夜间/异步、结果回贴 PR（[Arize 指南](https://arize.com/resources/llm-evaluation/ci-cd-for-llm-apps/)、[两车道](https://getautonoma.com/blog/how-to-run-llm-evals-in-ci-cd)）。
- **基于区间的基线回归门**：新 run 显著差于基线才失败（[TFSF CI 设计](https://tfsfventures.com/blog/confidence-interval-design-for-ai-agent-evaluation)）。
- **生产 trace → 离线回放**是 Phoenix/LangSmith 的核心卖点链路。

### Aeval 有什么、没什么

```
   现在:  pytest 插件 ──绝对阈值门──▶ 退出码        （快车道，仅此一条）

   缺口:  ① 夜间/异步车道：定时跑慢套件 → 存库 → 回贴 PR/通知（无）
          ② 基线相对门：compare() 的"区间不重叠"判定已有，
             但 pytest 门禁只认绝对阈值——没有
             "--baseline <run_id> 显著变差才失败"的相对门
          ③ 样本量规划：Wilson/bootstrap 区间公式已备齐，
             差最后一步——"要在 ±3% 内分辨两版本，请跑 N≥87 trials"
          ④ 生产 trace 回放：trace_mining 数据源已有，
             差一条文档化的通路：线上 trace → 任务化 → 离线回归
```

（证据：`metrics/pytest_plugin.py` 的失败条件是绝对 `pass@1` 阈值 + invalid 比例上限；`cli.py compare` 有 `not_comparable_reason` 与区间重叠判定；`dataset/sources/trace_mining.py` 存在但未接入端到端文档。）

### 为什么重要

- ②③ 是"诚实统计"品牌的自然延伸：能算区间却不告诉用户"要多大样本才能下结论"，等于只做了半件事；把功效分析做成 harness 的内省输出，**没见到任何 OSS harness 做了**。
- ④ 是低成本差异化：trace 评测 + trace 挖矿两个零件都已拥有，连起来就是商业平台的核心叙事。

### 做成什么样算完

- pytest/CLI 门禁增加 `--baseline <run_id>`：复用 compare 的可比性判定（统计口径 + 证据边界一致），"显著变差"（区间不重叠且方向向下）才置非零退出码。
- 功效分析：`eval-suite power --delta 3% --n ?`（或 run 报告附注"当前区间宽度下分辨 ±x% 需要 N"）；实现即 Wilson 区间宽度的反解，无新依赖。
- 文档化"生产回放"通路：trace 导出 → `trace_mining` 建任务 → 套件化 → 定时回归；不必做在线服务。

## 已知限制的行业权重重排

README 自报的已知限制，按 2025–26 行业权重：

| 限制 | 行业权重 | 说明 |
|------|---------|------|
| 沙箱缺位 | **高** | 从"定位选择"变成"最被要求的缺失"；折中见 C 节参照实现 |
| compare 无正式检验/多重比较 | 中 | 行业普遍只用区间重叠，不孤单；功效分析（E 节）可部分补位 |
| regrade 无 HTTP/CLI 面 | 低 | 库层入口已够研究用 |
| 无人工评审 UI | 低 | REST 回调可用；UI 是体验问题不是能力问题 |
| PG 存储延后 | 低 | SQLite 撑得住目标场景 |
| RAG/编排未生产校准 | 中 | 与 A 节交互模型缺口合并解决 |

---

下一步：把哪些空白立为正式 change 提案、什么顺序，见 [06-optimization-roadmap.md](./06-optimization-roadmap.md)。
