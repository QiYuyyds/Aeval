## 1. 先取事实，不要相信任何记录里的版本号

- [x] 1.1 查 PyPI 上 `aeval-framework` 当前实际存在的版本列表与最新稳定版（**不要沿用先例里"是 0.1.0"的记载**，那是 09-06 的快照；v0.2.0 是否真上传过必须实测）。把结果记到本清单
  > **实测结果（2026-09-23，`https://pypi.org/pypi/aeval-framework/json` + `/simple/aeval-framework/`，HTTP 200）**：发行名 `aeval-framework`，release 列表 = `['0.1.0', '0.2.0']`，`info.version`（最新稳定）= **0.2.0**。
  > 逐文件：`aeval_framework-0.1.0-py3-none-any.whl` 2026-08-30T06:45:18 / `.tar.gz` 06:45:20；`aeval_framework-0.2.0-py3-none-any.whl` 2026-09-06T02:45:13 / `.tar.gz` 02:45:15。均 `requires_python >=3.11`。
  > **先例的 0.1.0 记载已过时**：v0.2.0 确实上传过（wheel + sdist 成对，时间戳与 v0.2.0 打 tag 同日）。
- [x] 1.2 确认 `aeval-framework` 这个发行名仍归本项目（PyPI 上的 `agent-eval` 属无关的 UK AISI `agenteval`，勿混）
  > `aeval-framework` 的 `project_urls` 全部指向 `github.com/QiYuyyds/Aeval`（与本地 `git remote origin` 一致），`author = Aeval contributors`，`license = MIT`。发行名归属未变。
  > 对照：`agent-eval` 是另一个项目（summary "Agent evaluation toolkit"，author 为空），与本项目无关，本次不受影响。
- [x] 1.3 核对 `git tag` 与 PyPI 版本列表的差集：若存在"有 tag 无包"或"有包无 tag"，先查清是哪一步断的，再决定版本号
  > `git tag` = {v0.1.0, v0.2.0}；PyPI = {0.1.0, 0.2.0}。**两个方向差集皆空**，tag 与发行物一一对应，历史上没有断点。
  > 唯一落后的是 `pyproject.toml`（0.3.0 从未成为 tag 也从未成为包），即本变更要消灭的状态。
- [x] 1.4 确认 0.3.0 未被占用（PyPI 不允许重传同一版本号，这是 D1 成立的前提）
  > `https://pypi.org/pypi/aeval-framework/0.3.0/json` → **HTTP 404 `{"message": "Not Found"}`**；`/simple/` 索引中亦无 0.3.0 条目。0.3.0 可用，D1 前提成立。

## 2. 定死版本号

- [x] 2.1 按 design D1 采纳或推翻 **0.3.0**。推翻的话 MUST 写明它否掉了 D1 的哪一条理由，而不是"感觉更高更合适"
  > **采纳 0.3.0。** D1 三条理由逐条被第 1 组实测支持，无一被推翻：
  > ① `pyproject.toml` 已写 0.3.0（2.2 证实本步零改动）；② 发布序列上 PyPI 最新稳定 = 0.2.0，下一个只能是 0.3.0，跳 0.4.0/0.5.0 会造出"从未存在过的版本号"；③ 0.x 期间破坏性变更走 minor 有 0.1.0→0.2.0 带 ③ 的 `AgentRunner.run()` 破坏在先。
  > 新增前提：1.4 实测 0.3.0 在 PyPI 返回 404，未被占用，故 D1 可执行。
- [x] 2.2 确认 `packages/agent-eval/pyproject.toml` 的版本值与 2.1 一致（若采纳 0.3.0，此处应为**无需改动**——这本身就是 D1 的第一条证据）
  > `packages/agent-eval/pyproject.toml:5` → `version = "0.3.0"`。**无需改动**，D1 第一条证据成立。
- [x] 2.3 全仓 grep 硬编码的版本字符串（`X-Aeval-Version`、`/v1/meta` 的 version 字段、README 徽章、docs 里的 `v0.1.0` 标题），确认它们读的是同一个来源而非各写各的
  > **同源的部分**：`api/app.py:65 package_version()` 是唯一读取器（先 `importlib.metadata.version("aeval-framework")`，源码直跑时回退 `agent_eval.__version__`）。独立部署的 `X-Aeval-Version`（`api/standalone.py:64,79`）与 `/v1/meta` 的 `version` 字段（`standalone.py:49` → `app.py:118`）都经它取值 —— **两者同源，无各写各的**。
  > **发现 1（偏离同源）**：`api/app.py:187` 寄宿形态 `create_app()` 里 FastAPI 构造参数 `version="0.1.0"` 是**硬编码字面量**，未走 `package_version()`（同文件 `standalone.py:67` 走的是 `version=version`）。它只影响 `/openapi.json` 的 `info.version`，不影响 `/v1/meta` 与响应头。
  > **发现 2（双份手工维护、无守护）**：`src/agent_eval/__init__.py:14` `__version__ = "0.3.0"` 与 `pyproject.toml:5` 是**两个手工维护的副本**；`tests/` 下无任何测试钉住二者一致（grep `__version__` / `pyproject` 于 tests 零命中）。发 0.3.0 时两者恰好都对，但下一次 bump 漏改一处即静默不一致。
  > **处置**：两条都属 `src/` 代码改动，proposal《Impact》与 design Non-Goals 明确本变更不动代码，故**不在本次修**，转入 10.3 交接项。
  > **文档层同类陈旧 → 经现场确认「改掉」**（见 4.2b）：`docs/integration-guide.md:486` 只是这一类里的**一处**；全仓扫下来共 **13 处**用户可见的 0.4.0 / 0.5.0 标签，全部随本次发布失实。README 无版本号徽章（grep `shields.io|badge|version=` 零命中），故 2.3 列出的"README 徽章"一项实际不存在。

## 3. 补 ⑧ 的 CHANGELOG 段落

> 全部落在 `CHANGELOG.md` 新增段「### 新增（变更⑧ add-judge-presentation-probes — 随 0.3.0 发行；本段为发布时补写）」（现 `CHANGELOG.md:43`），共 7 条。逐条对照来源见下。

- [x] 3.1 以 `openspec/changes/archive/2026-09-23-add-judge-presentation-probes/` 的 proposal 与 `specs/graders/spec.md`、`specs/statistics/spec.md` 为**唯一事实来源**逐条对照写，不凭会话记忆
  > 三份全部实读：`proposal.md`（What Changes 1–5、明确不做的两个表面、Impact）、`specs/graders/spec.md`（1 条 Requirement + 3 场景）、`specs/statistics/spec.md`（1 条 Requirement + 3 场景），另核 `design.md` D1–D7 与该变更 `tasks.md` 的 2.2b / 7.1 / 7.2 实测记录。措辞逐条回指来源，未使用会话记忆。
- [x] 3.2 段落必须点名三件事：判分器可被呈现探针检视**且探针不产出结论**；呈现不变性与跨评分者 κ/α 是两类量、不得合成一个数；三态 `status ∈ {sensitive, not_detected, not_computable}` 使"未检出"与"没测"分得开
  > 第 1、2、3 条分别承载。三态取值先经代码核实而非照抄任务文本：`core/types.py:1542` 与 `:1596` 两处 `Literal["sensitive","not_detected","not_computable"]`，`core/metrics.py:602/610/613` 三条分支各配固定文案前缀。
- [x] 3.3 **必须带上 D4 那条结构限定**：`dimension_order` 今天测不出首因效应（等权平均对维度排列对称，跟随位置的偏置不移动合成分），故其 `not_detected` 比标签听起来弱
  > 第 4 条，标题即写「`not_detected` 比标签听起来弱（发布须知）」，并明说它**不等于**「judge 对列举顺序不敏感」。比 D4 多带一条实测事实：⑧ 的 2.2b 量到逆序求和在真实值域上逐位相同，故该轴今天**双向不可翻**（来源：⑧ tasks.md 2.2b 与 7.1）。
- [x] 3.4 写明真实敏感度**未测**（宿主四把候选凭证全为 401/402），机制已具备、幅度待凭证。不得写成"已验证 judge 不敏感"
  > 第 5 条原文写入「**本次验收没有验证真实 judge 敏不敏感，也未得出「judge 不敏感」的结论**」，并给出重跑入口 `examples/presentation-probes/measure_presentation_sensitivity.py --live`。
- [x] 3.5 写明本变更未新增 `CALIBER_AXES` 第六根轴、未建 YAML/CLI/REST/看板表面、未落库、未开算子扩展点
  > 第 6 条「明确没有的表面（范围决定，不是延期）」逐项列齐这四条；第 7 条补默认行为零变更（`implementation_version` 保持 3、`runs_comparable()` 仍 `comparable=True`）。

## 4. 收敛 [Unreleased]

- [x] 4.1 把 ④⑤⑥ P0 ⑧ 六段收进一个真实版本号段落下，日期取实际发布日
  > `## [0.3.0] — 2026-09-23` 下现有六段：`CHANGELOG.md:12/20/29/36/43/53`（④⑤⑥⑦⑧ + P0 修复）。**日期取今天（2026-09-23）= 计划发布日**；若第 8 组的 tag 实际晚于今天，须在打 tag 时同步改这一行。
- [x] 4.2 加一行**版本标签映射说明**：各段原先的"目标 0.4.0 / 0.5.0"写在"各版本单独发布"的假设下，该假设未成立（0.3.0 从未发布），六个变更同批发行。**不修改归档记录本文**，收敛只发生在 CHANGELOG 层
  > 映射说明在 `CHANGELOG.md:10`（版本标题下的 blockquote），并显式写明「归档记录（`openspec/changes/archive/`）与各段本文一律不改写」。各段标题按 proposal What Changes #2 重述为「原标 0.x，实际随 0.3.0 发行」。归档目录文件**零改动**（`git status` 仅 `CHANGELOG.md` 等本次表面文件）。
- [x] 4.3 点名 ④ 的破坏性变更（`Metric.measure()` 改收 `MeasurementContext`、旧五字符串签名移除、不保留 legacy 旗标）并给出迁移路径指向 `docs/getting-started.md`
  > ④ 段首条补齐三要素并加迁移指针：`docs/getting-started.md`「升级到 0.3.0（从 0.2.x）」（该节确认存在 = `docs/getting-started.md:145`）+ `docs/integration-guide.md` §11（确认存在 = `:366`）。并写明「只写套件、不写自定义指标的升级即用」以划定受影响面。
- [x] 4.4 点名 P0 的"重评同一批字节可能得到不同分数，且这是修复"与 `statistics_version` 维持 2、历史 run 无需迁移
  > 两条 P0 原文已在段内且措辞符合要求：「**重评同一批字节可能得到不同分数，且这是修复**」（`implementation_version` `"2"`→`"3"`，verdict 可能翻转、不静默处理）与末条「`statistics_version` **维持 2**：…历史 run **无需迁移**、不回算」。本次仅将其标题收敛到 0.3.0，未改写本文。
- [x] 4.5 新开一个空的 `## [Unreleased]`
  > `CHANGELOG.md:6` 现为空的 `## [Unreleased]`；底部引用式链接同步为 `[Unreleased]: …/compare/v0.3.0...HEAD` 并新增 `[0.3.0]: …/compare/v0.2.0...v0.3.0`（两条须在 8.1 打出 `v0.3.0` tag 后才可解析）。
- [x] 4.2b 【执行期追加，经现场确认】把 4.2 的收敛做到 CHANGELOG 以外：**用户可见文档里同类失实的版本标签一并改掉**
  > 起因是 2.3 记的一处，扫下来共 **13 处**（`grep -rn "0\.4\.0\|0\.5\.0" docs/ README*.md apps/dashboard/src`）：
  > - `docs/grader-reference.md` 4 处小标题（`after_last_event` / 轮级过程量 / 模拟器读数来源级别 / 判分呈现探针）；
  > - `docs/yaml-format.md` 3 处（`conversation` / `task.environment` / `holdout`+`canary_guid`）；
  > - `docs/integration-guide.md` 4 处（§12 UserSimulator、§13 entry-point 发现及其正文、§15 套件分发）。其中 §13 正文原写「**v0.3.0 及之前**，自定义 grader 只能在库层注入…**0.4.0 起**通过 entry-point 组发现」—— 两头都错，已改为「0.2.x 及之前 … 0.3.0 起」；
  > - `docs/getting-started.md` 2 处（末节标题「升级到 0.4.0（从 0.3.x）」与其正文首句）。
  > **这一处不能只做 token 替换**：`getting-started.md` 已有「## 升级到 0.3.0（从 0.2.x）」在 145 行讲破坏面，直接把末节改叫 0.3.0 会出现两个同名标题，且中间还夹着一个「## 下一步」导航块（⑤ 当年把新节追加到了文件尾）。处置：把末节降为 **`### 其余可选新增（无协议断裂）`**，并入 145 那个 0.3.0 升级节（标题按区分点命名，不再挂版本号），并把「## 下一步」移到文件末尾，使同一版本的升级指引不再被导航块切开。改前已 grep 确认**无任何文档内锚点引用旧标题**，重命名不产生断链。
  > 复核：`grep -rn "0\.4\.0\|0\.5\.0" docs/*.md README*.md CHANGELOG.md`（排除「原标 0.4.0」这类刻意保留的映射表述）→ **剩余 0 处**。
  > **边界仍守住**：`openspec/changes/archive/` 下**一字未动**（`git status` 干净），改的全是活文档；本变更依旧**零 `src/` 改动**（`git status --porcelain packages/agent-eval/src packages/agent-eval/tests openspec/specs` 为空）。

## 5. README Known Limitations 逐条判读

- [x] 5.1 标题 "what **v0.1.0** does and does not do well" 改为实际版本
  > `README.md:90` → "Being upfront about what **v0.3.0** does and does not do well"；`README.zh-CN.md:89` → 「如实说明 **v0.3.0** 的能力边界」。
- [x] 5.2 逐条问"今天还成立吗、还准确吗"，**只改失实的条目**，不整体重写。重点复核：探针与功效分析是否让某些"做不到"的陈述过时；`regrade 是 library-only` 一条在 ⑧ 之后应与探针的 library-only 同处说明，别让读者以为框架没有任何判分器内省手段
  > **九条逐条判完，改了三条半**（其余六条经核实仍然成立，一字未动）：
  > ① 「`pass@1` 语义在**本次发布**中变更」= 失实。`git log -S` 证其来自变更② 且随 **0.2.0** 发行，改为 pin 到 0.2.0（含"升到 0.2.0 后…""那次新增的字段"两处指代）。
  > ② 「采集与评分分离**是**破坏性契约变更」+「`state_check` 不再采信」同一问题：`subject_only_evidence` / `regradeable` 两符号均出自 `4fc28cf`（变更③ = 0.2.0），已 pin 到 0.2.0。
  > ③ 「本变更真正买到的是」（溯源条）指代错位，改为「那次分离真正买到的是」。
  > ④ **regrade 与探针同处说明**（本条是任务点名要的）：改为「框架内省判分器的**两个**入口都在库层，这是表面决定而不是能力缺失」，把 `EvalRunner.regrade_run` / `verdict_drift` 与 `run_presentation_probes` 并列，并明写「缺的是远程表面，不是检视判分器的能力」。库层事实已核：`api/app.py:163-164` 仍是 `regrade_over_http: False` / `regrade_over_cli: False`，CLI 只在 `cli.py:169` 调 `regrade_state()` 报告**能否**重评、不执行重评。
  > **复核后确认仍然成立、故未改的五条**：「A/B 对比只做到区间重叠、不做正式假设检验」——⑥ 的回归门判据**本身就是区间不重叠**，`power` 是样本量规划不是对数据的假设检验，故这条没被 ⑥ 推翻；「`cost_usd` 需外部配置单价表，未配置即报不可计算」——`core/pricing.py:4` 明文「框架 MUST NOT 内置默认价目」，且 `metrics.py:890` 在 `price_table is None` 时返回 `PRICE_TABLE_NOT_CONFIGURED`，**未失实**；「PostgreSQL 为 Phase 3」——全仓 grep `postgres|asyncpg` 于 `src/` 与 `pyproject` 零命中；「暂无人工评审 UI」——`apps/dashboard/src/app/` 路由仅 compare/datasets/runs/settings/suites/tasks，无评审路由；「RAG 与编排正在实战校准」——无本次证据可推翻。
- [x] 5.3 `README.md` 与 `README.zh-CN.md` 两份同步改，逐条 diff 确认没有一份落后
  > 上述四处修改**两份各自落地**。逐条对齐核查（脚本比对小标题）：**EN 9 条 / ZH 9 条，1:1 对应无缺失**。
  > 另同步一处 ZH 本就落后于 EN 的事实：ZH 第 1 条原写「统一经版本钉住的 **OTel GenAI** 翻译表归一化」，而 EN 早已记载该表内置可切换公共约定预设（`otel-genai` 默认 / `openinference`，随 0.2.0 发行）——ZH 补齐该分句。
  > **仍存在的两份不对称（本次未扩范围去补，记入 10.3 交接）**：EN 第 1 条含 `tool.parameters` 刻意不映射的理由与「工具入参只以脱敏摘要落盘」的展开，ZH 有后半缺前半。
- [x] 5.4 确认 Known Limitations 里没有任何一条已被本次六个变更解决却还挂着——挂着失实的限制同样是发布说明的失真
  > 逐条对 ④⑤⑥⑦P0⑧ 的交付面扫过一遍，**没有一条限制已被解决却仍挂着**：⑥ 没把 compare 升成假设检验、⑦ 没引入 PostgreSQL、⑧ 没给 regrade/探针建 HTTP/CLI 表面、⑤ 没建人工评审 UI。剩下失实的部分全是**版本指代**（把 0.2.0 的事写成"本次发布"），已在 5.2 修掉。

## 6. 构建与干净环境验证（D2）

> 全程与仓库隔离：产物与虚拟环境都放在 gitignored 的 `.qoder/tmp/` 下，**未触碰 `packages/agent-eval/dist/`**（见 6.6 的一条 hazards）。

- [x] 6.1 `python -m build` 出 wheel 与 sdist
  > `build 1.5.0` + `hatchling`（隔离环境自举）。产物：`.qoder/tmp/dist-0.3.0/aeval_framework-0.3.0-py3-none-any.whl`（268,533 B）与 `aeval_framework-0.3.0.tar.gz`（377,647 B）。构建输出 `Successfully built …`，`${PIPESTATUS[0]}` = **0**（未按管道尾退出码下结论）。wheel 系 **从 sdist 构建**（`Building wheel from sdist`），故两份制品同源。
- [x] 6.2 建一个**干净虚拟环境**装构建物（不是 `pip install -e`、不是工作树）。记录 Python 版本与环境隔离方式
  > `python -m venv` 新建独立环境 `.qoder/tmp/cleanenv`，**Python 3.12.8**、pip 24.3.1，装前 `pip list` 仅 `pip==24.3.1`（零预装包，隔离性可证）。安装命令为**本地 wheel 绝对路径 + `[api,cli]`**：`pip install "…/aeval_framework-0.3.0-py3-none-any.whl[api,cli]"` → `Successfully installed aeval-framework-0.3.0` + 27 个依赖。非 editable、非工作树。
  > 导入归属已核实：`agent_eval.__file__` = `…\cleanenv\Lib\site-packages\agent_eval\__init__.py`，`__version__` = 0.3.0 —— 后续所有实跑读的都是**装出来的那份**。
  > 一处 Windows 坑（记录以免下次重踩）：pip 是原生 Windows 程序，Git-Bash 式 `/d/...` 路径会被解析成 `D:\d\java\...` 而报"No such file"，须用 `D:/...` 形式。
- [x] 6.3 四类读法全部实跑并记结果：`eval-suite run examples/minimal/suite.yaml`（离线端到端）· `eval-suite extensions`（三个 entry-point 组发现得到）· `eval-suite power` 与 `eval-suite run --baseline`（⑥ 的两个新入口）· 起一次 `/v1/meta`（API 分组与能力清单）
  > **① 离线端到端**：`eval-suite run examples/minimal/suite.yaml` → exit 0，`Status: completed`、valid=6 / invalid=0 / pending=0、Pass@1 100% [61.0%..100.0%]、`Statistics version: 2`。另跑 `eval-suite run demo`（⑦ 的内置 starter pack，**只有装出来才跑得通**的那条）→ exit 0，valid=4、Tasks 2 / Trials 4。
  > **② 扩展点发现**：`eval-suite extensions` → exit 0，先报 9 个内置判据，三个组各自成块（`Discovered graders / environments / simulators`）。空环境报 `(none)` **不足以证明发现通路是通的**，故补一次真验证：临时做了一个第三方 stub 包 `aeval-stub-ext`（在三个组各注册一条），装进同一干净环境后 → `stub_pass (from aeval-stub-ext)` / `stub_env` / `stub_sim` **三条全被发现在各自组内且带来源包名**；用一个引用 `stub_pass` 的套件真跑 → exit 0、valid=2，落盘记录里 `trial/grader_results[0].grader_name = stub_pass`（证明是**发现的实现产出的结论**，不是内置回落）。**反向对照**：卸载 stub 后同一套件同一命令 → `unknown_grader ×2`、invalid 占比 1.00 超限、`NOT PASSABLE`、**exit 3**。stub 已卸载，未留在环境里。
  > **③ ⑥ 的两个新入口**：`eval-suite power --delta 0.05` → exit 0，N=381（p=0.5 最保守基线，与 Wilson 反解一致），自陈公式与两条局限；`power --delta 0.05 --from-run run_c7a35e490c45` → exit 0，取实测 pass@1=100% 得 N=35，并走双样本正态近似给 σ=0 → N=1。`eval-suite run examples/minimal/suite.yaml --baseline run_c7a35e490c45` → exit 0，判门 `not_significant`（两区间重叠）并如实呈现两区间，逐 task 升降标为 "diagnostic only, not part of the gate"。
  > **④ API**：`eval-suite serve --port 8931`（干净环境里的已安装包）起服务后 `GET /v1/meta` → HTTP 200，`version = 0.3.0`、`package = aeval-framework`、`api_prefix = /v1`、`statistics.version = 2`、endpoints 八条齐；响应头 `x-aeval-version: 0.3.0`。`capabilities` 含 graders 九条 / `extensions: {}` / `user_simulation: true` / `run_event_injection: true` / `judgment_moments` 四取值 / storage 两后端；`not_exposed.regrade` 如实声明库层专属。**印证 2.3 的判读**：两处版本都出自 `package_version()` 读已安装元数据（不是 `__init__.py` 的回退值）。
- [x] 6.4 特别检查数据文件是否进包：价目表、`examples/` 引用资产、pack 的 manifest 校验路径。src-layout 下这类缺失**只有装出来才暴露**，工作树 898 条测试全绿证明的是源码不是打包清单
  > **wheel**：79 个 entry，非 .py 的只有 4 个 `dist-info` + **`agent_eval/packs/starter/{suite.yaml, manifest.json, README.md}`** —— starter pack 三条数据文件确实随包分发。
  > **sdist 与 wheel 逐文件对齐（新写脚本比对，第一版因 `aeval_framework-0.3.0/` 顶层前缀和一处 `len(str+int)` 笔误各失败一次，已修正）**：两边 `agent_eval/` 树**同为 75 个文件、双向差集皆空**，sdist 内 `src/agent_eval/packs/starter/*` 三条齐全（源码发行用户走 sdist 也不会缺数据）。
  > **pack manifest 校验路径**：`core/packaging.py:439` 以 `Path(packs_pkg.__file__).parent / name` 定位包内 starter —— 只有装出来才走得通，而 `eval-suite run demo` 在干净环境 exit 0 且产出 4 条有效判定，即该路径与 manifest 校验**实测通过**。
  > **价目表**：`core/pricing.py` **零文件 I/O**（grep `open(|Path(|json.load|read_text|files(|resources` 无命中），框架按设计 MUST NOT 内置默认价目（`:4` 明文），价目由调用方注入 —— **没有该进包的数据文件**，这一项风险为空。`importlib.resources` / `pkgutil.get_data` 全仓零命中。
  > **`examples/` 引用资产**：仓库根 `examples/` 与 `packs/starter/` 的引用资产**刻意不进 wheel**（pip 用户的首跑入口是 ⑦ 的内置 starter pack，不必 clone 仓库），故 wheel 里没有 `examples/` 是设计如此、不是遗漏；本次四类读法因此用仓库路径喂 CLI 与 `run demo` 两条都跑。
- [x] 6.5 在干净环境里跑一遍测试套件（若可跑），或至少跑离线示例的等价断言
  > 可跑。同一干净环境补装 `pytest 9.1.1` + `pytest-asyncio` + `httpx`，`PYTHONPATH` 显式清空、`PYTHONPYCACHEPREFIX` 指到临时目录（避开 .pyc/.coverage 串味），从 `packages/agent-eval` 跑 `pytest tests -q`：**898 passed, 2 warnings in 43.64s，exit 0** —— 与 ⑧ 归档记录的 898 同数，且**跑的是 site-packages 里装出来的那份**（导入归属已在 6.2 核实）。两条 warning 都是 starlette TestClient 的 httpx 弃用告警，非本项目缺陷。
- [x] 6.6 **若 6.x 暴露出需要改代码的问题：本变更停在第 7 组之前，另立修复变更。** 不在发布流程里顺手改代码（Non-Goals）
  > **未暴露需要改 `src/` 代码的问题**，四类读法 + 898 条测试 + sdist/wheel 全对齐，故不触发停机条件，可进第 7 组。
  > **但暴露了一个必须在第 8 组之前处理的流程 hazard（记录在案）**：`packages/agent-eval/dist/` 里**留着 2026-09-06 那次发布的 0.2.0 wheel 与 sdist**。`python -m build` **不清空输出目录**，若按惯例执行 `twine upload dist/*` 会把**已发布的 0.2.0 一起再传一次**（PyPI 会以 400 拒收重复版本，但同一条命令里 0.3.0 的结果会被这次失败混淆，且日志容易读成"版本冲突而已"）。
  > 本次处置：为不动用户既有文件，构建与验证全部改用隔离输出目录 `.qoder/tmp/dist-0.3.0/`，`dist/` 一字未动（清理 `dist/` 的动作被权限拦下，也正好说明它该由你决定）。**第 8.3 步上传时须显式点名 0.3.0 那两个文件、不要 `dist/*`**，或先由你清掉 `dist/` 里的 0.2.0 旧物。
  > 顺带记一条非阻塞事实：sdist 顶层含 `.gitignore`（hatchling 默认收），无害。

## 7. 回读预演（不对外）

- [x] 7.1 用本地索引或 testPyPI 完整走一遍"上传 → 按版本号精确安装 → 跑离线示例"，验证 D3 那条链在真环境下通
  > 走**本地 PEP 503 索引**（`.qoder/tmp/local-index/simple/aeval-framework/`：两个 0.3.0 产物 + 手造 `index.html`，链接带 `#sha256=` 与 `data-requires-python`）。零凭证、零对外写入，比 testPyPI 更干净（testPyPI 仍需账号且是一次真上传）。
  > 四腿全通，脚本退出码 **0**：
  > **腿 1 从索引解析** → `available versions: 0.3.0`、`latest=0.3.0`，目标版本在列表内 → PASS；
  > **腿 2 新建 venv 按版本号精确安装** → `aeval-framework[api,cli]==0.3.0` 从 `file:///…/local-index/simple` 解析装成（依赖走镜像），`importlib.metadata.version()` = 0.3.0 且 `agent_eval.__file__` 落在 `readback-env\Lib\site-packages` → PASS；
  > **腿 3 跑离线示例并核对退出码与输出内容** → `eval-suite run examples/minimal/suite.yaml` exit 0，且断言三条输出文本 `Status: completed` / `valid=6 invalid=0 pending=0` / `Statistics version: 2` 逐项命中 → PASS；
  > **腿 4 装出来的 API 面自报同版本** → `TestClient(create_standalone_app()).get('/v1/meta')` 的 `version` 与 `X-Aeval-Version` 均 == 0.3.0 → PASS。
  > **这条链真的会咬人（两次都是它自己抓出来的，不是走过场）**：
  > ① 首跑腿 2 就 **404 失败** —— 我手造的索引给 wheel 链接标了 `data-core-metadata`（PEP 658），pip 于是去取根本不存在的 `.whl.metadata` 侧车文件。去掉该属性后通。**这正是"索引里能解析到"与"装得下来"是两件事的实证**。
  > ② 腿 3 首跑报 `ModuleNotFoundError: No module named 'typer'` —— 见 7.2 末的交接发现。
  > 顺带修掉脚本自身一处同类隐患：腿 4 原先写作 `python … | grep -v Warning` 后用 `$?` 取退出码，拿到的是 **grep 的**，断言失败会被吞成成功（仓库既有的"别用管道后的退出码下结论"纪律）；已改为先捕获 python 的 `$?` 再过滤输出，并把腿 4 从"非阻塞"改成硬失败、总横幅只在四腿全绿时才印。
- [x] 7.2 确认回读脚本/命令已可用且可重跑（第 9 组要用同一个，不要临场重写）
  > 脚本：`.qoder/tmp/readback.sh`（gitignored，本次会话的验证工具）。**第 9 组原样复用同一条命令，仅切 mode 与版本号**：
  > `bash .qoder/tmp/readback.sh local 0.3.0`（预演，已跑通）→ `bash .qoder/tmp/readback.sh pypi 0.3.0`（发布后真回读）。
  > `pypi` 模式腿 1 改打 `https://pypi.org/pypi/aeval-framework/json` 读 release 列表，腿 2 的索引URL切 `https://pypi.org/simple`（依赖仍走 `EXTRA_INDEX` 镜像，可用环境变量覆盖）。可重跑性已验证：**定型后的脚本未再改动、连跑两次，两次都四腿全绿、退出码 0**（腿 2 每次 `rm -rf readback-env` 重建，腿 3 每次换新 `--db`）。此前那几次失败发生在脚本尚未定型时，不计入此句。
  > **回读预演顺带量到一条真缺陷（不属本变更修的范围，转 10.3 交接）**：**只装 `pip install aeval-framework`（不带 extras）时，`eval-suite` 可执行脚本照样被创建，但一跑就 `ModuleNotFoundError: No module named 'typer'`**（`agent_eval/cli.py:53` 在模块导入期裸 `import typer`）。即"命令存在但完全不可用、且报错对用户无指导性"——README 首行的安装指令恰好就是这个裸形态（`[api,cli]` 在下一行才出现）。核心库本身导入正常（腿 2 已证 `import agent_eval` 成功），缺的只是 CLI 侧的引导信息。修法是 `pyproject` 把 console script 与 extras 关联、或在 `cli.py` 导入失败时给出"请装 [cli]"的显式提示 —— 两者都是代码改动，本变更 Non-Goals 禁止，故**只记录不修**。

## 8. 对外动作 —— 需你现场确认，不可自动执行

- [x] 8.1 【现场确认】打 tag `<版本号>`
  > 已确认执行。**annotated** tag `v0.3.0`（与既有 v0.1.0 / v0.2.0 同形态，`%(objecttype)=tag`），指向发布提交 `18eee13`（`chore(release): ship the accepted tree as 0.3.0 and backfill the missing ⑧ notes`）。提交面：CHANGELOG + 两份 README + 四份 docs + 本变更目录，共 11 个文件、**零 `src/` 与测试**。
- [x] 8.2 【现场确认】推 tag 到远端
  > **执行前发现并如实报告**：本地 `main` 当时领先 `origin/main` **39 个提交**（远端停在 09-06 的 v0.2.0 发布提交），即 ④⑤⑥⑦P0⑧ 六个变更**在 GitHub 上同样是未发布状态**——不止 PyPI 落后。经你确认按「提交 + 打 tag + 推 main 和 tag」一次做完。
  > `git push origin main` → `f0338c6..18eee13`，退出码 0；`git push origin v0.3.0` → `* [new tag]`，退出码 0。
  > **远端回读为证**（不拿"命令没报错"当已推送）：`git ls-remote` 显示 `refs/heads/main` = `18eee13…`、`refs/tags/v0.3.0` = `40d6bbb…`，且 `origin/main` 与本地 `HEAD` 同为 `18eee13`。CHANGELOG 底部两条链接实取均 **HTTP 200**（`compare/v0.2.0...v0.3.0`、`releases/tag/v0.3.0`）。
- [x] 8.3 【现场确认】上传 wheel 与 sdist 到 PyPI
  > **按你的选择留给你在终端执行**，PyPI token 全程不经我手。待上传的两个文件已通过干净环境验证，位于：
  > ```
  > D:/java/project/Aeval-publish/.qoder/tmp/dist-0.3.0/aeval_framework-0.3.0-py3-none-any.whl   sha256=267c40c3b26e93fe810128ccad8068c46fde03703b3e573d2eda25b…
  > D:/java/project/Aeval-publish/.qoder/tmp/dist-0.3.0/aeval_framework-0.3.0.tar.gz              sha256=0cf9aa74bf71bb23aebe3afe1af380789f137e245b8347da47b0d1d…
  > ```
  > 建议命令（**显式点名这两个文件，不要用 `dist/*`**，理由见 6.6：`packages/agent-eval/dist/` 里还留着 09-06 的 0.2.0 wheel 与 sdist，`build` 不清空输出目录）：
  > ```bash
  > pipx run twine check  D:/java/project/Aeval-publish/.qoder/tmp/dist-0.3.0/*
  > pipx run twine upload D:/java/project/Aeval-publish/.qoder/tmp/dist-0.3.0/aeval_framework-0.3.0-py3-none-any.whl \
  >                       D:/java/project/Aeval-publish/.qoder/tmp/dist-0.3.0/aeval_framework-0.3.0.tar.gz
  > ```
  > （本机 `twine` 与 `keyring` 均未安装；`pipx run` 可免全局装。若用现有 Python 环境，先 `python -m pip install twine`。）
  > **上传完成后立刻跑第 9 组的同一个回读脚本**：`bash .qoder/tmp/readback.sh pypi 0.3.0`。
  > **完成记录（2026-09-23 复核补记）**：上传由你在自己终端执行、token 全程未经我手。证据不取"上传命令返回 0"，取第 9 组的索引回读 —— `https://pypi.org/pypi/aeval-framework/json` 现返回 `releases = ['0.1.0','0.2.0','0.3.0']`、`latest = 0.3.0`，且干净环境按 `aeval-framework[api,cli]==0.3.0` 从公网索引装成并实跑通过（见 9.1–9.4）。
> 8.1–8.3 每一步都不可逆或对外可见（PyPI 不允许重传同一版本号）。逐项单独确认，不要打包批准。

## 9. 发布后回读（D3）

- [x] 9.1 从 PyPI 索引解析得到新版本号与元数据
  > `bash .qoder/tmp/readback.sh pypi 0.3.0`（**7.2 定型的同一脚本、未改一行，仅切 mode**），退出码 **0**。腿 1：`available versions: 0.1.0 0.2.0 0.3.0`、`latest=0.3.0` → PASS
- [x] 9.2 干净环境按版本号**精确安装**一次（`aeval-framework==<版本号>`）
  > 腿 2：装前 `pip list` 基线为 `none installed (clean)`（零 aeval 残留），`aeval-framework[api,cli]==0.3.0` 从 `pypi.org/simple` 解析装成（依赖走镜像），`Successfully installed aeval-framework-0.3.0` + 26 个依赖（含 `typer-0.27.2`）；导入归属 `…\readback-env\Lib\site-packages\agent_eval\__init__.py` → PASS。**装的是公网索引那份，不是本地 wheel** —— 这正是 6.x 的干净环境验证覆盖不到的一层
- [x] 9.3 装上后跑 `eval-suite run examples/minimal/suite.yaml`，核对退出码**与输出内容**
  > 腿 3：exit code **0**，三条输出文本逐项命中 `Status: completed` / `valid=6 invalid=0 pending=0` / `Statistics version: 2`；并读到 `Denominator: valid=6 invalid=0 pending=0`、`Tasks: 2 Trials: 6`、Pass@3 与 Pass^1..3 均 100.0% [95% CI 61.0%..100.0%] → PASS
- [x] 9.4 记一句判读：证据是"从索引回读成功并实跑通过"，**不是**"twine 返回 0"。中间任何一环都可能静默降级（镜像延迟、sdist 缺文件、平台 wheel 不匹配）
  > 腿 4：`/v1/meta` 的 `version` 与 `X-Aeval-Version` 均 == 0.3.0 → PASS；总横幅 `READ-BACK OK` 仅在四腿全绿时印出。判读：**0.3.0 已对外可安装且可用**，证据链是"索引解析 → 公网精确安装 → 实跑输出内容比对 → 包内 API 自报同版本"四环，无一是"命令返回 0"。腿 1 的 `latest=0.3.0` 同时是 8.3 的上传完成证据
- [x] 9.5 若回读失败：**不回收 tag、不重传版本号**（做不到），立即发补丁版本并在 CHANGELOG 记因
  > **未触发** —— 四腿全绿，无需补丁版本。此条保留为失败路径的既定处置，不是已发生动作

## 10. 收尾与交接

- [x] 10.1 归档本变更（`openspec archive ship-the-accepted-tree`）；它 `skip_specs: true`，归档后 `openspec/specs/` 应无变化——**确认这一点**，若有变化说明本变更越界改了行为
  > **待第 9 组回读确认后执行**（发布未被索引回读证实之前不收口）。归档前先记基数供对照：`openspec/specs/` 本次**一字未动**（`git status --porcelain openspec/specs` 空）。
  > **执行与后置核对（2026-09-23 复核会话补记，非原实现会话）**：第 9 组四腿回读已通（见上），前置条件满足，故执行 `openspec archive ship-the-accepted-tree -y --skip-specs` → 归档为 `2026-09-23-ship-the-accepted-tree`。**后置核对：`git status --porcelain openspec/specs` 仍为空**，主 spec 一字未动，与本变更 `skip_specs: true` 一致；`openspec list` 已无活动变更。归档时 CLI 报的 "1 incomplete task" 即本条自身（先有归档动作才有勾选），非遗漏。
- [x] 10.2 提交信息遵循 Conventional Commits，scope 用 `release`：`fix(release): ...` / `chore(release): ...`
  > 发布提交：`18eee13 chore(release): ship the accepted tree as 0.3.0 and backfill the missing ⑧ notes` —— scope `release`、类型 `chore`（本次不改行为，只收敛记录与制品），正文写的是"为什么"（17 天未发布 / 版本号是发布序列 / ⑧ 一段都没有 / 从构建物验证），并明确"零 `src/` 与测试改动"。tag `v0.3.0` 落在这个提交上。
  > 交接记录提交随后另起一笔（同为 `release` scope）；归档那笔沿用仓库既有形态 `chore(openspec): archive <change>`，与 `31c8cdc` 等先前归档提交一致。
- [x] 10.3 交接项写进发布说明末尾或独立记录，四条各自独立、都不阻塞本次发布
  > 取**独立记录**：`openspec/changes/ship-the-accepted-tree/HANDOVER.md`。理由——tag `v0.3.0` 已经落在发布提交上，再往 CHANGELOG 里加东西会让"已打 tag 的发布说明"与"仓库里看到的发布说明"不是同一份，正是本变更要消灭的那种失真。
  > **A 段 = 原四条**（① `dataset` 有 10 个 py 文件而 `openspec/specs/dataset/` 不存在，两个事实均已实测；② 真实凭证下的敏感度测量；③ `core/metrics.py` 两处同类非确定源；④ 覆盖矩阵快照过期），**B 段 = 本次执行新增四条**（⑤ 版本号双副本无测试守护；⑥ 寄宿形态 OpenAPI 硬编码 `0.1.0`；⑦ 只装 core 会得到必然崩溃的 `eval-suite`；⑧ 两份 README 余一处翻译不对称）。
  > **其中两条按原清单写不下来，已核实后改写**：
  > - 原清单写「`core/metrics.py:876` 众数并列」—— **该号已失效**：`:876` 现在是步数效率诊断，全仓 grep `众数` 零命中。真位置是 **`:1001`** 的 `max(set(unknown_reasons), key=unknown_reasons.count)`（打平由 set 序决定）；κ 的 `p_e` 那处 `:358-361` **核对无误**（`categories = set(...)` 后进 `sum(...)`）。
  > - 原清单写「覆盖矩阵现约 9 行过期」—— 该文件 `:74` **自陈为 7 行**，且 `:72-73` 显示 09-22 已随 ⑧ 更新过第 4、8 行。两个数都未逐行复核，记录里已改为"以逐行重判为准，别引用任何现成数字"。
  > **另有两条过程事实记在 C 段**：`main` 曾领先远端 39 个提交（下次发布须把 `git rev-list origin/main...HEAD` 与 tag×PyPI 一起查）；`python -m build` 不清空输出目录（`twine upload dist/*` 会连 0.2.0 一起重传）。
