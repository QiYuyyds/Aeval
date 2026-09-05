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

内置 Mock Agent 只用于演示。接入真实 Agent 只需实现一个方法（`AgentRunner` 协议）：

```python
class MyAgentRunner:
    async def run(self, task) -> tuple[str, list[dict], dict]:
        # 执行 task.prompt，收集 trace_id / transcript / outcome
        return trace_id, transcript, outcome
```

详细见 [接入指南](./integration-guide.md)；HTTP Agent 的适配模板见 `examples/achat/`。

## 下一步

- [YAML 格式](./yaml-format.md) — 套件怎么写
- [Grader 参考](./grader-reference.md) — 9 个内置评分器
- [CLI 参考](./cli-reference.md) — 全部命令与选项
- [架构](./architecture.md) — 模块与数据流
