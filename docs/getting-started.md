# 快速开始

本指南带你从零跑通 Aeval：安装 → 运行离线示例 → 查看结果 → 对比两次运行。

> **升级提示**：统计口径修正后，**以前放行的 CI 可能开始拦下**。三类原因，判读方式不同：
>
> 1. `pass@1` 数值下降 —— 旧值含义是「n 次里至少成功一次」（等于 `pass@n`），新值是有效 trial 的成功比例 `c/n`。3 次里蒙对 1 次以前报 100%，现在报 33.3%。这不是退化，是之前的数在撒谎。
> 2. 退出码 3 / `GATE FAILED (evaluation-side, not agent performance)` —— 有 trial 被判 `invalid`（判分器异常、超时、grader 未注册、判据为空、judge 不可用）。这些以前被折成 0 分计入通过率，现在从分母里剔除。**先修评测配置**，再看分数。
> 3. 关键量显示 `insufficient_data` —— 该 task 没有任何 valid trial（全无效或全待人工评分），不再伪造 0.0。
>
> 排查入口：`eval-suite show <run_id> --task <id>` 看逐 trial 的 `VALID/INVALID/PENDING` 判定与原因；`GET <prefix>/meta` 与 `eval-suite show` 首行公布统计口径版本。**历史 run 不回算**（`statistics_version` 为 `null`），与新旧 run 的对比会被标为不可比。

## 1. 安装

```bash
pip install aeval-framework            # 核心（编排 / 评分 / 存储）
pip install "aeval-framework[cli]"     # + eval-suite 命令行
pip install "aeval-framework[api]"     # + REST API 服务（挂载或独立部署时）
```

要求 Python ≥ 3.11。trace 导出到 Arize Phoenix 是可选能力，按需自行 `pip install arize-phoenix`（框架内部懒加载导入）。

## 2. 运行离线示例

仓库自带一个完全离线的最小示例（Mock Agent，无需任何服务）：

```bash
git clone https://github.com/QiYuyyds/Aeval
cd Aeval

eval-suite run examples/minimal/suite.yaml
```

输出形如：

```
Starting eval run: aeval-minimal v1.0.0 (2 tasks, up to 3 trials each)
  [1/2] echo-qa: 3/3 valid trials passed
  [2/2] artifact-check: 3/3 valid trials passed
────────────────────────────────────────────────────────
Results Summary
────────────────────────────────────────────────────────
  Run: run_xxxxxxxxxxxx  Status: completed  Duration: 0.1s
  Statistics version: 2
  Evidence: runner=6
  Regrade: available
  Pass@1:  100.0%  [95% CI 61.0%..100.0%]
  Pass@2:  100.0%  [95% CI 61.0%..100.0%]
  Pass@3:  100.0%  [95% CI 61.0%..100.0%]
  Pass^1:  100.0%  [95% CI 61.0%..100.0%]
  Pass^2:  100.0%  [95% CI 61.0%..100.0%]
  Pass^3:  100.0%  [95% CI 61.0%..100.0%]
  Denominator: valid=6 invalid=0 pending=0
  Avg Score: 1.0000  [95% CI 1.0000..1.0000]  worst_of_n: 1.0000
  Tasks: 2  Trials: 6
────────────────────────────────────────────────────────
```

注意 `Pass@1: 100.0% [95% CI 61.0%..100.0%]`：6 个 trial 全通过是真的全通过，但 6 次观测不足以支撑「这个 agent 一定能做对」—— 区间下界 61% 就是这个诚实的提醒。想要更窄的区间就加 `--trials`。

`Evidence: runner=6` 说的是同一件事的另一半：这 6 条通过全部由**适配层交付**的观测支撑（mock 没实现评测侧取证探针），不是评测框架自己去看环境看到的。接一个真实环境并实现 `probe()` 之后，这一行会变成 `harness=N`；出现 `subject=N` 则说明有结论依赖了 agent 的自述，需要 `allow_subject` 显式放行才可能成立。`Regrade: available` 表示这批证据已归档，可以在**不再跑一次 agent** 的前提下重新评分（库层 `EvalRunner.regrade_run`）。

## 3. 查看结果

结果默认落库到 `./aeval.db`（SQLite）：

```bash
eval-suite list runs                  # 运行历史
eval-suite show run_xxxxxxxxxxxx      # 单次运行详情
eval-suite show run_xxx --task t_ok   # 下钻单个任务（逐 trial + grader 明细）
eval-suite list suites                # 已运行过的套件清单
```

## 4. 对比两次运行

改坏一点东西再跑一次，然后 A/B 对比：

```bash
eval-suite compare run_aaa111 run_bbb222
```

输出全局指标的 delta 表与逐任务分数，每行附**显著性判定**：两个 95% 区间重叠时标 `not significant`，不给方向；口径版本不同则整对标注 `NOT COMPARABLE`。退化/提升清单只收录可比且显著的差值，为空时会写明是 `(no significant difference)` 还是 `(runs not comparable)` —— 与 REST API `POST /compare` 复用同一实现。

## 5. 校验套件（CI 友好）

```bash
eval-suite validate examples/minimal/suite.yaml   # 合法 → 退出码 0
```

`validate` 只做加载与校验（semver、task id 唯一、grader 配置格式等），不执行 Agent，适合放进 CI 在运行前先挡住格式错误。

## 6. 接入你自己的 Agent

内置 Mock Agent 只用于演示。接入真实 Agent 只需实现一个方法（`AgentRunner` 协议，**0.2.0 起的签名**）：

```python
from agent_eval.core.types import TrialEvidence

class MyAgentRunner:
    async def run(self, view, session) -> TrialEvidence:
        # view 只暴露 id/description/prompt/env —— 判据与答案键不会递给被评方
        trace_id, transcript, outcome = await my_agent.run(view.prompt, view.env)
        # 最小路径：一次交付全部证据
        return TrialEvidence.runner_reported(
            trace_id=trace_id, transcript=transcript, state=outcome,
        )
```

要渐进交付（边做边推读数、运行中让评测侧取证）就走 `session.emit(...)` 与
`await session.harness_probe(...)`；参考实现见 `src/agent_eval/examples/mock_runner.py`。
详细见 [接入指南](./integration-guide.md)；HTTP Agent 的适配模板见 `examples/achat/`。

## 升级到 0.2.0（从 0.1.x）

0.2.0 是**破坏性版本**，四件事需要动手，其余升级即用：

1. **`AgentRunner` 签名断裂（不留兼容层）**。旧写法
   `async def run(self, task) -> tuple[str, list[dict], dict]` 不再被接受，
   新签名是 `run(view: TaskView, session: TrialSession) -> TrialEvidence`。
   宿主侧最小改法：把三元组换成 `TrialEvidence.runner_reported(trace_id=…, transcript=…, state=…)`；
   环境状态想被评测侧独立取证时，实现 `probe()` 并让适配层读 `session.harness_probe()` 的读数
   （自报数据走 `subject` 级，判定取信见 [Grader 参考](./grader-reference.md)）。
   会话句柄的三个能力（推送读数 / 当场取证 / 取消与时限）全部可选，只用返回值的接入方依旧最简。
2. **trace 词汇选择**。trace 属性翻译表默认仍是 `otel-genai`；trace 由 Phoenix 或
   OpenInference 系埋点导出时改用 OpenInference 预设：
   库侧 `default_mapping(vocabulary="openinference", extra_entries=宿主专有条目)`，
   CLI 侧 `eval-suite run --vocabulary openinference`（或环境变量 `AEVAL_TRACE_VOCABULARY`）。
   未知名字在装配期报错并列出可选值；`/v1/meta` 公布全部预设与各自的规范修订号。
3. **门禁可能拦下历史放行的构建**（统计口径收紧，见顶部「升级提示」）。三类信号
   （`pass@1` 语义修正 / 退出码 3 / `insufficient_data`）都不是退化而是旧数在撒谎。
   **没有提供 legacy 旗标**，也不会有——回退口径等于继续拿错的数做决策。
4. **历史 run 不回算、也不可重评**。0.1.x 落盘的 run 仍完整可读，但它们没保留来源分级与
   证据边界，API/CLI 会显示 `Regrade: no — <原因>`；对它们重新评分只会产出另一个错的数。
5. **`cost_usd` 需要价目表**。框架不内置任何价格数据：没有配置价目表时成本报告为
   「不可计算」（带原因），不会用猜测的单价折算。

## 升级到 0.3.0（从 0.2.x）

0.3.0 的破坏点集中在**指标作者**一侧；只写套件、不写自定义指标的升级即用。

1. **`Metric.measure()` 签名断裂（不留 legacy 旗标）**。旧五字符串签名
   `measure(input, actual_output, expected_output, context, retrieval_context)`
   被移除，新签名是 `measure(ctx: MeasurementContext) -> MetricResult` ——
   旧签名指标在注册/注入时（装配期）即报错并说明新签名形状。逐字段对照表与
   迁移写法见 [接入指南 §11](./integration-guide.md)。
2. **`confidence` 语义澄清（不改数据）**。单评分者多采样得到的
   `confidence = 1 - uncertainty` 是**同一 judge 的自一致（self-consistency）**，
   不是评分者间信度。跨评分者信度是新字段：判据配置 ≥2 个独立 `judges` 后，
   run 汇总的 `agreement` 块报告 Cohen's κ / Krippendorff's α（评分者不足或
   对齐样本过少时为 `None` + 原因），与 confidence 分块呈现、不混排。
3. **诊断块不改变历史口径**。task 级 `diagnostic_metrics` 启用的指标只进报告的
   诊断块（CLI / 报告默认折叠，`--verbose` 展开），不进通过率、pass^k、任何分母
   与判分聚合 —— `statistics_version` 维持 2，新旧 run 的分母语义可比。历史 run
   汇总读回时诊断块为空、κ/α 为 `None`、门字段缺省，不报错。
4. **门是显式 opt-in**。不声明 `gate` / `reward_basis` 的套件分数与 0.2.0 逐位
   一致；声明了门判据而 `reward_basis` 为 additive（默认）时，门声明只随结论
   落盘并标注「未启用」。语法见 [YAML 格式](./yaml-format.md)。
5. **trial 总分落盘为 `synthesized_score`**。乘性门塌缩后的 trial 总分现在随结论
   一起存，汇总的 `avg_score` 就是对它取均值，CLI 下钻与 `/runs/{id}/trials` 读它。
   API 里既有的 `trials[].score` 语义不变（仍是 grader 简均，为兼容保留），两者会
   在门塌缩时不同；该字段落盘前的历史 run 读回为 `None`，呈现层自动回退简均。

## 下一步

- [YAML 格式](./yaml-format.md) — 套件怎么写
- [Grader 参考](./grader-reference.md) — 9 个内置评分器
- [CLI 参考](./cli-reference.md) — 全部命令与选项
- [架构](./architecture.md) — 模块与数据流
