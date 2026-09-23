# 把验收过的这一版真正发出去：补齐 CHANGELOG 并结束 17 天的未发布状态

> 日期：2026-09-23 ｜ 先例：[2026-09-06-make-published-artifact-match-accepted-code](../archive/2026-09-06-make-published-artifact-match-accepted-code/proposal.md)（同一失败模式的上一修复）
> 本变更 `skip_specs: true` —— 发布与记录完整性不改变任何 spec 级行为，不编造 Requirement。

## Why

**最后一次发布是 17 天前的 v0.2.0，而 `pyproject.toml` 写的 0.3.0 从来没有作为可安装的制品存在过。**

```
git tag            v0.1.0 (08-30)  ·  v0.2.0 (09-06)        ← 到此为止
pyproject.toml     version = "0.3.0"                        ← 从未打过 tag
CHANGELOG          ## [Unreleased] 压着六个已归档变更          ④ ⑤ ⑥ ⑦ P0 ⑧
```

README 第一行的安装指令是 `pip install aeval-framework`。一个今天新 clone 的人照文档装上去，拿不到用户模拟器、基线回归门、功效分析、套件打包、可复现的判分输入，也拿不到呈现探针——**他拿到的是 0.2.0 那一版**。这不只是"没更新"：仓库里已经有一次专门的修复叫《让发布出去的包与我们验收过的代码是同一份》，正是这个缺口的疤。每多压一天，"已验收"与"已发布"之间的面就越大。

**第二个 Why 是我造成的**：⑧ `add-judge-presentation-probes` 发了一个新能力（探针协议、两个算子、一个独立报告类型、三态 status），`CHANGELOG.md` 里**一个字都没有**。P0 的任务清单里有 7.2「CHANGELOG 点名重评会变数」，我给 ⑧ 写 7.1–7.5 时漏了对应的一条。现在 ④⑤⑥ P0 各有段落、⑧ 缺席。这条不补，发布说明就漏掉一次口径新增（"判分器可以被探针检视，且探针不产出结论"是一条新的可观察行为）。

**第三个 Why 是版本号本身已经互相矛盾**，不解决就没法发：

| 变更 | 自己声明的目标版本 |
|---|---|
| ④ agent-metric-catalog | 0.3.0 |
| ⑤ user-simulator | 0.4.0 |
| ⑥ baseline-gate | 0.4.0 / 0.5.0 |
| ⑦ suite-packaging | 0.5.0 |
| P0 prompt-deterministic | 0.3.0 / 0.4.0 |
| ⑧ presentation-probes | 未声明 |

这些标签是在"0.3.0、0.4.0、0.5.0 会各自单独发布"的假设下分别写下的。**那个假设没成立**——0.3.0 一次都没发。六个变更现在是同一坨未发布内容，而版本号是**发布序列**不是**计划序列**：0.2.0 之后的下一个发行物只能是 0.3.0。

## What Changes

1. **补 ⑧ 的 CHANGELOG 条目**：新增能力段落，点名三件事——探针可检视判分器但不产出结论、呈现不变性与 κ/α 是两类量、`dimension_order` 那条**结构上测不出首因效应**的限定（把"not_detected 比标签听起来弱"写进发布说明，而不是只活在归档记录里）。
2. **定版本号并改写标签**：六个变更统一落到一个真实存在的版本号下，各条的"目标 0.x"改为按实际归属重述。推荐 **0.3.0**（理由见 design D1），并记下一条支持该决定的先例：0.1.0→0.2.0 那次就带着 ③ 破坏的 `AgentRunner.run()` 签名——0.x 期间破坏性变更走 minor 是既有做法，④ 的 `Metric.measure()` 破坏同理。
3. **从构建物验证，不从工作树验证**：照先例第 4 步，把构建出的 wheel/sdist 装进**干净环境**，跑离线示例与测试，通过后才打 tag。工作树 898 条测试全绿与"装出来那份能跑"是两件事。
4. **发布后回读验证**：从 PyPI 索引重新解析出版本与元数据、在干净环境里 `pip install aeval-framework==<新> ` 装一次、跑 `eval-suite run examples/minimal/suite.yaml` 并核对退出码与输出。**不得以"发布命令返回 0"作为已发布的证据。**
5. **顺带核对 README 的版本陈述**：`README.md` 的 Known Limitations 标题写着 "what v0.1.0 does and does not do well"，而 ⑤⑥ P0  已经改变了其中若干条的事实（尤其探针与功效分析）。逐条判读哪些还成立，不成立的改掉。

**不含**：`dataset` 能力的 spec 补齐、真实 judge 凭证下的敏感度测量、`core/metrics.py` 两处同类非确定源、覆盖矩阵 `04` 的 9 行回填。这些各自独立，见文末交接项。

## Capabilities

### New Capabilities

（无。）

### Modified Capabilities

（无 —— 本变更不改任何 spec 级行为，`.openspec.yaml` 已设 `skip_specs: true`。发布说明、版本号与制品验证都是过程，不是契约。）

## Impact

- **文件**：`CHANGELOG.md`（补 ⑧ 段 + 重述各条版本标签 + 把 `[Unreleased]` 收敛成一个真实版本）；`packages/agent-eval/pyproject.toml`（版本号）；`README.md` / `README.zh-CN.md`（Known Limitations 逐条复核）；`docs/release-checklist.md` 若核对中发现缺项。
- **外部动作**：`git tag`、PyPI 上传。**两者都在任务清单里标为需你现场确认**——不可逆且对外可见，不由我在流程里默认执行。
- **不改动**：任何 `src/` 代码、任何 `openspec/specs/` 主 spec、任何测试。本变更若需要动代码，说明前面六个变更里有未闭合项，那属于回归而非发布步骤。
- **风险面**：版本号一旦对外发布就不可回收（PyPI 不允许重传同一版本）。所以"定版本号"这一步必须在构建物验证**之前**定死，且在打 tag 之前完成回读演练。
