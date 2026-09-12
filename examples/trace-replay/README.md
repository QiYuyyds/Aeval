# Trace replay example — 回放线上流量

「线上 trace → 任务化 → 套件化 → 评测」这条通路的最小演示，**全程离线可跑**：
trace 归档用内存字典模拟，评测对象是内置 Mock Agent，零外部依赖（除
`aeval-framework` 本身）。

> 这是**通路的演示形状**，不是生产接法：真实 trace 来自 Phoenix / OTLP /
> 宿主后端的导出（替换示例里的 `ExportedTraceProvider` 一处）；判据需要
> 人工评审挖掘结果后补写（挖掘只产出 prompt 与溯源，不替人下判断）；
> 定时回归由外部调度（cron / CI）驱动 —— 框架本身不引入任何在线服务。

## 运行

```bash
python examples/trace-replay/replay.py
```

脚本走完五步并打印每步的输入输出：

1. **trace 导入** —— `ExportedTraceProvider` 持有「导出的 trace 归档」
   （真实接法替换为从后端拉取）
2. **挖掘** —— `TraceMiner.mine("failed_tasks")`：按策略筛 trace，从根
   span 的 `input.value` 提取用户输入；没有输入的 trace 进 `skipped`
   （不猜 prompt），条目保留 `source_ref=trace_id` 溯源
3. **判据补全** —— 给每个挖掘条目补 `code_based` 判据（人工评审位）
4. **套件化** —— `EvalDataset.to_suite()`：复用套件校验器，数据集 id/版本
   写进 suite 元数据，run 结果可关联回挖掘批次
5. **执行 run** —— 落库到 `./aeval.db`，用 `eval-suite show <run_id>` 复查

## 与定时回归衔接

首轮 run 落库后，把套件固化成 YAML（或用脚本重放挖掘），由外部调度周期性执行：

```bash
eval-suite run examples/trace-replay/suite.yaml --baseline <首轮 run_id>
```

`--baseline` 是基线相对回归门：本轮与基线 run 的实测 `pass@1` 95% 区间
不重叠且方向向下才判「显著变差」（退出码非 0）；统计口径或证据边界不同
则直接判不可比并给原因 —— 宁可红不可哑。详见
[CLI 参考](../../docs/cli-reference.md)。

## 通路全文

[接入指南 §14「回放线上流量」](../../docs/integration-guide.md)写明了各步骤
的输入输出、策略语义与边界（不做在线服务）。
