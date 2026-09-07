## Context

只写影响做法的事实。动机见 `proposal.md` — Why。

- 现状签名：`Metric.measure(input, actual_output, expected_output, context, retrieval_context)`（`metrics/base.py:95`），纯字符串；`MetricGraderAdapter.to_grader()` 是指标进判分的唯一桥。judge 提示词只含最终输出。
- ③ 已建立的两套可复用语义：取信声明（`GraderConfig.evidence_levels` + `allow_subject`，`graders/_evidence.py`）与"诊断量不进分母"（statistics 已有「步数效率作为诊断量输出」的先例）。④ 是把这两套语义推广到指标目录，不是发明第三套平行机制。
- `confidence = 1 - uncertainty`（多采样极差的一半）在 `core/types.py:922-927`，字段文档已写明是多采样口径——改的是呈现与文档，不是数据模型。
- 汇总结构同一大版本内向后兼容是 API 承诺；新增字段必须对历史 run 回读安全。
- 框架是 pip 装的开发时工具，无认证/多租户；κ/α 的样本量级是单 run 的 trial 数（个位数到几十），手写公式即可，不值得引入 numpy/pingouin。
- import 隔离硬约束：以下方案全部落在 `agent_eval` 内，经 `core/contract.py` 既有扩展协议（Metric / Grader）演进，不触碰 `app.*`。

## Goals / Non-Goals

**Goals:**

- 指标能读到带来源分级的观测，judge 能看到轨迹——但都按声明取信，未声明即不可见。
- 评分者间信度（κ/α）成为 first-class 统计量，judge 换代的翻判幅度可量化。
- 硬性安全维度可以乘性合成，不被平均稀释；门的选择与结果落盘可见。
- 轨迹类指标扩充不悄悄改口径：默认诊断，升格显式。

**Non-Goals:**

- 不做 ⑤（覆盖面）与差异化项（⑥）。
- 不改 ③ 的证据采集与判定语义（取信声明原样复用）。
- 不引入新的外部依赖（κ/α 手写）。
- 不给旧 `measure()` 签名留兼容层（见 D1）。

## Decisions

### D1：宽签名走测量上下文对象，旧签名直接移除

`measure(ctx: MeasurementContext) -> MetricResult`。`MeasurementContext` 携带：任务输入（task id / prompt / 期望输出）、按声明过滤后的观测序列（复用 `Observation`）、指标自身声明。不选"保留旧签名 + 新增可选参数"：可选参数超集会让"拿不到证据"与"没传证据"不可区分，重演 ② 之前缺失与零值混同的老问题；不选"继承旧类兼容"：双路径违反扩展协议"协议演进不保留双路径"。`MetricGraderAdapter` 同步改造，`to_grader()` 对外形状不变。

### D2：指标取信声明复用 ③ 的 `evidence_levels` 语义，默认与今天等价

指标声明 = 现有 `GraderConfig.evidence_levels` 的同构（`transcript/steps/harness_state/subject_state` 通道级声明，而非按 ObservedBy 三级另造一套——通道与 ③ 判分侧的声明单位一致，装配期校验复用同一校验器）。默认声明 = 仅最终输出（等价今天的 `actual_output`），保证未声明轨迹的指标行为逐位不变。不选"指标默认可见全部证据"：那会让新增指标悄悄扩大证据边界，重演 ③ 之前宿主私名腐化的教训。

### D3：κ 与 α 的分工按评分者数与缺失情况自动选择

两个评分者 → Cohen's κ（二值/序数，权重用未加权）；≥2 评分者或存在缺失评分 → Krippendorff's α（nominal/ordinal 由判据类型定）。两者都只在评分者 ≥2 时计算；单评分者多采样的 `confidence` 保留，呈现时标注 self-consistency。不选"只用 α"：κ 是两评分者场景的通行量，宿主"judge 换代对照"恰好是两评分者场景。κ/α 进 `RunSummary` 新字段，样本不足时值为 `None` + 原因（复用 `insufficient_data` 语义，不伪造 0）。

### D4：reward_basis 门是判据级声明，乘子合成在结论落盘时可见

套件判据可声明 `gate: {factor: 0.0..1.0}` 并在 task/run 级声明 `reward_basis: multiplicative`（缺省 `additive`，行为不变）。合成规则：multiplicative 时总分 = 基础分 × ∏(生效门因子)；门未生效（证据不足/证据级别不满足）不乘任何因子并在结论标注原因——不选"证据不足按失败处理"：那会把"评测侧没取到证据"折成被评方的失败，违反 ① 确立的"缺失 ≠ 失败"。门结果（因子、生效与否、所依据级别）进 `GraderResult` 新字段，报告可见。不选"run 级全局门"：门与判据绑定才能复用 ③ 的证据声明与时刻语义。

### D5：诊断/判分是指标目录的角色轴，缺省诊断、升格显式

`MetricResult` / 指标注册表增加 `role: diagnostic | judging`；轨迹类指标（工具效率、绕路、自纠等）注册时缺省 `diagnostic`。汇总新增诊断块（分值 + 分布摘要），不进任何分母——这是把 statistics 已有的「步数效率作为诊断量输出」先例推广成目录级语义。升格 = 套件判据里显式引用该指标为判分量（此时它经 `to_grader()` 走判分流程，受 D2/D4 约束）。不选"按指标类型自动判分"：类型推断会让新指标悄悄改变历史口径可比性。

### D6：版本取 0.3.0；汇总新增字段不改 statistics_version

`measure()` 签名断裂 + 指标作者迁移 → minor 破坏版 0.3.0（与 ①→②→③ 的既定节奏一致，迁移说明随发布出）。汇总的新增字段（诊断块、κ/α、门结果）都是可回读安全的增量：历史 run 读回时诊断块为空、κ/α 为 `None`、门字段缺省——不改分母与口径语义，`statistics_version` 维持 2；若未来诊断量被证明影响口径解读，再随真正改变分母的变更递增。不选"顺手递增 statistics_version"：该版本号是分母口径的契约，为展示性新字段递增会把"口径变了"的假信号发给对比逻辑。

## Risks / Trade-offs

- **签名断裂伤害既有指标作者** → 缓解：迁移说明给逐行对照（旧五参 → `MeasurementContext` 字段映射），随 0.3.0 发布；框架内全部内置指标同步迁移，测试先行。
- **κ/α 被误读成质量分** → 缓解：报告字段命名带 `agreement_` 前缀并附解释行；self-consistency 与 inter-rater 在报告里分开两块。
- **门配置写错（因子全 0）造成总分恒 0** → 缓解：装配期校验因子范围与至少一个非门判据存在；文档标注。
- **诊断块让报告变宽** → 缓解：CLI/报告默认折叠诊断块，`--verbose` 展开；API 结构固定但客户端可忽略。

## Migration Plan

内置指标与 `MetricGraderAdapter` 在本变更内一次迁完（无 dual path，无过渡期）；外部自定义指标作者按迁移说明升级。历史 run 汇总回读兼容由测试固化（D6）。回滚：第 1-3 组逐笔 revert；发布后若发现签名设计缺陷只能 0.4.0 再改——所以 D1 的上下文形状在实现前先在 design 评审定稿。

## Open Questions

- 轨迹指标首发目录收录哪几个（工具效率已在 statistics 有先例；绕路/自纠的精确定义可在实现时以独立小提案补入目录，不阻塞本变更的协议与门机制）。
