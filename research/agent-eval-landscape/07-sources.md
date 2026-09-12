# 07 · 来源汇总

> 调研快照 2026-09-07 的全部独立来源，按类别汇总。正文中的引用均可在此核对。

## 基准：通用 / 动态环境

- GAIA2 + ARE（Meta）：https://ai.meta.com/research/publications/are-scaling-up-agent-environments-and-evaluations/ · https://github.com/facebookresearch/meta-agents-research-environments · https://arxiv.org/pdf/2602.11964 · https://huggingface.co/spaces/meta-agents-research-environments/leaderboard
- Terminal-Bench 2.0 + Harbor：https://www.tbench.ai/news/announcement-2-0 · https://www.harborframework.com/docs/tutorials/running-terminal-bench · https://github.com/harbor-framework/terminal-bench-2 · https://snorkel.ai/blog/terminal-bench-2-0-raising-the-bar-for-ai-agent-evaluation/
- OSWorld-Verified / 2.0：https://xlang.ai/blog/osworld-verified · https://github.com/xlang-ai/osworld · http://osworld-v1.xlang.ai/
- WebArena Verified / WebArena-x：https://webarena.dev/ · https://openreview.net/forum?id=94tlGxmqkN · https://arxiv.org/abs/2307.13854
- TheAgentCompany：https://arxiv.org/abs/2412.14161 · https://the-agent-company.com/ · https://github.com/TheAgentCompany/TheAgentCompany
- Vending-Bench 1/2：https://arxiv.org/abs/2502.15840 · https://andonlabs.com/evals/vending-bench-2 · https://epoch.ai/benchmarks/vending-bench-2 · https://www.anthropic.com/research/project-vend-1
- AgentBench FC + AgentRL：https://github.com/THUDM/AgentBench · https://arxiv.org/html/2510.04206v1

## 基准：编程 / SWE

- SWE-bench Pro：https://arxiv.org/abs/2509.16941 · https://labs.scale.com/leaderboard/swe_bench_pro_public
- SWE-bench-Live：https://arxiv.org/html/2505.23419v2
- LiveSWEBench / LiveBench / LiveCodeBench：https://liveswebench.ai/ · https://livebench.ai/ · https://livecodebench.github.io/
- SWE-Lancer：https://openai.com/index/swe-lancer/ · https://arxiv.org/pdf/2502.12115 · https://github.com/openai/swelancer-benchmark
- MLE-bench：https://openai.com/index/mle-bench/ · https://arxiv.org/pdf/2410.07095

## 基准：浏览 / 计算机使用 / 对话

- BrowseComp + MM-BrowseComp：https://arxiv.org/html/2504.12516v1 · https://openai.com/index/browsecomp/
- Humanity's Last Exam：https://agi.safe.ai/ · https://www.nature.com/articles/s41586-025-09962-4
- τ²-bench：https://arxiv.org/abs/2506.07982 · https://github.com/sierra-research/tau2-bench · https://taubench.com/ · https://sierra.ai/resources/research/tau-squared-bench
- METR Time Horizon（含 1.1）：https://metr.org/time-horizons/ · https://arxiv.org/html/2503.14499v3 · https://metr.org/blog/2025-07-14-how-does-time-horizon-vary-across-domains/ · https://metr.org/blog/2026-1-29-time-horizon-1-1/ · https://epoch.ai/benchmarks/metr-time-horizons

## 基准效度 / 元研究

- ABC 清单（"AI Agent Benchmarks are Broken"）：https://arxiv.org/abs/2507.02825 · https://ddkang.substack.com/p/ai-agent-benchmarks-are-broken · https://uiuc-kang-lab.github.io/agentic-benchmarks
- 基准饱和研究：https://arxiv.org/html/2602.16763v1
- 高效基准化：https://arxiv.org/abs/2603.23749
- LLM Agent 评测综述：https://arxiv.org/html/2507.21504v1
- Phil Schmid 基准汇编：https://www.philschmid.de/benchmark-compedium
- 命名辨析（HALO-Bench/HalluBench、AgentArena）：https://gorilla.cs.berkeley.edu/blogs/14_agent_arena.html · https://arxiv.org/html/2504.06468v1

## Harness / 框架

- Inspect AI：https://inspect.aisi.org.uk/ · https://inspect.aisi.org.uk/agents.html · https://www.aisi.gov.uk/blog/inspect-cyber · https://github.com/UKGovernmentBEIS/inspect_evals
- Harbor：https://www.harborframework.com/docs/tutorials/running-terminal-bench · https://github.com/harbor-framework/terminal-bench-2
- OpenAI Evals：https://developers.openai.com/api/docs/guides/agent-evals · https://developers.openai.com/api/docs/guides/evals · https://developers.openai.com/api/docs/guides/evaluation-best-practices · https://github.com/openai/evals
- Anthropic 工程指南：https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents · https://www.anthropic.com/engineering/writing-tools-for-agents
- lm-evaluation-harness：https://github.com/EleutherAI/lm-evaluation-harness · https://github.com/EleutherAI/lm-evaluation-harness/releases
- HELM 家族：https://crfm.stanford.edu/helm/ · https://crfm.stanford.edu/helm/medhelm/latest/ · https://github.com/stanford-crfm/helm

## 可观测性优先平台 / 开源库

- LangSmith：https://www.langchain.com/blog/production-monitoring · https://www.langchain.com/resources/llm-as-a-judge
- Braintrust：https://www.braintrust.dev/ · https://www.braintrust.dev/docs/evaluate · https://www.braintrust.dev/articles/top-5-platforms-agent-evals-2025
- Arize Phoenix：https://github.com/arize-ai/phoenix · https://arize.com/blog/measuring-human-llm-judge-alignment/ · https://arize.com/resources/llm-evaluation/ci-cd-for-llm-apps/
- Langfuse：https://langfuse.com/ · https://developers.openai.com/cookbook/examples/agents_sdk/evaluate_agents
- W&B Weave：https://wandb.ai/site/weave · https://wandb.ai/onlineinference/genai-research/reports/AI-agent-evaluation-Metrics-strategies-and-best-practices--VmlldzoxMjM0NjQzMQ
- Opik：https://github.com/comet-ml/opik · https://www.comet.com/site/products/opik/
- Galileo：https://galileo.ai/blog/continuous-integration-ci-ai-fundamentals
- DeepEval：https://deepeval.com/docs/evaluation-unit-testing-in-ci-cd · https://deepeval.com/blog/deepeval-alternatives-compared
- promptfoo：https://www.promptfoo.dev/ · https://qaskills.sh/blog/promptfoo-vs-deepeval-2026
- RAGAS：https://docs.ragas.io/
- MLflow GenAI：https://mlflow.org/top-5-agent-evaluation-frameworks/ · https://mlflow.org/articles/benchmarking-ai-agent-performance/ · https://mlflow.org/docs/latest/genai/tracing/opentelemetry/genai-semconv/

## Judge 方法学

- 位置偏差：https://arxiv.org/html/2406.07791v9
- 偏差叠加（自我偏好）：https://arxiv.org/abs/2410.21819
- Judge 偏差综述：https://www.sciencedirect.com/science/article/pii/S2666675825004564
- 12 类 judge 偏差实践分类：https://www.channel.tel/blog/llm-judge-12-biases
- Judge 集成/评审团：https://arxiv.org/html/2604.13717v1 · https://arxiv.org/abs/2606.01034 · https://orq.ai/blog/llm-juries-in-practice
- Judge 校准：https://arxiv.org/html/2508.06225v2 · https://arxiv.org/html/2601.03444v1
- Rubrics as Rewards：https://arxiv.org/abs/2507.17746 · https://arxiv.org/abs/2508.12790 · https://cameronrwolfe.substack.com/p/rubric-rl
- Human-LLM 对齐方法论（Arize α）：https://arize.com/blog/measuring-human-llm-judge-alignment/
- κ 校验 judge：https://arxiv.org/html/2510.09738v1 · https://openreview.net/forum?id=DHI9h7Zcni
- 标注平台一致性监控：https://labelstud.io/blog/how-to-use-krippendorff-s-alpha-to-measure-annotation-agreement/

## 统计 / pass^k

- pass@k vs pass^k：https://www.philschmid.de/agents-pass-at-k-pass-power-k · https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents · https://runloop.ai/blog/i-have-opinions-on-pass-k-you-should-too · https://leehanchung.github.io/blogs/2025/09/08/pass-at-k/
- 随机性形式化：https://arxiv.org/html/2512.06710v1
- CI 门禁设计：https://tfsfventures.com/blog/confidence-interval-design-for-ai-agent-evaluation

## 轨迹 / 过程评测

- PRM 综述：https://arxiv.org/html/2510.08049v3
- AgentPRM：https://www.alphaxiv.org/abs/2511.08325
- 工具使用 agent 的 PRM：https://aclanthology.org/2026.findings-acl.602.pdf · https://github.com/RyanLiu112/Awesome-Process-Reward-Models

## 沙箱 / 可复现性

- 三层沙箱：https://modal.com/resources/best-sandboxes-swe-bench-coding-agents · https://northflank.com/blog/how-to-sandbox-ai-agents · https://addozhang.medium.com/ai-agent-code-execution-sandboxes-isolation-from-containers-to-microvms-e80848effea5
- 腾讯 gVisor 百万级：https://gvisor.dev/blog/2026/04/23/scaling-agentic-rl-sandboxes-to-the-millions-with-gvisor-at-tencent/
- SWE-ReX：https://github.com/SWE-agent/swe-rex
- Epoch SWE-bench Docker：https://epoch.ai/latest/swebench-docker
- SWE-MiniSandbox：https://arxiv.org/html/2602.11210v5

## 污染 / canary

- 55 项污染研究综述（GEM 2026）：https://aclanthology.org/2026.gem-main.50/ · https://github.com/lyy1994/awesome-data-contamination
- Canary 发布指南：https://arxiv.org/html/2505.18102v6
- BIG-bench canary 案例：https://www.alignmentforum.org/posts/kSmHMoaLKGcGgyWzs/big-bench-canary-contamination-in-gpt-4
- 抗污染数据集：https://arxiv.org/html/2605.19999v1
- 私有评测批评：https://iclr-blogposts.github.io/2025/blog/risks-private-evals/

## 在线评测 / 成本轴

- 生产监控：https://www.langchain.com/blog/production-monitoring · https://launchdarkly.com/docs/tutorials/when-to-add-online-evals · https://microsoft.github.io/ai-agents-for-beginners/10-ai-agents-production/ · https://www.confident-ai.com/knowledge-base/compare/10-llm-observability-tools-to-evaluate-and-monitor-ai-2026
- 五维企业评测框架：https://arxiv.org/html/2511.14136v1
- AgentRace：https://openreview.net/forum?id=eUuxWAQA5F
- Redis 效率缺口：https://redis.io/blog/ai-agent-benchmarks/
- Artificial Analysis：https://artificialanalysis.ai/

## Agent 安全评测

- InjecAgent：https://arxiv.org/abs/2403.02691
- AgentHarm：https://arxiv.org/pdf/2410.09024 · https://ukgovernmentbeis.github.io/inspect_evals/
- AgentDojo：https://agentdojo.spylab.ethz.ch/
- Agent Security Bench：https://openreview.net/forum?id=V4y0CpX4hK
- OS-Harm：https://arxiv.org/pdf/2506.14866
- Agent-SafetyBench：https://github.com/thu-coai/Agent-SafetyBench
- 动态开放式注入基准：https://arxiv.org/html/2602.03117v1

## 多智能体 / HITL

- 多智能体评测综述（32 篇）：https://www.lesswrong.com/posts/tGcLA596E8g3KnphE/
- AgentRL：https://arxiv.org/html/2510.04206v1
- MAS 回归测试实践：https://bhargavaparvaparv.medium.com/trust-at-scale-regression-testing-multi-agent-systems-in-continuous-deployment-environments-99dfcc5872e9
- A2A 及其降温：https://blog.fka.dev/blog/2025-09-11-what-happened-to-googles-a2a/ · https://discuss.google.dev/t/using-vertex-ai-to-evaluate-an-example-a2a-agent/194032
- Inspect Intervention（Intervention API 见 Agents 文档）：https://inspect.aisi.org.uk/agents.html

## Trace 标准

- OTel GenAI semconv：https://opentelemetry.io/docs/specs/semconv/gen-ai/ · https://particula.tech/blog/opentelemetry-genai-semantic-conventions-stable · https://john-hodge.com/blog/opentelemetry-genai-semantic-conventions/
- OpenInference：https://github.com/Arize-ai/openinference
- AG-UI：https://github.com/ag-ui-protocol/ag-ui · https://docs.ag-ui.com/introduction

## EDD / CI 门禁

- Anthropic（EDD）：https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents
- Future AGI（EDD 2026）：https://futureagi.com/blog/what-is-eval-driven-development-2026/
- MLflow（AI 工作流集成 2026）：https://mlflow.org/articles/integrating-evaluation-into-ai-workflows-2026-guide/
- Fireworks（Claude Code 实践）：https://fireworks.ai/blog/eval-driven-development-with-claude-code
- 两车道 CI：https://getautonoma.com/blog/how-to-run-llm-evals-in-ci-cd · https://ai-tldr.dev/learn/evaluation-safety/evaluation-basics/llm-eval-ci-cd-pipeline/ · https://nhimg.org/articles/cicd-evals-are-becoming-the-control-plane-for-llm-quality/

---

**方法学说明**：以上均为 2026-09-07 可访问的公开来源；arXiv 编号以 25xx/26xx 开头者对应 2025/2026 年论文。若引用时链接失效，可按论文标题在 arXiv/OpenReview 检索。调研过程中对两个待验证名称做了辨析并排除（"HaloBench"、"AIRIS"——均非真实存在的主流基准，见 [01](./01-benchmark-landscape.md) 与 [03](./03-methodology-themes.md) 的命名辨析）。
