## Context

0.3.0 已发布且经索引回读确认可装可跑（`aeval-framework[api,cli]==0.3.0`，四腿全绿）。本变更处理的是**这个已对外可安装的制品自身**的两处失真，来源是发布变更自己留下的 HANDOVER B 组第 5、6、7 条。事实与证据位置见 `proposal.md`；行为要求见 `specs/cli/spec.md` 与 `specs/rest-api/spec.md`。

约束：

- 已发布的版本号不可回收，所以修复只能走 0.3.1（见 D5）。
- 回读脚本与干净环境入口已在发布时定型（`.qoder/tmp/readback.sh`，四腿、`local`/`pypi` 两模式），本变更**复用**它而不是另写一套。
- 本变更不碰评测口径：`statistics_version` 维持 2，判分链与 `core/` 语义零改动。

## Goals / Non-Goals

**Goals:**

- 让"版本号"在一个进程的所有出口只有一个来源，且这个来源由**结构**保证而非由"这次改的时候两处都记得改"保证。
- 让裸装的人得到的是一条可行动的指引，不是一个指向内部依赖模块的 traceback。
- 把「pip 首跑」从一条从未在真实形态下验过的场景，变成一条有产物、有环境、有 run 级证据的验收。

**Non-Goals:**

- 不改 `[project.scripts]` 的**存在性**（已装 `[cli]` 的用户零影响）。
- 不动 README 的安装表达式（是否顺手改，见 Open Questions）。
- 不引入运行时依赖，不做打包后端更换。
- 不处理 HANDOVER 其余五条（各自独立，尤其 `dataset` 无 spec 那条是另一条线）。

## Decisions

### D1 版本号改为**结构性单一来源**，而不是加一条一致性测试

首选：`pyproject.toml` 声明 `dynamic = ["version"]`，由 hatchling 的 `[tool.hatch.version] path = "src/agent_eval/__init__.py"` 从模块常量读。真相源变成**一处**（`__init__.py:14`），`pyproject.toml:5` 那个手工值随之消失。

*备选*：保留两份手工值 + 加一条断言二者相等的测试。**否决，并据此修正 `proposal.md` 第 1 项** —— 测试只能在有人跑它的时候起作用，而"这次两处都对属运气"这句话本身就说明缺的是结构不是检查。加测试等于承认可以不一致、然后事后抓；改 dynamic 是让不一致**无法被写出来**。

`api/app.py:187` 那处硬编码与本决定无关，它独立地改成走 `package_version()`（同文件第 65 行已有）。

**风险与前置**：`dynamic` 版本影响构建产物元数据，必须在干净环境里从**构建物**验一次（`package_version()` 走的是已安装元数据路径，只有装出来才测得到）。发布时定型的回读脚本正好覆盖这条链。若该验法发现 hatchling 的 dynamic 与本项目现有配置冲突，退到"加测试"那条路并在本条记下冲突原因 —— 退路不是首选，但在发布后制品上动打包配置需要一条实测退路。

**实现期结果（任务 3.1-3.5）**：无冲突，**退路未启用**。构建产物两侧都解析到模块常量（wheel `METADATA: Version: 0.3.0`、sdist `PKG-INFO: Version: 0.3.0`，且 sdist 内的 `pyproject.toml` 已无静态 `version` 行 —— 即"从源码发行物再走一遍构建"也是同一来源）；干净环境装上后 `importlib.metadata.version('aeval-framework')`、`agent_eval.__version__`、`package_version()` 与接口文档 `info.version` 四处同为 `0.3.0`，且把模块常量手工改成别的值后 `package_version()` 仍报安装元数据的值，证明确实走的是元数据腿而不是回退腿。
"让不一致无法被写出来"这句也量过了：`version = "x"` 与 `dynamic = ["version"]` 并存时 hatchling 直接**构建失败**（`ValueError: Metadata field 'version' cannot be both statically defined and listed in field 'project.dynamic'`），不是留给测试去抓。

### D2 无法把 console script 绑到 extra 上，这是规范限制不是实现偷懒

PEP 621 的 `[project.scripts]` 是**项目级**表，可选依赖只增依赖、不增入口点；hatchling 遵循 PEP 621，没有"装了某个 extra 才生成这条命令"的声明方式。

因此 HANDOVER 第 7 条写的两种修法里，"把 console script 归到 cli extra（装了才存在）"**这条路不存在** —— 它当时被写成"取哪种是产品决定"，实际上只剩一条。剩下的那条是把 `typer` 的导入从模块期（`cli.py:53`）移进 `main()` 的引导路径，失败时给可行动指引。

**已实测确认无受支持写法**（实现期按任务 1.1 证伪，探针留在 `.qoder/tmp/d2-probe/`，hatchling 1.32.0 / CPython 3.12.8）：

| 试过的写法 | 实测结果 |
|---|---|
| `[project.scripts.cli]` 嵌套表（按 extra 分组） | 构建失败：`TypeError: Object reference 'cli' of field 'project.scripts' must be a string`（`metadata/core.py:1079`） |
| `[project.entry-points."console_scripts"]` | 构建失败：`ValueError: Field 'console_scripts' must be defined as 'project.scripts' instead of in the 'project.entry-points' table`（`metadata/core.py:1148`） |
| 值里写环境标记 `"pkg.cli:main; extra == 'cli'"` | **hatchling 原样接受并写进 `entry_points.txt`，pip 完全忽略**：core-only 干净环境装上后 `d2probe.exe` 照样生成，一跑仍是 `ModuleNotFoundError: No module named 'typer'` —— 与本变更要修的现象一字不差 |
| `[tool.hatch.envs.<name>.scripts]` | 不是打包面：产出的 wheel 里 `entry_points.txt` 根本不存在（那是 env 命令，不是装机入口点） |
| `dynamic = ["scripts"]` + `hatch_build.py` 自定义元数据钩子 | **钩子能生成 scripts**（这条要记，否则结论像"没试透"），但 `MetadataHookInterface.update(metadata)` 收到的只有静态项目表 —— 实测打印为 `['dynamic','name','optional-dependencies','requires-python','version']`，**没有任何安装方 extra 信息**。一个 wheel 只有一份无条件 `[console_scripts]`（`builders/wheel.py:807-828`），所以"按装机形态生成"在构建期这一层就无从谈起 |

旁证：已发布的 `aeval_framework-0.3.0-py3-none-any.whl` 里 `entry_points.txt` 只有一份、内容就两行 `[console_scripts] / eval-suite = agent_eval.cli:main`；`cli` extra 只以 `Requires-Dist: typer>=0.12.0; extra == 'cli'` 的形式存在于 METADATA。**可选依赖的作用域止于依赖解析，不触及入口点生成。**

结论：D2 成立，HANDOVER 第 7 条列的两种修法只剩 D3 这一种，"取哪种是产品决定"这句作废。

**核实所用的版本与出处**（打包判断挂在这些号上，将来 hatchling 加了该能力要能追回本节结论过期）：hatchling **1.32.0**（`D:\python\Lib\site-packages\hatchling`，本机构建期实测版本），关键源码位置 `hatchling/metadata/core.py`（`scripts` 属性 1059-1080、`entry_points` 属性 1122-1171、`console_scripts` 禁用 1142-1148）与 `hatchling/builders/wheel.py`（`construct_entry_points_file` 807-828）；规范面为 PEP 621 的 `[project.scripts]` / `[project.optional-dependencies]` 两节（前者是项目级表、后者只增依赖）。复核命令：`cd .qoder/tmp/d2-probe && python -m build --wheel --no-isolation --outdir dist .`。

### D3 指引放在导入失败点，而不是让命令消失

`main()` 内先尝试取 CLI 依赖，取不到则输出一条指明"该命令需要带命令行依赖的安装形态 + 可复制的安装表达式"的文本并以非零码退出；取到则原样继续。

*备选*：让 `[project.scripts]` 指向一个只抛指引的桩，装 `[cli]` 时由别的机制替换 —— 否决，那要在打包层做条件生成，与 D2 的限制同源且更复杂。

硬约束：**已装齐依赖时的行为必须与本变更前逐字节一致**。指引是给走错路的人的，不是给正常路径加一层包装。且正常路径不得因为引导逻辑的存在而多一次导入开销或改变启动失败时序。

**实现形态（任务 4.1 落地时确定的，比"把 import typer 移进 main()"更费一层）**：pip 生成的 console script 第一件事是 `from agent_eval.cli import main`，所以只要 `agent_eval.cli` 的**模块体**还碰 typer，崩溃就仍发生在 `main()` 被调用之前。于是实现体整体搬到了 `agent_eval/_cli_app.py`，`cli.py` 只剩"入口 + 指引"这一点代码 —— `[project.scripts]` 的字符串 `agent_eval.cli:main` 原样不动（Non-Goal 里"存在性未变"的那条），被移动的是模块内部而非入口签名。代价：`agent_eval.cli` 模块导入比先前**更便宜**（不再拉 typer），正常路径多一个模块导入（`_cli_app`），实测 `--help` 端到端 405ms vs 直调实现体 404ms，typer 在两条路径上都恰好导入一次。

### D4 「pip 首跑」的验收必须是真产物 + 真隔离环境

做法：构建产物 → 新建干净 venv（装前基线为空可证）→ 按带 CLI 依赖的形态安装 → 跑 `eval-suite run demo` → 核对退出码**与输出内容**。

⑦ 漏掉的正是这一步，且它当时**自己发现了**并写在归档记录第 49 行（"现有测试只在仓库树内 chdir"），但没有把它变成一条带证据的验收就归档了。所以本变更的这条 MUST 有 run 级证据（环境路径、安装表达式、退出码、输出比对项），否则等于把同一个洞再留一次。

**放哪跑**：需要构建物，不进默认 CI 快道，归慢道/人工道。但"人工道"正是它上次被跳过的原因，因此它是**归档前置**，不是可选项。

### D5 走 0.3.1 补丁版本

对已装 `[cli]` 的用户零影响；`api/app.py` 那处修的是对外文档里一个一直写错的字面量；打包层若采纳 D1 的 dynamic，构建产物元数据不变（同一版本值，来源变了）。都不构成破坏。

发布 0.3.1 需重走发布与回读，并沿用 HANDOVER C 组那条纪律：**`dist/` 里留着 0.2.0 的旧产物且 `build` 不清空输出目录**，上传必须显式点名文件，`twine upload dist/*` 会把旧版本一起再传一次并把真实结果混进失败噪声。

## Risks / Trade-offs

- **[D1 动打包配置，可能在构建期才暴露问题] →** 前置是"干净环境从构建物验"；退路是回落到一致性测试并记录冲突原因。代价可控：最坏情况是回到本变更想解决的问题，而不是引入新问题。
- **[把 `import typer` 移进函数会改变 CLI 的失败时序与错误文本] →** 需要一条测试钉住"装齐依赖时输出逐字节不变"，以及一条钉住"缺依赖时输出含指引且退出码非零"。前者是防回归的主闸。
- **[D2 的"无法按 extra 声明脚本"若判断有误] →** 已显式标注为待核实并配一条证伪任务。判断错的后果是修法选择面变宽（回到产品决定），不是做错事。
- **[真产物验收容易被再次跳过] →** 这是本变更最现实的失败模式，因为 ⑦ 已经跳过一次。缓解是把它写成归档前置并要求 run 级证据；无法在机制上根除，除非把它做成 CI 作业 —— 那属工具面，不在本变更范围。
- **[0.3.1 与后续 0.4.0 的内容边界] →** 本变更只含这三处修复。若实现期发现需要动判分链才能验"输出逐字节不变"，说明范围判断错了，应停下来重新划界而不是顺手扩。

## Migration Plan

1. 先证伪 D2（确认无法按 extra 声明脚本）→ 定修法。
2. `api/app.py:187` 改走 `package_version()` + 一条"接口层无第二处版本字面量"的断言测试。
3. D1 的 dynamic 版本改造 → 构建 → 干净环境验元数据路径。
4. CLI 引导路径改造 + 两条测试（正常路径零变化 / 缺依赖给指引）。
5. 真产物首跑验收（D4），取 run 级证据。
6. 0.3.1 发布 + 回读，显式点名上传文件。
7. 回滚：D1 可单独回退到手工版本值（其余两项不依赖它）；CLI 引导与 `app.py` 一行各自独立可回退。无数据迁移、无 schema 变更。

## Open Questions

- README 首行的安装表达式要不要在本变更里一并改成带 CLI 依赖的形态。它是文档改动、零风险，但**改了也不消除裸装者手上那个可执行文件**（D3 才管这个），且改了会让"首行最简形态"这个习惯继续存在。属可分开决定的小事。
- 真产物验收是否值得做成一个 CI 作业。本变更不做（属工具面），但如果它第二次被跳过，就该做。
