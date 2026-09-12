# 01 · 基准全景：2025–26 的 Agent Benchmark 在测什么

> 快照日期：2026-09-07。来源明细见 [07-sources.md](./07-sources.md)。
> 本文回答"行业在评什么"；工具侧见 [02](./02-harness-tooling-landscape.md)，方法学见 [03](./03-methodology-themes.md)。

## 总览：三条演化主线

```
 2023–24 静态 QA 型            2025 动态环境型              2025–26 现实效度型
 ─────────────────           ─────────────────           ─────────────────
 单轮问答/固定网页            异步事件、双控协同           污染防护、基准审计
 结果对错即可判分             动作级验证、过程评分          经济/时间作头牌指标
 (AgentBench v1, WebArena)   (GAIA2/ARE, τ²-bench)      (SWE-bench Pro, ABC)
```

## 1. 通用 / 动态环境

- **GAIA2 + ARE**（Meta，2025-09）— GAIA 后继；800+ 场景跑在模拟手机世界（"Mobile"）里，特点是**动态、异步事件 + 动作级验证**（检查 agent 做了哪些动作，而非只看终态）；报告 pass@1 及搜索/执行/适应性子分。是对静态、易污染 QA 型基准的直接否定。
  [发布页](https://ai.meta.com/research/publications/are-scaling-up-agent-environments-and-evaluations/) · [GitHub](https://github.com/facebookresearch/meta-agents-research-environments) · [论文](https://arxiv.org/pdf/2602.11964) · [榜单](https://huggingface.co/spaces/meta-agents-research-environments/leaderboard)
- **Terminal-Bench 2.0 + Harbor**（Stanford/Laude Institute，2025-11）— 更难、人工复核的终端任务（89–100+：DevOps、生物信息、安全）；经 **Harbor registry** 分发（`harbor run -d terminal-bench@2.0`）；亮点是**过程级里程碑评分**与结果检查并存。
  [公告](https://www.tbench.ai/news/announcement-2-0) · [Harbor 文档](https://www.harborframework.com/docs/tutorials/running-terminal-bench) · [GitHub](https://github.com/harbor-framework/terminal-bench-2) · [Snorkel 解读](https://snorkel.ai/blog/terminal-bench-2-0-raising-the-bar-for-ai-agent-evaluation/)
- **OSWorld-Verified → OSWorld 2.0**（XLANG，2025-07→）— 先对原版 OSWorld 的缺陷任务配置/检查器做就地修复（"Verified"）并云上并行评测；2.0 转向**小时/天级长时程桌面工作流**。
  [博客](https://xlang.ai/blog/osworld-verified) · [GitHub](https://github.com/xlang-ai/osworld)
- **WebArena Verified / WebArena-x**（2025–26）— 社区对 WebArena 的再评测：保留容器化环境、修复测量信度；官网已转向"在演化环境中持续、可扩展的评测"。
  [WebArena-x](https://webarena.dev/) · [Verified (OpenReview)](https://openreview.net/forum?id=94tlGxmqkN) · [原论文](https://arxiv.org/abs/2307.13854)
- **TheAgentCompany**（CMU，2024-12；NeurIPS 2025）— 模拟软件公司（RocketChat/GitLab/ownCloud），测有后果的办公室工作；已成"数字工人"参照基准，常用于长时程端到端测量。
  [论文](https://arxiv.org/abs/2412.14161) · [站点](https://the-agent-company.com/) · [GitHub](https://github.com/TheAgentCompany/TheAgentCompany)
- **Vending-Bench 2**（Andon Labs，2025-11）— 长期一致性基准（经营模拟自动售货机生意一年）；V2 弃用 V1，强调多月连贯性与净值结果；Anthropic 真实世界 "Project Vend" 的姊妹篇。
  [V1 论文](https://arxiv.org/abs/2502.15840) · [V2](https://andonlabs.com/evals/vending-bench-2) · [Epoch 条目](https://epoch.ai/benchmarks/vending-bench-2) · [Project Vend](https://www.anthropic.com/research/project-vend-1)
- **AgentBench FC + AgentRL**（THUDM，2025-10）— AgentBench 以**函数调用**基准重发并全容器化部署，同时整合进 agentic-RL 训练框架——**benchmark 与训练环境正在合流**。
  [GitHub](https://github.com/THUDM/AgentBench) · [AgentRL](https://arxiv.org/html/2510.04206v1)

## 2. 编程 / SWE

- **SWE-bench Pro**（Scale AI，2025-09）— 1,865 题来自 41 个活跃商业/企业仓库；分公开/商业/**held-out 私有**三档；GPT-5 在商业子集仅 ~23–36%。这是对 SWE-bench Verified 饱和（头部模型 70–80%）与污染的旗舰回应。
  [论文](https://arxiv.org/abs/2509.16941) · [榜单](https://labs.scale.com/leaderboard/swe_bench_pro_public)
- **SWE-bench-Live**（2025-05）— 从训练截止后的新仓库 issue 持续滚动构建；**抗污染与可复现是设计目标**。
  [论文](https://arxiv.org/html/2505.23419v2)
- **LiveSWEBench / LiveCodeBench / LiveBench** — "活基准"家族：滚动上新（LiveBench 约每 6 个月；LiveCodeBench 收割赛后新题），把污染变成可测量属性。
  [LiveSWEBench](https://liveswebench.ai/) · [LiveBench](https://livebench.ai/) · [LiveCodeBench](https://livecodebench.github.io/)
- **SWE-Lancer**（OpenAI，2025-02）— 1,400+ 真实 Upwork 任务、总价值 $1M；按**赚到的美元**计分，另含端到端经理决策任务——经济价值成为计分轴。
  [OpenAI](https://openai.com/index/swe-lancer/) · [论文](https://arxiv.org/pdf/2502.12115) · [GitHub](https://github.com/openai/swelancer-benchmark)
- **MLE-bench**（OpenAI，2024-10）— 75 个 Kaggle 竞赛作为离线 ML 工程环境，按官方 Kaggle 规则评分；2025–26 仍是 ML-agent 标准基准。
  [OpenAI](https://openai.com/index/mle-bench/) · [论文](https://arxiv.org/pdf/2410.07095)

## 3. 网页浏览 / 计算机使用

- **BrowseComp + MM-BrowseComp**（OpenAI，2025-04）— 1,266 道需持续多跳浏览的题，答案刻意深埋；DeepResearch 型 agent 把成绩从 <10% 拉到 ~50%+。"深研"类基准的定音之作。
  [论文](https://arxiv.org/html/2504.12516v1) · [OpenAI](https://openai.com/index/browsecomp/)
- **Humanity's Last Exam**（Scale AI/CAIS，2025；Nature 论文）— ~2,500 道专家手写、带 canary 保护的前沿题，覆盖 100+ 学科；现常以 agentic/工具使用模式运行。
  [站点](https://agi.safe.ai/) · [Nature](https://www.nature.com/articles/s41586-025-09962-4)

## 4. 对话 / 工具使用 / 协同

- **τ²-bench**（Sierra，2025-06）— 在 τ-bench 上加**双控电信域**：agent 与模拟用户**各持工具、必须协同**（如共同调试路由器）；另加语音支持与 banking_knowledge 域；已被广泛采用（Artificial Analysis 跟踪 36+ 模型）。
  [论文](https://arxiv.org/abs/2506.07982) · [GitHub](https://github.com/sierra-research/tau2-bench) · [站点](https://taubench.com/)
- **METR Time Horizon**（+ Time Horizon 1.1，2026-01）— 不是任务集而是派生指标：agent 能以 50%/80% 成功率完成的人类专家用时当量；50%-horizon 约每 7 个月翻倍（且在加速到 ~4 个月）；TH 1.1 把方法学延展到 2025–26 前沿模型。已是标准的"能力天花板"轴。
  [Time horizons](https://metr.org/time-horizons/) · [原论文](https://arxiv.org/html/2503.14499v3) · [TH 1.1](https://metr.org/blog/2026-1-29-time-horizon-1-1/) · [Epoch 跟踪](https://epoch.ai/benchmarks/metr-time-horizons)

## 5. 基准效度 / 元研究

- **"AI Agent Benchmarks are Broken" + ABC 清单**（UIUC Kang lab，2025-07）— 证明任务规范缺陷能让模型"什么都不做也赢"（τ-bench airline 例）；提出基准作者的 **Agentic Benchmark Checklist**，引发 2025 年的基准审计浪潮。
  [论文](https://arxiv.org/abs/2507.02825) · [作者博客](https://ddkang.substack.com/p/ai-agent-benchmarks-are-broken) · [项目页](https://uiuc-kang-lab.github.io/agentic-benchmarks)
- **基准饱和系统性研究**（2026）— 分析 60 个基准：公开基准比私有 held-out 集饱和得更快（记忆化）。[arXiv 2602.16763](https://arxiv.org/html/2602.16763v1)
- **高效基准化**（2026）— 小任务子集可以以低成本保持 agent 排名——统计子集选择正在成为 harness 特性。[arXiv 2603.23749](https://arxiv.org/abs/2603.23749)
- **LLM Agent 评测综述**（2025-07）— 领域标准综述；明确把"跨重复运行的一致性"与"过程评测"列为开放问题。[arXiv 2507.21504](https://arxiv.org/html/2507.21504v1)
- 参考：Phil Schmid 的 Agent Benchmark Compendium（50+ 基准分类）。[链接](https://www.philschmid.de/benchmark-compedium)

> 命名辨析：截至 2026-09 不存在名为 "HaloBench" 的重要通用基准（只有纳米光子学领域的 HALO-Bench 和幻觉方向的 HalluBench）；"AgentArena" 指 Berkeley Gorilla 的[平台](https://gorilla.cs.berkeley.edu/blogs/14_agent_arena.html)与 [arXiv 2504.06468](https://arxiv.org/html/2504.06468v1)，并非单一旗舰基准。

## 2025–26 真正新的东西（基准侧）

1. 从静态 QA 转向**动态、异步、动作验证的环境**（GAIA2/ARE）。
2. **held-out 私有拆分与滚动上新**成为默认抗污染姿态（SWE-bench Pro / SWE-bench-Live / Live 家族）。
3. **Verified/修复版任务集**以补丁形式发布（OSWorld-Verified、WebArena Verified、Terminal-Bench 2.0）——基准自身也会"发版"。
4. **里程碑/过程评分**进入旗舰基准（Terminal-Bench 2.0）。
5. **经济与时间轴**（赚到的美元、人类时间当量）成为头牌指标。
6. **基准自身的效度审计**成为正式方法（ABC 清单、饱和研究、污染综述）。

## 对 Aeval 的直接含义

- 行业在评的**交互形态**（异步事件、双控用户、长时程）超出 Aeval 当前"单轮静态 prompt"的任务模型——这是覆盖空白，详见 [05-gap-analysis.md](./05-gap-analysis.md) 的 A 节。
- **里程碑评分**与 Aeval 的 step-level grader / 轨迹指标方向一致，可借鉴 Terminal-Bench 2.0 的"里程碑清单"表达法。
- **抗污染姿态**（canary、私有拆分、滚动集）是 Aeval 若要承载"公开任务包分发"时必须补的卫生层，详见 [05-gap-analysis.md](./05-gap-analysis.md) 的 D 节。
