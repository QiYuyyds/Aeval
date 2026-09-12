# cli 变更（delta）

## ADDED Requirements

### Requirement: run 与 validate 接受 pack 与 git URL 来源

`eval-suite run` 与 `eval-suite validate` 的来源参数 SHALL 按 suite-distribution 的解析规则接受本地 `suite.yaml` 文件、本地目录/压缩包（pack）与 git URL；`validate` 对 pack 来源执行与 `run` 相同的 manifest 校验。解析失败（文件不存在 / 校验不过 / clone 失败 / 找不到套件）SHALL 给含来源形态与具体原因的报错，退出码非零。

#### Scenario: validate 对 pack 执行完整性校验

- **WHEN** `eval-suite validate <被篡改的 pack>`
- **THEN** 以非零退出码失败，错误点名被改动的文件

#### Scenario: git URL 来源可运行

- **WHEN** `eval-suite run <git-url>` 且环境中 git 可用
- **THEN** 浅 clone、定位套件、校验（若为 pack）并运行；临时目录运行后清理

### Requirement: holdout 默认排除与 include-holdout 旗标

`eval-suite run` SHALL 默认排除 holdout 任务并报告跳过数；`--include-holdout` 放行后照常运行（suite-distribution 的排除语义）。`validate` SHALL 对含 holdout 任务的套件在输出中提示"含 N 个 holdout 任务，默认运行将排除"。

#### Scenario: run 报告跳过数

- **WHEN** 运行含 holdout 任务的套件
- **THEN** 输出含"跳过 N 个 holdout 任务"，汇总只含非 holdout 任务

#### Scenario: validate 提示 holdout 存在

- **WHEN** `eval-suite validate` 指向含 holdout 任务的套件
- **THEN** 校验通过且输出含 holdout 数量提示

### Requirement: run demo 零 setup 首跑

`eval-suite run demo` SHALL 解析内置 starter pack 并完整运行（suite-distribution 的内置分发语义）；`demo` 不是文件路径，与同名本地文件冲突时本地路径优先并给出提示。

#### Scenario: pip 安装形态下首跑

- **WHEN** 仅 pip 安装（无仓库 checkout）执行 `eval-suite run demo`
- **THEN** 内置套件完整运行并输出报告，全程离线
