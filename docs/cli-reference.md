# CLI 参考

`eval-suite` 随 `[cli]` extra 提供（`pip install "agent-eval[cli]"`）。查看帮助：

```bash
eval-suite --help
eval-suite <command> --help
```

## run — 执行套件

```bash
eval-suite run <suite.yaml> [选项]
```

| 选项 | 默认 | 说明 |
|------|------|------|
| `--trials N` | 用 suite 配置 | 覆盖每个任务的 trial 数 |
| `--concurrency N` | 1（串行） | trial 并发数 |
| `--runner NAME` | `mock` | AgentRunner；也读环境变量 `AEVAL_RUNNER` |
| `--db PATH` | `./aeval.db` | 结果 SQLite 路径；也读 `AEVAL_DB` |
| `--invalid-limit RATIO` | `0.2` | 可接受的 `invalid` trial 占比上限（`0.0`–`1.0`），超过以退出码 3 结束 |

行为：加载校验 suite → 解析 runner → 执行 → 打印汇总（统计口径版本 / pass@k 及其区间 / pass^k / 平均分与 `worst_of_n` / `valid`·`invalid`·`pending` 分母 / 失败任务清单）。

退出码（评测可信度条件**先于** agent 表现结论判定 —— 不可信的分数不参与放行）：

| 码 | 含义 |
|----|------|
| 0 | 放行：无评测侧问题且无未通过任务 |
| 1 | agent 表现：存在未通过任务（或 suite 加载失败） |
| 2 | 用法错误：runner 未知等参数问题 |
| 3 | **评测本身不可信**：`invalid` 占比超 `--invalid-limit`，或关键统计量为 `insufficient_data`（无有效 trial 进入分母） |

退出码 3 输出 `NOT PASSABLE - evaluation reliability problem (not an agent performance result)` 并逐条列出原因：它是评测配置的故障，**不是** agent 退化的结论，因此不复用退出码 1。

自定义 runner 通过 entry-point 注册（见接入指南 §2），例如：

```toml
[project.entry-points."agent_eval.runners"]
my-agent = "my_pkg.runner:create_runner"
```

## 门禁 — pytest 插件（CI 侧）

```bash
pytest --eval-suite=suite.yaml --eval-threshold=0.7 [--eval-invalid-limit=0.2]
```

常规测试循环结束后执行 suite，在 terminal summary 打印修正后的 pass@k（外推值带 `*`）、分母计数与 `pass@1` 的 95% 区间，并按**两条互相独立的失败条件**判定：

| 条件 | 输出 | 含义 |
|------|------|------|
| `pass@1` 低于 `--eval-threshold`，或有效样本为 0 | `GATE FAILED: pass@1 …` / `GATE FAILED (insufficient evidence)` | agent 表现结论（后者是证据不足，不是表现差） |
| `invalid` trial 占比 > `--eval-invalid-limit` | `GATE FAILED (evaluation-side, not agent performance)` | 评测本身不可信，须先修 grader/judge 配置 |

任一条成立都会置 `session.testsfailed`（会话退出码非 0）；suite 加载或 runner 装配失败同样判门禁失败——装配失败却静默放行是 CI 事故。判定量 `pass@1` 的分母已排除 `invalid` 与 `pending`。

## validate — 校验套件

```bash
eval-suite validate <suite.yaml>
```

只做加载校验不执行。合法输出 `VALID: <name> vX — N task(s)...`；非法输出 `INVALID:` + 具体校验错误（如 `Duplicate task IDs`），退出码 1。适合放进 CI 在运行前挡格式错误。

## list — 列出 runs / suites

```bash
eval-suite list runs [--db PATH] [--limit N]
eval-suite list suites [--db PATH]
```

- `runs`：run_id / suite / 状态 / 开始时间（默认 50 条）
- `suites`：曾执行并落库的套件（名称 / 版本 / 任务数 / 描述）

## show — 运行详情

```bash
eval-suite show <run_id> [--task TASK_ID] [--db PATH]
```

输出 run 元信息、统计口径版本、`valid / invalid / pending` 分母、pass@k（附 95% 区间，外推值带 `*`）、pass^k、平均分与 `worst_of_n`、逐任务表格（分母、pass@1 及区间、平均分、VALID/INVALID/PENDING 判定、待评数）。

- `--task <id>`：下钻单任务 —— 逐 trial 的判定（`VALID` / `INVALID` / `PENDING`）、成败、得分、耗时、错误，以及每个 grader 的评分明细（分数、通过、判定与 invalid 原因、解释）
- 无有效 trial 的 task 其 pass@1 显示 `insufficient_data`，不显示 0.0；全 pending 的 task 标 `NO-VERDICT`
- run 不存在 → 退出码 1；task 不在 run 内 → 退出码 1

## compare — A/B 对比

```bash
eval-suite compare <run_a> <run_b> [--db PATH]
```

输出（与 REST API `POST /compare` 同一语义，复用同一实现）：

- 首行声明两个 run 各自的统计口径版本；版本不同（或其一为未记录版本的历史 run）时打印 `NOT COMPARABLE` 横幅，说明以下差值不构成任何方向性结论
- 全局指标表：`Pass@k` / `Pass^k` / `Avg Score`，每行给出 A 值、B 值、差值与**显著性判定**
- 显著性规则：95% 置信区间重叠 → `not significant (95% CI overlap)`，**不判方向**；区间缺失 → `significance undetermined`；只有口径一致且区间不重叠才允许结论
- 值为 `insufficient_data` 时显示该文本，而不是 0.0000
- `Regressions:` / `Improvements:` 只收录口径可比、区间不重叠且 |delta| > 0.1 的任务；为空时注明 `(no significant difference)` 或 `(runs not comparable)`

## serve — 独立 API 服务

```bash
eval-suite serve [--host 127.0.0.1] [--port 8000]
```

以 uvicorn 启动独立 API（`create_standalone_app()`）：

- 全部评测路由挂 **`/v1`** 前缀（`/v1/suites`、`/v1/runs`、`/v1/graders`、`/v1/compare`、`/v1/datasets`、`/v1/metrics`、`/v1/health`）
- 每个响应带 `X-Aeval-Version` 头
- `GET /v1/meta` 返回版本、能力清单与**统计口径**（`statistics.version` 及四个数值默认值）；寄宿挂载形态下同一份信息经 `<prefix>/meta` 取得
- **默认仅监听本机回环地址**（对外暴露请显式 `--host 0.0.0.0` 并自行考虑访问控制）

注意：`serve` 不注入 AgentRunner，run 类操作返回 503 —— 需要真实执行时用 Python 侧 `create_standalone_app(runner=...)` 或寄宿挂载。

## 环境变量汇总

| 变量 | 作用 |
|------|------|
| `AEVAL_RUNNER` | `run` 的默认 runner 名 |
| `AEVAL_DB` | 结果 SQLite 默认路径 |
