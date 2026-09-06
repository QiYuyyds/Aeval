## 1. 测量上下文与宽签名（唯一不可逆步骤，集中在此）

- [x] 1.1 定义 `MeasurementContext`：任务输入（task id / prompt / 期望输出）、按声明过滤后的观测序列（复用 `Observation`，带来源分级与时刻）、指标自身的声明引用；放在 `core/types.py`
- [x] 1.2 指标取信声明结构：通道级 `evidence_levels` 声明（transcript/steps/harness_state/subject_state），缺省 = 仅最终输出；装配期校验复用 ③ 的校验器（未知通道/空声明/重复报错并列可选值）
- [x] 1.3 `Metric.measure(ctx: MeasurementContext)` 替换旧五字符串签名，删除旧路径（不留兼容层、不留继承桥）；`MetricGraderAdapter` 同步改造，`to_grader()` 对外形状不变
- [x] 1.4 迁移全部内置指标到新签名（answer_relevancy / faithfulness / context_precision / context_recall / llm_judge / prompt_metric / batch_evaluation / synthetic_data / pytest_plugin 触达处逐个过）
- [x] 1.5 上下文观测过滤实现：未声明通道不交付（harness 读数不出现）；声明了不存在/空/重复级别装配期报错
- [x] 1.6 单测：旧签名注册报错并说明新签名形状；默认声明下 `MeasurementContext` 交付内容与 0.2.0 的五参逐字段等价；声明 transcript 后观测含消息序列且每条带 `observed_by`

## 2. judge 轨迹注入

- [x] 2.1 `metrics/llm_judge.py`：声明含 transcript/steps 时，把轨迹（经脱敏钩子后）注入 judge 提示词；未声明时提示词与 0.2.0 逐字节等价
- [x] 2.2 judge 结论的 `explanation` 可引用轨迹事件（提示词要求引用具体消息；不强制）
- [x] 2.3 单测：声明轨迹的 judge 收到完整轨迹且脱敏生效；未声明的 judge 提示词不含轨迹内容（用 fake llm_fn 断言收到的 prompt）

## 3. 诊断 / 判分角色轴

- [x] 3.1 指标注册与 `MetricResult` 增加 `role: diagnostic | judging`；轨迹类指标注册缺省 `diagnostic`，判分路径只接受 judging（或经套件升格）
- [x] 3.2 套件升格语法：判据里显式引用指标为判分量（经 `to_grader()` 走判分流程，带取信声明）；YAML 校验规则与错误文案
- [x] 3.3 汇总新增诊断块（分值 + 分布摘要），不进通过率 / pass^k / 分母 / 判分聚合；CLI 与报告默认折叠，`--verbose` 展开
- [x] 3.4 历史回读兼容：无诊断块字段的旧 run 汇总读为空、κ/α 读 `None`、不报错（测试固化）
- [x] 3.5 端到端：启用若干诊断指标的 run 与未启用的 run，通过率/pass^k/分母逐字段一致；升格一个指标后它出现在判分块

## 4. reward_basis 乘性安全门

- [x] 4.1 套件判据 `gate: {factor}` 声明 + task/run 级 `reward_basis: additive|multiplicative`（缺省 additive）；YAML 校验：因子 ∈ [0,1]、乘性时至少存在一个非门判据、门判据必须有取信声明
- [x] 4.2 合成实现：multiplicative 时总分 = 基础分 × ∏(生效门因子)；门未生效（证据级别不满足/证据缺失）不乘因子并在结论标注原因
- [x] 4.3 `GraderResult` 增加门结果字段（因子、生效与否、原因、所依据级别），随 run 落盘、报告与 API 可见
- [x] 4.4 单测：门失败塌缩总分；证据不足门不生效不冒充；subject 级证据不得触发门；additive 套件分数与 0.2.0 逐位一致
- [x] 4.5 与 ③ 语义的交互测试：门的取信声明走同一套 evidence_levels / allow_subject / 判定时刻机制（门是判据，不新造第二套）

## 5. 跨评分者一致性 κ/α

- [x] 5.1 手写 Cohen's κ（二值/序数、未加权）与 Krippendorff's α（nominal/ordinal、容忍缺失），纯函数 + 已知小样本对照表测试（含文献经典数值）
- [x] 5.2 判据级多评分者配置：≥2 个 judge 定义（不同模型/提示词）跑同一批 trial，评分按 trial 对齐
- [x] 5.3 `RunSummary` 新增 agreement 字段：κ（两评分者）/ α（≥2 或含缺失）；评分者不足或样本过少 → `None` + 复用 `insufficient_data` 原因语义
- [x] 5.4 self-consistency 标注：单评分者多采样的 `confidence` 在报告与 API 呈现时标注 self-consistency，与 agreement 分块呈现，不混排
- [x] 5.5 单测：两评分者已知样本 κ 吻合对照表；含缺失评分的 α 吻合；单评分者场景 κ/α 为 None 且带原因

## 6. 文档与迁移说明

- [x] 6.1 `docs/integration-guide.md`：指标作者迁移指南（旧五参 → MeasurementContext 字段对照表、声明写法、升格写法）
- [x] 6.2 `docs/grader-reference.md` / `docs/yaml-format.md`：gate 与 reward_basis 的 YAML 语法、校验规则表、诊断/判分块说明
- [x] 6.3 迁移说明（getting-started 升级章节追加 0.3.0 段）：签名断裂、无 legacy 旗标、confidence 语义澄清（自一致 ≠ 信度）、诊断块不改变历史口径
- [x] 6.4 README（中英）Features 一行：证据感知指标 + 跨评分者一致性

## 7. 验证门

- [x] 7.1 `ruff check packages/agent-eval` 通过
- [x] 7.2 `cd packages/agent-eval && PYTHONPATH=src pytest tests/ -q` 全绿（含新指标/judge/门/κ/α/兼容性测试）
- [x] 7.3 `tests/test_import_isolation.py` 与 `tests/test_vocabulary_isolation.py` 仍通过
- [x] 7.4 离线 `eval-suite run examples/minimal/suite.yaml` 正常产出，报告出现诊断块且分母与 0.2.0 一致
- [x] 7.5 `openspec validate add-agent-metric-catalog --strict` 通过

## 8. 宿主真流量验收（归档前置；本组是 ④ 的唯一真验收）

> 补记（2026-09-06）：原清单漏写活跑项——①②③ 的致命缺陷全部是单测绿、活跑暴露
> （provider 不交付属性 / token 分桶 / 进程 locality），④ 的四类新机制（judge 看轨迹、
> 诊断轴、κ/α、乘性门）不验真流量就不许归档，同理。

- [ ] 8.1 宿主新增验收套件：真用 `type: metric` 判据（judge 声明读轨迹）+ `reward_basis: multiplicative` 安全门（真流量上可能失败的门，如产物禁含密钥类内容）+ ≥2 个独立 judge 定义产 κ/α 数据；`eval-suite validate` 通过
- [ ] 8.2 宿主 venv 临时切回 editable（本变更已提交的 0.3.0 树；PyPI 上没有 0.3.0）——验收后随发版切回 `==0.3.0`
- [ ] 8.3 取得用户授权：真实 agent 调用 + judge 的真实 LLM 调用（多 judge 使 judge 调用量 ≥2×，成本预估先行）
- [ ] 8.4 真流量活跑：记录 run id；验证诊断块不进分母、门因子塌缩、κ/α 在真实 trace 上与单测语义一致；report/API 可读
- [ ] 8.5 结果写回本清单（勾选 + run id + 判读）；发现缺陷先修复再归档——本组完成是 `openspec archive add-agent-metric-catalog` 的前置条件
