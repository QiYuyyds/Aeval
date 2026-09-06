## ADDED Requirements

### Requirement: 多评分者一致性以 κ/α 报告

当同一判据配置了两个独立评分者（不同模型或不同提示词配置）时，汇总 MUST 报告 Cohen's κ；配置 ≥2 个评分者且存在缺失评分时，MUST 报告 Krippendorff's α。两类信度统计 MUST 与单评分者多采样得到的 `confidence` 明确区分——后者是同一 judge 的自一致，MUST NOT 被呈现为评分者间信度。评分者数量不足或一致样本过少时，MUST 标注为不可计算（带原因），不得伪造数值。

#### Scenario: 两个评分者报告 κ

- **WHEN** 一个 judge 判据配置了两个独立 judge 并完成同一批 trial 评分
- **THEN** 汇总报告两者的 Cohen's κ 及一致/分歧计数
- **AND** κ 值与已知小样本对照表吻合

#### Scenario: 多评分者含缺失报 α

- **WHEN** 评分者 ≥2 且部分 trial 缺少某评分者的评分
- **THEN** 汇总报告 Krippendorff's α，缺失单元不冒充一致或分歧

#### Scenario: 单评分者多采样不算信度

- **WHEN** 一个 judge 判据只配置单个评分者但启用了多采样
- **THEN** 汇总呈现的该指标置信度标注为自一致（self-consistency）
- **AND** κ/α 字段标注为不可计算并说明原因（评分者数不足）

### Requirement: 诊断量与判分量在汇总结构中分离

汇总结构 MUST 把仅诊断的指标与参与判分的指标分开呈现：诊断指标的分值、分布摘要单独成块，MUST NOT 计入通过率、pass^k、各 task 分母或任何判分聚合；判分块 MUST 保持既有分母声明语义。历史 run 的汇总回读 MUST 与新字段兼容（缺失的诊断块读为空，不报错）。

#### Scenario: 诊断块不改变既有聚合

- **WHEN** 一个 run 启用了若干仅诊断的轨迹指标
- **THEN** 通过率、pass^k、平均分与分母声明与未启用时一致
- **AND** 报告的诊断块列出这些指标的分值与分布摘要

#### Scenario: 历史 run 回读兼容

- **WHEN** 读取一个没有诊断块字段的历史 run 汇总
- **THEN** 诊断块读为空，其余统计照常呈现，不报错
