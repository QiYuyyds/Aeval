# 判分呈现探针 — 机制验收与「等凭证就能跑」的真实测量入口

变更 `add-judge-presentation-probes` 要回答的问题是：**同一个 judge 对不该影响结论的
呈现细节敏不敏感？** 本目录放的是这件事的**可复现入口** —— 归档记录写在变更的
`tasks.md` 里，这里放的是"别人按同样方法能重跑出同样的数"的脚本。

机制部分今天就能跑完，**不需要任何 LLM 凭证**；幅度部分要等一把能用的 judge 凭证。

## 运行

```bash
# 零凭证：两个构造性替身，双向钉机制（退出码 0 = 机制自校验通过）
python examples/presentation-probes/measure_presentation_sensitivity.py

# 有凭证：同一份报告结构，judge 换成真实的（OPENAI_API_KEY / --model）
python examples/presentation-probes/measure_presentation_sensitivity.py --live --model gpt-4o-mini

# 用真实归档证据跑（从任一 run 导出 trial 数组）
python examples/presentation-probes/measure_presentation_sensitivity.py \
    --live --trials-json /path/to/trials.json --sample 0.5 --seed 20260922
```

在仓库根执行。脚本自己把 `packages/agent-eval/src` 加进 `sys.path`，不需要装包。

探针是 judge 调用的乘法，所以脚本**先报价再跑**：默认两个算子 = 每个抽中 trial 4 次
调用（基线 1 + 锚定变体 2 + 逆序变体 1）。`--sample` 无内核默认值，`--seed` 落进报告，
使抽样本身可复现（design D5）。

## 它证明什么、不证明什么

| 读数 | 来源 | 能引用成什么 |
|---|---|---|
| 替身 `anchor_reactive` 报出敏感 | 构造性替身（分数回归到提示词展示的预填值） | 探针**能发现**敏感 |
| 替身 `body_only` 报未检出 | 构造性替身（只读证据正文） | 探针**不虚报**敏感 |
| `--live` 的数字 | 真实 judge + 真实归档证据 | 真实锚定敏感度（**今天还没有这一行**） |

前两条合起来只证明机制有效，**不证明真实 judge 敏感或不敏感**。这句话同时印在报告
对象的 `interpretation_note` 里，不依赖读者先来看这份 README。

## 为什么今天只有前两行

真实锚定敏感度需要一把可用的 judge 凭证，宿主四把候选截至 2026-09-22 全为 401/402
（与 P0 的 tasks 6.2 记录同一手）。这不是把测量推迟到将来的修辞——脚本的 `--live`
分支现在就通着，凭证恢复后直接重跑即可，**不需要任何新的设计决定**。

无凭证时 `--live` 会如实报「不可计算」并带上失败原因（judge 调用失败），而不是给出
一个冒充测过的「未检出」；那条失败路径由常驻测试
`tests/test_presentation_probes.py::TestReportSemantics::test_unavailable_judge_is_reported_as_not_measured`
钉住（本仓库的验证不执行真实 API 调用）。

## 已知边界（不是疏漏）

- **不成对比较位置偏置**。框架内没有成对比较判据，`candidate_order` 那一类无宿主 ——
  要测它得先有 pairwise 判据，那是另一个变更的前置。
- **系统提示词与截断策略不在算子集内**。`"You are an evaluation expert."` 对所有 rubric
  硬编码相同，是一处未测面；改写被评正文与截断轨迹都破坏不变量，构造期即被拒绝。
  判读表全文在 `docs/grader-reference.md` 的「判分呈现探针」节。
- **默认夹具是 6 份同内容 trial**，只用来让"未检出"能以一条结论的形态印出来。真归档
  不足 5 个对齐 trial 时报「不可计算」是正确的读数，不要为了出数而伪造样本。
- 探针是纯库层入口：无 YAML / CLI / REST / 看板表面、不落库。跑一次问一次。
