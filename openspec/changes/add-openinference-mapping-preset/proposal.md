# 内置 OpenInference 映射预设

> 日期：2026-09-05

## Why

框架自带的唯一 trace 后端是 `PhoenixProvider`，而 Phoenix 导出的是 OpenInference 属性名——**自带 provider 的默认词汇与自带 provider 本身对不上**。内置条目只对齐 OTel GenAI，所以一个刚 clone 下来、用 Phoenix 观测自己 agent 的开发者，开箱得到的是一堆「证据缺失」，而框架会诚实地把缺失报成缺失（这正是 ② 的行为），于是指标全空、门禁判不可信。

真实数据（2026-09-05 在活的 AChat + Phoenix 上测得）：`llm.token_count.prompt` 出现在 **199/199** 条 LLM span 上，`gen_ai.*` 出现 **0** 条。我们当天是靠手写 9 条映射条目才跑通的——每个 Phoenix 用户都要重新发现同一件事，而这个框架的目标用户恰恰是「下载下来评自己 agent」的开发者。

## What Changes

- `default_mapping()` 增加 `vocabulary` 参数，可选 `"otel-genai"`（**默认，语义不变**）与 `"openinference"`；后者以 OpenInference 名优先、OTel GenAI 名保留为兜底，两套公共约定同时可读。
- OpenInference 条目取自 `openinference-semantic-conventions` 0.1.30 的真实常量（`llm.model_name`、`llm.token_count.prompt` / `.completion` / `.total` / `.prompt_details.cache_read` / `.completion_details.reasoning`、`session.id`、`tool.name`、`openinference.span.kind` 等），规范修订号作为版本标签随 run 落盘。
- 不引入 `openinference` 运行时依赖：预设只是一张纯数据表，包不存在也能用。
- 未知词汇名在装配期报错并列出可选值，而不是静默退回默认表（那会产出一整套看起来正常、实则全空的观测）。
- 刻意**不**映射 `tool.parameters`：无法确认它是调用实参还是参数 schema，猜错的代价是把「证据不可用」变成错误的判定。

## Capabilities

### New Capabilities

（无。）

### Modified Capabilities

- `trace-provider`: 「span 归一化由版本钉定的翻译表驱动」扩为「内置条目覆盖多套公共约定且可选」；新增一条「公共约定词汇以可选预设提供」，规定默认值不变、可选集合可枚举、未知值报错、版本随 run 记录。

## Impact

- 代码：`packages/agent-eval/src/agent_eval/trace/mapping.py`（新增预设表与 `vocabulary` 参数）、`core/runner.py`（如需暴露装配参数）、`cli.py` 与 `api/standalone.py` 的 `/v1/meta` 能力清单（公布可选词汇）
- 宿主：`bitdance-agenthub` 的 `TRACE_MAPPING_ENTRIES` 可从 9 条缩到只剩宿主专有条目（`tool.success`、`agenthub.*` 那几个），版本递增为 `agenthub-3`；这一步在预设落地后做，属于宿主仓库独立一笔
- 测试：`tests/test_trace_normalization.py` 增加「仅选预设、零 extra_entries，Phoenix 形状的 span 即可读出 token/模型/角色」用例；未知 vocabulary 报错用例
- 文档：`docs/integration-guide.md`（词汇选择与何时该用哪个）、`docs/architecture.md` §6 扩展点、README 的 Features 一行
- 兼容性：默认词汇不变，既有 suite 与宿主映射行为不受影响；`AttributeMapping` 结构未变，`with_extra` 语义不变
- 不涉及：采集开关、脱敏、契约变更（那些属于 ③/④）
