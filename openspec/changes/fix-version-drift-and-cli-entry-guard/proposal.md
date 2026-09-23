# 发出去的包只说一个版本号，且裸装的那条命令要么能跑、要么说清缺什么

> 日期：2026-09-23 ｜ 来源：[2026-09-23-ship-the-accepted-tree/HANDOVER.md](../archive/2026-09-23-ship-the-accepted-tree/HANDOVER.md) B 组第 5、6、7 条
> 0.3.0 已发布（PyPI `latest=0.3.0`，索引回读四腿已核）。本变更处理的是**这个已经人人可装的制品自身的两处失真**。

## Why

### 一、版本号有三个说法，没有一个机制保证它们一致

```
packages/agent-eval/pyproject.toml:5        version = "0.3.0"      ← 手工，成为已安装元数据
src/agent_eval/__init__.py:14               __version__ = "0.3.0"  ← 手工，源码直跑时的回退
src/agent_eval/api/app.py:187               version="0.1.0"        ← 硬编码字面量
```

`package_version()`（`api/app.py:65`）本来就是为"只有一处真相"而写的：优先取已安装元数据、源码直跑时回退 `__version__`。**而定义这个解析器的文件，正是第 187 行绕过它的那个文件。** `api/standalone.py:49,64` 走的是 `package_version()`，所以独立部署形态是对的、寄宿形态是错的。

后果：宿主 `create_app()` 挂出去的那份 `/openapi.json` 的 `info.version` 从 v0.2.0 起就一直说自己是 0.1.0，而同一进程的 `/v1/meta` 与 `X-Aeval-Version` 报 0.3.0。**一个制品对外给出两个版本号，且错的那个正是别人用来对接的那份文档。**

`tests/` 下 grep `__version__` / `pyproject` 零命中——**没有任何测试钉住前两处一致**。发布记录里那句"本次两处都对属运气而非机制"就是这个意思。

### 二、README 第一行那条安装路径，装出来的命令一跑就崩

```
pyproject.toml:48-49   [project.scripts]  eval-suite = "agent_eval.cli:main"   ← 无条件，属 core
pyproject.toml         cli extra          只加 typer + rich
src/agent_eval/cli.py:53                  import typer                        ← 模块导入期裸执行
```

`pip install aeval-framework`（不带 extras）会**创建** `eval-suite` 可执行文件，一跑即 `ModuleNotFoundError: No module named 'typer'`，traceback 里没有任何"请装 `[cli]`"的指引。核心库本身导入正常（发布回读腿 2 已证）。

**这条真正的要害不是"文档没写"** —— `README.md:29-31` 三条安装路径写得很清楚。要害是**装完的人手上多了一个必然抛 traceback 的可执行文件**，而报错把"你少装了个 extra"这件事说成了"这个库是坏的"。

### 三、而 `cli` 有一条场景恰好覆盖这个形态，且从未在真实形态下验过

`openspec/specs/cli/spec.md:154` 「pip 安装形态下首跑」：*WHEN 仅 pip 安装（无仓库 checkout）执行 `eval-suite run demo` THEN 内置套件完整运行*。

⑦ 自己的归档记录第 49 行写着：**「starter pack 不再声称已在『仅 pip 安装、无仓库 checkout』形态下实测（现有测试只在仓库树内 chdir）」**。也就是说这条场景从来没在真 pip 形态下跑过。而 HANDOVER B 组第 7 条量到了裸装崩溃，**两件事分处两份记录、没人连起来看**。

需要说清的是：该场景的"仅 pip 安装"本意是**对照源码安装**（无 checkout），不是"不带 extras"。所以严格讲它没被违反——但它把"装哪个形态"留成了空白，而 README 第一行给的恰好是那个跑不起来的形态。**模糊的场景 + 首行示例 + 一个会崩的命令，三者合起来就是一条没人走过的死路。**

## What Changes

1. **版本号收敛为一个结构性真相源**（依 design D1）：`pyproject.toml` 改用 `dynamic = ["version"]` 由 hatchling 从 `src/agent_eval/__init__.py` 读，手工值只留模块常量一处；`api/app.py:187` 的 `version="0.1.0"` 改走同文件既有的 `package_version()`，并断言接口层不存在第二处版本字面量。**回退方案**（仅当 dynamic 与本项目打包配置实测冲突时启用，需在 D1 记下冲突原因）：保留两份手工值 + 加一条断言二者相等的测试。首选不是测试而是让不一致**无法被写出来**。
2. **裸装形态的 `eval-suite` 要么能跑、要么说清缺什么**：把 `typer` 的导入从模块期挪进 `main()` 并给出可行动指引（指名 `pip install "aeval-framework[cli]"`），退出码与错误文本都要能被脚本判别。
3. **把那条 pip 首跑场景补成可执行的验收**：明确它假设的安装形态，并在**真·干净虚拟环境**里从索引或构建物装一次跑通 —— 不再用"仓库树内 chdir"冒充 pip 形态。

## Capabilities

### New Capabilities

（无。）

### Modified Capabilities

- `cli`：新增「core-only 安装形态下 CLI 入口必须给出可行动指引而非 traceback」；并**修改**「run demo 零 setup 首跑」——其场景需点名所假设的安装形态，使"pip 首跑"不再是一个从未在真实形态下验过的空白。
- `rest-api`：新增「同一进程对外声明的版本必须同源」——寄宿形态的 OpenAPI `info.version` 与 `/v1/meta`、响应头 MUST 出自同一个解析器，MUST NOT 出现第二处硬编码。

## Impact

- **代码**：`api/app.py`（187 一行 + 视需要把 `package_version()` 提给 OpenAPI 构造点）；`cli.py`（`import typer` 从模块期移入 `main()` 的引导路径）；`pyproject.toml` **不改**（`[project.scripts]` 无法按 extra 声明，见 design D2）。
- **测试**：`tests/` 新增版本一致性断言、core-only 入口指引断言、以及一条**在干净环境里跑真装包**的首跑验收（需构建物或索引可达，标注为慢道/人工道，不进默认 CI）。
- **不动**：`storage/`、`dataset/`、判分链、任何 `src/agent_eval/core/` 语义。本变更不碰评测口径，`statistics_version` 维持 2。
- **版本**：这是发布后修复，走 **0.3.1**（补丁版本，非破坏）。`[project.scripts]` 的存在性不变，只是崩溃路径变成有指引的路径——对已装 `[cli]` 的用户零影响。
- **验证门**：`cd packages/agent-eval && pytest tests/ -q` + `ruff check packages/agent-eval` 双范围；**并复用发布时定型的回读脚本**（`.qoder/tmp/readback.sh`）在干净环境验一次 core-only 与 `[cli]` 两种形态的差别。
- **发布面**：0.3.1 需重走一遍发布与回读（含 `dist/` 里 0.2.0 旧物的显式点名上传纪律，见 HANDOVER C 组）。
