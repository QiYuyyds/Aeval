"""eval-suite CLI tests (change extract-aeval-repo, task 5.4).

CliRunner-based: run/validate exit-code semantics (0 = 放行, 1 = agent 表现
未达标, 2 = 用法错误, 3 = 评测本身不可信即 invalid 超阈或证据不足), list/show
querying, compare output, unknown-runner handling. serve is covered as a
TestClient smoke in test_standalone_api.py (uvicorn itself is not started here).
"""

import re

from typer.testing import CliRunner

from agent_eval.cli import app

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
        assert "not found" in result.output


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


class TestServeSmoke:
    def test_serve_command_builds_standalone_app(self, tmp_path, monkeypatch):
        """serve 不真正起 uvicorn — 校验 app 工厂可用 (TestClient 冒烟见
        test_standalone_api.py); 这里验证命令选项接线 (host/port 默认回环)。"""
        from agent_eval.api.standalone import create_standalone_app

        app_instance = create_standalone_app()
        assert app_instance is not None

    def test_serve_defaults_are_loopback(self):
        import inspect

        from agent_eval import cli

        sig = inspect.signature(cli.serve)
        default = sig.parameters["host"].default
        assert getattr(default, "default", default) == "127.0.0.1"
