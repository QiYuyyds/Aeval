# 06 · 优化方向与候选提案

> 快照日期：2026-09-07。空白依据见 [05-gap-analysis.md](./05-gap-analysis.md)。
> 本文把五块空白落成**候选 change 提案**（对齐 OpenSpec 工作流需要的信息：scope / 能力归属 / 破坏性 / 验证门），给出优先级与排期建议，并列出**反范围清单**。

## 优先级总览

```
 影响力（对"开源可复用"目标）
 ▲
 │  P1 交互模型层          P2 安全内容层
 │  (A: 用户模拟器+         (C: 金丝雀+注入
 │   事件注入+checkpoint)    graders+沙箱参照)
 │
 │            P4 套件分发      P3 rubric+swap
 │            (D: 打包+任务包  (B: 应接变更④)
 │             +污染卫生)
 │                        P5 统计内省
 │                        (E: 基线门+功效分析)
 └──────────────────────────────────────────────▶
   破坏性/结构风险（拖得越晚越贵）          实现成本（低→高）
```

排序逻辑：

1. **P1 最急**——它改套件格式与扩展点协议，与"协议演进不留双路径"的既定原则叠加，拖得越晚破坏性越大；且它决定框架能评"哪类 agent"（解题型 vs 对话/长时程型）。
2. **P2 复用率最高**——80% 基础设施（probe/verify_clean/门）已存在，补的是内容层。
3. **P3 顺承变更④**——④给了 κ/α 测量地基，缓解（swap）与结构化 rubric 是同一故事的下集；等④归档后开工。
4. **P4 是采纳率杠杆**——不依赖其他块，随时可插队；建议作为"小步快跑"与 P1 并行。
5. **P5 最便宜且独有**——功效分析没有任何 OSS harness 做了；基线门是 compare 现成能力的接线。

---

## 候选提案

### P1-A · `add-user-simulator-and-run-events`（交互模型层）

- **Why**：行业主战场是对话/双控/长时程任务（τ²-bench/GAIA2/OSWorld 2.0）；Aeval 的任务模型锁死在单轮静态 prompt。
- **What**（三件，可拆分立项）：
  1. `UserSimulator` 扩展点——多轮任务的"用户侧"协议；模拟用户读数记 `observed_by: harness`；套件任务增加 `conversation` 脚本维度（用户话术/目标/工具）。
  2. `TrialSession` 运行中钩子——`inject_event(...)`（环境事件）与 `human_message(...)`（运行中介入，对齐 Inspect Intervention）；事件流进 transcript 证据并带时刻；判定时刻枚举增加"事件后状态"值。
  3. Trial 级 checkpoint/续跑——崩溃的 run 可从最近事件边界恢复重放（先做事件边界恢复，不做全量状态序列化）。
- **能力归属**：`extension-contracts`（新协议）、`suite-format`（conversation/事件表）、`orchestration`（多轮执行循环）。
- **破坏性**：`TaskView`/`EnvironmentManager`/`TrialSession` 协议扩展；套件格式新增可选字段（向后兼容）；`AgentRunner` 契约若需感知多轮则断裂（按"不留双路径"原则升 minor 大版本 0.4.0）。
- **依赖**：无硬依赖；建议在变更④归档后开工。
- **验证门**：`pytest tests/ -q` + `ruff check`；MockRunner 双词汇参数化扩展到多轮场景；事件注入的时序确定性用离线 e2e 验证。

### P2-C · `add-safety-eval-content`（安全内容层）

- **Why**：门机制已证明"安全失败塌缩总分"的合成语义正确；行业（AgentDojo）要求安全与效用联合评分；Aeval 的 harness 探针模型使诱饵不可伪造。
- **What**：
  1. 金丝雀/诱饵探针——环境 setup 时 harness 植入 secret；新 grader `canary`：transcript/token 流无金丝雀内容 + 结束态金丝雀仍在位（全 harness 级证据）。
  2. 注入鲁棒性 graders——工具返回/环境文档埋注入 payload（InjecAgent 分类）；判据 = 未执行注入 **且** 原任务照常完成（联合评分，防"装死满分"）。
  3. `DockerEnvironmentManager` 参照实现——可选 extra 懒加载，框架核心零 Docker 依赖；作为"强制溯源边界"的事实标准。
- **能力归属**：`graders`（canary/injection 新类型）、`extension-contracts`（参照实现）、`suite-format`（安全判据声明）。
- **破坏性**：无（纯新增 grader 类型与可选 extra）。
- **依赖**：门机制（变更④）——若④未归档则把"门语义"作为前置 spec 依赖声明。
- **验证门**：已知小样本注入/外泄用例的离线 e2e；`canary` grader 对"agent 未见过诱饵"与"agent 读到但未外泄"两类场景的正反测试。

### P3-B · `add-structured-rubric-and-judge-swap`（judge 方法学）

- **Why**：偏差先缓解再测量；结构化 rubric 是开放性任务的首选格式（RaR）；与变更④的 κ/α 构成"缓解→测量→校准"完整故事。
- **What**：
  1. 结构化 rubric grader——rubric 从字符串升级为项列表（`id/描述/权重/证据要求`）；judge 逐项返回 `{pass, 引用}`；报告呈现逐项达成率；项级可声明门。
  2. swap/顺序随机化——成对比较型判据自动交换 A/B 重判，不一致即标记；grader 缓存 key 增加顺序维度（缓存 key 已含取信声明，扩展一维）。
  3. 金标校准——套件可声明 `golden_set`（人工已判样本）；run 报告输出 judge-人类 κ/α；低于阈值显式警示"此 judge 不可信"。
- **能力归属**：`graders`（rubric 结构化）、`llm-metrics`（swap/校准）、`statistics`（金标一致率口径）。
- **破坏性**：rubric 配置格式变更（旧自由文本 rubric 需迁移说明；若沿用变更①③④的"不留 legacy 旗标"原则则为 breaking，随下一个 minor 发迁移指南）。
- **依赖**：变更④（κ/α 与轨迹注入）必须已归档。
- **验证门**：κ/α 已知小样本对照表法沿用④；swap 一致性用确定性 stub judge 测试；金标一致率用构造样本验证公式。

### P4-D · `add-suite-packaging-and-hygiene`（分发生态）

- **Why**："开源可复用"的采纳率杠杆；Harbor/inspect_evals 证明"一条命令跑起来"的价值；公开发布前需要污染卫生。
- **What**：
  1. 套件包格式与远程来源——`eval-suite run <git-url|打包文件>`；包 = YAML + 环境种子 + grader 配置 + 内容寻址校验和 + semver。
  2. 内置任务包——开箱即跑的离线小包（终端/工具编排/RAG 各若干），独立 `packages/` 或 `examples/` 升级。
  3. 污染卫生——suite 元数据 `canary_guid` 字段 + `holdout` 任务标签（默认只在显式 flag 下运行）+ "公开发布前检查单"文档。
- **能力归属**：`cli`（run 来源解析）、`suite-format`（包格式/元数据）、新能力 `suite-distribution`（若词表需扩）。
- **破坏性**：无（纯新增）。
- **依赖**：无；**可与 P1 并行小步快跑**（不碰协议）。
- **验证门**：打包/解包往返一致性；git 来源解析的离线测试（本地 file:// 协议）；holdout 默认不可见的 e2e。

### P5-E · `add-baseline-gate-and-power-analysis`（统计内省）

- **Why**：诚实统计品牌的自然延伸；compare 的可比性判定与区间公式都是现成的；功效分析无任何 OSS harness 同类物。
- **What**：
  1. 基线相对回归门——pytest 插件与 CLI 增加 `--baseline <run_id>`：复用 compare 的双道可比判定（统计口径 + 证据边界），"区间不重叠且方向向下"才置非零退出码；不可比时报 `not_comparable_reason` 并失败（宁可红不可哑）。
  2. 功效分析——`eval-suite power`（或 run 报告附注）：给定期望分辨精度 δ 与当前样本，输出所需 trial 数 N（Wilson 区间宽度反解；bootstrap 分数用正态近似）。
  3. 文档化"生产 trace 回放"通路——trace 导出 → `trace_mining` 建任务 → 套件化 → 定时回归；纯文档 + 一个示例，不做在线服务。
- **能力归属**：`statistics`（功效分析口径）、`cli`（power/baseline）、`dashboard`（可选：报告页附注）。
- **破坏性**：无。
- **依赖**：无；**最便宜的快赢，可最先做**。
- **验证门**：功效公式与已知表格对照（如 n=30/δ=±10% 的标准结果）；基线门的"统计口径不一致 → 失败"分支 e2e。

---

## 建议排期

```
 变更④ (进行中 35/38)
   │
   ▼ 归档后
 P5-E 统计内省 ──── 最便宜、独立、先拿下        ┐
 P4-D 套件分发 ──── 不碰协议，与 P1 并行小步快跑 ├── 可并行
 P3-B rubric+swap ── 顺承④的 judge 故事        ┘
 P1-A 交互模型 ──── 0.4.0 旗舰，先立 spec（conversation/事件表/协议扩展）
 P2-C 安全内容 ──── 依赖④的门语义；沙箱参照实现可先出
```

**里程碑建议**：0.3.x = P5 + P4（增量、无破坏）→ 0.4.0 = P1（协议级变更，配套迁移指南）→ 0.4.x = P2 + P3（挂在 0.4.0 的新协议上）。

## 反范围清单（明确不做，防止漂移）

| 不做 | 理由 |
|------|------|
| 在线评测 / 生产监控服务 | dev-time 定位（`docs/architecture.md` §1）；生产回放用离线链路覆盖 |
| 托管榜单 / 多租户 / 认证 | 自部署定位；行业已有平台做这件事 |
| 多智能体编排框架 | 只评不编；`dispatch_mode` 保持机制层 |
| 自研 PRM / 训练过程奖励模型 | 步级评分用 grader 表达即可，模型训练超出评测框架边界 |
| 框架核心依赖 Docker | 用参照实现（可选 extra）而非核心依赖，保住轻量定位 |
| 把 LLM 评测物生成（Braintrust Loop 式）作为特性 | 可作 dataset 模块的自然延伸，但非主线；避免 scope 膨胀 |

## 与 OpenSpec 流程的衔接

每个候选提案已含 Why/What/能力归属/破坏性/依赖/验证门——即 `openspec-propose` 所需的全部输入。开工前建议：

1. 选定提案后先 `openspec-propose` 立项（本调研文档作为 proposal 的 Why 引用来源）。
2. P1-A 建议先在 `design.md` 里解决两个开放问题：
   - 模拟用户与事件流的证据该记 `observed_by: harness` 还是单开一级（当前倾向：harness，理由——它是评测侧代码的产物，与探针同级）；
   - 多轮任务下 `pass@k` 的 trial 定义是否需要改（当前倾向：一个完整对话 = 一个 trial，轮数只进过程指标）。
3. P2-C 若④尚未归档，需在 proposal 里显式声明对"门语义" spec 的依赖关系。
