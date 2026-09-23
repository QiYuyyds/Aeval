"""
eval-suite — the Aeval command line.

本模块是命令行入口点 (`pyproject [project.scripts]`)，**不得在模块期导入
typer**：发行包无条件生成 `eval-suite` 可执行文件（PEP 621 的 `[project.scripts]`
是项目级表，无法按可选依赖声明，见 change 的 design D2），所以只装了 core 的人
手上也有这条命令。它必须在这里被说清楚缺什么，而不是抛一个指向内部依赖的
traceback (spec: cli)。真正的实现体在 `agent_eval/_cli_app.py`。

Commands:
    run        Execute a suite (default runner: built-in MockAgentRunner).
               Exit 0 all passed / 1 failed tasks or baseline regression /
               3 evaluation untrustworthy. --baseline adds a relative
               regression gate against a stored run
    validate   Validate a suite without running it
    list       List runs or suites from the storage DB
    show       Show one run's details (--task drills into a single task);
               reports valid/invalid/pending counts, pass@1 95% CI and
               extrapolation markers
    compare    A/B compare two runs; overlapping 95% intervals read as
               "not significant" with no directional verdict, and differing
               statistics versions or evidence boundaries read as not comparable
    power      Sample-size planning: trials needed for a target resolution
               (--delta with an assumed or measured baseline pass rate)
    serve      Serve the standalone API (/v1) via uvicorn

Exit codes: 0 放行 / 1 agent 表现未达标 / 2 用法错误 / 3 评测本身不可信 /
            4 命令行依赖缺失（安装形态不对，见 `MISSING_CLI_DEPENDENCY_EXIT_CODE`）

Source forms (run/validate, spec: suite-distribution): a single suite.yaml
file (status quo); a pack directory or .tar.gz/.zip archive (integrity-checked
against manifest.json); a git URL (shallow clone into a temp dir — needs the
git executable); or the literal "demo" (the built-in starter pack shipped
inside the wheel; a local file/dir named demo wins with a hint). Temp dirs are
cleaned up when the command ends.

Holdout (run/validate): tasks marked holdout: true are excluded by default
and the skip count is reported; --include-holdout runs them. validate reports
the holdout count without running anything.

Runner selection (run): --runner option > AEVAL_RUNNER env var > "mock"
(forced "mock" for the built-in demo pack unless --runner is explicit).
Custom runners register via the "agent_eval.runners" entry-point group
(name → zero-arg factory returning an AgentRunner).

Trace vocabulary (run): --vocabulary option > AEVAL_TRACE_VOCABULARY env var >
"otel-genai". The value selects a built-in public-convention preset
(agent_eval.trace.known_vocabularies()); host-private attribute names still go
in through default_mapping(extra_entries) in library code, never through the CLI.

Storage (list/show/compare and run persistence): SQLite, ./aeval.db by
default; override with --db or the AEVAL_DB environment variable.
"""

from __future__ import annotations

import sys

# 与评测结果 (1/3) 和用法错误 (2) 都区分开，脚本可以只凭退出码判定"安装形态不对"
MISSING_CLI_DEPENDENCY_EXIT_CODE = 4
# 缺依赖指引的稳定标记：文本可判别，不需要去匹配 traceback
MISSING_CLI_DEPENDENCY_TOKEN = "error: missing-cli-dependency"

# `cli` 可选依赖组实际提供的顶层模块 —— 只有缺这些才给安装指引
_CLI_EXTRA_MODULES = frozenset({"typer", "rich"})

_DISTRIBUTION_NAME = "aeval-framework"


def missing_dependency_guidance() -> str:
    """缺命令行依赖时的可行动指引 (spec: cli「未安装 CLI 依赖时命令行入口必须给出可行动指引」)。"""
    return (
        f"{MISSING_CLI_DEPENDENCY_TOKEN}\n"
        "eval-suite needs the command-line dependencies, which are not installed\n"
        f"in the {_DISTRIBUTION_NAME} environment. The library itself is fine — only\n"
        "this command is missing its extras.\n"
        "\n"
        f"    pip install \"{_DISTRIBUTION_NAME}[cli]\"\n"
        "\n"
        f"(from a source checkout: pip install -e \".[cli]\")\n"
    )


def _is_missing_cli_dependency(exc: BaseException) -> bool:
    """区分"命令行依赖没装"与"安装坏了/代码本身有 bug"——后者不能被指引用盖住。"""
    missing = getattr(exc, "name", None) or ""
    return missing.partition(".")[0] in _CLI_EXTRA_MODULES


def main() -> int | None:
    """Console-script entry point (pyproject [project.scripts])."""
    try:
        from agent_eval._cli_app import main as _cli_main
    except ImportError as exc:
        if not _is_missing_cli_dependency(exc):
            raise
        sys.stderr.write(missing_dependency_guidance())
        return MISSING_CLI_DEPENDENCY_EXIT_CODE
    return _cli_main()


if __name__ == "__main__":
    sys.exit(main())
