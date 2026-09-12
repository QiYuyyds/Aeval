# ⑥ 统计内省：让门禁看基线、让样本量可规划、让线上 trace 能回放

> 日期：2026-09-12 ｜ 对应研究文档 [06-optimization-roadmap.md](../../../research/agent-eval-landscape/06-optimization-roadmap.md) 的 **P5-E**（快赢项，与变更⑤并行、零冲突）

## Why

框架的统计地基（无偏估计、Wilson/bootstrap 区间、双道可比判定）足以支撑两类行业已成默认的工程实践，但最后一步没有接上：

1. **门禁只认绝对阈值**。pytest 插件与 CLI 在"实测 `pass@1` 低于阈值"时失败——但 agent 评测的正确问法是**相对基线**："这次改动是否显著差于上一次可信的 run"。`compare` 已经会判"区间不重叠才给方向性结论"、已经会按"统计口径 + 证据边界"拒绝跨口径对比，但这条判定链没有接进门禁：CI 里只能对着一组拍脑袋的固定阈值挡合并。行业（两车道 CI）的模式是：快车道卡确定性检查，慢车道**基于区间的基线回归门**。
2. **能算区间却不告诉用户要多少样本**。小样本区间宽是常态，但"要在 ±3% 内分辨这两个版本、当前基线通过率 p≈0.7，请跑 N≥87 trials"这个问题，框架答不上来——区间公式全在 `core/metrics.py` 里，差一个反解。调研里没有任何 OSS harness 把功效分析做成 harness 的内省输出。
3. **生产 trace 回放是半个零件**。`dataset/sources/trace_mining.py` 能从 trace 建任务，但"线上 trace → 任务化 → 套件化 → 定时回归"这条通路没有文档化示例——商业平台（Phoenix/LangSmith）把它当核心卖点，Aeval 离它只差一条文档化的路。

时机：⑤ 的会话机制不碰 `cli.py` 门禁与统计层，本变更与它的 9.4 补验零冲突；`compare` 的可比判定与环境身份（⑤ 刚落）正是基线门需要的两道前置。

## What Changes

1. **基线相对回归门**：`eval-suite run --baseline <run_id>` 与 pytest 插件 `--eval-baseline <run_id>`。复用 compare 的双道可比判定（统计口径一致 **且** 证据边界一致，含环境身份），不可比 → 退出码非零并给 `not_comparable_reason`（**宁可红不可哑**）；可比时，套件实测 `pass@1` 的 95% 区间**不重叠且方向向下**才置失败——"显著变差"，而不是"数字变低"。逐 task 的变差/变好以诊断输出呈现，不参与门判定（门判套件级，与既有绝对阈值门同一量）。
2. **样本量规划（功效分析）**：`eval-suite power` 命令。两个问法：`--delta`（"要在 ±δ 内分辨通过率，基线 p 取多少，需要多少 trials"——Wilson 区间宽度反解）与 `--from-run <run_id>`（按某个已落盘 run 的实测 p 与分数 σ 回答"分辨两个版本的差异 d 需要 N"——分数用正态近似）。全部闭式公式，零新依赖；输出同时给出假设与局限（正态近似对小样本偏乐观等），延续"诚实统计"口径。
3. **生产 trace 回放通路（文档）**：`docs/` 新增"回放线上流量"一节 + `examples/` 一个可跑的最小示例：trace 导出 → `trace_mining` 建任务 → 套件化 → `eval-suite run`（定时回归交由外部调度，框架不引入在线服务）。不做任何运行时改动。

## Capabilities

### New Capabilities

（无——全部落进既有能力的需求扩展。）

### Modified Capabilities

- `statistics`：新增功效分析口径（样本量规划的输入/输出/假设声明）；基线门所依赖的"显著变差"判定语义（区间不重叠 + 方向）与"不可比时拒绝判定"的口径。
- `cli`：`eval-suite power` 命令需求；`run --baseline` 的门禁行为（含不可比即失败、与绝对阈值门的关系）；pytest 插件 `--eval-baseline` 同语义。

## Impact

- **代码**：`core/metrics.py`（功效函数：通过率反解 + 分数正态近似，含已知对照表测试）、`cli.py`（`power` 子命令、`run --baseline`）、`metrics/pytest_plugin.py`（`--eval-baseline`）、`core/types.py` 或 `api/routes/runs.py`（如需把 `_build_comparison` 的判定复用为可导出函数，抽到核心层避免 CLI 依赖 API 路由模块）。
- **破坏性**：无。`--baseline` 不传时行为与今天逐字节一致；`power` 是新命令。
- **版本**：0.5.0（若在变更⑤的 0.4.0 发布前落地，可并入同一次发布——发布时以实际先到者为准，statistics_version 不受影响：功效分析是报告量与门禁行为，不改分母口径）。
- **依赖**：无新增运行时依赖（闭式公式，不引 statsmodels/scipy）。
- **验证门**：`pytest tests/ -q` + `ruff check` 双范围；功效公式对照已知表格（如 n=30/±10% 的标准结果）；基线门用 MockRunner 离线 e2e（变差/变好/不可比三路径）；`examples/` 新示例全程离线可跑。
