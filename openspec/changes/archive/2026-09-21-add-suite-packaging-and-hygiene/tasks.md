## 1. 套件包格式与完整性（core/packaging.py）

- [x] 1.1 新增 `core/packaging.py`：manifest 构建（包名、suite semver、逐文件 sha256）与校验（任一文件哈希不符 → 拒绝并点名文件；manifest 缺失/非法 → 拒绝并给原因）；支持目录、`.tar.gz`、`.zip` 三种形态（标准库 `tarfile`/`zipfile`，零新依赖）
- [x] 1.2 pack 内资产引用相对 pack 根解析；引用解析逃出 pack 根 → 拒绝加载并点名该引用（防绝对路径不可移植/越界）
- [x] 1.3 单测：打包/解包往返逐字节等价；篡改一个字节被拒且点名文件；缺 manifest 被拒；压缩包顶层布局不符（嵌套 pack 根）被拒并给实际布局

## 2. 来源解析（文件 / pack / git URL）

- [x] 2.1 统一来源解析函数：含 `://`（http/https/ssh/git/file）→ git URL（`git clone --depth 1` 到临时目录后定位 `suite.yaml` 或 pack 根）；否则本地处理（单文件 → 现状；`.tar.gz`/`.zip` → 解包校验；目录 → pack 校验）；run 与 validate 共用同一实现
- [x] 2.2 git 缺失（不在 PATH）→ 报错点名"该来源形态需要 git"并列出已尝试命令，无裸 traceback；clone 失败/找不到套件 → 报错带 URL；临时目录用后即清
- [x] 2.3 单测/e2e：`file://` git URL 的离线 clone→加载 e2e；本地三种形态各一条加载路径；解析失败的报错文案断言

## 3. 任务 holdout 与套件 canary_guid（types + suite 校验）

- [x] 3.1 `EvalTask` 新增 `holdout: bool = False`（仅布尔校验，无任何字段联动）；`EvalSuite` 新增 `canary_guid: str | None`（加载期 UUID 格式校验，非法即失败并点名 `canary_guid` 字段路径）
- [x] 3.2 `canary_guid` 随 run 输出与 suite 落盘记录呈现；未声明时一切输出与今天逐字节一致（零漂移测试）
- [x] 3.3 单测：含 holdout 套件正常加载且既有校验规则不变；canary 合法接受/非法拒绝/缺省零漂移三路径

## 4. holdout 运行语义（runner + cli）

- [x] 4.1 `EvalRunner.run_suite` 增加 `include_holdout: bool = False` 关键字参数：入口过滤 holdout 任务，其余循环不变；过滤后零任务 → 报错拒绝运行并点名"过滤后无任务可运行"；跳过数**不进 RunSummary**（统计口径零变更）
- [x] 4.2 `eval-suite run` 默认排除 holdout 并打印"跳过 N 个 holdout 任务"；`--include-holdout` 放行；`validate` 对含 holdout 套件提示数量
- [x] 4.3 e2e：2 holdout + 3 普通 → 默认只跑 3 个且报告跳过数；放行后 5 个全跑且与无标记等价套件行为一致；无 holdout 套件 `run` 输出与引入前一致

## 5. 内置 starter pack 与 `run demo`

- [x] 5.1 新建 `packs/starter/`（离线可跑：确定性 code/state 判据、零 LLM 零网络零凭据、带 manifest）；打包进 wheel 作为 `agent_eval.packs.starter` package data（`pyproject.toml` 更新）
- [x] 5.2 CI 测试断言仓库 `packs/starter/` 与包内 `agent_eval.packs.starter/` 内容一致（防漂移）
- [x] 5.3 `eval-suite run demo`：解析内置 starter pack，未显式传 `--runner` 时默认接 `MockAgentRunner`（复用 `agent_eval.examples.mock_runner`）；与本地同名文件冲突时本地路径优先并提示
- [x] 5.4 e2e：`run demo` 全程离线跑通出报告；纯安装形态（不依赖仓库文件路径）可运行

## 6. 文档与发布检查单

- [x] 6.1 `docs/yaml-format.md`：`holdout` 字段与 `canary_guid` 的写法、校验规则与默认排除语义
- [x] 6.2 新增发布检查单文档（canary 生成与用途、holdout 拆分、许可证与数据来源声明、pack 发布方式），integration-guide 链接 pack 格式与 git URL 来源
- [x] 6.3 `getting-started.md`：quickstart 增加 `eval-suite run demo` 的 pip 首跑路径；`cli-reference.md` 补 `--include-holdout`/`demo`/来源形态

## 7. 验证门（离线）

- [x] 7.1 `cd packages/agent-eval && PYTHONPATH=src pytest tests/ -q` 全绿 + 双范围 `ruff check` 通过
- [x] 7.2 CLI e2e：来源四形态（单文件/目录/压缩包/file:// git URL）+ holdout 三路径 + `run demo`，全部离线通过
- [x] 7.3 零漂移：不含新字段的既有套件在 run/validate 下的输出与引入前逐字节一致（回归测试钉住）
- [x] 7.4 `openspec validate add-suite-packaging-and-hygiene --strict` 通过；CHANGELOG Unreleased 段追加本变更条目

> **归档附记（2026-09-21）**：并入主 spec 前逐条把规范文本对到了实现上，三处改准 ——
> ① pack「其引用的本地资产」被去掉：`resolve_pack_asset` / `resolve_asset` 在生产加载路径上零调用方
> （CLI 只把 `suite_path` 交给 `load_suite`），改为只声明已实现的边界属性「资产引用不得逃出 pack 根」；
> ② 压缩包形态收窄到 `.tar.gz` / `.tgz` / `.zip`（`_ARCHIVE_SUFFIXES`），其余扩展名按单文件路径处理；
> ③ starter pack 不再声称已在「仅 pip 安装、无仓库 checkout」形态下实测（现有测试只在仓库树内 chdir，
> 无构建产物断言），并把 `run demo` 强制使用内置确定性被评方这一实际行为写进规范。
> 另补一条新需求，把已实现却无人记录的六种拒绝形态钉住（清单双向不一致、semver 与 suite.yaml 不符、
> 压缩包成员越界、嵌套 pack 根、clone 内多个 `suite.yaml`、全 holdout 套件），并把 canary_guid 的
> 「未声明逐字节一致」限定到命令行输出 —— 持久化记录与 API 里该键恒在、未声明时为 null。
>
> **两个未修的完整性缺口，留作后续变更（不当作已满足写进规范）**：
> ① git URL 来源若 clone 结果里没有 manifest，会静默退回单文件路径加载，绕过完整性校验；
> ② clone 内未被 manifest 列出的 `suite.yaml` 同样不经校验。两者都属于「看起来在校验、实际没校」。
> 另有一处测试缺口：`validate` 对被篡改 pack 的端到端行为只由 `verify_pack` 的单元测试覆盖，无 CLI e2e。
