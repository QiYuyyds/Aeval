## 1. 预设表与选择参数

- [x] 1.1 在 `trace/mapping.py` 新增 OpenInference 条目表，属性名逐条取自 `openinference-semantic-conventions` 0.1.30 的真实常量（`llm.model_name`、`llm.token_count.prompt` / `.completion` / `.total` / `.prompt_details.cache_read` / `.completion_details.reasoning`、`session.id`、`tool.name`、`openinference.span.kind`）；**不**引入该包为运行时依赖
- [x] 1.2 声明该预设的规范修订标识常量（`OPENINFERENCE_SPEC_VERSION = "openinference-0.1.30"`，注释写明是取名依据版本而非依赖）+ `known_vocabularies()` 枚举入口
- [x] 1.3 `default_mapping()` 增加 `vocabulary` 参数，默认 `"otel-genai"`；选 openinference 时其名排候选首位、OTel GenAI 名保留兜底
- [x] 1.4 未知 `vocabulary` 在装配期抛 `ValueError` 并列出可选值
- [x] 1.5 `tool.parameters` 刻意不映射，理由已写在表旁注释
- [x] 1.6 确认无运行时依赖：全局 python 未安装 `openinference-*`，`import agent_eval.trace.mapping` 与全量测试照常通过

## 2. 装配与能力清单

- [x] 2.1 决定：`EvalRunner` 继续只接受已构造好的 `AttributeMapping`（词汇名在 `default_mapping(vocabulary=...)` 处选择），不新增第二入口 —— 少一条会漂移的配置路径
- [x] 2.2 `/v1/meta` 与 `eval-suite` 的相应输出公布可选词汇预设列表（`evidence.vocabularies` 公布 default/known/各预设修订号；CLI 侧按决定补了 `--vocabulary` + `AEVAL_TRACE_VOCABULARY`，词汇名仍只在 `default_mapping(vocabulary=...)` 处选，`EvalRunner` 未新增第二入口 —— 否则公布列表对 CLI 用户没有可执行的去向）
- [x] 2.3 `docs/architecture.md` §6 扩展点与 `docs/integration-guide.md` 的映射章节同步

## 3. 测试

- [x] 3.1 `test_openinference_preset_needs_no_host_entries`：零 extra_entries 读出四类 token / 模型 / 会话 / LLM 角色
- [x] 3.2 `test_default_vocabulary_is_unchanged`：不指定词汇时候选链逐字段不变（含 `usage.total_tokens` 仍为空）
- [x] 3.3 `test_unknown_vocabulary_fails_loudly_at_assembly`：报错且信息含可选值
- [x] 3.4 `tests/test_vocabulary_isolation.py` 仍通过（宿主私有字面量未进入框架源码）
- [x] 3.5 追加 `test_openinference_preset_keeps_genai_names_as_fallback`：验证迁移期两种埋点共存

## 4. 宿主侧收敛

- [x] 4.1 `TRACE_MAPPING_ENTRIES` 由 9 条收敛到 3 条（只留 `tool.name` / `tool.success` / `session.id` 三个宿主专有名），构造时 `vocabulary="openinference"`，版本递增 `agenthub-3`；随之删除三个不再引用的常量导入
- [x] 4.2 宿主 `tests/test_eval_integration_config.py` 现有断言全部仍然成立（41 passed）；`ruff check` 干净
- [x] 4.3 离线回归：用收敛后的映射重读 6 条真实 Phoenix trace，6/6 仍读出 token；`llm.token_count.total` 不再出现在未识别属性清单（说明预设接管）
- [ ] 4.4 在宿主环境重跑一次 `run_first_suite.py`，确认 9 trial 依然全部 valid（本次未跑：会再次产生真实 agent 调用）

## 5. 文档

- [x] 5.1 `docs/integration-guide.md`：怎么选词汇（用 Phoenix → openinference；用 OTel GenAI SDK → 默认）、`known_vocabularies()` 用法、预设与宿主条目的分工
- [x] 5.2 README 的 Features 补一行「开箱支持 OTel GenAI 与 OpenInference 两套公共埋点约定」；`docs/architecture.md` §6 同步
- [x] 5.3 Known Limitations 复核（若「需要手写映射」类表述存在则撤掉）—— 原「经 OTel GenAI 翻译表归一化（宿主名是映射条目）」已改为「按名字选的公共约定预设，标准埋点零手写条目」，并补上 `tool.parameters` 刻意不映的理由；`docs/cli-reference.md` 与 `trace/mapping.py` 模块文档同步

## 6. 验证门

- [x] 6.1 `ruff check packages/agent-eval` 通过
- [x] 6.2 `PYTHONPATH=src pytest tests/ -q` 全绿 —— **563 passed**（本变更前 559）；2.2/5.x 落地后复跑仍全绿，实测 **638 passed**（含本任务新增 4 条：`--vocabulary` 回显与未知预设退出码 2、`/v1/meta` 预设清单、预设枚举与修订号一致性）。563 与 638 的差值来自工作树里其他变更前已落盘的测试，未逐条追查
- [x] 6.3 确认未新增运行时依赖（见 1.6）
- [x] 6.4 `openspec validate add-openinference-mapping-preset --strict` 通过
