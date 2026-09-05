# 统计修正与分母语义

> 日期：2026-09-04

## Why

Aeval 的招牌统计量此刻正在系统性地发假绿灯。`pass@k` 在 `k ≤ n` 时返回的是「n 次里至少成功一次」，与 k 无关——所以名为 `pass@1` 的数字实际是 `pass@n`，整条 k 曲线在实测区间是平的。饱和度检测（判据 `pass@1 ≥ 0.95`）与 `pytest --eval-suite` 门禁都建立在这个量之上，后果是**门禁会放过它本该拦下的构建**：一个 3 次里蒙对 1 次的 agent 会以 pass@1 = 1.0 通过 `--eval-threshold=0.7`。

同一批数字还有第二处污染源：grader 抛异常、grader 名未注册、judge 的 LLM 调用失败、JSON 解析失败（当前给每维 0.5）、以及全部 trial 处于人工 pending，全都被压成 0 分或 0 通过率。**评测侧的基建抖动因此被折算成 agent 能力下降**，反之解析失败给半分又在给能力充值。任何后续指标目录与覆盖面扩展都建在这套分母上，所以它必须先修。

## What Changes

- `pass@k` / `pass^k` 在 `k ≤ n` 区间改用有限样本无偏组合估计（`1 - C(n-c,k)/C(n,k)` 与 `C(c,k)/C(n,k)`），`pass@1` 恢复为单次成功率，且 `pass^k` 不再依赖 trial 完成顺序。
- `k > n` 的二项外推**保留**，但每个外推值 MUST 与实测值在数据与展示两层可区分，并附带所用单次成功概率的 95% 置信下界。
- 新增不确定性口径：二分类通过率附 Wilson 95% 区间；连续分数附 bootstrap 95% 区间（≥1000 次重采样）、mean、SD、`worst_of_n`；过程指标附 p50/p95。
- trial 判定引入 `valid / invalid / pending` 三态：凡因评测侧原因未能产生有效判定的 trial 归为 `invalid`，**不计入通过率的分子与分母**，且其失败原因可查询。`invalid` 与「评分不通过」是两类结论。
- 每个聚合量 MUST 报告其分母；有效样本不足时输出 `insufficient_data`（空值）而不是 `0.0`。
- trial 超时归为 `invalid` 而非能力失败（预算类终止原因的细化由后续证据对齐变更承接）。
- 一致性判定与成功判定统一到同一口径（加权分），不再一个用简单平均、一个用加权。
- 饱和度检测改基于实测 `pass@1` 且分母排除 `invalid`，样本不足的任务不参与饱和判定。
- CLI `show` / `compare` 输出、`pytest --eval-suite` 门禁、REST 汇总响应、Dashboard 图例全部对准修正后的口径；`compare` 在两侧区间重叠时明确标注「差异不显著」，不给方向性结论。
- **BREAKING（值语义，非结构）**：`pass@1` 等既有字段的 JSON 结构不变但数值含义变化；同一大版本内不升 `/v2`，改由 `/v1/meta` 能力清单声明口径版本，并在文档与 Dashboard 明示。历史已落盘 run 的汇总**不回算**，跨口径对比时标注为不可比。

## Capabilities

`openspec/specs/` 目前为空，以下能力均为首次锁定，全部使用 context 能力词表中的既有名称，不新增域层级。

### New Capabilities

- `statistics`: 通过率估计量、外推标注、不确定性与分布摘要、分母语义、饱和度与一致性口径
- `orchestration`: trial 判定三态的分类规则与原因可查询性
- `cli`: `eval-suite show / compare` 的输出口径与退出码语义
- `llm-metrics`: `pytest --eval-suite / --eval-threshold` 套件门禁所判定的量
- `rest-api`: 汇总响应的口径声明与新增字段
- `dashboard`: 指标图例与后端口径的一致性、外推与 invalid 的可见性

### Modified Capabilities

（无——词表下尚无既有 spec。）

## Impact

- 代码：`core/metrics.py`（估计量与统计函数）、`core/types.py`（`GraderResult` 判定态、`TaskSummary`/`RunSummary` 新增字段）、`core/runner.py`（三态分类、饱和度与一致性口径）、`graders/*` 与 `graders/metric.py`（失败不再折 0 分）、`metrics/pytest_plugin.py`（门禁目标量）、`api/routes/runs.py` 与 `api/routes/metrics.py`（响应字段与 `/v1/meta` 声明）、`cli.py`（输出与退出码）、`apps/dashboard/src`（图例与新增计数列）
- 测试：`tests/test_runner.py`、`tests/test_metrics.py`、`tests/test_eval_metrics_api.py`、`tests/test_cli.py`、`tests/test_builtin_graders.py`、`tests/test_eval_pytest_plugin.py`；`examples/minimal` 的期望数字需按新口径重算
- 文档：`docs/architecture.md` §4 统计语义、`docs/cli-reference.md`、`docs/getting-started.md`、`README.md` / `README.zh-CN.md` 的 Known Limitations
- 依赖：无新增外部依赖（Wilson 区间与 bootstrap 自行实现）
- 兼容性：SQLite 历史 run 保持可读；`/v1` 响应结构向后兼容
- 与后续变更的接缝：本变更定义 `invalid` 语义及其消费方式；「证据缺失」这一类 `invalid` 来源由 `align-trace-evidence` 产出。两者可并行开发，合并顺序需本变更在前
