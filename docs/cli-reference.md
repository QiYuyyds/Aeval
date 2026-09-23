# CLI 参考

`eval-suite` 随 `[cli]` extra 提供（`pip install "agent-eval[cli]"`）。查看帮助：

```bash
eval-suite --help
eval-suite <command> --help
```

## run — 执行套件

```bash
eval-suite run <来源> [选项]
```

**来源形态**（`run` 与 `validate` 共用同一套解析，spec: suite-distribution）：

| 形态 | 示例 | 行为 |
|------|------|------|
| 单文件 | `eval-suite run suite.yaml` | 现状路径，直接加载（不做 pack 校验） |
| pack 目录 | `eval-suite run packs/starter` | 按 pack 校验（`manifest.json` 逐文件 sha256），资产引用相对 pack 根解析 |
| pack 压缩包 | `eval-suite run pack.tar.gz` / `pack.zip` | 解包到临时目录、校验顶层布局与 manifest 后加载，用后即清 |
| git URL | `eval-suite run https://…/suite.git` | 浅 clone 到临时目录后定位套件（http/https/ssh/git/file；`file://` 可离线测试）。git 不在 PATH 时明确报错 |
| 内置示例 | `eval-suite run demo` | 运行随 wheel 分发的 starter pack，纯 pip 安装零 setup 首跑（与本地同名路径冲突时本地优先并提示） |

| 选项 | 默认 | 说明 |
|------|------|------|
| `--trials N` | 用 suite 配置 | 覆盖每个任务的 trial 数 |
| `--concurrency N` | 1（串行） | trial 并发数 |
| `--runner NAME` | `mock` | AgentRunner；也读环境变量 `AEVAL_RUNNER` |
| `--vocabulary NAME` | `otel-genai` | trace 属性词汇预设（可选值见接入指南 §3）；也读 `AEVAL_TRACE_VOCABULARY`，写错在装配期报错 |
| `--db PATH` | `./aeval.db` | 结果 SQLite 路径；也读 `AEVAL_DB` |
| `--invalid-limit RATIO` | `0.2` | 可接受的 `invalid` trial 占比上限（`0.0`–`1.0`），超过以退出码 3 结束 |
| `--baseline RUN_ID` | 不启用 | 基线相对回归门：run 完成后与同库中该 run 比较（语义见下文「基线门」） |
| `--include-holdout` | 关闭 | 放行 `holdout: true` 的私有保留集任务；默认排除并在开始前打印「跳过 N 个 holdout 任务」 |

行为：加载校验 suite → 装配 trace 映射与 runner → 执行 → 打印汇总（统计口径版本 / pass@k 及其区间 / pass^k / 平均分与 `worst_of_n` / `valid`·`invalid`·`pending` 分母 / 失败任务清单）。开头回显 `Trace vocabulary: NAME  spec=…  mapping=…`，即这一轮数字是按哪套埋点约定读出来的；pack 来源回显 `Source: pack '<name>' (manifest 校验通过, N 个文件)`；套件声明了 `canary_guid` 时汇总含 `Canary GUID: …` 行（未声明无此行，输出与引入前一致）。

退出码（评测可信度条件**先于** agent 表现结论判定 —— 不可信的分数不参与放行）：

| 码 | 含义 |
|----|------|
| 0 | 放行：无评测侧问题且无未通过任务 |
| 1 | agent 表现：存在未通过任务，或基线门判显著变差 / 不可比 / 不可判（见下文「基线门」） |
| 2 | 用法错误：runner 或 `--vocabulary` 未知等参数问题 |
| 3 | **评测本身不可信**：`invalid` 占比超 `--invalid-limit`，或关键统计量为 `insufficient_data`（无有效 trial 进入分母） |
| 4 | **命令行依赖缺失**：装的是不含 `[cli]` 的形态，与评测结果无关 |

退出码 3 输出 `NOT PASSABLE - evaluation reliability problem (not an agent performance result)` 并逐条列出原因：它是评测配置的故障，**不是** agent 退化的结论，因此不复用退出码 1。

退出码 4 存在的原因很实在：`eval-suite` 这个可执行文件由**每一种**安装形态生成（PEP 621 的 `[project.scripts]` 是项目级表，无法声明成「装了某个 extra 才有」），所以只装 core 的人手上也有这条命令。它不会抛指向内部依赖的 traceback，而是先输出稳定标记 `error: missing-cli-dependency` 再给出可复制的安装表达式（`pip install "aeval-framework[cli]"`）。脚本要区分「评测跑失败了」与「装错形态了」，认 4 或认这一行即可。

自定义 runner 通过 entry-point 注册（见接入指南 §2），例如：

```toml
[project.entry-points."agent_eval.runners"]
my-agent = "my_pkg.runner:create_runner"
```

## 基线门 — `run --baseline` 与 pytest `--eval-baseline`

基线门是**相对回归门**：本次结果与一个显式指定的基线 run 比较，判定复用 `compare` 的同一套语义（与 REST `POST /compare`、CLI `compare` 同源）。

```bash
eval-suite run <suite.yaml> --baseline <run_id> [--db PATH]
pytest --eval-suite=suite.yaml --eval-baseline=<run_id>
```

判定对象是**套件实测 `pass@1` 的 95% 区间**（与绝对阈值门同一个量）：

| 结论 | 条件 | `run --baseline` 退出码 |
|------|------|------|
| 显著变差 | 两区间不重叠且新值更低 | 非 0（1） |
| 不显著 | 两区间重叠——差异落在噪声内，**不等于没有变化** | 0 |
| 优于基线 | 两区间不重叠且新值更高 | 0 |
| 不可比 | 统计口径版本或证据边界（含环境身份）不同，或一侧为缺记录的历史 run | 非 0（1）+ `not_comparable_reason` |
| 不可判 | 一侧 `pass@1` 无实测区间（无有效样本 / 外推值） | 非 0（1）+ 原因 |

不可比与不可判都以非零退出码显形（**宁可红不可哑**）：没法比较的基线门不许静默放行。输出包含两侧 `pass@1` 与 95% 区间、逐 task 的新旧对照（**诊断块**——标注方向与显著性，不参与门判定；门判套件级）。基线是显式声明：建议取同口径、同证据边界的新鲜 run，太老的基线会持续报不可比（有意行为）。`eval-suite power` 回答"要多少样本才够"。

pytest 插件同语义：`--eval-baseline` 指向评测 runner storage 中的基线 run，显著变差 / 不可比 / 不可判 / 基线缺失都置 `session.testsfailed`；terminal summary 独立成块打印基线比较结论并点名 `BASELINE GATE FAILED/PASSED`。可与 `--eval-threshold` 并用——任一门失败即失败，输出区分触发的是哪个门。

## 门禁 — pytest 插件（CI 侧）

```bash
pytest --eval-suite=suite.yaml --eval-threshold=0.7 [--eval-invalid-limit=0.2]
```

常规测试循环结束后执行 suite，在 terminal summary 打印修正后的 pass@k（外推值带 `*`）、分母计数与 `pass@1` 的 95% 区间，并按**两条互相独立的失败条件**判定：

| 条件 | 输出 | 含义 |
|------|------|------|
| `pass@1` 低于 `--eval-threshold`，或有效样本为 0 | `GATE FAILED: pass@1 …` / `GATE FAILED (insufficient evidence)` | agent 表现结论（后者是证据不足，不是表现差） |
| `invalid` trial 占比 > `--eval-invalid-limit` | `GATE FAILED (evaluation-side, not agent performance)` | 评测本身不可信，须先修 grader/judge 配置 |
| 基线门触发（`--eval-baseline`）：显著变差 / 不可比 / 不可判 / 基线缺失 | `BASELINE GATE FAILED (...)` | 与基线相比显著变差，或没法比较（宁可红不可哑） |

任一条成立都会置 `session.testsfailed`（会话退出码非 0）；suite 加载或 runner 装配失败同样判门禁失败——装配失败却静默放行是 CI 事故。判定量 `pass@1` 的分母已排除 `invalid` 与 `pending`。

## validate — 校验套件

```bash
eval-suite validate <来源>
```

只做加载校验不执行；来源形态与 `run` 完全一致（单文件 / pack 目录 / pack 压缩包 / git URL，pack 形态执行与 `run` 相同的 manifest 完整性校验）。合法输出 `VALID: <name> vX — N task(s)...`；套件含 `holdout: true` 任务时额外提示「含 N 个 holdout 任务，默认运行将排除」。非法输出 `INVALID:` + 具体校验错误（如 `Duplicate task IDs`），退出码 1。适合放进 CI 在运行前挡格式错误。

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

## power — 样本量规划（功效分析）

```bash
eval-suite power --delta 0.03 [--p 0.7] [--db PATH]
eval-suite power --delta 0.05 --from-run <run_id> [--db PATH]
```

回答"达到目标分辨率需要多少有效 trial"。两种问法：

- `--delta δ`（0 < δ < 0.5，必填）：通过率 95% 区间半宽反解。基线 `--p` 缺省取 **0.5（最保守）**——真实 p 越极端区间越窄。
- `--delta δ --from-run <run_id>`：从已落盘 run 取**实测** pass@1 作 p（Wilson 反解），并取实测分数标准差 σ 作双样本正态近似回答「分辨两版本分数差异 d = δ 需要 N」。

输出固定包含：N、所用公式（Wilson 半宽反解 / `n ≈ 2·(z_{α/2}·σ/d)²`，z=1.960）、假设与局限声明（Wilson 区间非对称、正态近似在小样本/偏态下**偏乐观**——把 N 当下限并留余量）。

退出码：0 正常；1 证据不足（`--from-run` 指向的 run 无有效 trial 时报 `insufficient evidence`，**不以 0/1 代算**）或 run 不存在；2 用法错误（δ 越界）。

## serve — 独立 API 服务

```bash
eval-suite serve [--host 127.0.0.1] [--port 8000]
```

以 uvicorn 启动独立 API（`create_standalone_app()`）：

- 全部评测路由挂 **`/v1`** 前缀（`/v1/suites`、`/v1/runs`、`/v1/graders`、`/v1/compare`、`/v1/datasets`、`/v1/metrics`、`/v1/health`）
- 每个响应带 `X-Aeval-Version` 头
- `GET /v1/meta` 返回版本、能力清单、**统计口径**（`statistics.version` 及四个数值默认值）与**证据口径**（`evidence.spec_version` / `mapping_version`，以及 `evidence.vocabularies`：可选的 trace 词汇预设、默认值与各自钉住的规范修订号）；寄宿挂载形态下同一份信息经 `<prefix>/meta` 取得
- **默认仅监听本机回环地址**（对外暴露请显式 `--host 0.0.0.0` 并自行考虑访问控制）

注意：`serve` 不注入 AgentRunner，run 类操作返回 503 —— 需要真实执行时用 Python 侧 `create_standalone_app(runner=...)` 或寄宿挂载。

## 环境变量汇总

| 变量 | 作用 |
|------|------|
| `AEVAL_RUNNER` | `run` 的默认 runner 名 |
| `AEVAL_TRACE_VOCABULARY` | `run` 的默认 trace 属性词汇预设 |
| `AEVAL_DB` | 结果 SQLite 默认路径 |
