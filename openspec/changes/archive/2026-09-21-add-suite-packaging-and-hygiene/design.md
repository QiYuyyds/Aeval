## Context

只写影响做法的事实。动机见 proposal.md — Why；行为契约见 specs/。

- `cli.py run/validate` 的来源参数是 `suite_path: str`，直接交给 `load_suite(path)`；`core/suite.py` 只认单文件 YAML。_pack 化是来源层的超集，不动套件格式本体_。
- `EvalSuite` 已有 `metadata: dict[str, Any]`（自由 JSON）；`canary_guid` 要做的是**规范字段**（格式可校验、可在 run 输出中一致呈现），不是往自由字典里塞约定。
- wheel 构建走 hatch（`packages = ["src/agent_eval"]`），`agent_eval/examples/` 已随包分发但**无任何套件 YAML**——starter pack 的分发通道是现成的（package data）。
- `agent_eval.examples.mock_runner`（MockAgentRunner / MockTraceProvider）已随 wheel 分发且全程离线——demo 的零依赖运行时零件已存在。
- 环境身份（⑤）随证据边界落盘，"同一 pack 的同一版本"与"同一环境"是同构的可比性问题——pack 的 manifest 校验和正好可以作为环境身份的输入之一，但那是可选联动，本期不做（见 Open Questions）。

## Goals / Non-Goals

**Goals:**

- pack = 可校验的分发单元：manifest + sha256 让"你跑的就是作者发的"可机器验证。
- 三类来源统一入口：本地文件（现状）/ 目录与压缩包（pack）/ git URL；解析规则一处实现，run 与 validate 共用。
- pip 用户首跑零 setup：`eval-suite run demo` 在纯安装形态下完整跑通。
- holdout 成为套件的一等标记：默认不跑、显式放行、跳过可见——为"公开/私有拆分"提供机制底座。

**Non-Goals:**

- 不做 registry / 中央索引（`harbor run -d <name>@<ver>` 那种）——git URL + 压缩包已覆盖"分享"的最小闭环，registry 等真需求另立变更。
- 不做 pack 签名（GPG/sigstore）——校验和解决"传输出错"，不解决"作者被冒充"；签名是分发成熟后的独立增量。
- 不做 holdout 的自动轮换/加密——`holdout` 只是标记 + 排除语义，保密性靠私有仓库与检查单流程，不靠框架。
- 不改套件 YAML 本体的任务/判据结构（pack 是容器，不是新格式）。

## Decisions

### D1：pack = "目录或压缩包 + 根部 manifest"，不用 wheel/自定义二进制

manifest（`manifest.json`）记录 `pack_name`、`suite_version`（取自 suite.yaml 的 semver）与包内每个文件的 sha256；压缩包支持 `.tar.gz` 与 `.zip`（Python 标准库 `tarfile`/`zipfile`，零依赖）。不选 wheel：那是 Python 包的分发格式，强行复用要引入构建链依赖；不选自定义二进制：文本可审、diff 可见对评测资产是价值不是缺陷。**目录也必须带 manifest**（"目录 = pack 形态"语义统一，免得"有没有清单"出现两套目录加载行为）；想免清单就直接指 `suite.yaml` 单文件（现状路径原样保留）。

### D2：来源形态按"是否含 scheme"区分，git 用 subprocess 浅 clone

来源串含 `://`（scheme ∈ http/https/ssh/git/file）→ git URL，`git clone --depth 1 <url> <tmpdir>` 后在 clone 内定位（`suite.yaml` 或 pack 根）；否则按本地路径处理（文件 → 现状；`.tar.gz`/`.zip` → 解包校验；目录 → pack 校验）。不选 ` dulwich` 等 pip 依赖：违背零新增运行时依赖；不选 HTTPS tarball 直下：网络来源先收敛到 git 一种（可缓存、可 pin commit 的生态位），HTTP 归档留作后续。`file://` URL 使整条链路可离线测试。临时目录用完即清；clone 失败/找不到套件时报错带 URL 与已尝试命令，git 不在 PATH 时点名"该来源形态需要 git"。

### D3：holdout 过滤放 `EvalRunner.run_suite` 入口，CLI 只传旗标

`run_suite(suite, include_holdout=False)` 在入口过滤并按既有循环执行——API/宿主/pytest 插件自动同享该语义，不出现"命令行一套、库一套"。跳过数**不进 RunSummary**（统计口径零变更、`statistics_version` 不动）：CLI 在运行前从 suite 自行计数并打印"跳过 N 个 holdout 任务"。全 holdout 套件（过滤后零任务）→ 报错拒绝运行，不产出空 run（空 run 的分母语义是 ② 特意建立的"证据不足"，不是拿来吞整份配置错误的）。不选"CLI 层过滤"：那会让 REST 与宿主挂载路径漏掉这个语义。

### D4：starter pack 判据全确定性，demo 默认 mock runner

`packs/starter/` 的任务用 `code` / `state_check` 等确定性判据（contains/state 断言），零 LLM、零网络、零凭据；`eval-suite run demo` 在未显式传 `--runner` 时默认接 `MockAgentRunner`（复用 `agent_eval.examples.mock_runner`）——demo 的目标是"装完 30 秒看到完整报告"，不是评测真 agent。仓库 `packs/starter/` 与包内 `agent_eval.packs.starter/` 同源：包内是打包产物，CI 测试断言两者文件一致（防漂移，与"发布制品 = 验收代码"的既定教训同一条纪律）。

### D5：canary_guid 是规范字段但零运行时强制

`EvalSuite.canary_guid: str | None`，加载期仅校验 UUID 格式（非法即失败、点名字段路径）；run 输出与 suite 落盘记录如实呈现，**不**注入 trace、不参与判定、未声明无任何提示。理由：canary 的价值在"发布时可被检测"，运行时强制（如要求所有公开套件必须带）是分发平台的责任，框架此时只有记录义务。发布检查单文档承载"怎么用"（生成 UUID、随套件发布、供下游 log-prob 检测）。

## Risks / Trade-offs

- **git 子进程的跨平台与环境差异** → 仅 git URL 来源触达；缺失/失败均有明确报错与退出码，CI 用 `file://` 离线覆盖主路径；Windows 下 `git` 经 PATH 解析与开发环境一致。
- **manifest 只防篡改不防冒充** → 文档写明威胁模型边界（校验和 vs 签名）；发布检查单建议配套 release 流程。
- **pack 内资产引用绝对路径**（不可移植/可能指向打包者本机）→ pack 加载时发现资产解析逃出 pack 根即拒绝加载，错误点名该引用。
- **`demo` 与用户本地 `demo` 文件冲突** → spec 已定本地路径优先并提示；帮助文本写明。
- **全 holdout 套件被拒** → 报错文案点名"过滤后无任务可运行"，与"分母不为配置错误买单"的既有口径一致。

## Migration Plan

纯新增：单文件路径加载、无 holdout 套件的运行、未声明 `canary_guid` 的套件，三条现状路径全部行为不变（各有一条零漂移测试钉住）。回滚按组 revert；无数据迁移（manifest/holdout/canary 都不进历史兼容面——老套件没有这些字段，读回即为缺省）。

## Open Questions

- manifest 校验和是否要参与 ⑤ 的环境身份（同一 pack 内容 = 同一环境证据）？倾向**本期不做**：pack 校验解决"加载完整性"，环境身份解决"结论可比性"，两个正交关切先不耦合；真有"套件内容变更导致不可比"的需求再立变更。
- 压缩包内目录布局是否允许嵌套（pack 根不在压缩包顶层）？当前倾向**不允许**（根部 `suite.yaml` + `manifest.json` 必须在顶层），错误信息给实际布局——简单、可预测，嵌套需求出现再说。
