## 1. 统计函数与区间估计（无行为变更，可独立合并）

- [x] 1.1 在 `core/metrics.py` 实现 `k ≤ n` 的组合无偏 `pass@k`（`1 - C(n-c,k)/C(n,k)`）与 `pass^k`（`C(c,k)/C(n,k)`），确认 `pass@1 == c/n` 且结果与 trial 顺序无关
- [x] 1.2 把 `k > n` 分支的返回值升级为结构体 `(value, extrapolated, p_point, p_lower_bound)`，保留二项外推公式不变
- [x] 1.3 实现 Wilson 95% 区间；实现 bootstrap 95% 区间（重采样次数默认 1000，支持注入 seed 以便测试复现）
- [x] 1.4 实现 `p50` / `p95` 与 `worst_of_n`，把 `aggregate_metrics` 的 avg/min/max 输出扩为其超集
- [x] 1.5 定义 `insufficient_data` 的表示（空值）与判定入口，替换所有「无数据即 0.0」的返回路径
- [x] 1.6 为 1.1–1.5 写参数化单测，逐条覆盖 `specs/statistics/spec.md` 的 scenario（含 n=3/c=1 得 0.333/0.667/1.0、失败 trial 换位不变、全 pending → `insufficient_data`）

## 2. 数据模型：判定态与新增字段（默认值保证行为等价）

- [x] 2.1 `GraderResult` 增加 `verdict ∈ {valid, invalid, pending}`，**默认 `valid`** 以保持第三方 grader 协议向后兼容
- [x] 2.2 定义 invalid 来源的封闭枚举：`grader_error` / `grader_timeout` / `unknown_grader` / `judge_unavailable` / `verdict_unparseable` / `no_criteria_configured` / `trial_timeout`
- [x] 2.3 `TaskSummary` / `RunSummary` 增加 `valid_trials` / `invalid_trials` / `pending_trials`、per-k 估计元数据、置信区间、`worst_of_n`
- [x] 2.4 增加统计口径版本常量，并让 `RunResult` 记录产生它时所用的版本
- [x] 2.5 确认 SQLite 与 Memory 两种后端能读写新增字段，且缺字段的历史行读为「未知」而非失败

## 3. 编排层判定分类（行为分水岭之一）

- [x] 3.1 `core/runner.py`：grader 异常、grader 超时、未知 grader 三处兜底改为写 `verdict=invalid` 并保留原因，不再折 0 分
- [x] 3.2 `graders/model_based.py`：LLM 调用失败 → `judge_unavailable`；完全无法解析 → `verdict_unparseable`；**删除各维度 0.5 兜底**；维度缺席时平均分母仍用配置的全集维度数
- [x] 3.3 `graders/metric.py` 与 `metrics/base.py` 的 `MetricGraderAdapter`：配置/计算错误同步改为 `invalid`，与 3.2 保持同一枚举与文案约定
- [x] 3.4 把「无判据即自动满分」的四处改为 `no_criteria_configured`：`code_based` 空 `checks`、`state_check` 空 `expectations`、`step_level` 缺 `expected_trace`、以及审计其余 grader 的同类兜底
- [x] 3.5 明确保留：依赖未满足仍记 `passed=False` + `score=0.0` + `verdict=valid`，并加单测锁住这条边界
- [x] 3.6 trial 超时归类 `trial_timeout`，且保留已采集的 transcript / 指标 / 产物不被丢弃
- [x] 3.7 汇总计算按 `verdict` 过滤分母；pending 保持独立通道且人工评分回传后重算
- [x] 3.8 一致性改用与成功判定同源的加权分序列；饱和度改用实测区间 `pass@1` 且最小有效样本数默认 5
- [x] 3.9 以 `specs/orchestration/spec.md` 的 scenario 为准写 MockRunner 端到端用例，覆盖异常/超时/无判据/依赖跳过/全 pending 五类矩阵

## 4. 门禁与 CLI

- [x] 4.1 `metrics/pytest_plugin.py`：门禁目标量改为修正后的 `pass@1`，分母排除 `invalid` 与 `pending`
- [x] 4.2 新增门禁 `invalid` 占比上限（默认 0.2），超限时会话以非零退出码结束并输出「评测侧问题」而非 agent 表现结论
- [x] 4.3 全 pending 的 task 判为未达标且原因为证据不足，不得静默放行
- [x] 4.4 `cli.py show`：输出三类计数、`pass@1` 区间、外推标注、`insufficient_data` 呈现
- [x] 4.5 `cli.py compare`：置信区间重叠则标注不显著且不给方向性结论；口径版本不同则标注不可比
- [x] 4.6 `cli.py run`：退出码纳入评测可信度条件（`invalid` 超阈或关键量为 `insufficient_data`）
- [x] 4.7 更新 `tests/test_cli.py` 与 `tests/test_eval_pytest_plugin.py`，补一个「原本放行、现在拦下」的回归用例

## 5. REST API

- [x] 5.1 run 汇总响应新增字段，确认既有字段名称与类型不变（结构向后兼容）
- [x] 5.2 `/v1/meta` 与寄宿形态的元信息接口公布统计口径版本与 D7 的数值默认值
- [x] 5.3 更新 `tests/test_api.py`、`tests/test_eval_metrics_api.py`、`tests/test_standalone_api.py`，含一个只读既有字段的老客户端用例

## 6. Dashboard

- [x] 6.1 `lib/types.ts` 与查询层补新增字段
- [x] 6.2 `pass@1` / `pass^k` 图例与 tooltip 文案同步修正后口径
- [x] 6.3 run 报告页展示 `valid` / `invalid` / `pending` 计数；外推值与实测值视觉区分并可查置信下界
- [x] 6.4 对比页：口径版本不同提示不可比；差值不显著时以中性样式呈现，不显示退化标签

## 7. 文档与示例

- [x] 7.1 `docs/architecture.md` §4 统计语义整节重写（估计量、外推标注、分母与三态、饱和度与一致性口径）
- [x] 7.2 `docs/cli-reference.md` 退出码与 `compare` 语义；`docs/grader-reference.md` 判定态与自定义 grader 该如何返回 invalid；`docs/integration-guide.md` 同步
- [x] 7.3 `README.md` / `README.zh-CN.md` 更新 Known Limitations 与统计特性描述，明示 `pass@1` 口径变化及历史 run 不回算
- [x] 7.4 `docs/getting-started.md` 加「升级后门禁可能开始拦下历史放行的构建」提示；更新 `examples/minimal`、`examples/achat` 的期望数字

## 8. 验证门

- [x] 8.1 `ruff check packages/agent-eval` 通过（All checks passed）
- [x] 8.2 `cd packages/agent-eval && pytest tests/ -q` 全绿（离线，无外部服务）— 429 passed
- [x] 8.3 确认 core 统计模块覆盖率不低于 90%（`pytest --cov=agent_eval.core.metrics --cov-report=term-missing`；注意 `docs/architecture.md` 提到的 `scripts/check_eval_coverage.sh` 在仓库中并不存在，属文档过时引用，顺手在本任务里改掉该句）— `core/metrics.py` 97%（174 stmts / 6 miss），该句已改为真实命令
- [x] 8.4 `pnpm --filter eval-dashboard build` 通过（含 Next.js 内建 TypeScript 检查）
- [x] 8.5 `openspec validate fix-stats-and-denominators --strict` 通过
