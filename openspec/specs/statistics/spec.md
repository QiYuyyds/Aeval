# statistics Specification

## Purpose
把一次套件运行汇总成可信的能力与可靠性数字：定义通过率的估计口径、外推的标注方式、不确定性与分布摘要，以及每个聚合量所依赖的分母语义。

## Requirements

### Requirement: pass@k 在实测区间使用有限样本无偏估计

对每个 task，设 n 为有效 trial 数、c 为其中成功数。当 `k ≤ n` 时 `pass@k` SHALL 按 `1 - C(n-c, k) / C(n, k)` 计算，因此 `pass@1` MUST 等于 `c / n`。

#### Scenario: 三次中一次成功

- **WHEN** 一个 task 有 3 个有效 trial，其中 1 个成功
- **THEN** `pass@1` 为 0.333（而非 1.0），`pass@2` 为 0.667，`pass@3` 为 1.0

#### Scenario: 全失败仍为零

- **WHEN** 一个 task 的 3 个有效 trial 全部失败
- **THEN** `pass@1`、`pass@2`、`pass@3` 均为 0.0

### Requirement: 超出实测样本的外推值必须可区分

当 `k > n` 时系统 SHALL 继续按二项外推给出 `pass@k` / `pass^k`，但该值 MUST 携带 `extrapolated` 标记、所用单次成功概率及其 95% 置信下界；任何报告界面 MUST NOT 以外推值冒充实测值。

#### Scenario: 三次样本外推 k=5

- **WHEN** 一个 task 实测 n=3、c=1，被要求给出 `pass@5`
- **THEN** 返回外推值，并同时返回 `extrapolated = true` 与该 c/n 的 Wilson 95% 下界

#### Scenario: 无有效样本时不外推

- **WHEN** 一个 task 的有效 trial 数为 0
- **THEN** 所有 k 的通过率为 `insufficient_data`，系统不给出任何外推值

### Requirement: pass^k 与 trial 完成顺序无关

`pass^k` 在 `k ≤ n` 时 SHALL 按 `C(c, k) / C(n, k)` 计算，表示任意 k 次全部成功的概率；MUST NOT 依据 trial 索引截取前 k 个判定。

#### Scenario: 失败 trial 换位不改变结果

- **WHEN** 同一组 trial 中失败的那个从索引 0 移到索引 2
- **THEN** `pass^2` 与 `pass^3` 的返回值不变

### Requirement: 聚合量必须附带不确定性与分布摘要

每个通过率 SHALL 附 Wilson 95% 置信区间；每个连续分数 MUST 附 bootstrap 95% 置信区间（重采样不少于 1000 次）、均值、标准差与 `worst_of_n`；过程指标 MUST 附 p50 与 p95。

#### Scenario: 小样本套件给出区间

- **WHEN** 一个 20 任务套件的 `pass@1` 为 0.75
- **THEN** 汇总同时给出该比例 95% 区间，使调用方能判断与另一 runs 的差异是否可能只是噪声

#### Scenario: 可靠性地板与均值并列

- **WHEN** 一个 task 的 5 个 trial 分数为 0.9 / 0.85 / 0.2 / 0.8 / 0.7
- **THEN** `worst_of_n` 为 0.2，与均值并列呈现，任一者都不被省略

### Requirement: 每个聚合量声明自己的分母

所有通过率与平均分 MUST 一并报告 `valid_trials`、`invalid_trials`、`pending_trials` 三个计数及其所用分母；当分母为 0 时结果 MUST 为 `insufficient_data`（空值）而不得为 `0.0`。

#### Scenario: 全部待人工评分

- **WHEN** 一个 task 的 3 个 trial 都在等待人工评分回传
- **THEN** 该 task 的通过率与平均分为 `insufficient_data`，`pending_trials` 为 3
- **AND** 该 task 不进入失败清单

#### Scenario: 无效 trial 不占分母

- **WHEN** 一个 task 有 2 个有效 trial 与 1 个无效 trial
- **THEN** 通过率的 n 为 2，且汇总中可见 `invalid_trials = 1`

### Requirement: 饱和度与一致性使用修正后的口径并与成功判定同源

饱和度检测 SHALL 基于实测区间的 `pass@1`，有效 trial 数低于最小样本要求的任务 MUST NOT 参与饱和判定；trial 间一致性 MUST 采用与 trial 成功判定相同的加权分口径。

#### Scenario: 蒙对一次不算饱和

- **WHEN** 一个 task 的 3 次尝试里成功 1 次
- **THEN** 其 `pass@1` 为 0.333，该 task 不被列为饱和任务

#### Scenario: 样本过少不判饱和

- **WHEN** 一个 task 只有 1 个有效 trial 且成功
- **THEN** 该 task 不出现在饱和清单中，并被标注为样本不足

#### Scenario: 配置了权重时口径统一

- **WHEN** 一个 task 为各 grader 配置了权重
- **THEN** 一致性所使用的分数序列与决定 trial 成功与否的分数序列是同一套加权分

### Requirement: 成本作为与通过率并列的独立轴

系统 MUST 分别计量输入、输出、推理与缓存 token，并按可配置单价表折算 `cost_usd`；成本与通过率 MUST 作为并列的两轴呈现，MUST NOT 折入任何单一复合分数。单价表未配置时 MUST 报成本不可计算，MUST NOT 以 0 冒充零成本。

#### Scenario: 未配置单价

- **WHEN** 运行环境提供了 token 数但没有单价表
- **THEN** 成本字段报不可计算并说明缺单价，token 分解仍照常报告

#### Scenario: 缓存与推理 token 参与计费

- **WHEN** 一次 trial 同时产生普通输入、缓存读取与推理 token
- **THEN** 三者分别计量并按各自单价折算，不合并为单一 token 总数

### Requirement: 成本折算不得对明细桶重复计费

缓存读取与推理 token 是输入与输出 token 的**子集**，不是与之并列的独立计数。折算成本时 MUST 先从父桶中扣出明细桶，再按各自单价相加；单价表 MUST 允许显式声明某些 provider 的父桶本就不含明细（此时四路相加），且该声明必须写在配置里而不是靠猜。子集口径下明细大于父桶时，成本 MUST 报不可计算并指明是哪一路矛盾，MUST NOT 夹取为零后继续给出一个看似合理的数字。

#### Scenario: 高缓存命中的调用

- **WHEN** 一次调用输入 8747 token 其中 8320 命中缓存，输出 164 token 其中 24 为推理
- **THEN** 输入一路只对 427 个非缓存 token 按输入价计费，其余 8320 按缓存价计费

#### Scenario: 父桶本就不含明细的 provider

- **WHEN** 单价表声明该 provider 的输入桶与缓存读桶互不相交
- **THEN** 四路各自相加计费，不做扣减

#### Scenario: 明细大于父桶

- **WHEN** 缓存读 token 数大于输入 token 数
- **THEN** 成本报不可计算并指明矛盾路别，而不是输出一个被夹取过的数字

### Requirement: 工具选择指标提供精确率、召回率与 F1

系统 MUST 提供工具选择的精确率、召回率与 F1（实际调用集合对照期望集合），现有仅覆盖召回率的部分 MUST 扩展为三者并列；调用总数 MUST 取自归一化观测而非名称猜测。

#### Scenario: 多余调用被精确率反映

- **WHEN** agent 调用了全部期望工具外加三个无关工具
- **THEN** 召回率为 1 而精确率小于 1，F1 反映两者，结论不再只由召回率给出

### Requirement: 步数效率作为诊断量输出

当 task 声明了 `optimal_steps` 时，系统 MUST 输出步数效率（最优步数除以实际步数）作为诊断量；在未于套件中显式声明其参与门禁之前，该量 MUST NOT 影响 trial 的通过判定。

#### Scenario: 绕远路但做成

- **WHEN** agent 用两倍于最优步数完成任务且判定成功
- **THEN** 步数效率小于 1 并被单独报告，该 trial 仍记为通过

### Requirement: 分别报告成功与失败尝试的资源消耗

汇总 MUST 分别给出成功 trial 与未通过 trial 的平均 token 与成本，使「失败尝试消耗更多资源」这类事实可见，而不被整体均值抹平。

#### Scenario: 失败比成功更贵

- **WHEN** 一次 run 中未通过 trial 的平均成本是通过者的三倍
- **THEN** 汇总按两类分别呈现该差异，而不是只给全局均值
