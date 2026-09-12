## 1. 可比性判定下沉核心层（纯移动，行为零变化）

- [ ] 1.1 把 `_build_comparison`（`api/routes/runs.py`）中的可比性前置抽为核心层纯函数（输入两个 `RunResult` → `(comparable, not_comparable_reason)`，覆盖统计口径 + 证据边界 + 环境身份 + 缺边界历史行）；API 路由改为此函数的委托调用，既有 compare 路由测试全量保留并全绿
- [ ] 1.2 该函数与 `EvidenceBoundary.compare_with` 的分工在 docstring 写清（run 级前置 vs 边界级比较），不出第二套比较逻辑

## 2. 基线比较 helper（CLI 与插件共用）

- [ ] 2.1 实现基线比较 helper：输入新 run + 基线 run，先过 1.1 的可比性判定，可比则按套件实测 `pass@1` 的 95% 区间判 `significantly_worse / not_significant / improved`，结论对象带两侧区间与 reason；不可比 → `not_comparable` + reason
- [ ] 2.2 单测：四条 verdict 路径（构造已知区间的 run 对）+ 缺实测区间（全外推/无有效样本）时的处理——外推值不参与门判定，按不可判处理并给原因
- [ ] 2.3 单测：历史 run（无证据边界记录）与今日 run 比较 → `not_comparable`，reason 点名"未记录"

## 3. CLI：`run --baseline`

- [ ] 3.1 `eval-suite run` 增加 `--baseline <run_id>`：完成后调 2.1 helper；`significantly_worse` → 退出码非零；`not_comparable` → 退出码非零 + reason；其余 → 退出码 0，输出两侧区间与"差异落在噪声内/优于基线"的如实文案
- [ ] 3.2 逐 task 升降以诊断块呈现（不参与门判定）：输出每 task 新旧 `pass@1` 与区间，标注方向与显著性
- [ ] 3.3 不传 `--baseline` 时 `run` 输出与今天逐字节一致（回归测试钉住）
- [ ] 3.4 CliRunner e2e：MockRunner 离线三路径——显著变差（构造基线存储）/ 不可比（改统计口径或边界）/ 不显著

## 4. pytest 插件：`--eval-baseline`

- [ ] 4.1 插件增加 `--eval-baseline`：套件评测后调 2.1 helper，`significantly_worse` 或 `not_comparable` → `session.testsfailed`，terminal summary 打印基线比较结论（复用 3.1 的文案）
- [ ] 4.2 与 `--eval-threshold` 并用：任一门失败即失败，输出点名触发的是哪个门
- [ ] 4.3 单测：基线门触发 / 阈值门触发 / 双门都过三条路径；未传 `--eval-baseline` 时插件行为与今天一致

## 5. 功效分析（core + CLI）

- [ ] 5.1 `core/metrics.py` 新增功效函数：Wilson 半宽反解（`--delta` 问法）与双样本正态近似（分数差异 d），均为闭式；docstring 写公式与适用条件
- [ ] 5.2 已知对照表测试：标准组合（如 p=0.5, δ=±10% 的 N；d/σ 若干比值的 n）对照手算值；反解与 `wilson_interval` 正向互验（用算出的 N 反代回区间宽度 ≤ δ）
- [ ] 5.3 `eval-suite power` 子命令：`--delta`（可配 `--p`，默认 0.5 最保守）与 `--from-run`（取实测 p 与分数 σ）两种问法；输出 N、公式、假设与"正态近似偏乐观"局限声明
- [ ] 5.4 `--from-run` 无有效样本 → 退出非零报证据不足，不以 0/1 代算；CLI 帮助文本收录 `cli-reference.md`

## 6. 生产 trace 回放通路（文档 + 离线示例）

- [ ] 6.1 `docs/` 新增"回放线上流量"小节：trace 导出 → `trace_mining` 建任务 → 套件化 → `eval-suite run`（定时回归交外部调度），写明各步骤的输入输出与边界（不做在线服务）
- [ ] 6.2 `examples/` 新增全程离线的最小回放示例（mock trace → 建任务 → 套件 → run），README 注明这是通路的演示形状
- [ ] 6.3 getting-started 或 integration-guide 交叉链接该小节

## 7. 验证门（离线）

- [ ] 7.1 `cd packages/agent-eval && PYTHONPATH=src pytest tests/ -q` 全绿 + 双范围 `ruff check` 通过
- [ ] 7.2 CLI e2e：`--baseline` 三路径 + `power` 两种问法 + 不传新参数零漂移，全部离线通过
- [ ] 7.3 `openspec validate add-baseline-gate-and-power-analysis --strict` 通过
- [ ] 7.4 docs（cli-reference / getting-started / integration-guide）与本变更行为一致；CHANGELOG Unreleased 段追加本变更条目
