# 判分提示词必须可复现：修掉 model_based 的集合迭代序

> 日期：2026-09-22 ｜ 无对应研究文档条目（提案由代码审计发现，非路线图项）

## Why

`model_based` 评分器把工具清单以 `list(set(...))` 拼进 judge 提示词（`graders/model_based.py:158`）。Python 对字符串的 hash 默认按进程随机化，因此**同一批归档字节在不同进程里 regrade 会生成不同的提示词**。

变更④ 把「采集与评分分离」立为契约，承诺"改判据、换 judge、收紧证据声明，都能对同一批字节重评"——而这条承诺成立的前提是：重评时喂给 judge 的东西与上次逐字节相同。今天它不成立。

这不是理论风险。一个用了两种以上工具、且判据读到工具清单的套件，其分数在跨进程重评时可能漂移；而 `verdict_drift` 会把这种漂移读成"judge 升级翻转了 N 个 trial"——**把不可复现误报成口径变化的收益**，正好污染④ 用来审计口径的那个工具。

它同时是后续一切判分器信度测量的前置：在提示词本身不可复现的前提下测"这条判据稳不稳"，测出来的全是噪声。

## What Changes

1. **提示词成为纯函数**：`_build_prompt` 中所有由集合派生的列举改为确定序，使提示词输出完全由（归档证据, rubric, dimensions, 判分配置）决定。
2. **`ModelBasedGrader.implementation_version` `"2"` → `"3"`**。这是行为变更：修复后重评历史 run 可能翻转动到多个 verdict。**不静默处理**——翻转数量由 `verdict_drift` 量出并记入本变更的验证记录，延续"历史 verdict 永不覆盖、口径随判定落盘"的既有纪律。
3. **守护测试覆盖一类而非一点**：同一份归档证据在**两个独立子进程**中各构建一次提示词，断言逐字节相等。测试约束的是"集合序泄进判分输入"这一整类缺陷，而不只是今天已知的这一处。
4. **审计结论落档**（见 design.md）：`graders/`、`metrics/`、`trace/` 下其余集合用法逐一判读，说明为何它们不构成同类缺陷——避免后来者重新怀疑一遍。

**无公开面破坏**：`AgentRunner` / `Grader` 协议、`/v1` 响应结构、`eval-suite` 参数均不改动。

## Capabilities

### New Capabilities

（无。）

### Modified Capabilities

- `graders`：新增「判分提示词必须对同一输入确定」这条要求。现有 9 条要求覆盖了结论自陈所见、按声明级别取信、披露最弱证据级别，但**没有任何一条约束判分输入本身的可复现性**——而这三条自陈类要求的可信度都建立在"重评看到的是同一份东西"之上。

## Impact

- **代码**：`packages/agent-eval/src/agent_eval/graders/model_based.py`（`_build_prompt` 一处 + 版本常量）。
- **测试**：`packages/agent-eval/tests/` 新增跨进程提示词一致性测试；现有 `model_based` 单测若断言过工具清单顺序需同步调整。
- **数据**：历史 run 无需迁移。`statistics_version` 维持 2——本变更改的是判分输入的确定性，不动分母口径；受影响的是评分器自身的 `implementation_version`。
- **版本**：随 0.4.0 发布（0.3.0 尚未发布到 PyPI，无跨版本兼容负担）。
- **依赖**：无新增运行时依赖。
- **验证门**：`cd packages/agent-eval && pytest tests/ -q` + `ruff check packages/agent-eval` 双范围。跨进程测试必须真起子进程——**不得用 `PYTHONHASHSEED=0` 自证**，那会掩盖缺陷而非验证它。
- **解锁**：判分器呈现不变性测量（bias probe 线）。那条线的成本与结论都只有在输入可复现时才有意义；本变更是它的前置，但**不预先为其引入任何配置面**。
