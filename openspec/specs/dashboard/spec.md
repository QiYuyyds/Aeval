# dashboard Specification

## Purpose
规定 Dashboard 呈现统计数字时如何与后端保持同一口径：图例文案随估计口径同步更新，外推值与实测值在视觉上可区分，`invalid` / `pending` 计数与置信区间一并可见，从而避免修正后的数值被旧图例继续误读。

## Requirements

### Requirement: 指标图例与后端口径同步

Dashboard 中标注为 `pass@1` / `pass^k` 的数值 MUST 使用与后端一致的口径说明；当某数值来自外推时 MUST 在视觉上与实测值区分；`invalid` 与 `pending` 计数 MUST 在 run 报告页可见。

#### Scenario: 外推值不冒充实测值

- **WHEN** 报告页展示由 3 次实测外推得到的 `pass@5`
- **THEN** 该单元格带外推标识，且可查看其置信下界

#### Scenario: 大面积无效不被读成退化

- **WHEN** 一次 run 中过半 trial 被判为 `invalid`
- **THEN** 页面显式呈现无效计数，而不是只呈现一个下降的通过率

### Requirement: 对比视图标注不可比与不显著

A/B 对比视图 MUST 在两次运行的统计口径版本不同时给出不可比提示；当某项指标的差值落在置信区间之内时 MUST NOT 以方向性颜色呈现该差值。

#### Scenario: 噪声差值不上色

- **WHEN** 两个 run 的 `pass@1` 差值不显著
- **THEN** 该差值以中性样式呈现，页面不显示「退化」标签
