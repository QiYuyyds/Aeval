"""eval-suite CLI tests (change extract-aeval-repo, task 5.4).

CliRunner-based: run/validate exit-code semantics (0 = 放行, 1 = agent 表现
未达标, 2 = 用法错误, 3 = 评测本身不可信即 invalid 超阈或证据不足), list/show
querying, compare output, unknown-runner handling. serve is covered as a
TestClient smoke in test_standalone_api.py (uvicorn itself is not started here).
"""

import re

from typer.testing import CliRunner

from agent_eval._cli_app import app

runner = CliRunner()

SUITE_PASS = """
name: cli-pass
version: 1.0.0
description: all tasks pass
tasks:
  - id: t_ok
    prompt: hello
    max_trials: 2
    graders:
      - type: code
        name: code_based
        config:
          checks:
            - type: contains
              value: "Mock response"
              target: transcript
"""

SUITE_MIXED = """
name: cli-mixed
version: 1.0.0
description: one task fails
tasks:
  - id: t_ok
    prompt: hello
    max_trials: 1
    graders:
      - type: code
        name: code_based
        config:
          checks:
            - type: contains
              value: "Mock response"
              target: transcript
  - id: t_dead
    prompt: world
    max_trials: 1
    graders:
      - type: code
        name: code_based
        config:
          checks:
            - type: contains
              value: "NEVER_PRESENT"
              target: transcript
"""

# 判据用 faithfulness、诊断用 answer_relevancy: 被判据引用的指标视为已升格,
# 不会再作为诊断重复运行, 所以两个指标必须不同才同时覆盖两块呈现面
SUITE_DIAGNOSTIC_AND_RATERS = """
name: cli-diag
version: 1.0.0
description: diagnostic metric plus two-rater judge panel
tasks:
  - id: t_diag
    prompt: hello
    max_trials: 2
    diagnostic_metrics:
      - answer_relevancy
    graders:
      - type: metric
        name: metric
        config:
          metric_name: faithfulness
        judges:
          - { name: lenient, config: { threshold: 0.2 } }
          - { name: strict, config: { threshold: 0.9 } }
"""

SUITE_NO_CRITERIA = """
name: cli-no-criteria
version: 1.0.0
description: grader configured without any check
tasks:
  - id: t_blind
    prompt: hello
    max_trials: 1
    graders:
      - type: code
        name: code_based
"""

SUITE_DUPLICATE_IDS = """
name: cli-dup
version: 1.0.0
tasks:
  - id: t1
    prompt: a
    graders:
      - type: code
        name: code_based
  - id: t1
    prompt: b
    graders:
      - type: code
        name: code_based
"""

# 基线门 e2e 用 10 个 trial: pass@1 全对的 Wilson 下界 (72.2%) 与全错的上界
# (27.8%) 不重叠, 小样本时 ([0.34, 1.0] vs [0.0, 0.66]) 会重叠而判不显著
SUITE_TEN_PASS = """
name: cli-gate
version: 1.0.0
description: ten all-pass trials for the baseline gate
tasks:
  - id: t_ok
    prompt: hello
    max_trials: 10
    graders:
      - type: code
        name: code_based
        config:
          checks:
            - type: contains
              value: "Mock response"
              target: transcript
"""

SUITE_TEN_FAIL = """
name: cli-gate
version: 1.0.0
description: ten all-fail trials for the baseline gate
tasks:
  - id: t_ok
    prompt: hello
    max_trials: 10
    graders:
      - type: code
        name: code_based
        config:
          checks:
            - type: contains
              value: "NEVER_PRESENT"
              target: transcript
"""


def _suite(tmp_path, text: str, name="suite.yaml"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def _run_id(result) -> str:
    match = re.search(r"Run: (run_[0-9a-f]+)", result.output)
    assert match, result.output
    return match.group(1)


class TestValidate:
    def test_valid_suite_exits_zero(self, tmp_path):
        result = runner.invoke(app, ["validate", _suite(tmp_path, SUITE_PASS)])
        assert result.exit_code == 0, result.output
        assert "VALID" in result.output
        assert "cli-pass v1.0.0" in result.output

    def test_duplicate_task_ids_exit_nonzero_with_error(self, tmp_path):
        result = runner.invoke(app, ["validate", _suite(tmp_path, SUITE_DUPLICATE_IDS)])
        assert result.exit_code != 0
        assert "INVALID" in result.output
        assert "Duplicate task IDs" in result.output

    def test_missing_file_exit_nonzero(self, tmp_path):
        result = runner.invoke(app, ["validate", str(tmp_path / "nope.yaml")])
        assert result.exit_code != 0
        # 来源解析统一后, 不存在的来源由 packaging.resolve_source 报「来源不存在」
        assert "来源不存在" in result.output


class TestRun:
    def test_all_pass_exits_zero(self, tmp_path):
        db = tmp_path / "aeval.db"
        result = runner.invoke(
            app, ["run", _suite(tmp_path, SUITE_PASS), "--db", str(db)]
        )
        assert result.exit_code == 0, result.output
        assert "Results Summary" in result.output
        assert "Pass@1" in result.output
        assert "Failures" not in result.output

    def test_failing_task_exits_nonzero_and_lists_failure(self, tmp_path):
        db = tmp_path / "aeval.db"
        result = runner.invoke(
            app, ["run", _suite(tmp_path, SUITE_MIXED), "--db", str(db)]
        )
        assert result.exit_code == 1, result.output
        assert "Failures:" in result.output
        assert "- t_dead: 0/1 valid trials passed" in result.output

    def test_checkless_grader_used_to_pass_now_blocks(self, tmp_path):
        """回归: 无 checks 的 grader 旧口径自动满分放行 (exit 0)。

        新口径下它是评测侧缺陷 -> invalid trial, 退出码 3, 且不给出放行结论。
        """
        db = tmp_path / "aeval.db"
        result = runner.invoke(
            app, ["run", _suite(tmp_path, SUITE_NO_CRITERIA), "--db", str(db)]
        )
        assert result.exit_code == 3, result.output
        assert "NOT PASSABLE" in result.output
        assert "not an agent performance result" in result.output
        assert "insufficient_data" in result.output
        assert "no_criteria_configured" in result.output
        # 0.0 分数不得伪装成 "agent 失败" 的 1 退出码
        assert "Failures:" not in result.output

    def test_invalid_limit_option_relaxes_the_gate(self, tmp_path):
        db = tmp_path / "aeval.db"
        result = runner.invoke(
            app,
            [
                "run",
                _suite(tmp_path, SUITE_NO_CRITERIA),
                "--db",
                str(db),
                "--invalid-limit",
                "1.0",
            ],
        )
        assert result.exit_code == 3, result.output
        assert "exceeds --invalid-limit" not in result.output

    def test_trials_override(self, tmp_path):
        db = tmp_path / "aeval.db"
        result = runner.invoke(
            app,
            ["run", _suite(tmp_path, SUITE_PASS), "--trials", "1", "--db", str(db)],
        )
        assert result.exit_code == 0, result.output
        assert "1 trials" in result.output or "1/1" in result.output

    def test_unknown_runner_exits_two(self, tmp_path):
        result = runner.invoke(
            app,
            [
                "run",
                _suite(tmp_path, SUITE_PASS),
                "--runner",
                "does-not-exist",
                "--db",
                str(tmp_path / "aeval.db"),
            ],
        )
        assert result.exit_code == 2
        assert "unknown runner" in result.output

    def test_run_echoes_the_selected_trace_vocabulary(self, tmp_path):
        """选中的词汇与其钉住的规范修订必须可见 —— 数字是在哪套约定上读的要能复核。"""
        result = runner.invoke(
            app,
            [
                "run",
                _suite(tmp_path, SUITE_PASS),
                "--vocabulary",
                "openinference",
                "--db",
                str(tmp_path / "aeval.db"),
            ],
        )
        assert result.exit_code == 0, result.output
        assert "Trace vocabulary: openinference" in result.output
        assert "spec=openinference-0.1.30" in result.output

    def test_unknown_vocabulary_exits_two_before_running(self, tmp_path):
        result = runner.invoke(
            app,
            [
                "run",
                _suite(tmp_path, SUITE_PASS),
                "--vocabulary",
                "open-infrence",
                "--db",
                str(tmp_path / "a.db"),
            ],
        )
        assert result.exit_code == 2
        assert "otel-genai" in result.output and "openinference" in result.output
        assert "Starting eval run" not in result.output

    def test_invalid_suite_file_exits_nonzero_before_running(self, tmp_path):
        result = runner.invoke(
            app,
            ["run", _suite(tmp_path, SUITE_DUPLICATE_IDS), "--db", str(tmp_path / "a.db")],
        )
        assert result.exit_code == 1
        assert "Duplicate task IDs" in result.output

    def test_run_persists_to_sqlite(self, tmp_path):
        db = tmp_path / "aeval.db"
        result = runner.invoke(
            app, ["run", _suite(tmp_path, SUITE_PASS), "--db", str(db)]
        )
        assert result.exit_code == 0, result.output
        listing = runner.invoke(app, ["list", "runs", "--db", str(db)])
        assert listing.exit_code == 0, listing.output
        assert _run_id(result) in listing.output


class TestListAndShow:
    def test_list_runs_empty_db(self, tmp_path):
        db = tmp_path / "aeval.db"
        result = runner.invoke(app, ["list", "runs", "--db", str(db)])
        assert result.exit_code == 0
        assert "No runs found" in result.output

    def test_list_rejects_bad_kind(self, tmp_path):
        result = runner.invoke(app, ["list", "bogus", "--db", str(tmp_path / "a.db")])
        assert result.exit_code == 2

    def test_list_suites_after_run(self, tmp_path):
        db = tmp_path / "aeval.db"
        runner.invoke(app, ["run", _suite(tmp_path, SUITE_PASS), "--db", str(db)])
        result = runner.invoke(app, ["list", "suites", "--db", str(db)])
        assert result.exit_code == 0
        assert "cli-pass" in result.output

    def test_show_run_details(self, tmp_path):
        db = tmp_path / "aeval.db"
        run_result = runner.invoke(
            app, ["run", _suite(tmp_path, SUITE_PASS), "--db", str(db)]
        )
        run_id = _run_id(run_result)

        result = runner.invoke(app, ["show", run_id, "--db", str(db)])
        assert result.exit_code == 0, result.output
        assert "cli-pass" in result.output
        assert "t_ok" in result.output
        assert "PASS" in result.output

    def test_show_renders_diagnostic_and_agreement_blocks(self, tmp_path):
        """show 读回存盘 run 也要呈现诊断块 (默认折叠) 与 agreement 分块"""
        db = tmp_path / "aeval.db"
        run_result = runner.invoke(
            app, ["run", _suite(tmp_path, SUITE_DIAGNOSTIC_AND_RATERS), "--db", str(db)]
        )
        run_id = _run_id(run_result)

        folded = runner.invoke(app, ["show", run_id, "--db", str(db)])
        assert folded.exit_code == 0, folded.output
        assert "Diagnostics: 1 metric(s)" in folded.output
        assert "not in any denominator" in folded.output
        assert "Inter-rater agreement" in folded.output
        # 信度与自一致不混排: agreement 行必须自证它不是 confidence
        assert "self-consistency" in folded.output
        assert "metric: None =" not in folded.output

        expanded = runner.invoke(app, ["show", run_id, "--db", str(db), "--verbose"])
        assert expanded.exit_code == 0, expanded.output
        assert "Diagnostics (not in any denominator)" in expanded.output
        assert "answer_relevancy" in expanded.output

    def test_show_task_drilldown(self, tmp_path):
        db = tmp_path / "aeval.db"
        run_result = runner.invoke(
            app, ["run", _suite(tmp_path, SUITE_MIXED), "--db", str(db)]
        )
        run_id = _run_id(run_result)

        result = runner.invoke(
            app, ["show", run_id, "--task", "t_dead", "--db", str(db)]
        )
        assert result.exit_code == 0, result.output
        assert "Task 't_dead'" in result.output
        assert "FAIL" in result.output
        assert "code_based" in result.output

    def test_show_unknown_task_exits_nonzero(self, tmp_path):
        db = tmp_path / "aeval.db"
        run_result = runner.invoke(
            app, ["run", _suite(tmp_path, SUITE_PASS), "--db", str(db)]
        )
        result = runner.invoke(
            app,
            ["show", _run_id(run_result), "--task", "nope", "--db", str(db)],
        )
        assert result.exit_code == 1

    def test_show_unknown_run_exits_nonzero(self, tmp_path):
        result = runner.invoke(
            app, ["show", "run_nope", "--db", str(tmp_path / "aeval.db")]
        )
        assert result.exit_code == 1
        assert "not found" in result.output


class TestCompare:
    def test_compare_two_runs(self, tmp_path):
        db = tmp_path / "aeval.db"
        a = runner.invoke(app, ["run", _suite(tmp_path, SUITE_PASS), "--db", str(db)])
        b = runner.invoke(app, ["run", _suite(tmp_path, SUITE_MIXED), "--db", str(db)])
        assert a.exit_code == 0 and b.exit_code == 1

        result = runner.invoke(
            app, ["compare", _run_id(a), _run_id(b), "--db", str(db)]
        )
        assert result.exit_code == 0, result.output
        assert "METRIC" in result.output
        assert "Pass@" in result.output
        assert "Avg Score" in result.output
        assert "Evidence boundary:" in result.output
        assert "Regressions:" in result.output

    def test_compare_missing_run_exits_nonzero(self, tmp_path):
        db = tmp_path / "aeval.db"
        result = runner.invoke(
            app, ["compare", "run_x", "run_y", "--db", str(db)]
        )
        assert result.exit_code == 1
        assert "not found" in result.output


class TestRunBaseline:
    """run --baseline 基线相对门 (变更⑥ tasks 3.1–3.4, MockRunner 离线 e2e)。"""

    def test_significantly_worse_exits_nonzero(self, tmp_path):
        db = tmp_path / "aeval.db"
        baseline = runner.invoke(
            app, ["run", _suite(tmp_path, SUITE_TEN_PASS, "pass.yaml"), "--db", str(db)]
        )
        assert baseline.exit_code == 0, baseline.output

        result = runner.invoke(
            app,
            [
                "run",
                _suite(tmp_path, SUITE_TEN_FAIL, "fail.yaml"),
                "--db",
                str(db),
                "--baseline",
                _run_id(baseline),
            ],
        )
        assert result.exit_code != 0, result.output
        assert "Baseline: new" in result.output
        assert "vs baseline" in result.output
        assert "verdict: significantly_worse" in result.output
        # 两侧区间如实呈现
        assert "baseline 100.0% [95% CI 72.2%..100.0%]" in result.output
        assert "new 0.0% [95% CI 0.0%..27.8%]" in result.output

    def test_not_significant_exits_zero_with_honest_text(self, tmp_path):
        db = tmp_path / "aeval.db"
        baseline = runner.invoke(
            app, ["run", _suite(tmp_path, SUITE_TEN_PASS, "a.yaml"), "--db", str(db)]
        )
        assert baseline.exit_code == 0

        result = runner.invoke(
            app,
            [
                "run",
                _suite(tmp_path, SUITE_TEN_PASS, "b.yaml"),
                "--db",
                str(db),
                "--baseline",
                _run_id(baseline),
            ],
        )
        assert result.exit_code == 0, result.output
        assert "verdict: not_significant" in result.output
        # 如实文案: 「差异落在噪声内」, 不宣称没有变化
        assert "噪声" in result.output
        assert "不等于「没有变化」" in result.output

    def test_improved_exits_zero(self, tmp_path):
        db = tmp_path / "aeval.db"
        baseline = runner.invoke(
            app, ["run", _suite(tmp_path, SUITE_TEN_FAIL, "a.yaml"), "--db", str(db)]
        )
        assert baseline.exit_code == 1  # 基线本身允许是失败的 run

        result = runner.invoke(
            app,
            [
                "run",
                _suite(tmp_path, SUITE_TEN_PASS, "b.yaml"),
                "--db",
                str(db),
                "--baseline",
                _run_id(baseline),
            ],
        )
        assert result.exit_code == 0, result.output
        assert "verdict: improved" in result.output

    def test_not_comparable_exits_nonzero_with_reason(self, tmp_path):
        """Scenario: 不可比宁可红不可哑 — 退出码非零并给 reason。"""
        db = tmp_path / "aeval.db"
        baseline = runner.invoke(
            app, ["run", _suite(tmp_path, SUITE_TEN_PASS, "a.yaml"), "--db", str(db)]
        )
        assert baseline.exit_code == 0

        result = runner.invoke(
            app,
            [
                "run",
                _suite(tmp_path, SUITE_TEN_PASS, "b.yaml"),
                "--db",
                str(db),
                "--vocabulary",
                "openinference",  # 证据边界 (规范修订) 不同 → 不可比
                "--baseline",
                _run_id(baseline),
            ],
        )
        assert result.exit_code != 0, result.output
        assert "verdict: not_comparable" in result.output
        assert "规范版本不同" in result.output

    def test_missing_baseline_run_exits_two(self, tmp_path):
        db = tmp_path / "aeval.db"
        result = runner.invoke(
            app,
            [
                "run",
                _suite(tmp_path, SUITE_TEN_PASS),
                "--db",
                str(db),
                "--baseline",
                "run_nope",
            ],
        )
        assert result.exit_code == 2
        assert "baseline run 'run_nope' not found" in result.output

    def test_per_task_deltas_present_as_diagnostics(self, tmp_path):
        db = tmp_path / "aeval.db"
        baseline = runner.invoke(
            app, ["run", _suite(tmp_path, SUITE_TEN_PASS, "a.yaml"), "--db", str(db)]
        )
        result = runner.invoke(
            app,
            [
                "run",
                _suite(tmp_path, SUITE_TEN_FAIL, "b.yaml"),
                "--db",
                str(db),
                "--baseline",
                _run_id(baseline),
            ],
        )
        assert "Task deltas (diagnostic only, not part of the gate):" in result.output
        assert "- t_ok: baseline 100.0%" in result.output
        assert "worse (significant)" in result.output

    def test_run_without_baseline_output_unchanged(self, tmp_path):
        """回归钉 (tasks 3.3): 不传 --baseline 时输出与既有形态逐字节一致
        (run_id 与 Duration 是非确定字段, 归一化后全量比对)。"""
        db = tmp_path / "aeval.db"
        result = runner.invoke(
            app, ["run", _suite(tmp_path, SUITE_PASS), "--db", str(db)]
        )
        assert result.exit_code == 0, result.output
        normalized = re.sub(r"run_[0-9a-f]+", "<run_id>", result.output)
        normalized = re.sub(r"Duration: \d+\.\d+s", "Duration: <d>", normalized)
        assert normalized == (
            "Starting eval run: cli-pass v1.0.0 (1 tasks, up to 2 trials each)\n"
            "Trace vocabulary: otel-genai  spec=genai-94f432d  mapping=1\n"
            "  [1/1] t_ok: 2/2 valid trials passed\n"
            "────────────────────────────────────────────────────────\n"
            "Results Summary\n"
            "────────────────────────────────────────────────────────\n"
            "  Run: <run_id>  Status: completed  Duration: <d>\n"
            "  Statistics version: 2\n"
            "  Evidence: runner=2\n"
            "  Regrade: available\n"
            "  Pass@1:  100.0%  [95% CI 34.2%..100.0%]\n"
            "  Pass@2:  100.0%  [95% CI 34.2%..100.0%]\n"
            "  Pass^1:  100.0%  [95% CI 34.2%..100.0%]\n"
            "  Pass^2:  100.0%  [95% CI 34.2%..100.0%]\n"
            "  Denominator: valid=2 invalid=0 pending=0\n"
            "  Avg Score: 1.0000  [95% CI 1.0000..1.0000]  worst_of_n: 1.0000\n"
            "  Tasks: 1  Trials: 2\n"
            "────────────────────────────────────────────────────────\n"
        )


class TestPower:
    """eval-suite power (变更⑥ tasks 5.3–5.4)。"""

    def test_delta_mode_with_default_p(self):
        result = runner.invoke(app, ["power", "--delta", "0.1"])
        assert result.exit_code == 0, result.output
        assert "Power analysis (sample size planning)" in result.output
        assert "+/-10.0% half-width (delta)" in result.output
        assert "p = 0.500 (assumed; 0.5 is the most conservative choice)" in result.output
        assert "N = 93" in result.output
        assert "Wilson 95% interval half-width w(p, N) <= delta" in result.output
        assert "z = 1.960 two-sided" in result.output
        assert "Limitation" in result.output

    def test_delta_mode_with_explicit_p(self):
        result = runner.invoke(app, ["power", "--delta", "0.03", "--p", "0.7"])
        assert result.exit_code == 0, result.output
        assert "p = 0.700 (assumed; given via --p)" in result.output
        assert "N = 894" in result.output

    def test_delta_out_of_range_exits_two(self):
        result = runner.invoke(app, ["power", "--delta", "0.6"])
        assert result.exit_code == 2
        assert "--delta must be in (0, 0.5)" in result.output

    def test_from_run_mode_uses_measured_p_and_sigma(self, tmp_path):
        db = tmp_path / "aeval.db"
        run_result = runner.invoke(
            app, ["run", _suite(tmp_path, SUITE_TEN_PASS), "--db", str(db)]
        )
        assert run_result.exit_code == 0, run_result.output

        result = runner.invoke(
            app,
            [
                "power",
                "--delta",
                "0.05",
                "--from-run",
                _run_id(run_result),
                "--db",
                str(db),
            ],
        )
        assert result.exit_code == 0, result.output
        assert f"Run: {_run_id(run_result)}" in result.output
        assert "Measured baseline: pass@1 = 100.0%" in result.output
        assert "Required trials (rate +/-delta):" in result.output
        assert "Measured score spread: sigma = 0.0000" in result.output
        assert "difference d = 0.05" in result.output
        assert "n ~= 2 * (z_(alpha/2) * sigma / d)^2, alpha = 0.05" in result.output
        # 局限声明随输出落盘
        assert "optimistic for small or skewed samples" in result.output

    def test_from_run_without_valid_trials_reports_insufficient_evidence(self, tmp_path):
        """Scenario: 无有效样本 → 报证据不足退出非零, 不以 0/1 代算。"""
        db = tmp_path / "aeval.db"
        run_result = runner.invoke(
            app, ["run", _suite(tmp_path, SUITE_NO_CRITERIA), "--db", str(db)]
        )
        assert run_result.exit_code == 3  # run 本身判评测不可信, 但 run 已落盘

        result = runner.invoke(
            app,
            [
                "power",
                "--delta",
                "0.05",
                "--from-run",
                _run_id(run_result),
                "--db",
                str(db),
            ],
        )
        assert result.exit_code != 0
        assert "insufficient evidence" in result.output
        assert "refusing to plan with a substituted 0 or 1" in result.output

    def test_from_run_missing_run_exits_nonzero(self, tmp_path):
        result = runner.invoke(
            app,
            [
                "power",
                "--delta",
                "0.05",
                "--from-run",
                "run_nope",
                "--db",
                str(tmp_path / "aeval.db"),
            ],
        )
        assert result.exit_code != 0
        assert "not found" in result.output


class TestServeSmoke:
    def test_serve_command_builds_standalone_app(self, tmp_path, monkeypatch):
        """serve 不真正起 uvicorn — 校验 app 工厂可用 (TestClient 冒烟见
        test_standalone_api.py); 这里验证命令选项接线 (host/port 默认回环)。"""
        from agent_eval.api.standalone import create_standalone_app

        app_instance = create_standalone_app()
        assert app_instance is not None

    def test_serve_defaults_are_loopback(self):
        import inspect

        from agent_eval import _cli_app

        sig = inspect.signature(_cli_app.serve)
        default = sig.parameters["host"].default
        assert getattr(default, "default", default) == "127.0.0.1"
