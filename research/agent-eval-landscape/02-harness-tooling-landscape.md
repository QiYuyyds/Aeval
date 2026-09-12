# 02 · 工具与 Harness 全景：2025–26 谁在做 eval、怎么做

> 快照日期：2026-09-07。来源明细见 [07-sources.md](./07-sources.md)。
> 本文回答"别人用什么评"；基准侧见 [01](./01-benchmark-landscape.md)。

## 1. 通用 harness / 框架

- **Inspect AI**（UK AISI）— 安全向 agent eval 的事实开源标准（samples/solvers/scorers 组合成 Tasks）。2025 年新增：**Checkpointing**（崩溃的 agent run 断点续跑）、**Intervention**（运行中人工介入、向 agent 发消息）、**Inspect Cyber**（标准化的 agentic 网络安全评测），以及策展任务库 inspect_evals。
  [站点](https://inspect.aisi.org.uk/) · [Agents 文档](https://inspect.aisi.org.uk/agents.html) · [Inspect Cyber](https://www.aisi.gov.uk/blog/inspect-cyber) · [inspect_evals](https://github.com/UKGovernmentBEIS/inspect_evals)
- **Harbor**（Laude Institute）— 从 Terminal-Bench 生长出的通用 harness：跑基准容器/agent，任务镜像经 registry 分发（`harbor run -d <benchmark>@<version>`）。
  [文档](https://www.harborframework.com/docs/tutorials/running-terminal-bench) · [GitHub](https://github.com/harbor-framework/terminal-bench-2)
- **OpenAI Evals 平台** — 托管 evals API 围绕 **datasets → traces → graders → eval runs** 组织，并配有专门的 agent 工作流指南；这三篇文档（evals / 评测最佳实践 / agent evals）正在成为各家抄的平台形态。**注意：trace→grader 正是 Aeval 的核心架构假设，行业收敛到它 = 方向被验证。**
  [Agent evals 指南](https://developers.openai.com/api/docs/guides/agent-evals) · [Evals 指南](https://developers.openai.com/api/docs/guides/evals) · [最佳实践](https://developers.openai.com/api/docs/guides/evaluation-best-practices) · [openai/evals](https://github.com/openai/evals)
- **Anthropic 工程指南** — "Demystifying evals for AI agents"：transcript 与 outcome 分开、组合多类 grader（groundedness/rubric/human）、实践 eval 驱动开发（先写 eval 再写能力）。
  [指南](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) · [Writing effective tools](https://www.anthropic.com/engineering/writing-tools-for-agents)
- **lm-evaluation-harness**（EleutherAI）— 学术基准的主力（60+ 任务，v0.4 支持多模态；2025-11 仍在发 v0.4.9.x）——但**不面向 agent**。
  [GitHub](https://github.com/EleutherAI/lm-evaluation-harness) · [Releases](https://github.com/EleutherAI/lm-evaluation-harness/releases)
- **HELM**（Stanford CRFM）— 从单一巨型榜单转向家族化：HELM Capabilities（2025-03）、MedHELM（121 个临床医生校验的任务）、HELM Lite 作为"活基准"。
  [站点](https://crfm.stanford.edu/helm/) · [MedHELM](https://crfm.stanford.edu/helm/medhelm/latest/)

## 2. 可观测性优先的 eval 平台

| 平台 | 定位 | 2025–26 的差异化动作 |
|------|------|---------------------|
| **LangSmith**（LangChain） | Agent 实验/标注队列/judge 数据集 | 生产监控 for agents；发布 CI 区间指引（噪声 run 的就绪清单）[监控](https://www.langchain.com/blog/production-monitoring) · [judge 指南](https://www.langchain.com/resources/llm-as-a-judge) |
| **Braintrust** | "主动式可观测" | **Loop**：AI agent 自动生成 prompt/scorer/数据集；CI 故事强 [站点](https://www.braintrust.dev/) · [文档](https://www.braintrust.dev/docs/evaluate) |
| **Arize Phoenix** | 开源 OTel 原生观测 + evals | agent 评测器（工具调用/规划/目标达成）；发布被广泛引用的 **human-LLM judge 对齐（Krippendorff's α）方法论** 与 CI/CD for LLM apps 指南 [GitHub](https://github.com/arize-ai/phoenix) · [α 方法论](https://arize.com/blog/measuring-human-llm-judge-alignment/) · [CI/CD 指南](https://arize.com/resources/llm-evaluation/ci-cd-for-llm-apps/) |
| **Langfuse** | 开源 LLM 工程平台 | traces+evals+prompt 管理；OpenAI Cookbook 评测 Agents SDK 的参照栈 [站点](https://langfuse.com/) · [Cookbook 示例](https://developers.openai.com/cookbook/examples/agents_sdk/evaluate_agents) |
| **W&B Weave** | evals/tracing 融入 W&B 生态 | 成本/时延看板与 judge scorer 强 [Weave](https://wandb.ai/site/weave) |
| **Opik**（Comet） | 开源、框架无关 | 明确瞄准 agentic 系统 [GitHub](https://github.com/comet-ml/opik) |
| **Galileo** | eval 驱动的 CI | 回归测试、漂移检测、运行时护栏 [CI 基础](https://galileo.ai/blog/continuous-integration-ci-ai-fundamentals) |

## 3. 测试框架风格的开源库

- **DeepEval**（Confident AI）— 50+ 研究背书的指标（RAG/agent/多轮），pytest 风格 `assert_test` 做 CI 门禁。[CI 文档](https://deepeval.com/docs/evaluation-unit-testing-in-ci-cd) · [对比文](https://deepeval.com/blog/deepeval-alternatives-compared)
- **promptfoo** — YAML 声明测试矩阵 + 红队 + CI 门禁；**与 Aeval 的 YAML 套件形态最接近的开源同类**。[站点](https://www.promptfoo.dev/) · [vs DeepEval](https://qaskills.sh/blog/promptfoo-vs-deepeval-2026)
- **RAGAS** — RAG 专用指标，现在更多作为 agent 框架的配套而非独立方案。[文档](https://docs.ragas.io/)
- **MLflow GenAI** — 加入 agent 评测 + OTel GenAI-semconv tracing，并发布 agent 基准化协议指引——**又一个 eval 工具收敛到 OTel 的信号**。[Top-5](https://mlflow.org/top-5-agent-evaluation-frameworks/) · [基准化协议](https://mlflow.org/articles/benchmarking-ai-agent-performance/) · [semconv 文档](https://mlflow.org/docs/latest/genai/tracing/opentelemetry/genai-semconv/)

## 4. 与 Aeval 的横向对比

> Aeval 侧的判断依据均来自本仓库代码（见 [04-aeval-coverage-matrix.md](./04-aeval-coverage-matrix.md) 的代码证据列）。

| 维度 | Inspect AI | Harbor | OpenAI Evals | 商业平台（LangSmith/Braintrust/…） | DeepEval/promptfoo | **Aeval** |
|------|-----------|--------|--------------|-----------------------------------|--------------------|-----------|
| 声明式套件 | Python Tasks | 容器任务 | 托管数据集 | 平台 UI + SDK | YAML ✓ | **YAML ✓ 严格校验 ✓** |
| trace 驱动评分 | 部分 | ✗ | ✓（平台模板） | ✓ | ✗ | **✓（归一化观测 + 词汇预设）** |
| 证据溯源（谁观测的） | ✗ | ✗ | 平台内部 | 部分 | ✗ | **✓ 独有（observed_by 三级）** |
| pass@k / pass^k / CI | 部分 | 部分 | 部分 | 部分 | ✗ | **✓ 无偏估计 + Wilson/bootstrap** |
| 采集/评分分离 + regrade | ✗ | ✗ | 平台内部 | 部分 | ✗ | **✓ 独有（grade_attempts 追加）** |
| 成本/时延一等轴 | 部分 | ✗ | ✓ | ✓ | 部分 | **✓（四路 token + 诚实成本）** |
| 容器/沙箱 | ✓ | ✓（核心） | 托管 | 云端 | ✗ | **✗（明确不做，无参照实现）** |
| 用户模拟器/双控 | ✗ | ✗ | ✗ | ✗ | ✗ | **✗** |
| 运行中人工介入 | ✓（Intervention） | ✗ | ✗ | 标注队列 | ✗ | **✗（只有结束后 human grader）** |
| checkpoint/续跑 | ✓ | ✓ | 托管 | ✗ | ✗ | **✗（只有 TransientError 重试）** |
| 安全评测内容 | ✓（Cyber/AgentHarm 等） | ✗ | ✗ | ✗ | 红队（promptfoo） | **✗（有门机制无内容）** |
| 任务包分发/registry | inspect_evals | ✓（核心） | 托管 | ✗ | ✗ | **✗（仅本地 YAML + examples）** |
| pytest/CI 门禁 | ✗（自有 CLI） | CLI | 托管 | SDK | ✓ | **✓（pytest 插件，仅绝对阈值）** |
| 自托管开源 | ✓ | ✓ | ✗ | 部分开源 | ✓ | **✓（MIT，无服务依赖）** |

## 2025–26 真正新的东西（工具侧）

1. 所有主流平台从"LLM 应用评测"转向 **agent 原生的 trace 评测**（OpenAI 的 trace→grader→run 模型已成模板）。
2. **AI 生成评测物**——LLM 写数据集/scorer（Braintrust Loop），甚至 judge 校准自动化。
3. 安全圈的 harness 级特性出圈成主流：Inspect 的 **checkpointing** 与 **HITL intervention**。
4. **收敛到 OpenTelemetry** 作为 trace 底座（Phoenix/MLflow/Langfuse 一致）。
5. **CI/CD 门禁**成为一等特性（DeepEval/promptfoo/Braintrust/Arize 内建，而非 DIY）——Aeval 的 pytest 插件方向正确但只有半条车道。
6. **YAML/配置声明套件**成为通用创作面（promptfoo/Harbor/Inspect）——直接验证 Aeval 的设计空间。

## 对 Aeval 的直接含义

- Aeval 的差异化（溯源、分母纪律、regrade 审计）在对比表中没有任何一列同时具备——**保持并讲清楚这个故事**。
- 三个"别人的核心、Aeval 没有"的高频能力：容器化（Harbor/Inspect）、checkpoint 续跑（Inspect）、任务包分发（inspect_evals/Harbor）——对应 [05-gap-analysis.md](./05-gap-analysis.md) 的 A/C/D 节。
- 商业平台的"标注队列/生产监控"是云服务形态；Aeval 若不做在线评测平台（反范围，见 [06](./06-optimization-roadmap.md)），至少应把"生产 trace → 离线回放"的链路文档化打通。
