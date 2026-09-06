"""④ agent 指标目录 — 宽签名 / 取信声明 / judge 轨迹注入 的协议测试。

覆盖 specs/llm-metrics 的三个 Requirement:
- 指标测量接收证据感知的测量上下文 (旧签名不被接受 / 未声明通道不交付)
- judge 指标按声明读取轨迹 (声明即注入, 未声明逐字节等价)
以及 tasks 1.6 的 0.2.0 逐字段等价固化。
"""

import json

import pytest

from agent_eval.core.types import (
    EvidenceKind,
    MeasurementContext,
    MetricEvidenceDeclaration,
    Observation,
    ObservedBy,
    TrialEvidence,
)
from agent_eval.metrics.base import (
    BaseLLMMetric,
    Metric,
    MetricProtocolError,
    MetricResult,
    assert_measurement_signature,
    build_measurement_context,
    metric_declaration,
)
from agent_eval.metrics.llm_judge import judge_user_prompt


def judge_response(score: float, reason: str = "ok") -> str:
    return json.dumps({"score": score, "reason": reason}, ensure_ascii=False)


class RecorderJudge(BaseLLMMetric):
    """记录收到的 (system, user) 并回放预设响应的 judge 指标"""

    name = "recorder_judge"

    def __init__(self, response: str = None, **kwargs):
        super().__init__(**kwargs)
        self._response = response or judge_response(0.9)
        self.prompts: list[tuple[str, str]] = []

    async def measure(self, ctx: MeasurementContext) -> MetricResult:
        user_prompt = judge_user_prompt(
            f"回答: {ctx.actual_output}", ctx, redactor=self.redactor
        )
        self.prompts.append((user_prompt, id(ctx)))
        data = await self._llm_judge("sys", user_prompt)
        return MetricResult(
            name=self.name,
            score=self._score_of(data),
            reason=str(data.get("reason", "")),
            threshold=self.threshold,
        )


class StubLLM:
    def __init__(self, response: str = None):
        self._response = response or judge_response(0.9)
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self._response


def transcript_evidence(
    messages: list[dict] | None = None,
    *,
    harness: list[dict] | None = None,
) -> TrialEvidence:
    evidence = TrialEvidence(trace_id="tr")
    for message in messages or []:
        evidence.transcript.append(
            Observation(
                kind=EvidenceKind.TRANSCRIPT,
                observed_by=ObservedBy.RUNNER,
                value=message,
            )
        )
    for reading in harness or []:
        evidence.harness_state.append(
            Observation(
                kind=EvidenceKind.STATE,
                observed_by=ObservedBy.HARNESS,
                channel="probe",
                value=reading,
            )
        )
    return evidence


TRANSCRIPT = [
    {"role": "user", "content": "什么是退款政策？"},
    {"role": "assistant", "content": "30 天内可退货"},
    {"role": "assistant", "content": "需要订单号"},
]

OLD_SIGNATURE_NOTE = "MeasurementContext"


# ─── 1.6 旧签名不被接受 ──────────────────────────────────────────────────────


class TestSignatureEnforcement:
    def test_old_signature_registration_raises_with_new_shape(self):
        """旧五字符串签名注册即报错, 错误说明新签名形状 (spec: 不得静默降级)"""

        class LegacyMetric(Metric):
            name = "legacy"
            threshold = 0.5

            async def measure(self, input, actual_output, expected_output=None,
                              context=None, retrieval_context=None) -> MetricResult:
                return MetricResult(name=self.name, score=1.0)

        with pytest.raises(MetricProtocolError) as exc:
            assert_measurement_signature(LegacyMetric())
        message = str(exc.value)
        assert OLD_SIGNATURE_NOTE in message
        assert "measure(ctx" in message
        assert "ctx.prompt" in message
        assert "ctx.observations" in message
        # 错误里点名旧形状的参数
        assert "input, actual_output" in message

    def test_two_param_signature_also_rejected(self):
        class HalfLegacy(Metric):
            name = "half_legacy"

            async def measure(self, input, actual_output) -> MetricResult:
                return MetricResult(name=self.name, score=1.0)

        with pytest.raises(MetricProtocolError):
            assert_measurement_signature(HalfLegacy())

    def test_wide_signature_passes(self):
        assert_measurement_signature(RecorderJudge(llm_fn=StubLLM()))

    def test_registry_rejects_legacy_at_runner_assembly(self):
        """runner 组合根同样拒绝 (装配期, 不等到第一次判分)"""
        from agent_eval.core.runner import EvalRunner
        from agent_eval.examples.mock_runner import MockAgentRunner

        class LegacyMetric(Metric):
            name = "legacy"

            async def measure(self, input, actual_output) -> MetricResult:
                return MetricResult(name=self.name, score=1.0)

        with pytest.raises(MetricProtocolError):
            EvalRunner(
                agent_runner=MockAgentRunner(latency_range=(0.0, 0.01)),
                metrics_registry={"legacy": LegacyMetric()},
            )


# ─── 1.6 默认声明下与 0.2.0 五参逐字段等价 ────────────────────────────────────


class TestDefaultDeclarationEquivalence:
    def test_adapter_context_matches_0_2_0_five_params(self):
        """默认声明 (未声明通道) 下 ctx 的五参取材路径与 0.2.0 逐字段一致"""

        captured: dict = {}

        class CapturingMetric(Metric):
            name = "capturing"
            threshold = 0.5

            async def measure(self, ctx: MeasurementContext) -> MetricResult:
                captured["ctx"] = ctx
                return MetricResult(name=self.name, score=0.9)

        metric = CapturingMetric()
        adapter = metric.to_grader()
        task_prompt = "什么是退款政策？"
        task_output = "30 天内可退货"
        trial = TrialResultStub(trial_index=0, transcript=[
            {"role": "user", "content": task_prompt},
            {"role": "assistant", "content": task_output},
        ])
        task = TaskStub(
            id="t1",
            grader_config={
                "expected_output": "30 天",
                "context": ["政策文档"],
                "retrieval_context": ["检索片段"],
            },
        )
        import asyncio

        asyncio.run(adapter.grade(trial, [], task))

        ctx = captured["ctx"]
        # 0.2.0 的五参逐字段等价
        assert ctx.prompt == task_prompt
        assert ctx.actual_output == task_output
        assert ctx.expected_output == "30 天"
        assert ctx.context == ["政策文档"]
        assert ctx.retrieval_context == ["检索片段"]
        assert ctx.task_id == "t1"
        # 默认声明 = 仅最终输出: 观测序列为空, 最终输出经 actual_output 交付
        assert ctx.observations == []
        assert ctx.declaration.channels == frozenset()
        assert ctx.declaration.wants_trajectory is False

    def test_declared_transcript_observations_carry_provenance(self):
        """声明 transcript 后观测含消息序列, 每条带 observed_by 与采集时刻"""
        evidence = transcript_evidence(messages=TRANSCRIPT)

        class TranscriptJudge(RecorderJudge):
            evidence_levels = ("transcript",)

        metric = TranscriptJudge(llm_fn=StubLLM())
        ctx = build_measurement_context(
            metric, task_id="t1", trial=TrialResultStub(trial_index=0, transcript=TRANSCRIPT),
            evidence=evidence,
        )
        assert len(ctx.observations) == 3
        for obs, message in zip(ctx.observations, TRANSCRIPT, strict=True):
            assert obs.kind is EvidenceKind.TRANSCRIPT
            assert obs.observed_by is ObservedBy.RUNNER
            assert obs.observed_at > 0
            assert obs.value == message
        # messages() 直接给出消息序列
        assert ctx.messages() == TRANSCRIPT
        # 五参基线不因声明改变
        assert ctx.actual_output == "需要订单号"
        assert ctx.prompt == "什么是退款政策？"


class TrialResultStub:
    """最小 trial 形状 (adapter 只读 transcript 首末条)"""

    def __init__(self, trial_index: int, transcript: list[dict]):
        self.trial_index = trial_index
        self.transcript = transcript


class TaskStub:
    def __init__(self, id: str, grader_config: dict):
        self.id = id
        self._config = grader_config

    def get_grader_config(self, name: str) -> dict:
        return self._config


# ─── 1.5 未声明通道不交付 / 非法声明装配期报错 ────────────────────────────────


class TestChannelFiltering:
    def test_undeclared_channel_not_delivered(self):
        """未声明 harness_state 通道 → harness 读数不出现在测量上下文"""
        evidence = transcript_evidence(
            messages=TRANSCRIPT,
            harness=[{"files": ["a.py"], "leak": False}],
        )

        class FinalOutputOnly(Metric):
            name = "final_only"
            threshold = 0.5

            async def measure(self, ctx: MeasurementContext) -> MetricResult:
                return MetricResult(name=self.name, score=1.0)

        ctx = build_measurement_context(
            FinalOutputOnly(), trial=TrialResultStub(0, TRANSCRIPT), evidence=evidence
        )
        assert ctx.observations == []
        # 声明了 transcript 的指标能看到 harness 读数被裁掉, 只剩 transcript
        class TranscriptOnly(Metric):
            name = "transcript_only"
            evidence_levels = ("transcript",)

            async def measure(self, ctx: MeasurementContext) -> MetricResult:
                return MetricResult(name=self.name, score=1.0)

        ctx2 = build_measurement_context(
            TranscriptOnly(), trial=TrialResultStub(0, TRANSCRIPT), evidence=evidence
        )
        assert all(obs.kind is EvidenceKind.TRANSCRIPT for obs in ctx2.observations)
        assert len(ctx2.observations) == 3

    def test_declared_harness_channel_is_delivered(self):
        evidence = transcript_evidence(harness=[{"files": ["a.py"]}])

        class HarnessReader(Metric):
            name = "harness_reader"
            evidence_levels = ("harness_state",)

            async def measure(self, ctx: MeasurementContext) -> MetricResult:
                return MetricResult(name=self.name, score=1.0)

        ctx = build_measurement_context(
            HarnessReader(), trial=TrialResultStub(0, []), evidence=evidence
        )
        assert len(ctx.observations) == 1
        assert ctx.observations[0].observed_by is ObservedBy.HARNESS

    def test_unknown_channel_declaration_lists_allowed_values(self):
        with pytest.raises(ValueError) as exc:
            MetricEvidenceDeclaration(evidence_levels=("vibes",))
        assert "可选值" in str(exc.value)
        assert "transcript" in str(exc.value)

    def test_empty_declaration_rejected(self):
        with pytest.raises(ValueError):
            MetricEvidenceDeclaration(evidence_levels=())

    def test_duplicate_channel_declaration_rejected(self):
        with pytest.raises(ValueError) as exc:
            MetricEvidenceDeclaration(evidence_levels=("transcript", "transcript"))
        assert "重复" in str(exc.value)

    def test_metric_class_attribute_declaration_validated(self):
        """类属性声明的合法性在 metric_declaration 复检 (空声明报错并列可选值)"""

        class EmptyDeclared(Metric):
            name = "empty_declared"
            evidence_levels = ()

            async def measure(self, ctx: MeasurementContext) -> MetricResult:
                return MetricResult(name=self.name, score=1.0)

        with pytest.raises(MetricProtocolError) as exc:
            metric_declaration(EmptyDeclared())
        assert "仅最终输出" in str(exc.value)
        assert "subject_state" in str(exc.value)

    def test_absent_readings_kept_in_delivered_channel(self):
        """声明通道内的缺失读数保留 (没取到 ≠ 被排除), 渲染为显式缺失"""
        evidence = TrialEvidence(trace_id="tr")
        evidence.transcript.append(
            Observation.absent(
                EvidenceKind.TRANSCRIPT, "provider_unavailable", channel="transcript"
            )
        )

        class TranscriptJudge(Metric):
            name = "tj"
            evidence_levels = ("transcript",)

            async def measure(self, ctx: MeasurementContext) -> MetricResult:
                return MetricResult(name=self.name, score=1.0)

        ctx = build_measurement_context(
            TranscriptJudge(), trial=TrialResultStub(0, []), evidence=evidence
        )
        assert len(ctx.observations) == 1
        assert ctx.observations[0].is_absent
        # 缺失读数不冒充消息正文
        assert ctx.messages() == []


# ─── 2.x judge 轨迹注入 ──────────────────────────────────────────────────────


class TestJudgeTrajectoryInjection:
    async def test_declared_judge_receives_full_trajectory(self):
        """声明 transcript 的 judge 收到完整轨迹 (fake llm_fn 断言收到的 prompt)"""
        llm = StubLLM()
        metric = RecorderJudge(llm_fn=llm)
        metric.evidence_levels = ("transcript",)

        evidence = transcript_evidence(messages=TRANSCRIPT)
        ctx = build_measurement_context(
            metric, task_id="t1",
            trial=TrialResultStub(0, TRANSCRIPT), evidence=evidence,
        )
        await metric.measure(ctx)

        assert len(llm.calls) == 1
        prompt = llm.calls[0][1]
        # 逐条消息都在轨迹里
        assert "[消息 0]" in prompt
        assert "[消息 1]" in prompt
        assert "[消息 2]" in prompt
        assert "什么是退款政策？" in prompt
        assert "需要订单号" in prompt
        # 提示词要求可引用具体消息 (不强制)
        assert "[消息 2]" in prompt
        # 基线 (最终输出) 仍在
        assert "回答: 需要订单号" in prompt

    async def test_trajectory_redaction_applied(self):
        """轨迹进入提示词前经脱敏钩子 (HashingEvidenceRedactor → 摘要)"""
        from agent_eval.core.redaction import HashingEvidenceRedactor

        secret = "sk-live-secret-credential"
        messages = [
            {"role": "user", "content": f"my key is {secret}"},
            {"role": "assistant", "content": "ok"},
        ]
        llm = StubLLM()
        metric = RecorderJudge(llm_fn=llm, redactor=HashingEvidenceRedactor())
        metric.evidence_levels = ("transcript",)

        evidence = transcript_evidence(messages=messages)
        ctx = build_measurement_context(
            metric, trial=TrialResultStub(0, messages), evidence=evidence
        )
        await metric.measure(ctx)

        prompt = llm.calls[0][1]
        assert secret not in prompt
        assert "redacted:sha256:" in prompt

    async def test_undeclared_judge_prompt_byte_identical(self):
        """未声明轨迹通道: 提示词不含轨迹内容, 与无轨迹基线逐字节一致"""
        llm = StubLLM()
        metric = RecorderJudge(llm_fn=llm)  # 未声明 evidence_levels

        evidence = transcript_evidence(messages=TRANSCRIPT)
        ctx = build_measurement_context(
            metric, trial=TrialResultStub(0, TRANSCRIPT), evidence=evidence
        )
        await metric.measure(ctx)

        baseline = "回答: 需要订单号"
        assert llm.calls[0][1] == baseline
        assert "退款政策" not in llm.calls[0][1]

    async def test_declared_but_empty_trajectory_prompt_unchanged(self):
        """声明了轨迹通道但本次无可渲染读数: 不注入空节标题"""
        llm = StubLLM()
        metric = RecorderJudge(llm_fn=llm)
        metric.evidence_levels = ("transcript",)

        ctx = build_measurement_context(
            metric, trial=TrialResultStub(0, TRANSCRIPT), evidence=TrialEvidence(trace_id="tr")
        )
        await metric.measure(ctx)
        assert llm.calls[0][1] == "回答: 需要订单号"

    async def test_steps_channel_rendered_as_events(self):
        """声明 steps 通道: 步骤读数以 [事件 N] 编号渲染"""
        llm = StubLLM()
        metric = RecorderJudge(llm_fn=llm)
        metric.evidence_levels = ("steps",)

        evidence = TrialEvidence(trace_id="tr")
        evidence.steps.append(
            Observation(
                kind=EvidenceKind.STEP, observed_by=ObservedBy.RUNNER,
                value={"tool": "search", "args": {"q": "退款"}},
            )
        )
        ctx = build_measurement_context(
            metric, trial=TrialResultStub(0, []), evidence=evidence
        )
        await metric.measure(ctx)
        assert "[事件 0]" in llm.calls[0][1]
        assert "search" in llm.calls[0][1]

    async def test_explanation_can_cite_trajectory(self):
        """judge 结论的解释可引用轨迹事件 (提示词要求引用; judge 回复带引用)"""
        reply = json.dumps(
            {"score": 0.3, "reason": "回答与 [消息 0] 的问题无关"},
            ensure_ascii=False,
        )
        metric = RecorderJudge(llm_fn=StubLLM(reply))
        metric.evidence_levels = ("transcript",)
        evidence = transcript_evidence(messages=TRANSCRIPT)
        ctx = build_measurement_context(
            metric, trial=TrialResultStub(0, TRANSCRIPT), evidence=evidence
        )
        result = await metric.measure(ctx)
        assert "[消息 0]" in result.reason
