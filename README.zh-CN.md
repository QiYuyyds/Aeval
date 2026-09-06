# Aeval

[English](./README.md) | [简体中文](./README.zh-CN.md)

**Aeval** 是一个由 OpenTelemetry trace 驱动的开源 AI Agent 评测框架。用 YAML 声明评测套件，让 Agent 反复执行（重试 / 并发 / 超时隔离），逐 trial 评分（内置或自定义 Grader），最终聚合为统计上有意义的指标 —— `pass@k` / `pass^k` / 一致性 / 饱和度。

> **命名说明**：本仓库叫 **Aeval**；PyPI 发行名为 **`aeval-framework`**（`pip install aeval-framework`），Python 模块为 **`agent_eval`**，CLI 为 **`eval-suite`**——与 `pip install pillow` → `import PIL` 同理。（PyPI 上的 `agent-eval` 属于另一无关项目。）

## 特性

- **Suite 即 YAML** — 任务、评分器、trial 数与阈值集中在一个声明式文件里，严格校验（semver、task id 唯一）。
- **9 个内置 Grader** — 确定性代码检查、LLM-as-Judge、环境状态检查、工具调用验证、转录分析、产物检查、人工评分（pending 语义）、步骤级评估、指标分发。
- **统计上严谨的聚合** — `pass@k`（能力）与 `pass^k`（可靠性）为有限样本无偏组合估计，且只在 **valid trial** 上计算；`k > n` 一律是二项外推并显式标注；每个通过率附 Wilson 95% 区间，每个分数附 bootstrap 区间与均值/σ/`worst_of_n`，过程指标附 p50/p95。判分器崩溃、判据未配置、judge 不可用等评测侧故障判为 `invalid` 且不进分母，不再被折成 agent 的失败。
- **证据可追溯，缺了就说是缺** — 一次 trial 交付的每一条观测都标明**是谁、在什么时候观测到的**（`observed_by: harness | runner | subject`），因此判据可以被要求「只认评测侧独立取证」，被评方自报的内容不能单独把 trial 判成通过。span 先经一张**版本钉住的 OTel GenAI 翻译表**归一化成词汇无关的标准观测，宿主的私有属性名只是运行时配置而非框架常量；每条结论自陈看到了哪些证据、哪些没看到及原因，读不到的字段是「缺失」而**不是 0**。trial 因何结束由框架判定（超时 / 步数·token·成本触顶 / 报错 / 取消各归其位，预算触顶计未通过但单列，超时走 invalid 不占分母），token 四分解与 `cost_usd` 作为与通过率**并列**的第二轴呈现，单价表取自外部配置。
- **采集与评分是两个相** — 证据在判分之前就按 trial 独立归档，因此对既有 run 重新评分**不会再次调用被评系统**：修好判分器、换掉 judge、收紧证据声明，都能拿同一批字节重判。每次判定都带着产生它的口径追加落盘（判分实现版本、属性翻译表与规范修订、判定模型、时间）并由 `current` 指针指明生效结论 —— **历史结论永不被覆盖**，于是「judge 换代让多少 trial 翻判」是个真能回答的问题。
- **全组件可插拔** — AgentRunner / TraceProvider / Storage / Environment / Grader 都是小型协议，实现即接入；属性翻译表、脱敏钩子与单价表同样可替换（脱敏钩子为整体替换，不叠加默认处理）。
- **REST API 与 SSE** — 可挂载进任意 FastAPI 应用，也可独立服务（`/v1` 前缀），支持运行事件流。
- **CLI** — `eval-suite run / validate / list / show / compare / serve`。
- **数据集与 LLM 指标** — 从 trace 构建数据集、回填套件，并用 RAG 质量指标（answer relevancy / faithfulness / context recall·precision）给输出打分。

## 安装

发布后从 PyPI 安装：

```bash
pip install aeval-framework            # 核心（编排 / 评分 / 存储）
pip install "aeval-framework[api]"     # + REST API 服务
pip install "aeval-framework[cli]"     # + eval-suite 命令行
```

或从源码检出安装（同一份代码，可编辑）：

```bash
pip install -e ./packages/agent-eval               # 核心
pip install -e "./packages/agent-eval[api,cli]"    # + API 服务与命令行
```

导出 trace 到 [Arize Phoenix](https://github.com/Arize-ai/phoenix) 是可选能力：自行安装 `arize-phoenix`，Aeval 会在使用时懒加载。

## 快速开始

`examples/minimal` 使用内置 Mock Agent，完全离线可跑：

```bash
pip install "aeval-framework[cli]"
eval-suite run examples/minimal/suite.yaml
eval-suite list runs
eval-suite show <run_id>
```

套件长这样：

```yaml
name: my-first-suite
version: 1.0.0
tasks:
  - id: simple-qa
    prompt: What files are in the workspace?
    max_trials: 3
    graders:
      - type: code
        name: code_based
        config:
          checks:
            - type: contains
              value: "files"
              target: transcript
```

通过 `eval-suite run --runner` 选项或 `agent_eval.runners` entry-point 注册你自己的 Agent（HTTP 适配示例见 `examples/achat`）。

## Dashboard

Next.js 前端位于 [`apps/dashboard`](./apps/dashboard)（总览 / Suite 管理 / SSE 实时 Run 报告 / Trial 下钻 / A/B 对比）。

## 文档

- [快速开始](./docs/getting-started.md)
- [接入指南](./docs/integration-guide.md)
- [Grader 参考](./docs/grader-reference.md)
- [YAML 格式](./docs/yaml-format.md)
- [CLI 参考](./docs/cli-reference.md)
- [架构](./docs/architecture.md)

## 已知限制

如实说明 v0.1.0 的能力边界：

- **过程证据以「读得到」为前提**：此前把 AChat 上 `transcript` 维度得分偏高归因于「span 覆盖不完整」，这个诊断是错的——真正的原因是框架在读一套宿主私有的 span 属性名。现在取证统一经版本钉住的 OTel GenAI 翻译表归一化，宿主的私有名字只是运行时映射条目；读不到的字段是**带原因的缺失**而不是零值，于是算不出的指标不占分母、也不再把分数刷得好看（缺失会让 `invalid` 占比上升，门禁会因此拒绝信任该 run）。仍然存在的实打实代价：工具入参与结果**默认不采集**，需要参数层面比对的判定在套件 opt-in 之前做不了，opt-in 之后也只以脱敏摘要形式落盘；`cost_usd` 需要外部配置单价表，未配置即报不可计算；`token_budget` / `cost_budget` 的判定精度受 provider 上报用量的及时性限制。
- **RAG 与编排场景正在实战校准**：机制已就绪并通过测试（RAG 场景：`env.agent_id` + `rag_search`；编排场景：`dispatch_mode` + `achat_dispatch`），但尚未在生产规模负载下完成校准。
- **A/B 对比只做到区间重叠判定**：`compare`（CLI / REST / Dashboard）在两个 95% 区间重叠时拒绝给出方向性结论，并把**统计口径**不同或**证据边界**不同（入参采集开关、规范与映射版本、脱敏处理标识）的 run 判为不可比，`not_comparable_reason` 会点名是哪一维；没有证据边界记录的历史 run 按「不可直接比较」处理，而不是假定与今天同边界。它不做假设检验、不校正多重比较、不建模 trial 间相关性 —— 小 `n` 下区间很宽，「不显著」不等于「没有变化」。
- **`pass@1` 语义在本次发布中变更**：现为有效 trial 中的成功比例（`c/n`）的无偏估计，修正前的含义是「n 次里至少成功一次」。**历史 run 不回算** —— 其 `statistics_version` 为 `null`，按「口径未知」呈现。升级后原先被门禁放行的构建可能开始失败（旧门禁会把侥幸与判分故障算成通过），排查指引见[快速上手](./docs/getting-started.md)。同一规则适用于本次新增字段：历史 trial 的终止原因读为 `unknown`，证据边界与资源/成本轴读为 `null`，都不是 0。
- **溯源是「可声明」而不是「可强制」**：一条读数的 `observed_by` 由产出它的一方填写。由框架自己发起的取证 —— 环境管理器的 `probe()`，在 teardown 之前由框架调用 —— 会被钉成 `harness` 级，被评方抬不了这一级；但接入方在自己返回的证据对象里把自报数据标成 `harness`，框架无法从数据本身分辨。本变更真正买到的是：撒谎从此需要显式撒谎（套件里写 `allow_subject: true`、实现里伪造探针），并且这个选择会显示在结论上；真正**强制**这条边界需要环境隔离，而 Aeval 刻意不做（不引容器、不做网络隔离 —— 它是 `pip` 装的开发时工具）。
- **采集与评分分离是破坏性契约变更**：`AgentRunner.run(view, session)` 现在返回 `TrialEvidence` 而不是旧的 `(trace_id, transcript, outcome)` 三元组，且**不留兼容层** —— 可被绕过的分级等于没有分级。两个后果明说不藏：(1) 变更前落盘的 run **可读但不可重评** —— 当时只存了三元组形态的产物，来源分级与证据边界在现场就丢了，用残缺信息重算只会产出另一个错的东西；它们读回时 `evidence = null`、`regradeable = false`、统计口径未知，API/CLI 会给出 `Regrade: no — <原因>` 这一行，且不得与新 run 同口径比较。(2) 重评分本期**只有库层入口**（`EvalRunner.regrade_run` / `verdict_drift`），HTTP 与 CLI 刻意尚未暴露 —— 它们背负同一大版本的响应结构兼容承诺，值得单独评审。
- **`state_check` 不再采信 agent 说了什么**：环境状态的判定优先用评测侧取证、其次用适配层交付的终态；只由自报内容支撑的通过会被判 `invalid`（`subject_only_evidence`），除非套件显式写下 `allow_subject`。过去因为「agent 提到了文件名」而变绿的套件，现在会改为失败或 invalid。这是在消灭一个一直在发的假绿，不是回归 —— 但在套件重读一遍之前，CI 看上去会像是退化了。
- **PostgreSQL 存储为 Phase 3**：当前存储后端为 SQLite。
- **暂无人工评审 UI**：`human` grader 以 *pending* 状态工作、经 REST API 回传分数落定；专门的评审 UI 未包含在 v0.1.0 中。

## 许可

[MIT](./LICENSE)
