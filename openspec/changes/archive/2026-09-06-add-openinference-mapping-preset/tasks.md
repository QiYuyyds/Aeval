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
- [ ] 4.3 离线回归：用收敛后的映射重读 6 条真实 Phoenix trace，6/6 仍读出 token；`llm.token_count.total` 不再出现在未识别属性清单（说明预设接管）
  - **2026-09-06 核对改回未勾**：两条仓库里都找不到这次回归的持久记录（无命令输出、无文档落痕），Phoenix 服务当前不在线、`~/.phoenix` 亦无可直接查询的原始 trace 库，无法复跑。替代性证据不足以等价替代：预设对真实 trace 的读取后来由宿主活跑 `run_8ca076ca783a`（host `.agenthub-data/aeval.db` 可查，9 trial 全 valid）间接覆盖，合成 trace 的读取有单测（`test_openinference_preset_needs_no_host_entries` 等）覆盖——但「6 条真实 trace + 未识别清单」这一具体断言缺证据。下次 Phoenix 在线时补跑一次即可重新勾选。
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

---

## 逐条核对结论（2026-09-06，由 make-published-artifact-match-accepted-code 任务 2.1 执行）

除 4.3（已改回未勾，理由见上）与 4.4（本就未勾，需用户授权）外，其余 23 条勾选逐条复核，均有可指认证据：

- 1.1 ✓ `trace/mapping.py` 内 OpenInference 条目表 + 属性名字面量；`pip show openinference-semantic-conventions` 为空、pyproject 无该依赖、源码无 import（仅字符串字面量）。
- 1.2 ✓ `OPENINFERENCE_SPEC_VERSION = "openinference-0.1.30"`（mapping.py:116）+ `known_vocabularies()`（:149）。
- 1.3 ✓ `default_mapping(vocabulary: str = VOCABULARY_OTEL_GENAI)`（:231-237）。
- 1.4 ✓ 未知词汇在 `_preset()` 抛 ValueError 并列可选值（:168）。
- 1.5 ✓ `tool.parameters` 刻意不映的注释在条目表旁（:124-125）。
- 1.6 ✓ 本次复跑全量测试 638 passed，无需 openinference 包。
- 2.1 ✓ `EvalRunner.__init__` 只收已构造的 `AttributeMapping`（core/runner.py:247 起），无词汇参数。
- 2.2 ✓ `/v1/meta` 能力清单含 `vocabularies{default,known,…}`（api/app.py:109-113）；CLI `--vocabulary` + `AEVAL_TRACE_VOCABULARY`（cli.py:286-339）。
- 2.3 ✓ docs/architecture.md 4 处 OpenInference 表述（:3/:29/:41/:203）与 integration-guide 映射章节同步。
- 3.1–3.5 ✓ 五个测试名全部实存且在 638 条内通过（test_trace_normalization.py:364/394/405/414，test_cli.py:219）。
- 4.1 ✓ 宿主 `TRACE_MAPPING_ENTRIES` 恰 3 条（tool.name/tool.success/session.id），`vocabulary="openinference"`，`TRACE_MAPPING_VERSION="agenthub-3"`（config.py:65-72）。
- 4.2 ✓ 本次实跑 `pytest tests/test_eval_integration_config.py` 11 passed 全绿（当年"41 passed"的计数与今天文件内容有出入——并发会话动过测试文件——但"现有断言全部仍然成立"这一实质主张成立）。
- 4.3 ✗ 见上，改回未勾。
- 4.4 未勾，正确（会再产生真实 agent 调用，由本变更 2.5 一并处理）。
- 5.1 ✓ integration-guide 词汇选择章节（含 Phoenix→openinference、OTel GenAI→默认）。
- 5.2 ✓ README.md:15「Two public trace vocabularies out of the box」整段。
- 5.3 ✓ Known Limitations 现表述为「按名字选的公共约定预设，标准埋点零手写条目」（README.md:81）+ `tool.parameters` 不映理由（integration-guide:163、mapping.py:124）。
- 6.1 ✓ 本次复跑 `ruff check packages/agent-eval` All checks passed。
- 6.2 ✓ 本次复跑 `PYTHONPATH=src pytest tests/ -q` 638 passed。
- 6.3 ✓ 同 1.6。
- 6.4 ✓ 本次复跑 `openspec validate add-openinference-mapping-preset --strict` 通过。

结论：**23/25 有据，1 条虚标改回（4.3），1 条留待授权（4.4）**。归档判断见 make-published-artifact-match-accepted-code 任务 3.2。
