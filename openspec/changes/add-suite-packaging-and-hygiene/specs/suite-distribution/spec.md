# suite-distribution Specification

## Purpose

让套件成为可分发、可校验、可一键运行的制品：定义套件包（pack）的清单与完整性语义、`eval-suite run` / `validate` 的来源解析（本地目录 / 压缩包 / git URL）、随 wheel 分发的内置 starter pack，以及 holdout 任务的默认排除与显式放行——支撑"新用户装完即跑、社区套件一条命令复现"的分发形态。

## ADDED Requirements

### Requirement: 套件包以带校验和的清单定义完整性

套件包 SHALL 由 `suite.yaml`、其引用的本地资产与一份 manifest 组成；manifest 记录包名、套件 semver 与包内每个文件的 sha256。加载 pack 时校验清单：任一文件哈希不符 → 拒绝加载并点名哪个文件被改动；manifest 缺失或自身格式非法 → 同样拒绝并给原因。单文件 YAML 路径不经过 pack 校验（行为与今天一致）。

#### Scenario: 打包解包往返一致

- **WHEN** 把一个合法目录打成压缩包再经来源解析加载
- **THEN** 得到的套件与直接加载原目录逐字节等价，校验通过

#### Scenario: 篡改被拒绝

- **WHEN** pack 内任一文件被改动一个字节后再次加载
- **THEN** 加载失败，错误信息点名被改动的文件与"完整性校验失败"原因

#### Scenario: 缺清单同样拒绝

- **WHEN** 目录含 `suite.yaml` 但无 manifest，且以 pack 形态（压缩包或显式 pack 路径）加载
- **THEN** 拒绝加载并说明缺 manifest；同一目录以"单文件 YAML 路径"直指 `suite.yaml` 时仍按今天的方式加载

### Requirement: run 与 validate 解析目录、压缩包与 git URL 来源

`eval-suite run` 与 `eval-suite validate` 的来源参数 SHALL 接受三类形态并按规则解析：本地 `suite.yaml` 文件（现状）；本地目录或压缩包（按 pack 加载）；git URL（浅 clone 到临时目录，在其中定位 `suite.yaml` 或 pack 根后按上述规则加载；clone 失败或找不到套件时给含 URL 的明确报错）。资产引用相对 pack 根解析。git 可执行文件不存在时报错指明"该来源形态需要 git"，而不是抛出未解释的异常。

#### Scenario: 压缩包来源直接运行

- **WHEN** `eval-suite run <path/to/pack.tar.gz>`
- **THEN** 校验 manifest 后加载套件并正常运行，行为与目录来源一致

#### Scenario: file:// git URL 离线解析

- **WHEN** 来源为指向本地仓库的 `file://` URL
- **THEN** 浅 clone 到临时目录、定位套件并加载成功——该路径不依赖网络，可离线测试

#### Scenario: git 缺失时明确报错

- **WHEN** 来源是 git URL 而环境中没有 git 可执行文件
- **THEN** 报错说明"git URL 来源需要 git"并列出已尝试的命令，不产生 traceback

### Requirement: 内置 starter pack 随 wheel 分发并可零 setup 运行

发行包 SHALL 内置一个离线可跑的 starter pack（确定性判据为主，零网络零凭据零外部服务）；`eval-suite run demo` SHALL 直接运行它。该路径 SHALL 在"仅 pip 安装、无仓库 checkout"的形态下可用——包内不含仓库文件也不影响首跑。仓库侧保留同源的 `packs/starter/` 并有测试钉住两者一致。

#### Scenario: pip 用户首跑

- **WHEN** 用户 `pip install aeval-framework[cli]` 后执行 `eval-suite run demo`
- **THEN** 无需 clone 仓库、无需凭据，评测完整跑完并输出报告

#### Scenario: 仓库与包内同源

- **WHEN** CI 比较仓库 `packs/starter/` 与包内 starter pack
- **THEN** 两者内容一致，漂移即测试失败

### Requirement: holdout 任务默认排除、显式放行

标记为 holdout 的任务 SHALL 在 `run` 中默认被排除——跳过并报告"跳过了 N 个 holdout 任务"；`--include-holdout` 显式放行时照常运行。排除只影响执行，不影响加载与校验（套件能加载、能 validate，holdout 任务在列表中可见）。run 结果与比较行为对被排除的任务不产生任何条目——不折零、不折无效，就是不在场。

#### Scenario: 默认排除

- **WHEN** 套件含 2 个 holdout 任务、3 个普通任务，执行 `run` 不带放行旗标
- **THEN** 只有 3 个普通任务产生 trial 与汇总，输出报告"跳过 2 个 holdout 任务"

#### Scenario: 显式放行

- **WHEN** 同一套件以 `--include-holdout` 运行
- **THEN** 5 个任务全部运行，与不带 holdout 标记的等价套件行为一致

#### Scenario: 无 holdout 时零漂移

- **WHEN** 套件不含任何 holdout 任务
- **THEN** `run` 的输出与加载行为与该字段引入前逐字节一致
