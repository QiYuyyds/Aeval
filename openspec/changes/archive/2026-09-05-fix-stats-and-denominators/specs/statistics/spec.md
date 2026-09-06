## Purpose

把一次套件运行汇总成可信的能力与可靠性数字：定义通过率的估计口径、外推的标注方式、不确定性与分布摘要，以及每个聚合量所依赖的分母语义。

## ADDED Requirements

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
