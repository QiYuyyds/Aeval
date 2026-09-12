## Context

只写影响做法的事实。动机见 proposal.md — Why；行为契约见 specs/。

- `compare` 的判定链已经存在：`EvidenceBoundary.compare_with`（`core/types.py`，统计口径 + 证据边界 + ⑤ 的环境身份）返回 `(comparable, reason)`；`_build_comparison`（`api/routes/runs.py`）组装呈现。**但判定函数住在 API 路由模块里**——CLI 与 pytest 插件依赖 API 模块会把装配方向搞反。
- 门禁的两个入口：`cli.py run`（退出码）与 `metrics/pytest_plugin.py`（`session.testsfailed` + terminal summary）。pytest 插件已有绝对阈值门（实测 `pass@1` 低于阈值 / `invalid` 占比超限），有成熟的 run 装配与汇总读取路径。
- 区间机制全在 `core/metrics.py`：`wilson_interval(p, n)`、`bootstrap_ci`（seed 可注入）。功效分析是对这些已测函数的反解与正态近似，不引入新统计量。
- `dataset/sources/trace_mining.py` 能从 trace 建任务（已测）；`examples/achat` 展示了套件 + 环境的组装形态。
- 基线门的前置天然成立：③ 之前落盘的 run 无证据边界记录、一律不可比（既有语义），因此"基线必须是 ③ 后的 run"不是新约束而是既有事实的再表述。

## Goals / Non-Goals

**Goals:**

- 把 compare 的判定语义复用为门禁语义——同一份"可比 + 显著变差"定义，CLI / pytest / API 三处同源，不长出第二套。
- 功效分析成为 harness 的内省输出：输入分辨率与基线，输出 N、公式、假设、局限——延续"每个数字自陈依据"的品牌。
- 生产 trace 回放通路文档化到"新用户一个示例就能跑"的程度。
- 不传新参数时，三个入口（run / pytest / power 不存在）行为与今天逐字节一致。

**Non-Goals:**

- 不做序列检验（跑够区间不重叠即提前停）——那是采样流程的语义变更，涉及"看多少次区间"的多重比较问题，另开变更。
- 不做 dashboard 呈现（基线门结论与功效输出本轮 CLI/pytest only；报告页可后补）。
- 不做自动基线管理（自动选"上一次绿色 run"之类）——基线是显式声明，选错基线是人的决定，框架不替人猜。
- 不做逐 task 门判定（门判套件级，与绝对阈值门同量；逐 task 升降以诊断呈现）。
- 不做在线评测/定时调度（回放通路的定时部分交外部 CI 调度）。

## Decisions

### D1：可比性判定从 API 路由抽到核心层，CLI/API/插件三处同源

`_build_comparison` 里的可比性前置（statistics_version + 证据边界比较）抽为 `core` 层的纯函数（输入两个 `RunResult`，输出 `(comparable, reason)`）；API 路由与新的基线门都调它。不选"CLI 从 api.routes 导入"：装配方向反了（CLI extras 不含 fastapi）；不选"两边各写一遍"：正是 ⑤ 的 discovery 决策（D5）刚消灭过的"命令行一套、接口一套"。呈现仍留在各自入口。

### D2：显著变差 = 套件实测 `pass@1` 区间不重叠且方向向下；不可比 → 失败

门判定的量与绝对阈值门同一个（套件实测 `pass@1`），判定语义与 compare 同一条（区间不重叠才给方向）。不选"逐 task 门"：小样本下逐 task 区间极宽，逐 task 门等于永远红；套件级门与现有阈值门的读者心智一致，task 级升降进诊断输出。不选"点估计下降即失败"：那正是 ② 之前"噪声被读成退化"的老坑。**不可比时置失败**是刻意的：基线门是合并门禁，"没法比较"必须以红色显形让人修边界，静默放行会让证据边界漂移悄悄累积（宁可红不可哑，与 pytest 插件对 invalid 比例超限的既有态度一致）。

### D3：功效公式两条，全闭式，局限随输出声明

- 通过率：Wilson 95% 区间半宽 `w(p, n) ≤ δ` 对 n 求最小整数解（对 n 单调，二分或解析式均可，实现在 `core/metrics.py` 与 `wilson_interval` 同源同测）。
- 分数差异 d：双样本正态近似 `n ≈ 2·(z_{α/2}·σ/d)²`（α=0.05，σ 取实测标准差），输出注明"正态近似，小样本/偏态下偏乐观"。
- `--from-run` 从 run 的 TaskSummary 取实测 p 与分数 σ；无有效样本 → 证据不足退出非零（与"缺失 ≠ 0"同一条纪律：用 0 或 1 代算会得出荒谬的 N=0 或 N=∞）。
- 不选引入 statsmodels/scipy：三个闭式公式不值得一个重依赖，也违背"零新运行时依赖"的既定约束。

### D4：基线门实现为共享 helper，CLI 与 pytest 插件各接一根线

`--baseline <run_id>` 的语义（读基线 run → 同源可比性判定 → 区间比较 → 结论对象）实为一个可复用函数，`cli.py` 与 `pytest_plugin.py` 各自消费（一个转退出码，一个转 testsfailed）。不选"插件调 CLI 子进程"：退出码解析脆且丢结构化结论；不选"各写各的"：两处语义漂移只是时间问题。输出的结论对象带 `verdict ∈ {significantly_worse, not_significant, improved, not_comparable}` + 两侧区间 + reason，pytest 的 terminal summary 与 CLI 打印消费同一结构。

### D5：生产回放只做文档 + 离线示例，不进运行时

`docs/`（integration-guide 或独立小节）+ `examples/` 一个纯离线示例（mock trace → trace_mining 建任务 → 套件 → run）。不选把 trace 导出器做成框架特性：导出来自各家后端（Phoenix/OTLP/宿主），格式繁多，先以示例立通路，等真需求再立变更。

## Risks / Trade-offs

- **基线 run 太老导致永远不可比** → 这是有意行为（边界漂移该被看见），但文档必须写明"基线建议取同边界的新鲜 run"；`not_comparable_reason` 点名维度让人能自查。
- **小样本区间极宽 → 门永不触发**（漏报退化）→ 这是区间语义的诚实代价，不是缺陷；`eval-suite power` 正是回答"要多宽才算够"的工具，两个特性互为答案。
- **正态近似的 N 偏乐观** → 局限声明随输出落盘；用户拿 N 当下限时留余量是文档化的建议，框架不假装精确。
- **`_build_comparison` 抽取动到 API 路由** → 既有 API 测试（compare 路由）全量保留，抽取为纯移动 + 委托，行为零变化由测试钉住。

## Migration Plan

无数据迁移（不新增落盘结构；基线门读既有 run）。发布顺序：随下一次 minor 发布；`--baseline`/`--eval-baseline`/`power` 全部为 opt-in 新参数。回滚：按组 revert 即可，无兼容层负担。

## Open Questions

- 基线门结论对象是否随 run 落盘（`RunResult` 增加可选字段）供 API 查询？当前倾向**不落盘**（门是 run 之后的比较行为，落盘会让"门结论"与"run 事实"混在一个对象里），先以命令输出存在，真有 API 消费需求再立变更。
