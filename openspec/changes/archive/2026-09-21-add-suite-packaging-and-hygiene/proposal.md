# ⑦ 套件分发与污染卫生：让套件成为可分享制品

> 日期：2026-09-12 ｜ 对应研究文档 [06-optimization-roadmap.md](../../../research/agent-eval-landscape/06-optimization-roadmap.md) 的 **P4-D**（采纳率杠杆，纯新增不碰协议）

## Why

对"开源可复用"这个目标，这是采纳率上最要紧的一块，依据有三：

1. **套件是本地 YAML 文件，不是制品**。`eval-suite run` 只接受本地文件路径；分享一套评测 = 传一个 YAML + 口头约定它引用了哪些资产。Harbor 的 `harbor run -d terminal-bench@2.0` 一条命令从 registry 拉任务包、inspect_evals 靠策展任务库撑起采纳率——"一条命令跑起来"已被两个头部项目验证为分发形态。
2. **pip 用户连一个能跑的套件都没有**。wheel 里随包分发的是 `agent_eval/examples/`（mock runner + 用法脚本），没有任何套件 YAML；README 的 quickstart（`eval-suite run examples/minimal/suite.yaml`）要求 clone 仓库。装完包的第一分钟里，新用户没有任何零-setup 的东西可跑。
3. **公开分发前的污染卫生缺位**。GEM 2026 综述报告基准污染水平 1–45% 且在上升；行业默认姿态是 canary 字符串（每数据集 GUID）+ 公开/私有拆分。Aeval 的 suite 元数据没有 canary 概念、任务没有 holdout 标记——一旦有人把套件当公开数据集发布，就是裸奔。

## What Changes

1. **套件包（suite pack）与来源解析**：`eval-suite run` 的来源参数从"本地 YAML 路径"扩展为三种形态——本地目录/压缩包（pack = `suite.yaml` + 引用资产 + `manifest`（文件 sha256 清单 + 包名 + semver，完整性校验失败即拒绝））、git URL（浅 clone 到临时目录后按 pack 加载，`file://` URL 支持离线测试；git 不在 PATH 时给明确报错）。`validate` 同步支持。pack 内资产引用相对 pack 根解析。
2. **内置 starter pack 随 wheel 分发**：新增 `agent_eval.packs.starter`（离线可跑的小套件：确定性 code/state 判据为主，零网络零凭据），打包进 wheel 作为 package data；`eval-suite run demo` 直接跑它——pip 用户装完即可首跑，不必 clone 仓库。仓库侧同步提供 `packs/starter/`（与包内同源，CI 验证两者一致）。
3. **污染卫生**：suite 元数据新增规范字段 `canary_guid`（UUID 格式校验，随 run 输出与证据边界落盘呈现，框架不做运行时强制——它的职责是让"发布前留 canary"成为一等习惯）；任务新增 `holdout: true` 标记——**默认不跑**（`run` 跳过并报告跳过数），`--include-holdout` 显式放行（`validate` 对含 holdout 的套件提示）；`docs/` 新增"公开发布前检查单"（canary、holdout 拆分、许可证与数据来源声明）。

## Capabilities

### New Capabilities

- `suite-distribution`：套件包格式（manifest/完整性/semver）、来源解析（目录/压缩包/git URL）、starter pack 随 wheel 分发与 `run demo`、holdout 任务的默认排除语义。

### Modified Capabilities

- `suite-format`：任务模型新增 `holdout` 标记、套件元数据新增 `canary_guid` 规范字段（校验规则与加载行为）。
- `cli`：`run`/`validate` 接受 pack 与 git URL 来源；`--include-holdout` 旗标；`run demo` 形态。

## Impact

- **代码**：新模块 `core/packaging.py`（pack 清单、校验、来源解析：本地/压缩包/git；git 经 subprocess `git clone --depth 1`，零 pip 新依赖）、`core/types.py`（任务 `holdout` 字段、套件 `canary_guid`）、`core/runner.py`（run 前按 holdout 过滤并报告）、`cli.py`（来源解析接入 run/validate、`--include-holdout`、`demo`）、`pyproject.toml`（packs 进 wheel 数据）。
- **破坏性**：无。本地单文件 YAML 路径继续按原样加载（pack 是来源的超集，不是格式变更）；不传 `--include-holdout` 且无 holdout 任务时行为与今天逐字节一致。
- **版本**：0.5.0（与变更⑥同版发布或紧随其后，视发布时点）。
- **依赖**：无新增 pip 依赖；git 为可选外部命令（仅 git URL 来源需要，缺失时报错不崩溃）。
- **验证门**：pack 打包/解包往返一致性；manifest 篡改（改一个字节）被拒绝；`file://` git URL 的离线解析 e2e；holdout 默认排除 + 显式放行的 e2e；`eval-suite run demo` 在纯 pip 安装形态下可跑（不依赖仓库文件）；双范围 ruff + `pytest tests/ -q` 全绿。
