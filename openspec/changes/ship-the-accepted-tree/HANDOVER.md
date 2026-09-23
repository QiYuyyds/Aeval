# 交接项 —— ship-the-accepted-tree

发布（0.3.0）本身不依赖以下任何一条。它们各自独立，是本次执行过程中**实测撞见**或**原提案已列**的，按「谁欠的、等什么」记在这里，免得下次重新推一遍。

判据统一：只记已经用代码或命令核实过的事实，未验证的推断一律标注。

## A. 原提案列出的四条（proposal《不含》段 / tasks 10.3）

1. **`dataset` 能力有实现、无 spec。** `packages/agent-eval/src/agent_eval/dataset/` 下 10 个 `.py`（`models.py` / `quality.py` / `storage.py` / `version.py` + `sources/` 四类来源），但 `openspec/specs/dataset/` **不存在**，而能力词表里有 `dataset` 这一条。即"词表说有契约、契约文件不在"。本次 wheel 里这 10 个文件照装（`agent_eval/dataset/**` 在包内），所以是**文档/契约面的债，不是功能缺失**。
2. **真实 judge 凭证下的敏感度测量。** 入口 `examples/presentation-probes/measure_presentation_sensitivity.py --live`。今天测不出来的原因记录在案：宿主四把候选凭证全为 401/402。⑧ 的机制已由双向构造性替身钉住，缺的只有幅度这一个数。
3. **`core/metrics.py` 另两处同类非确定源**（与 P0 修掉的 `list(set(...))` 同一毛病：集合迭代次序进到了产出里）。**位置按当前文件核实过——原任务清单写的 `:876` 已经不对**（那行现在是步数效率诊断，且全仓 grep `众数` 零命中）：
   - **`metrics.py:1001`（众数并列）**：`cost_unknown_reason = max(set(unknown_reasons), key=unknown_reasons.count)` —— 两个原因计数打平时，取谁由 `set` 迭代次序决定，而字符串 hash 按进程随机化，于是同一批数据在不同进程里可能给出**不同的"最主要原因"**。
   - **`metrics.py:358-361`（κ 的 `p_e` 求和次序）**：`categories = set(labels_a) | set(labels_b)` 之后 `p_e = sum(... for c in categories)` —— 浮点加法不可结合，迭代序变则末位可动，κ 因此可能跨进程不完全一致。
   两者都不在 P0 的范围内、本次未动。要不要修取决于它们能否真移动结论，那是一次测量而不是一个决定；另注：`dimension_order` 探针的不变量声明已经把"求和次序造成的末位浮点差异"明确排除在呈现敏感之外，两件事同源。
4. **覆盖矩阵快照过期。** `research/agent-eval-landscape/04-aeval-coverage-matrix.md:3` 自陈快照日期 **2026-09-07**，其 `:74` 自己写明「另有 **7** 行已过期（⑤⑥⑦ 落地后：用户模拟器、事件注入、套件分发、基线门等），逐行回填属独立工作」。**注意数字对不上**：本次任务清单写的是"约 9 行"，文件自陈是 7 行，两个数都没被逐行复核过 —— 回填时以逐行重判为准，不要引用任何一个现成数字。另：该文件在 09-22 已随 ⑧ 更新过第 4、8 行（`:72-73`），所以"过期"的部分不含这两行。

## B. 本次执行新增的四条（原提案未列）

5. **版本号有两个手工副本，且无测试守护。** `pyproject.toml:5` 与 `src/agent_eval/__init__.py:14` 各写一份 `0.3.0`，`tests/` 下**没有任何**测试钉住二者一致（grep `__version__` / `pyproject` 于 tests 零命中）。本次发 0.3.0 恰好两处都对，属运气而非机制。**代价**：下次 bump 漏改一处，`importlib.metadata` 路径（已安装时）与 `__version__` 回退路径（源码直跑时）会给出两个不同版本，而 `/v1/meta` 与 `X-Aeval-Version` 正好各走一头。**等什么**：一次 `chore(release)` 里顺手加一条断言两相一致的测试即可，成本一行。
6. **寄宿形态的 OpenAPI 版本是硬编码字面量。** `api/app.py:187` 的 `FastAPI(version="0.1.0")` 未走同文件的 `package_version()`（对照 `api/standalone.py:67` 走的是 `version=version`）。影响面窄：只污染 `/openapi.json` 的 `info.version`，不影响 `/v1/meta` 与响应头 —— 干净环境实测两处都是 0.3.0。**注意**：这是**寄宿形态**（宿主 `mount` 的那份）独有的偏差，独立部署没有。
7. **只装 core 时，`eval-suite` 会被创建但必然崩溃。** 回读预演量到的：`pip install aeval-framework`（不带 extras）之后，console script `eval-suite.exe` 照样生成，一跑即 `ModuleNotFoundError: No module named 'typer'`（`cli.py:53` 在模块导入期裸 `import typer`）。核心库本身导入正常。**这条的要害不是文档没写** —— `README.md:29-31` 三条安装路径写得很清楚（core / `[api]` / `[cli]`，且 `[cli]` 那行注明"+ eval-suite CLI"；要补的 `api,cli` 组合形态在 `:38` 的源码安装块里）。要害是**打包层没有把命令与它所需的 extra 绑在一起**：装了 core 的人手上就多了一个必然抛 traceback 的可执行文件，而 traceback 里没有任何"请装 `[cli]`"的提示。**修法**两种都属代码/打包改动：`pyproject` 里把 console script 归到 `cli` extra（装了才存在），或在 `cli.py` 导入失败时给出显式指引（命令存在但会说清楚缺什么）。两种都比现在好，取哪种是产品决定。
8. **两份 README 的 Known Limitations 仍有一处不对称。** 本次已把 ZH 落后 EN 的「翻译表只有一个 OTel GenAI 预设」补齐，并对齐了 9 条小标题；但 EN 第 1 条含 `tool.parameters` **刻意不映射**的理由，ZH 只有后半句（脱敏摘要落盘）。属翻译债不是事实错误（ZH 没说不映射），但这条理由是设计里最容易被误改的一处，值得两份都有。**与发布无关，不阻塞。**

## C. 本次做掉但值得记住的两件事

- **`main` 曾领先 `origin/main` 39 个提交。** 远端停在 09-06 的 v0.2.0 发布提交，即 ④⑤⑥⑦P0⑧ 六个变更**在 GitHub 上同样是未发布状态**。此前所有验收记录都只盯 PyPI 落后，没人看 GitHub 落后 —— 下次发布核对时，`git rev-list --count origin/main...HEAD` 应与 `git tag` × PyPI 索引**一起**查。
- **`python -m build` 不清空输出目录。** `packages/agent-eval/dist/` 里留着 0.2.0 的 wheel 与 sdist（按你的决定保留）。任何 `twine upload dist/*` 都会把已发布版本一起再传一次，PyPI 以 400 拒收、把同批次里 0.3.0 的真实结果混进失败噪声。上传请**显式点名文件**。
