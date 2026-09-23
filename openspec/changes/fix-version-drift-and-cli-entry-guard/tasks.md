## 1. 先证伪 D2，再定修法

- [x] 1.1 核实"PEP 621 / hatchling 无法按可选依赖声明 console script"这一判断：查 hatchling 当前版本的配置面与 PEP 621 规范原文，**尝试找一个受支持的写法**。找到则 D2 作废、修法回到产品选择并更新 design；找不到则在 D2 记一句"已实测确认无受支持写法"
- [x] 1.2 记录核实所用的 hatchling 版本与出处。本变更的打包判断都挂在这个版本号上，将来 hatchling 加了该能力要能追回结论过期

## 2. 版本出口同源（`rest-api`）

- [x] 2.1 `src/agent_eval/api/app.py:187` 的 `version="0.1.0"` 改为走同文件 `package_version()`（`:65` 已定义）。注意 `package_version()` 定义在本文件、被 `:118` 与 `api/standalone.py:49,64` 使用 —— 绕过它的那一处就在定义它的文件里
- [x] 2.2 对照 `api/standalone.py` 确认独立部署形态的行为未变（它本来就对，别把它改坏）
- [x] 2.3 一条测试：同一进程内接口文档版本、`/v1/meta` 的 `version`、`X-Aeval-Version` 响应头三处相等
- [x] 2.4 一条测试：挂载形态与独立形态读到的接口文档版本相同（对应 `specs/rest-api/spec.md` 第二个场景）
- [x] 2.5 一条静态断言：`api/` 下不存在写死的版本号字面量（grep 级或 AST 级，防的是"新增第二处"）。**先确认它在 2.1 之前是红的** —— 一条永远绿的守护测试比没有更糟
- [x] 2.6 全仓再 grep 一次 `"0\.[0-9]\.[0-9]"` 于 `src/`，确认除版本真相源与**历史叙述性文字**（如 `metrics/base.py:126` 的"0.3.0 起统一为…"，那是正确的过去时表述，不是版本声明）外无其它字面量

## 3. 版本号结构性单一来源（design D1）

- [x] 3.1 `pyproject.toml` 改 `dynamic = ["version"]` + `[tool.hatch.version] path = "src/agent_eval/__init__.py"`；删除 `:5` 的手工 `version`
- [x] 3.2 构建产物并核对元数据里的版本等于 `agent_eval.__version__`。**必须在装出来的那份上核**，不能只看 `pyproject` 文本 —— `package_version()` 走的是已安装元数据路径
- [x] 3.3 确认 sdist 与 wheel 两条路径都带上解析后的版本（sdist 是源码发行者走的形态）
- [x] 3.4 若 3.1/3.2 实测冲突：**启用 D1 的回退方案**（保留两份手工值 + 加断言二者相等的测试），并把冲突原因记进 design D1，不要静默换路线
- [x] 3.5 确认 `importlib.metadata` 路径与 `__version__` 回退路径给出同一个值（源码直跑 vs 安装后各测一次）

## 4. CLI 引导路径（design D3）

- [x] 4.1 `src/agent_eval/cli.py:53` 的模块期 `import typer` 移入 `main()` 的引导路径；取不到依赖时输出可行动指引（指明需带命令行依赖的安装形态 + 可复制安装表达式），退出码非零
- [x] 4.2 **一条测试钉住"装齐依赖时输出逐字节不变"** —— 这是本组的主闸，防的是为了指引给正常路径加包装。先确认它在改动前是绿的、改动后仍绿
- [x] 4.3 一条测试：缺依赖时输出含指引文本、退出码非零、且**不含**指向 `typer` 的原始 traceback
- [x] 4.4 指引文本与退出码可被脚本判别（别让成功与两种失败共用一个码）
- [x] 4.5 确认正常路径没有因为引导逻辑多出一次导入尝试的可测开销或改变启动失败时序

## 5. 真产物首跑验收（design D4 —— 归档前置，不可跳过）

- [x] 5.1 新建干净 venv，装前 `pip list` 基线为空可证（沿用发布时的做法：`none installed (clean)`）—— 四个环境全部由 `.qoder/tmp/cleanenv/Scripts/python.exe -m venv` 新建，基线逐行印出 `none installed (clean)`：`.qoder/tmp/g5-A-published-cli`（改前参照）、`g5-B-published-core`（改前缺陷现场）、`g5-C-built-cli`（改后产物）、`g5-D-built-core`（改后守卫）。脚本 `.qoder/tmp/g5-acceptance.sh`，产物 `.qoder/tmp/g5-evidence/`
- [x] 5.2 按**带命令行依赖**的形态安装构建出的 wheel（非 editable、非工作树），核 `agent_eval.__file__` 落在 `site-packages` —— C 环境：`pip install --extra-index-url https://pypi.tuna.tsinghua.edu.cn/simple "<…>/dist-0.3.1/aeval_framework-0.3.1-py3-none-any.whl[api,cli]"`；装后 `importlib.metadata.version('aeval-framework') == 0.3.1`，`agent_eval.__file__ = …\g5-C-built-cli\Lib\site-packages\agent_eval\__init__.py`（`in site-packages: True`，非工作树、非 editable）
- [x] 5.3 跑 `eval-suite run demo`，核对退出码**与输出内容**（比对项逐条列出，别只看 0）—— 退出码 **0**；比对项九条全部命中：`Source: pack 'starter' (manifest 校验通过, 2 个文件)`、`Trace vocabulary: otel-genai  spec=genai-94f432d  mapping=1`、`[1/2] echo-qa: 2/2 valid trials passed`、`[2/2] artifact-check: 2/2 valid trials passed`、`Status: completed`、`Statistics version: 2`、`Denominator: valid=4 invalid=0 pending=0`、`Pass@1/@2/^1/^2: 100.0% [95% CI 51.0%..100.0%]`、`Tasks: 2  Trials: 4`（全文 `.qoder/tmp/g5-evidence/c_demo_raw.txt`）
- [x] 5.4 与仓库树内运行同一命令的结果比对，确认一致（对应 `specs/cli/spec.md` 修改后场景的 THEN）—— 工作树侧 `PYTHONPATH=src python -m agent_eval.cli run demo` 同样退出 0；把随机量（`run_<ID>`、`Duration`）归一后与产物侧 **逐字节相同**（`demo.diff` 为空）
- [x] 5.5 再取一个**裸装 core** 的干净环境跑同一命令，确认命中 4.3 的指引而非 traceback —— 两个形态都要有产物级证据。**改后**（D，`pip install <…>aeval_framework-0.3.1-py3-none-any.whl` 不带 extras）：`eval-suite.exe` 照样生成，`run demo` 输出 `error: missing-cli-dependency` + 可复制的 `pip install "aeval-framework[cli]"`，退出码 **4**，输出内零 `Traceback`。**改前**（B，`pip install aeval-framework==0.3.0` 从 PyPI 装）：同一命令退出码 **1**、`Traceback` 1 处、`ModuleNotFoundError: No module named 'typer'`、指引 0 行 —— 两形态各一份产物级证据，缺陷与修复都量到
- [x] 5.6 记 run 级证据：环境路径、安装表达式、退出码、输出比对项。**⑦ 正是缺这一步才把同一个洞留到今天**（其归档记录第 49 行自陈"现有测试只在仓库树内 chdir"）—— 上述四条即 run 级证据，全部可由 `.qoder/tmp/g5-acceptance.sh` 重跑。另在**已安装产物**上量到七处版本出口同为 `0.3.1`（外层文档 / 内层 `/v1/openapi.json` / `/v1/meta` / `X-Aeval-Version` / 宿主挂载文档 / 寄宿 `/meta` / `package_version()`），而在**已发布的 0.3.0** 上同一把尺量出宿主挂载文档 `0.1.0`、其余五处 `0.3.0` —— rest-api 那条缺陷从此有了产物级前像（`.qoder/tmp/g5-evidence/A-outlets-before.txt`、`G-outlets.txt`）
- [x] 5.7 在 `specs/cli/spec.md` 修改后场景下确认：以工作树内 chdir 冒充验证**不构成**验收证据（本变更若重犯即自我否定）—— 本组的 pip 形态结论**只**取自 C/D 两个 `site-packages` 安装（`agent_eval.__file__` 路径逐条印出可查）；工作树那次运行只充当 5.4 的**比对基线**，不用来证明发行物可跑。两个角色在证据里分开写，未混用
- [x] 5.8 （执行期新增，组 7 要用）回读脚本 leg 4 的环境前提变了：`starlette.testclient` 现在要的是 **`httpx2`**，而 `.qoder/tmp/readback.sh:50` 装的是 `httpx` —— 首次跑 leg 4 因此报 `RuntimeError: The starlette.testclient module requires the httpx2 package`。仓库树内不受影响（全局环境两者都在）。7.3 前须把 `httpx2` 加进那一行，否则四腿里的一腿会假红

## 6. 回归与静态门

- [x] 6.1 `cd packages/agent-eval && pytest tests/ -q` 全绿 —— `919 passed in 65.59s`，**pytest 自身退出码 0**（不经管道判读）。基线为本变更前的 `898 passed`，新增 21 条即第 2 组的 8 条与第 4 组的 13 条
- [x] 6.2 `ruff check packages/agent-eval` 双范围干净 —— 仓库根与 `packages/agent-eval` 内各跑一次，另加 `ruff check src tests`，三次均 `All checks passed!` 且 rc=0。中途真出过一次并已修：机械改 import 后 `test_discovery.py` 的导入块不再有序（I001）
- [x] 6.3 确认未碰 `core/` 判分链、`storage/`、`dataset/`；`statistics_version` 维持 2 —— `git status --porcelain` 的改动面为 `docs/cli-reference.md`、`CHANGELOG.md`、`pyproject.toml`、`src/agent_eval/{__init__.py,cli.py→_cli_app.py,api/app.py}` 与四个测试文件，`core/` `storage/` `dataset/` 一行未动；`core/types.py:102` 仍 `STATISTICS_VERSION = "2"`
- [x] 6.4 确认 `[project.scripts]` 的**存在性**未变（Non-Goals：已装 `[cli]` 的用户零影响）—— `pyproject.toml:50-51` 仍是 `eval-suite = "agent_eval.cli:main"`，字符串一字未动（被移动的是模块内部实现，不是入口签名）；产物级复核见 5.5 的 B/D 两栏：改前改后 core-only 都照样生成 `eval-suite.exe`

## 7. 发布 0.3.1 与回读

- [x] 7.1 版本值改为 `0.3.1`（若 3.1 已落地，只改 `__init__.py:14` 一处 —— 这本身就是 D1 的验收证据）—— **实测就是这一处**：`git status --porcelain` 里带版本号的文件只有 `src/agent_eval/__init__.py`（`pyproject.toml` 已无 version 行可改），构建产物随之命名为 `aeval_framework-0.3.1.{whl,tar.gz}`。提前到第 5 组之前做，是为了让产物级证据落在一个与已发布 0.3.0 **不同号**的制品上，比对不产生歧义
- [x] 7.2 CHANGELOG 新增 0.3.1 段：三处修复各一条，并点名"裸装者手上的 `eval-suite` 从 traceback 变为指引"与"挂载形态的接口文档版本自 v0.2.0 起一直误报 0.1.0"—— 两处点名都按原话写进对应条目，另附「发行物验证」小节记四个干净形态
- [x] 7.3 **复用发布时定型的回读脚本**（`.qoder/tmp/readback.sh`），不另写一套：先 `local 0.3.1` 预演，再 `pypi 0.3.1` 真回读，四腿全绿为准 —— `local 0.3.1` **预演四腿全绿、脚本退出码 0**（腿 1 `latest=0.3.1`；腿 2 装前 `none installed (clean)`、装成 `0.3.1` 于 `readback-env\…\site-packages`；腿 3 exit 0 且三条文本命中；腿 4 两处出口同版本）。**两处必要修正记在这里**：① 腿 2 的前置装包补了 `httpx2`（现版 starlette 的 TestClient 要的是它，见 5.8，不补则腿 4 因环境假红）；② 腿 4 补了**挂载形态的 `info.version`** 这一断言 —— 原脚本只查 `/v1/meta` 与响应头，而这两处在 0.3.0 里本来就是对的，**错的那一腿它压根没测**。补上后同一脚本对**已发布的 0.3.0** 跑为**红色**（`AssertionError: 0.1.0`，`mounted info.version: 0.1.0` 而其余三处 `0.3.0`，退出码 1），对 0.3.1 为绿 —— 这条负向对照既证明新断言有牙，也证明 0.3.0 那次"四腿全绿"确实抓不到本缺陷（与 proposal 第三节的判断一致）。`pypi 0.3.1` 真回读待 7.4 之后
- [ ] 7.4 上传**显式点名 0.3.1 的两个文件**，禁止 `dist/*` —— `packages/agent-eval/dist/` 里留着 0.2.0 旧产物且 `build` 不清空输出目录（HANDOVER C 组）。待上传（`.qoder/tmp/dist-0.3.1/`）：`aeval_framework-0.3.1-py3-none-any.whl` sha256=`bbf94275d48ecde15ecc45dd83d77483f17b5686830e7791eac73ef795e9c9ca`、`aeval_framework-0.3.1.tar.gz` sha256=`801d42b5b6aa1b32e0ec1f74a0731c26c7cf85273c6ce4bdfa10b81679179546`。命令沿用 0.3.0 的定型形态（本机 `twine`/`pipx` 均未安装）：`python -m pip install twine` 后 `twine check` + `twine upload` 逐一点名两个文件。**按 0.3.0 的既定做法，上传由使用者在自己终端执行、token 不经 agent 手**
- [ ] 7.5 打 annotated tag `v0.3.1` 并推 `main` 与 tag；**推送后 `git ls-remote` 回读**，不拿退出码当已推送
- [ ] 7.6 核对 `git rev-list --count origin/main..main` 为 0（HANDOVER C 组那条纪律：这个数要与 `git tag` × PyPI 索引一起查）—— 归档提交前该数为 **0**（`main` 与 `origin/main` 同为 `7236774`，本次已 `git fetch` 后实测），发布后需连同 `v0.3.1` tag 与索引 `latest` 一起复查一次

## 8. 归档收口

- [x] 8.1 `openspec validate fix-version-drift-and-cli-entry-guard --strict` 通过（两个能力的 delta：`cli` + `rest-api`）—— `Change 'fix-version-drift-and-cli-entry-guard' is valid`
- [x] 8.2 归档前复查：`cli` 其余 11 条 Requirement 本文未被改动（本变更对该能力是"新增 1 条 + 修改 1 条"，被改的只有「run demo 零 setup 首跑」）—— **条数与提案差一，实测为准**：归档前 `openspec/specs/cli/spec.md` 是 **11 条 Requirement / 21 个场景**（`grep -c "^### Requirement:"`），不是提案假设的 12 条；本变更 ADDED 1 条（「未安装 CLI 依赖时命令行入口必须给出可行动指引」，标题不与任何既有条目重叠）+ MODIFIED 1 条（「run demo 零 setup 首跑」，原 1 个场景改为 2 个），故其余**未被改动的为 10 条**。`rest-api` 为 **3 条 / 5 场景**，与提案一致。主 spec 本文一行未动：`git diff --stat -- openspec/specs` 为空
- [x] 8.3 确认第 5 组全部有 run 级证据后才归档 —— 它是归档前置，不是可选项 —— 5.1…5.8 每条都带环境路径 / 安装表达式 / 退出码 / 输出比对项，且脚本（`.qoder/tmp/g5-acceptance.sh`）与回读脚本（`local 0.3.1`）均可重跑取同一批事实；前置成立
- [ ] 8.4 归档后核对主 spec：`cli` 应为 12→13 条 Requirement 且其中 1 条本文已更新；`rest-api` 3→4 条 —— **按 8.2 实测更正预期**：`cli` 应为 **11→12 条**（场景 21→24，其中 1 条本文已更新）、`rest-api` **3→4 条**（场景 5→8）。归档动作与 7.4/7.5 的发布序列一并决定，未先归档
