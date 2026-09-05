## 1. 归一化观测与翻译表（纯新增，无行为变更）

- [x] 1.1 定义标准观测记录结构：工具名、调用成败、入参/结果槽位、输入/输出/推理/缓存 token、调用时延、会话标识、agent 名称与版本
- [x] 1.2 定义缺失原因枚举（provider 未覆盖 / 属性名不认识 / trace 中确无此类调用）与「缺失 vs 零值」的表示方式
- [x] 1.3 实现翻译表数据结构，内置条目对齐 OTel GenAI 当前版本，并声明规范版本常量
- [x] 1.4 把 `agenthub.*` 的 5 处硬编码改为默认映射条目，删除框架源码中的宿主私有属性名字面量
- [x] 1.5 工具/轮次识别从 span 名称子串匹配改为按标准观测字段判定；未识别属性名收集为清单
- [x] 1.6 provider 不可用、未安装或返回空时按缺失归类，不崩溃、不返回空列表冒充零
- [x] 1.7 单测：同一逻辑 trace 分别以标准词汇与宿主词汇表达，归一化结果一致；未识别属性名出现在清单中

## 2. 打破 mock 的自证循环

- [x] 2.1 `examples/mock_runner.py` 支持产出标准词汇与宿主词汇两种 span
- [x] 2.2 `tests/conftest.py` 参数化，使 grader / metrics / e2e 用例在两种词汇下都通过
- [x] 2.3 新增静态扫描测试：断言 `agent_eval` 包内不出现宿主私有属性名字面量（与 `tests/test_import_isolation.py` 同类的 AST 检查）

## 3. 终止原因与预算（行为分水岭）

- [x] 3.1 `TrialResult` 增加 `termination_reason` 枚举字段与向后兼容默认值
- [x] 3.2 编排层在结束处判定原因；步数/token/成本任一达上限立即停止该 trial
- [x] 3.3 归类收口：`timeout` 走 ① 的 invalid 通道；`*_budget_exceeded` 计为未通过但单列计数；两者在汇总中可辨
- [x] 3.4 `agent_error` 要求接入方显式声明类别；未声明归为「需人工判定」，不默认折算通过或不通过
- [x] 3.5 task 与 run 两级汇总输出终止原因分布计数
- [x] 3.6 MockRunner 端到端矩阵用例：正常完成 / 步数触顶 / token 触顶 / 超时 / 未分类报错 / 被取消

## 4. 套件字段与采集开关

- [x] 4.1 `EvalTask` 增加 `tags` / `category` / `difficulty` / `optimal_steps` / `step_budget` / `token_budget` / `cost_budget` 及校验，错误信息含文件与字段路径
- [x] 4.2 suite 与 task 级工具入参采集开关（task 覆盖 suite），默认关闭；开关状态写入 run
- [x] 4.3 `core/suite.py` 校验与 `docs/yaml-format.md` 字段表、校验规则一览同步
- [x] 4.4 `tests/test_suite.py` 覆盖非法预算值与「全新增字段缺省时行为不变」

## 5. 脱敏钩子

- [x] 5.1 定义可替换的脱敏接口，提供默认实现（摘要/哈希化而非删字段，保留可判定性）
- [x] 5.2 采集开启时强制经钩子后才进入证据存储、API 响应与 Dashboard；未脱敏值不落盘
- [x] 5.3 钩子标识与版本写入 run，供审计识别「谁关掉了脱敏」
- [x] 5.4 用例：含凭据样式内容的入参在呈现层为脱敏形式，且参数比对结果稳定可复现

## 6. 派生指标

- [x] 6.1 token 四分解与 `cost_usd`；单价表取自外部配置且无内置默认价，未配置时报成本不可计算
- [x] 6.2 工具选择扩展为 precision / recall / F1，保留既有 recall 字段以兼容消费方
- [x] 6.3 步数效率作为诊断量输出，未在套件显式声明参与门禁前不影响通过判定
- [x] 6.4 成功与未通过 trial 的 token/成本分别汇总，避免被全局均值抹平
- [x] 6.5 `n_total_tokens` 保留为输入+输出的只读派生别名，不用于计费推导
- [x] 6.6 为 6.1–6.5 补齐 `specs/statistics/spec.md` scenario 对应的单测

## 7. 存储与历史兼容

- [x] 7.1 新增字段随 run 落盘；缺字段的历史行读为 unknown，既不失败也不报 0
- [x] 7.2 沿用 ① 的口径版本机制，增加证据边界维度；跨边界或跨口径的 run 标注为不可直接比较
- [x] 7.3 删除 run 时一并清除其持久化的证据内容，含派生缓存
- [x] 7.4 用例：读历史 run、成本趋势排除 unknown 并标注排除原因、删除后查询无残留

## 8. 宿主对照与文档

- [x] 8.1 在宿主环境跑一次对照 dry-run，产出归一化前后的字段覆盖差异清单；据此确认切换不会把大量真实数值换成缺失
  - 静态清单（先前）：`host-field-coverage.md`，逐点核对宿主 span 写入点
  - **运行时实测（2026-09-05，宿主后台 + Phoenix 在线，取 10 条真实 trace）**：静态清单的「持平」结论被推翻。真实故障不在名字而在**交付层** —— Phoenix 的 dataframe 不给 `attributes` 键，旧 `_normalize_spans` 读 `span.get("attributes", {})` 恒得空，实测 **0/1000 条 span 带属性**。即自 0.1.0 起 Aeval 从未读到过任何一个 trace 属性，全部过程指标一路静默为 0；宿主自己那个「一直读到 `""`」的 artifact grader 也是同一根因，不是它单独的问题（已核实宿主接入层不产生合成 span，`spans` 只来自 `PhoenixProvider`；`dispatch` grader 另有自身兜底来源，不在该结论范围内）
  - 教训（已写进 `specs/trace-provider/spec.md` 新 Requirement）：只核对属性名会漏掉「一个名字都送不到」这类故障，静态审计不能替代运行时实测
  - 修复后实测：7/7 过程字段在 10/10 条 trace 上全部可读（turns 2–3、in 5.8k–23.8k、out 132–1972、cache 0–21120），成本通路打通且手算吻合
- [x] 8.2 `docs/integration-guide.md`：映射条目编写方式、`agent_error` 分类契约、脱敏钩子替换示例、单价表配置
- [x] 8.3 `docs/architecture.md` §3/§4：归一化边界、终止原因分类与归类分野、成本并列轴
- [x] 8.4 `docs/grader-reference.md`：证据不可用的结论语义与对分母的影响
- [x] 8.5 `README.md` / `README.zh-CN.md`：撤掉「过程指标偏乐观」的错误诊断，改述为已对齐标准词汇并说明入参默认不采集的代价

## 9. 验证门

- [x] 9.1 `ruff check packages/agent-eval` 通过（All checks passed）
- [x] 9.2 `cd packages/agent-eval && pytest tests/ -q` 全绿（离线，无外部服务与凭据）—— 553 passed
- [x] 9.3 双词汇参数化用例（2.2）与私有字面量静态检查（2.3）均通过
- [x] 9.4 确认本变更未引入 OTel SDK 硬依赖：核心依赖仍为 pydantic/pyyaml/aiosqlite，且屏蔽 `opentelemetry`/`phoenix`/`fastapi`/`typer` 后 `core.runner`、`trace`、`graders` 仍可导入
- [x] 9.5 `openspec validate align-trace-evidence --strict` 通过

## 10. 运行时验收暴露的缺陷与修复

真实 trace 探针（8.1）查出两个静态审查漏掉的缺陷，均已修复并补回归用例。

- [x] 10.1 **provider 从未交付任何属性** —— `PhoenixProvider._normalize_spans` 读 `span.get("attributes", {})`，而 Phoenix 的 dataframe 不给出该键（属性摊平成 `attributes.<name>` 列，且宿主的点号键被按前缀收进一个嵌套 dict）。实测 0/1000 条 span 带属性 → 自 0.1.0 起全部过程指标静默为 0。修复：从摊平列还原点号键（含递归展开嵌套 dict），并把 `status_code` 还原成归一化层期望的形状（此前错误状态判定同样静默失效）
- [x] 10.2 为 10.1 补回归用例 —— 断言摊平列与按前缀嵌套两种形状都能还原出工具名与 token；对应 spec 为 `specs/trace-provider/spec.md` 新增的「后端摊平交付的属性必须还原为契约形状」
- [x] 10.3 **成本对明细桶重复计费** —— `PriceTable.cost_usd` 把 cache_read 与 reasoning 当作与 input/output 并列的第四路相加。真实数据核实：`total = prompt + completion` 在 199/199 条 LLM span 上恒成立，且 `cache_read ≤ prompt`、`reasoning ≤ completion`，即二者是子集。高缓存命中场景下成本被放大 5.4 倍（四价齐全）至 9.3 倍（仅给输入/输出价，走回落档）。而 `gen_ai.usage.cache_read.input_tokens` 本就在默认映射里，任何开启 prompt 缓存的标准埋点都会立刻中招
- [x] 10.4 修复为子集口径（先从父桶扣出再各自计价），新增 `detail_semantics="disjoint"` 作为 Anthropic 风格 provider 的显式逃生口，明细大于父桶时报 `detail_tokens_exceed_parent` 不可计算而不夹取；三个用例分别锁定子集、disjoint、矛盾口径
- [x] 10.5 宿主侧映射按实测词汇修正 —— token/模型只有 OpenInference 名有值（`llm.token_count.*`，199/199），`agenthub.input_tokens` 一类实测 0/1000 仅作兜底；候选首位改为有数据者，版本升至 `agenthub-2`
- [ ] 10.6 宿主 venv 仍是 PyPI 的 `aeval-framework 0.1.0`（`agent_eval.trace.mapping` 不存在）。升级后需在同一 suite 上复跑一次探针与 `run_first_suite.py`，把本文 8.1 的数字换成「一次完整评测」而非「历史 trace 取样」
