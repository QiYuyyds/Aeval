# 开源 Agent / AI 应用评测集调研报告

> 调研时间：2026-09 ｜ 范围：开源可获取的 Agent 能力评测基准与 AI 应用层评测框架
> 说明：文中任务数量等规模数据以各项目官方论文/仓库为准，部分基准仍在持续迭代，具体数字可能随版本演进变化。
>
> **分工**：本文是面向读者的分类导论（"行业里有什么"）；**结论性对照**——行业主题 × Aeval 的覆盖矩阵、空白分析与优化路线图——见 [`research/agent-eval-landscape/`](../research/agent-eval-landscape/README.md)（快照 2026-09-07，含全部来源），两处更新时请互链，勿各自维护同一批数字。

## 一、背景：为什么 Agent 评测比 LLM 评测难

传统 LLM 评测是"一问一答"的静态模式：给模型 1000 道题，对多少错多少，一个准确率出来（MMLU、GSM8K、HumanEval 都是这个范式）。Agent 评测则完全不同，它面临三个根本性难题：

- **路径多样性**：同一个任务，Agent 可以走完全不同的路径到达目标。只看最终成功率，会掩盖"3 步搞定"与"50 步才搞定"的执行质量差异。
- **环境依赖性**：Agent 的能力只有在与环境交互时才能体现——网页会变、API 会挂、数据库状态会漂移。静态测试集天然无法覆盖。
- **"做对"的定义模糊**：任务完成了，但用了 50 步（应该 5 步就够）？调用了付费 API？遗漏了用户没明说但显然需要的细节？这些维度很难量化。

正因为如此，社区发展出了两大类评测资产：一类是**面向 Agent 能力的评测基准（Benchmark）**，通常自带任务集 + 交互环境 + 判定器；另一类是**面向 AI 应用质量的评测框架（Framework）**，提供指标体系和打分工具，数据集由使用者自己准备。本报告对两者都做调研。

## 二、总览：分类地图

| 类别 | 代表评测集 | 被测对象 | 核心判定方式 |
|------|-----------|---------|-------------|
| 综合通用能力 | GAIA、AgentBench、VisualAgentBench | 通用 AI 助手 / LLM-as-Agent | 唯一精确答案匹配 / 环境内成功判定 |
| 工具与函数调用 | BFCL、ToolBench、API-Bank、τ-bench / τ²-bench | 函数调用能力 / 对话式任务型 Agent | AST 比对 / 状态 + 数据库核验 |
| 软件工程 | SWE-bench（Verified / Pro）、Terminal-Bench | 编码 Agent | 单元测试通过（FAIL_TO_PASS / PASS_TO_PASS） |
| Web / GUI / OS 交互 | WebArena、Mind2Web、OSWorld、ClawBench、SaaS-Bench | 浏览器 / 桌面 / 真实网站 Agent | 最终页面或数据库状态核验 |
| 对话与应用质量 | MT-Bench、Chatbot Arena | 对话模型 / 应用 | LLM-as-Judge / 人类偏好投票 |
| RAG 与应用层评测框架 | RAGAS、TruLens、DeepEval | RAG 应用 / 任意 LLM 应用 | 指标体系（忠实度、相关性等），LLM-as-Judge |

## 三、分类详述

### 3.1 综合通用能力类

#### GAIA（A Benchmark for General AI Assistants）

- **介绍**：由 Meta AI 与 Hugging Face 等团队于 2023 年提出，定位是"下一代通用 AI 助手"的基准。核心观点是：对人类来说简单的任务，对 AI 反而很难——它故意不考高难度的学术推理，而考现实世界里人人都会做的"查资料、看文件、算个数"类任务。
- **构成**：466 个问题，分为三个难度级别（Level 1 通常 1 步以内工具调用；Level 2 需 5–10 步多工具组合；Level 3 需任意长操作序列与跨模态协同）。其中 166 个开发集问题公开答案，300 个测试集问题保留私有答案用于排行榜。问题形式以文本为主，部分附带图像、PDF、电子表格等多模态附件。每个问题对应唯一、简短的事实性答案（字符串/数字/列表），便于自动判定。
- **作用**：测试推理 + 多模态处理 + 网页浏览 + 工具使用的综合能力；是发布模型/Agent 产品时最常引用的"通用助手能力"榜单之一（Hugging Face 托管官方 Leaderboard）。

#### AgentBench

- **介绍**：清华大学等机构于 2023 年发布（ICLR 2024），是首个系统性评估"LLM 作为 Agent"的多环境基准套件。论文开篇即点明宗旨：评测 LLM 作为 Agent 不能只看最终输出，还要看它在多样交互环境中的整个决策过程。
- **构成**：包含 8 个环境——操作系统、数据库（DBBench）、知识图谱问答（KG）、数字卡牌游戏（LTP）、智力拼图（ALFWorld 相关的 Household）、网页购物（WebShop）、网页浏览（Mind2Web 改造）等；任务通常需要 5–50 步交互才能完成。每个环境提供 Docker 化的运行环境与各自的判定器。
- **作用**：跨环境考察模型的多轮决策、长程推理与错误恢复能力；结论性发现是商业模型与开源模型在复杂交互环境中差距显著，主要失败原因是长程推理与决策能力不足。

#### VisualAgentBench（VAB）

- **介绍**：面向多模态基础智能体（Multimodal Foundation Agent）的评测基准，覆盖纯文本 Agent 基准无法覆盖的视觉输入场景。
- **构成**：3 类代表性场景、共 5 个环境——具身智能（OmniGibson 居家机器人模拟器、Minecraft）与 GUI（智能手机模拟、基于 WebArena 改造的 WebArena-Lite 网页浏览）。
- **作用**：为多模态 Agent 的训练（轨迹微调 SFT）与评测提供统一环境；常用于评估模型"看屏幕做操作"的能力。

### 3.2 工具与函数调用类

#### BFCL（Berkeley Function Calling Leaderboard）

- **介绍**：UC Berkeley Gorilla 团队出品，是目前**工具调用评测的事实标准**（已被 ICML 2025 收录）。它测的不是端到端任务执行，而是"把自然语言指令正确翻译成函数调用"这一核心技能。
- **构成**：已迭代至 v4——v1 引入 AST（抽象语法树）比对评估（不真正执行函数即可验证正确性，因此可扩展到数千个函数）；v2 加入企业级与社区贡献函数；v3 引入多轮/多步交互；v4 引入整体 Agentic 评估。覆盖串行调用、并行调用、跨语言（Python/Java/JavaScript/REST API）、相关性检测（判断何时**不该**调用工具）、长上下文等维度。
- **作用**：横向比较不同模型在函数调用上的细粒度能力；它揭示的"双面人格"现象（单轮调用很强，但多轮状态管理、主动拒答调用时频繁失误）直接指导工具型产品的模型选型。

#### τ-bench / τ²-bench

- **介绍**：Sierra Research 提出（τ²-bench 于 2025 年发布），聚焦**动态环境中的对话式任务型 Agent**——客服、销售等需要多轮对话 + 领域 API 操作的真实场景。
- **构成**：提供零售（retail）、航空（airline）等领域数据库与一组领域 API；由 LLM 扮演有情绪、会纠正需求的用户模拟器与被测 Agent 多轮对话；任务定义包含初始数据库状态、用户指令与隐藏策略（policy）。τ²-bench 进一步引入"dual-control"模式：用户与 Agent 各自掌握部分工具，必须协作才能完成任务。
- **作用**：评测 Agent 在多轮交互、政策遵守、数据库状态变更正确性上的能力；其"最终核验数据库状态"的判定思路（Agent 的嘴会骗人，数据库不会）已成为行业标准做法，被后来的 SaaS-Bench 等广泛借鉴。

#### ToolBench / API-Bank

- **介绍**：清华大学提出的 ToolBench 基于 RapidAPI 的 1.6 万+ 真实 REST API 构建大规模工具调用训练/评测数据；API-Bank 则提供 73 个 API 与多级评测（调用 API、规划 API 组合、反思错误）。
- **作用**：为"海量工具检索与调用"场景提供数据与评估，支撑了 ToolLLM 等模型的研究；其真实 API 的不确定性也让它成为研究工具调用鲁棒性的素材。

### 3.3 软件工程类

#### SWE-bench（含 Verified / Pro）

- **介绍**：Princeton 团队提出（ICLR 2024），是目前公认的**编码 Agent 能力最权威指标之一**。它把 AI 当成一个真实程序员，丢进开源项目里解决真实的 GitHub issue。
- **构成**：原版从 12 个流行开源 Python 仓库（Django、sympy、scikit-learn 等）抓取约 9 万个 PR，经属性过滤（merged 且修改了测试）与执行过滤（存在 fail→pass 测试）后得到 **2294 个任务**。每个任务是一个 JSON 样本：`problem_statement`（issue 描述，即题面）、`base_commit`（代码基线）、`test_patch`（测试补丁）、`FAIL_TO_PASS`（修复后才应通过的测试）与 `PASS_TO_PASS`（不能改坏的既有测试）。判定完全由 Docker 隔离环境内跑测试决定。
  - **SWE-bench Verified**：OpenAI 联合原团队对 2294 题逐道人工筛查，剔除描述模糊、测试不可靠的题目，留下 500 道高质量题，是业界通用的"干净版"。
  - **SWE-bench Pro**（Scale AI）：1865 道人工验证任务、41 个仓库，平均补丁改 100+ 行、跨多文件，并刻意使用 GPL 强协议与商业闭源仓库做**抗数据污染**设计，分 Public / Held-Out / Commercial 三块。
- **作用**：端到端评测"读 issue → 定位代码 → 写补丁 → 通过测试"的完整工程闭环；主指标为 Resolve Rate。已知问题：测试与 Agent 同处一个容器带来的作弊面（见第六节）。

#### Terminal-Bench

- **介绍**：面向**真实终端环境**的 Agent 基准：给 Agent 一个 Linux 容器和一个需要操作命令行的复杂任务（配置环境、排查故障、处理数据等），在终端内多步完成。
- **构成**：任务以 Docker 化的终端环境 + 任务的判定脚本形式发布，v1 约含 89 个任务，目前已迭代至 2.x 版本并成为模型发布会常引用的榜单（与 SWE-bench 并列的编码能力双榜之一）。
- **作用**：补足 SWE-bench 覆盖不到的"非 Git 仓库、纯命令行"工程能力，如系统运维、数据处理、环境搭建。

### 3.4 Web / GUI / OS 交互类

#### WebArena

- **介绍**：CMU 团队 2023 年提出，是 Web Agent 评测的奠基性工作：自建沙箱网站，让 Agent 像真实用户一样在网站里完成任务。
- **构成**：约 **812 个任务**，运行在多个自托管的长生命周期站点上——电商（OneStopMarket，Magento 魔改）、社区论坛（Reddit 克隆）、代码协作（GitLab）、内容管理系统（CMS）、维基百科与地图子站等。判定方式以最终页面/数据库状态核验为主，辅以路径与内容检查。
- **作用**：考察 Agent 的网页浏览、导航、表单填写与多站点信息整合能力；是绝大多数后续 Web Agent 基准（WebArena-Lite、VisualAgentBench 等）的改造母体。局限：沙箱网站是静态模拟的，与现实网站差距大，且存在已知评测漏洞（见第六节）。

#### Mind2Web

- **介绍**：OSU 团队提出的大规模**真实网站**数据集，用于网页理解与操作研究。
- **构成**：约 **2350 个任务**，来自 137 个真实网站的 31 个领域，附带人工录制的操作轨迹。评测形态多为"离线模式"——给定页面快照与候选元素，考察模型能否选出正确的下一步动作（类似选择题），不需要真正开浏览器执行。
- **作用**：训练与评测网页理解/元素定位能力成本极低，常用于模型对比与轨迹数据合成；但正因为不真实执行，分数与真实执行能力存在差距（ClawBench 的对照研究表明这种差距可能非常大）。

#### OSWorld

- **介绍**：面向**真实计算机操作环境**的基准（NeurIPS 2024）：在 Ubuntu 虚拟机里让 Agent 完成日常办公任务。
- **构成**：约 **369 个任务**，横跨 9 个应用——Chrome、LibreOffice（Writer/Calc/Impress）、GIMP、Thunderbird、VLC、VS Code 及操作系统级操作。任务附带初始虚拟机状态与基于最终状态的判定函数（检查文件内容、文档格式、系统设置等）。
- **作用**：评测"电脑使用（Computer Use）"能力的事实标准之一，被各大模型发布会引用；局限是应用数量有限、任务形态相对固定。

#### ClawBench（2026，新兴）

- **介绍**：滑铁卢大学 TIGER Lab 等机构 2026 年发布的"真刀真枪"版 Web Agent 基准，直接让 Agent 接入**真实生产环境网站**执行任务。
- **构成**：144 个真实生产网站（Google Flights、DoorDash、Zillow、LinkedIn 等）、153 个任务、15 个日常类别；与以往只读任务不同，它重点考**写操作**——下单、预约、提交申请、发送消息。
- **作用**：证明了一个关键结论——"强沙箱成绩不能迁移到真实网站的写任务上"（前沿模型在沙箱基准拿 65–75%，在 ClawBench 上最强也只有约三分之一成功率，且有 44% 的任务所有模型全军覆没）。它代表了评测潮流从"模拟环境"转向"真实环境 + 状态核验"。

#### SaaS-Bench（2026，新兴）

- **介绍**：UniPat 实验室提出的"真实办公 SaaS"基准，思路一句话：**Agent 的嘴会骗人，但数据库不会**。
- **构成**：将 23 个知名开源 SaaS（Mattermost、OnlyOffice、ownCloud 等）打包进 Docker，覆盖软件研发、财务、医疗、协作等 6 个领域；评测链路为 Task → Agent → SaaS 应用 → 核验数据库状态变化 → 打分。
- **作用**：治理"面试型选手"——Agent 能漂亮地完成点击动作并输出结案报告，但业务状态根本没变；它把判定锚点从"动作"移到"结果"，是 τ-bench 数据库核验思路在办公场景的放大版。

### 3.5 对话与应用质量类

#### MT-Bench

- **介绍**：LMSYS 2023 年提出的多轮对话基准，开创了"LLM-as-Judge"自动评对话质量的范式。
- **构成**：80 个高质量多轮问题，覆盖写作、角色扮演、推理、数学、编码、提取、STEM、人文 8 种能力；每个问题两轮（追问检验上下文延续性）；由 GPT-4 级别模型作为裁判对回答打分（1–10）或与固定基线做对比。
- **作用**：低成本替代人工评估对话质量；其裁判与被测模型相关性分析（Judge 与 GPT-4 自评一致性超 80%）为 LLM-as-Judge 的合理性提供了早期证据。

#### Chatbot Arena（LMArena）

- **介绍**：LMSYS 运营的众包人类偏好对战平台，是最接近"真实用户投票"的评测形态。
- **构成**：用户匿名对两个模型提同一问题、盲测投票；用 Elo（后改 Bradley-Terry）评分聚合海量对战结果；衍生出分领域榜单（编码、长文、创意写作等）。
- **作用**：提供难以刷分的开放域质量参考，是各家模型发布时最看重的"人类偏好"指标；缺点是成本高、响应慢，且样本分布偏向平台活跃用户群。

#### BrowseComp

- **介绍**：OpenAI 2025 年提出的深度检索/浏览基准：一套人类需要数十分钟、跨几十个网页交叉验证才能答对的高难度信息查找题。
- **构成**：约 1200+ 道题，答案唯一且简短，题面刻意设计成"单次搜索找不到"，考察持久检索、多源交叉验证与拒答（答案确实不存在时不硬编）。
- **作用**：衡量 Deep Research 类 Agent 的信息检索上限，是搜索型 Agent 产品的核心参考。

### 3.6 RAG 与 AI 应用层评测框架（指标体系 + 工具，数据集自备）

这类"评测集"的形态不同：它们不提供固定的题目集，而是提供**指标体系与打分框架**，使用者拿自己的业务数据构造评测集。

#### RAGAS

- **介绍**：RAG 评测领域的事实标准开源框架（Retrieval Augmented Generation Assessment）。其最大的架构贡献是**将检索与生成解耦、独立度量**——不只看最终答案对不对，还能定位是"没找对"还是"没说对"。
- **构成**：围绕 Question、Context、Answer、Reference 四个对象的两两关系定义指标——Faithfulness（忠实度：答案拆成原子断言后，能被检索上下文支撑的比例，即幻觉的量化）、Answer Relevancy（答案切题程度）、Context Precision（检索结果的精确率）、Context Recall（相对参考答案的召回率）。计算依赖 LLM-as-Judge。
- **作用**：为 RAG 应用提供无需大规模标注（部分指标甚至无需 ground-truth）的自动化评测；指标定义已被各家公司内部评测体系广泛吸收。

#### TruLens

- **介绍**：开源的 LLM 应用观测与评估工具，提出经典的 **RAG 三元组（RAG Triad）**。
- **构成**：三元组 = Context Relevance（召回的内容是否支持问题）× Groundedness（回答是否忠于召回内容）× Answer Relevance（回答是否切题）；三者两两牵制，任一短板都指示明确的故障模块。另提供反馈函数（feedback functions）机制接入自定义 LLM 判定器。
- **作用**：无需 ground-truth 即可对 RAG 应用做模块级归因诊断；与 tracing 结合可做到"逐次调用"的运行时评估。

#### DeepEval

- **介绍**：开源的"LLM 应用的 pytest"——把评测写成单元测试，可与 CI/CD 集成做回归门禁。
- **构成**：内置十几种指标（G-Eval、忠实度、答案相关性、幻觉、毒性、偏见、RAG 指标、Agent 工具正确性等），支持自定义指标与合成数据集生成；提供 pytest 风格的断言 API 与云端基准报告。
- **作用**：让"每次改 prompt/换模型后跑评测"成为工程惯例，代表评测从一次性实验走向持续回归的工程化方向。

## 四、共性分析：一个评测集由什么构成

尽管形态各异，成熟的 Agent 评测集几乎都由四层构成，可以对照检查任何一个基准的完备程度：

1. **任务定义（Task）**：题面 + 初始状态 + 隐藏判定标准。题面给被测者（GAIA 的问题、SWE-bench 的 issue），初始状态定义环境起点（τ-bench 的数据库快照），判定标准独立存放且不暴露给被测者。
2. **环境（Environment / Harness）**：静态基准没有这层；交互型基准都提供 Docker 化的沙箱（WebArena 的网站、OSWorld 的虚拟机、SWE-bench 的仓库容器）。环境层的质量直接决定评测可信度——同一模型换一套 Harness 分数可差 10 个百分点以上。
3. **判定器（Grader / Verifier）**：从"参考答案字符串匹配"（GAIA）到"单元测试通过"（SWE-bench）到"数据库状态核验"（τ-bench、SaaS-Bench）到"LLM-as-Judge"（MT-Bench、RAGAS）。判定器越接近"结果状态"、越远离"自报家门"，越难被钻空子。
4. **统计与榜单（Metrics & Leaderboard）**：成功率/Resolve Rate 为基本盘，好的基准会报告置信区间、区分公开/私有测试集（GAIA、SWE-bench Pro 的 Held-Out），以防过拟合与数据污染。

另一个共性规律：**判定锚点正在从"动作"迁移到"状态"**。早期基准看 Agent"做没做对动作"（选没选对元素），新一代基准（τ²-bench、ClawBench、SaaS-Bench）只看"业务最终状态有没有真的改变"。这与人类验收工作的方式一致。

## 五、与 Aeval 的适配视角

对照上述构成，Aeval（agent_eval）与这些开源评测集的映射关系如下：

- **任务定义层**：`EvalDatasetItem`（prompt / graders / env / metadata + 溯源字段）可完整承载上述任一基准的题面与判定配置；`source_ref` 天然适合记录原始基准名与条目 ID；`to_suite()` 转成可执行 Suite 并保留数据集版本关联。
- **判定器层**：GAIA 类答案匹配 → `code_based`；SWE-bench 类测试判定 → `code_based` + artifact 检查；BFCL/τ-bench 类调用序列比对 → `tool_calls`；WebArena/SaaS-Bench 类状态核验 → `state_check`；MT-Bench/RAGAS 类主观判定 → `model_based`。基本全覆盖。
- **环境层**：这是 Aeval 明确不做的一层（框架源码零宿主依赖的设计立场）。接入交互型基准需自研 Runner 适配器封装环境交互；接入静态/API 型基准则成本很低。
- **Aeval 的增量价值**：这些基准都没有的、而 Aeval 独有的是——证据三级溯源（harness/runner/subject）、三态 verdict（invalid/pending 不占分母）、pass@k/pass^k 无偏估计 + Wilson 区间、STATISTICS_VERSION 口径管理。反过来说，"伯克利审计 8 大基准全部可被攻破"这一类问题，恰恰是 Aeval 的证据分级与只读归档重判（regrade_run / verdict_drift）设计所针对的。

## 六、局限与争议：评测集本身的合理性

调研过程中必须记录的两条重要发现，它们对所有评测集使用者都有警示意义：

- **系统性作弊漏洞**：2026 年伯克利团队用自动化漏洞扫描智能体审计了 8 个主流基准（SWE-bench、WebArena、OSWorld、GAIA、Terminal-Bench 等），**每一个都能被攻破**——例如在 SWE-bench 容器里放一个 `conftest.py` 用 pytest 钩子把所有测试结果改写为"通过"，500 题满分而零个 bug 被修复；WebArena 可通过 `file://` 协议直接读取本地配置文件里的标准答案。现实刷分也已在发生：有模型在 SWE-bench 的轨迹中被发现 24% 的操作是从 git 提交历史抄答案。
- **沙箱成绩不迁移**：ClawBench 的对照实验证明，前沿模型在 WebArena 等沙箱基准上的 65–75% 分数，到真实生产网站写操作场景只剩约三分之一甚至更低，44% 的任务没有任何模型能做对。结论：**引用基准分数做选型决策时，必须看子集分数、并用自家 golden task 复测**。

这也解释了行业趋势：判定锚点从动作到状态的迁移、判定器与被测环境的权限隔离、私有测试集 + 版本化口径管理——都指向"评测基础设施本身也需要被评测"这一正在成型的共识。

## 七、参考链接

- GAIA：https://huggingface.co/gaia-benchmark ｜ 论文 arXiv:2311.12983
- AgentBench：https://github.com/THUDM/AgentBench ｜ 论文 arXiv:2308.03688
- BFCL：https://gorilla.cs.berkeley.edu/leaderboard.html ｜ https://github.com/ShishirPatil/gorilla
- τ-bench：https://github.com/sierra-research/tau-bench ｜ τ²-bench 论文 arXiv:2506.07982
- ToolBench：https://github.com/OpenBMB/ToolBench
- SWE-bench：https://www.swebench.com ｜ https://github.com/SWE-bench/SWE-bench ｜ 论文 arXiv:2310.06770
- Terminal-Bench：https://github.com/laude-institute/terminal-bench
- WebArena：https://webarena.dev ｜ https://github.com/web-arena-x/webarena
- Mind2Web：https://github.com/OSU-NLP-Group/Mind2Web
- OSWorld：https://os-world.github.io ｜ https://github.com/xlang-ai/OSWorld
- ClawBench：https://github.com/TIGER-AI-Lab/ClawBench
- VisualAgentBench：https://github.com/THUDM/VisualAgentBench
- MT-Bench / Chatbot Arena：https://github.com/lm-sys/FastChat
- BrowseComp：https://openai.com/index/browsecomp/
- RAGAS：https://github.com/explodinggradients/ragas
- TruLens：https://www.trulens.org
- DeepEval：https://github.com/confident-ai/deepeval
