# orchestration Specification

## Purpose
规定一次 trial 的结论如何分类：哪些情况属于 agent 未通过评测，哪些情况属于评测本身没有产生有效结论，并要求后者的原因可被查询与计数。

## Requirements

### Requirement: 评测侧失败归类为无效而非零分

当 trial 的评分过程未能产生有效判定时，该 trial SHALL 归类为 `invalid`，且 MUST NOT 计入任何通过率的分子或分母。未注册的 grader、评分器抛出异常、评分器超时、LLM 判定调用失败、LLM 判定输出无法解析，均属评测侧失败；任何评测侧失败 MUST NOT 被折算为 0 分或半分。

#### Scenario: 判分器崩溃

- **WHEN** 某 trial 的一个 grader 抛出异常
- **THEN** 该 grader 结论为 `invalid` 并携带原因文本，该 trial 不计入通过率
- **AND** 整场 run 继续正常完成而不崩溃

#### Scenario: 解析失败不再给半分

- **WHEN** LLM 判定返回的内容无法解析出任何维度分数
- **THEN** 结论为 `invalid`，而不是各维度 0.5

#### Scenario: 部分维度缺席

- **WHEN** LLM 判定只返回了配置维度中的一部分
- **THEN** 缺席维度按 `invalid` 处理，且维度平均分的分母仍为配置的全集维度数

#### Scenario: 依赖未满足保持既有语义

- **WHEN** 某 grader 因前置依赖未通过而被跳过
- **THEN** 其结论仍按既有语义记 0 分并说明依赖原因，不归类为 `invalid`

### Requirement: 超时归类为无效

trial 因超过单次执行时限而结束时，该 trial SHALL 归类为 `invalid` 并记录原因为超时，MUST NOT 与「评分判定为不通过」共用同一结论。

#### Scenario: 超时不等于失败

- **WHEN** 一个 trial 超时
- **THEN** 它计入 `invalid_trials` 且原因为 timeout
- **AND** 它不出现在该 task 的失败 trial 清单中

### Requirement: 未配置的判据不得自动得满分

当评分器已在 task 上配置但不含任何判据时，该评分结论 SHALL 判定为 `invalid` 并说明未配置判据，MUST NOT 返回满分或判为通过。

#### Scenario: 空的确定性检查列表

- **WHEN** 一个 `code` 类评分器的 `checks` 列表为空
- **THEN** 结论为 `invalid`（原因为未配置判据），而不是 1.0 自动通过

#### Scenario: 缺少期望轨迹

- **WHEN** 步骤级评分器没有配置 `expected_trace`
- **THEN** 结论为 `invalid`，且该 task 的通过率分母相应排除这次试验

#### Scenario: 空状态期望列表

- **WHEN** 环境状态检查评分器的 `expectations` 列表为空
- **THEN** 结论为 `invalid`，并出现在配置的告警清单中，提示该 grader 形同未挂载

### Requirement: 无效结论必须可追溯

每个 `invalid` 结论 MUST 保留其证据与原因文本并可通过 run 查询接口取回；系统 MUST NOT 因判定无效而丢弃该 trial 已采集的 transcript、过程指标或产物。

#### Scenario: 事后排查判分器故障

- **WHEN** 一次 run 结束后有人查询某个 invalid trial
- **THEN** 返回结果包含异常原因、涉及的 grader 名称与该 trial 的原始证据

### Requirement: pending 与 invalid 是不同状态

等待人工评分的 trial SHALL 保持 `pending` 语义而不被改写为 `invalid`；人工评分回传后，该 trial MUST 重新参与汇总计算并转为 `valid` 或 `invalid`。

#### Scenario: 人工评分回传后重算

- **WHEN** 一个 pending trial 收到人工分数
- **THEN** 所属 task 的通过率与分母计数被重算，该 trial 从 pending 计数中移出

### Requirement: 每次 trial 记录终止原因

每个 trial MUST 记录 `termination_reason`，取值范围为 `agent_completed`、`step_budget_exceeded`、`token_budget_exceeded`、`cost_budget_exceeded`、`timeout`、`agent_error`、`cancelled`。该值 MUST 由框架在编排过程中判定；被评测方提交的文本或自报状态 MUST NOT 单独决定该值。

#### Scenario: 正常结束

- **WHEN** agent 自行宣告任务完成且未触任何上限
- **THEN** 终止原因为 `agent_completed`，进入正常评分

#### Scenario: 被评测方自称完成但已触顶

- **WHEN** 被评测方返回「已完成」而该 trial 的步数已达上限
- **THEN** 终止原因为 `step_budget_exceeded`，而非采信自报的完成

### Requirement: 预算触顶立即停止并单独归类

步数、token、成本任一上限被达到时，框架 MUST 立即停止该 trial 并记为对应的预算原因。预算触顶属于任务约束未达成，MUST 与「评分判定为不通过」以及框架强制时限造成的 `timeout` 三者分别可辨。

#### Scenario: token 上限触顶

- **WHEN** 某 trial 累计 token 达到 `token_budget`
- **THEN** 该 trial 被停止且原因为 `token_budget_exceeded`，不计入通过，但计入预算触顶计数

#### Scenario: 超时与触顶不混淆

- **WHEN** 同一套件里既有超时结束的 trial 也有 token 触顶的 trial
- **THEN** 汇总中两者的原因计数不同，且超时者按评测不可信的无效通道归类、触顶者按任务约束未达成归类

### Requirement: agent 报错的归类不由框架猜测

当 trial 因被评测系统报错而结束时，`agent_error` 属于「agent 自身缺陷」还是「外部依赖不可达」MUST 由接入方显式声明；未声明时框架 MUST 将其归为结论不可信并标注需要人工判定，MUST NOT 默认折算为通过或不通过。

#### Scenario: 接入方未区分的报错

- **WHEN** runner 只抛出通用异常而未声明错误类别
- **THEN** 该 trial 记为需要人工判定的无效结论，并在汇总中单列

### Requirement: 终止原因分布进入汇总

run 与 task 两级汇总 MUST 给出各终止原因的计数；任何原因的占比 MUST NOT 被折叠进通过率或平均分，以避免「分数下降」掩盖「评测根本没跑完」。

#### Scenario: 大面积取消可见

- **WHEN** 一次 run 中 40% 的 trial 原因为 `cancelled`
- **THEN** 汇总显式呈现该占比，通过率旁同时给出有效样本数

### Requirement: trial 生命周期按采集、停止、评分三相推进

一次 trial MUST 按以下顺序推进：准备环境 → 运行被评系统 → **在环境停止之前完成评测侧取证** → 停止被评系统 → 评分。取证 MUST 发生在被评系统仍可能改动环境之前，且 MUST NOT 在停止之后才尝试采集环境终态。

#### Scenario: 停止之后才取证会读到被清理的状态

- **WHEN** 一次 trial 的环境在停止阶段清理了工作目录
- **THEN** 评测侧取证已在清理之前完成，评分依据的是清理前的真实状态

#### Scenario: 取证与被评方写入隔离

- **WHEN** 被评系统在其运行期间写过伪造的产物清单
- **THEN** 评测侧取证读数不受其影响，二者作为不同来源并列存在

### Requirement: 判分与采集解耦并可重做

评分 MUST 在证据已完整落盘之后进行，且 MUST 能在**不重新运行被评系统**的前提下对既有结果重新评分。重评分 MUST 复用原证据，MUST NOT 触发任何对被评系统的调用。

#### Scenario: 判分器缺陷修复后重评

- **WHEN** 某次 run 因评分器自身缺陷产生了一批无效判定，缺陷随后被修复
- **THEN** 对该 run 重新评分即可得到新结论，被评系统未被再次调用

#### Scenario: 判定器换代后重评

- **WHEN** 语言模型判定器更换了版本，对历史 run 重新评分
- **THEN** 新结论与原结论并列可查，用于量化换代带来的翻转比例

### Requirement: 每次判定记录其评分口径且历史结论不被覆盖

每一次评分结论 MUST 记录当次生效的评分口径标识（判分实现版本、属性翻译表与规范修订、判定所用模型标识）与时间。重新评分 MUST 追加新的结论条目，MUST NOT 覆盖或删除既有结论；系统 MUST 指明哪个条目是当前生效结论。

#### Scenario: 审计一次翻判

- **WHEN** 有人质疑某 trial 的结论为何与上周看到的不同
- **THEN** 可查看该 trial 的全部判定条目及各自口径标识，指出是哪一项版本变了

### Requirement: 来源分级不匹配时判为无效而非照原样评分

当评分结论将只依据其未声明来源级别的证据时，系统 MUST 判该结论为无效并说明原因，MUST NOT 用更低可信级别的证据替代。

#### Scenario: 只读到一个来源

- **WHEN** 一个只允许消费评测侧取证的评分器，本次 trial 只有被评方自报数据
- **THEN** 该评分结论为无效并说明缺哪一级证据，而不是拿自报数据打出一个看起来正常的分数

### Requirement: 一个完整会话构成一个 trial

多轮任务 MUST 作为**一个** trial 执行：从首轮输入开始，按套件声明的轮次推进到会话结束（声明轮数用尽、模拟器判定目标达成，或模拟器不再产出话术），全过程产出的证据归入同一 trial 的结论；取消与预算边界沿用既有终止语义（在已交付的这一次调用之后生效），本要求 MUST NOT 承诺在索取下一条话术之前拦截取消或超预算。既有的通过率、pass^k 与分母声明 MUST 保持原口径不变 —— 轮数 MUST NOT 成为分母单位。会话内部产生的轮级度量 MUST 只进入诊断块。适配器未走完套件声明的轮次而会话即结束时（早退、异常、忽略会话维度），该 trial MUST 判为无效并标注配置侧原因，MUST NOT 按已发生的轮次给出看似完整的通过结论。每次会话 MUST 落盘一个可区分的结束原因，至少区分「目标达成」「模拟器无更多话术」「达到轮数上限」三种，使「提前结束」与「被上限切断」在结论上分得开。

#### Scenario: 三轮上限被切断与目标达成可区分

- **WHEN** 两个多轮 trial 同样在第三轮结束，一个因模拟器判定目标已达成、另一个因达到轮数上限
- **THEN** 两者的结束原因不同并随结论落盘，报告不把「被上限切断」显示成「顺利完成」

#### Scenario: 四轮会话只算一次成败

- **WHEN** 一个声明了四轮的用户-agent 会话被完整执行
- **THEN** 汇总里该 task 的 trial 数仍按一次计，pass@1 的分母不因轮数改变
- **AND** 每轮的过程量（轮数、token、是否纠正自身）出现在诊断块中

#### Scenario: 适配器忽略会话维度

- **WHEN** 套件声明了三轮后续话术，而被评适配器只消费了首轮就返回
- **THEN** 该 trial 判为无效并给出「声明轮数未消费完」的原因
- **AND** 该 trial 不进入通过率分母，也不被折成 agent 失败

#### Scenario: 会话中途取消

- **WHEN** 一次多轮 trial 在第 2 轮被取消
- **THEN** 已采集的证据照常落盘并标注终止原因，该 trial 不计入通过率分母
- **AND** 同 run 其余 trial 不受影响

### Requirement: 会话可由评测侧记录独立重放

框架 MUST 把一个 trial 内的用户侧输入序列（首轮、后续话术或模拟产物、注入的事件与人工介入）连同其采集时刻作为证据落盘，使得**不重新调用被评系统**即可回放并核对这些输入。重放 MUST 只使用评测侧记录，MUST NOT 声称能恢复被评系统的内部状态。被评方的输出 MUST NOT 因重放而被改写或重新记账。该序列 MUST 在使用序列化持久化的存储后端上同样完整可核对：凡「写入处与读取处分居两处」的字段，其测试 MUST 同时覆盖内存与序列化两个后端，MUST NOT 只在内存后端成立即视为已证 —— 内存后端就地改动对象，会恰好掩盖序列化后端读回时丢字段或错读默认值那一类缺陷。

#### Scenario: 复核一次判翻

- **WHEN** 有人怀疑某次多轮判分源于第 3 轮的措辞
- **THEN** 可以从该 trial 的证据中取出三轮用户输入及其时刻逐条核对，而不需要再跑一次 agent

#### Scenario: 重放不重开被评系统

- **WHEN** 触发一次基于已存证据的重评分
- **THEN** 会话的用户侧输入按原记录回放，被评系统调用次数为零

#### Scenario: 内存后端不得替序列化后端作证

- **WHEN** 一次多轮 run 落进使用序列化持久化的后端后再读出并重放
- **THEN** 用户侧输入序列的通道名、时刻与来源级别逐条与落盘前一致，重放仍不产生任何被评系统调用
- **AND** 同一条断言在内存与序列化两个后端上都成立；只在内存后端通过的实现不算通过本要求

### Requirement: 跨判定条目的翻判归因必须指明变化在哪一根口径轴

当同一 trial 存在多条判定条目而结论不同时，系统 MUST 能指出这些条目之间**哪一根口径轴**的取值不同，且「判分实现版本」MUST 是被考察的轴之一。考察该轴时 MUST 只计入本次判定实际产出结论的评分器 —— 未参与本次判定的评分器改版 MUST NOT 被读作口径变化。当全部被考察轴取值均相同时，系统 MUST 明确报告"翻判无法归因到任何已记录口径轴"，而 MUST NOT 以一个"口径种类数 = 1"的计数暗示口径一致 —— 前者是可行动的结论（存在未记录的翻判原因），后者会把人引向相反方向。

#### Scenario: 评分器换代是唯一变化

- **WHEN** 某 trial 重评后结论翻转，两次判定条目之间只有某个参与判定的评分器实现版本不同
- **THEN** 归因输出直接点名为「判分实现版本」这一轴，无需人工逐条比对各字段

#### Scenario: 无关评分器改版不污染归因

- **WHEN** 套件注册了九个评分器而本 trial 只由其中一个判定，另一个未参与的评分器实现版本在两次条目之间发生了变化
- **THEN** 该变化不出现在归因结果中，本 trial 仍被正确判为"唯一变化是参与判定的那个评分器换代"

#### Scenario: 全部口径轴相同而结论仍翻转

- **WHEN** 两次判定条目的所有被考察口径轴取值相同，但结论不同
- **THEN** 系统报告存在无法归因到已记录口径轴的翻判（提示存在未记录的判分原因），而不是显示为"两次口径相同"
