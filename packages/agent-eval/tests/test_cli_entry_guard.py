"""core-only 安装形态下命令行入口的指引 (change fix-version-drift-and-cli-entry-guard,
组 4 / spec: cli「未安装 CLI 依赖时命令行入口必须给出可行动指引」).

全部用子进程跑真入口点 —— 引导路径的成立与否取决于"模块导入期碰不到 typer"，
进程内 import 早就把模块缓存好了，测不到那一层。
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import agent_eval
from agent_eval.cli import (
    _CLI_EXTRA_MODULES,
    MISSING_CLI_DEPENDENCY_EXIT_CODE,
    MISSING_CLI_DEPENDENCY_TOKEN,
)

PACKAGE_ROOT = Path(agent_eval.__file__).parent
CHILD_ENV = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONPATH": str(PACKAGE_ROOT.parent)}

# 屏蔽指定顶层模块后再走真入口点，模拟"该依赖没装"
GUARD_PROBE = """
import sys


class _Blocker:
    def __init__(self, names):
        self.names = set(names)

    def find_spec(self, name, path=None, target=None):
        if name in self.names or name.partition(".")[0] in self.names:
            raise ModuleNotFoundError(f"No module named {{name!r}}", name=name)
        return None


sys.meta_path.insert(0, _Blocker([{blocked!r}]))
sys.argv = ["eval-suite", *{argv!r}]
from agent_eval.cli import main

sys.exit(main())
"""

CLI_ARGS = ["--help", "run --help", "validate --help", "list --help", "show --help",
            "compare --help", "power --help", "serve --help", "extensions"]


def _run_python(body: str, extra_env: dict | None = None, **fmt) -> subprocess.CompletedProcess:
    env = {**CHILD_ENV, **(extra_env or {})}
    return subprocess.run(
        [sys.executable, "-c", body.format(**fmt)],
        capture_output=True, text=True, encoding="utf-8", env=env,
    )


def _module_entry(module: str, args: str, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    env = {**CHILD_ENV, **(extra_env or {})}
    return subprocess.run(
        [sys.executable, "-m", module, *args.split()],
        capture_output=True, text=True, encoding="utf-8", env=env,
    )


# 两条路径都在 -c 子进程里跑并把 argv[0] 钉成 eval-suite —— 与真 console script 同形，
# click 由此算出同一个 prog_name，剩下的字节差只可能来自引导层。
RUN_ENTRY = """
import sys
sys.argv = ["eval-suite", *{argv!r}]
from agent_eval.cli import main

sys.exit(main())
"""
RUN_IMPL = """
import sys
sys.argv = ["eval-suite", *{argv!r}]
from agent_eval._cli_app import app

app()
"""


class TestNormalPathIsUntouched:
    """Scenario: 已装齐依赖时零影响 —— 引导路径完全不介入。"""

    def test_entry_module_import_alone_never_touches_typer(self):
        """改动前 `import agent_eval.cli` 就会 ModuleNotFoundError; 入口模块必须与依赖解耦。"""
        body = """
import sys
import agent_eval.cli
print("typer" in sys.modules, "agent_eval._cli_app" in sys.modules)
"""
        resp = _run_python(body)
        assert resp.returncode == 0, resp.stderr
        assert resp.stdout.strip() == "False False", resp.stdout

    def test_bootstrap_adds_zero_bytes_and_zero_exit_code_shift(self):
        """主闸: 走入口点的输出与退出码, 和直接跑实现体逐字节相同。"""
        for args in CLI_ARGS:
            argv = args.split()
            through_entry = _run_python(RUN_ENTRY, argv=argv)
            direct = _run_python(RUN_IMPL, argv=argv)
            label = f"[{args}]"
            assert through_entry.stdout == direct.stdout, f"{label} stdout 变了"
            assert through_entry.stderr == direct.stderr, f"{label} stderr 变了"
            assert through_entry.returncode == direct.returncode, f"{label} 退出码变了"

    def test_guidance_text_is_absent_when_dependencies_are_installed(self):
        from agent_eval.cli import missing_dependency_guidance

        guidance_word = "pip install"
        for args in CLI_ARGS:
            resp = _module_entry("agent_eval.cli", args)
            assert guidance_word not in resp.stdout + resp.stderr, args
        assert guidance_word in missing_dependency_guidance()

    def test_run_demo_still_exits_zero_through_the_entry(self, tmp_path):
        """正常路径不止 help: 真跑一次内置套件仍按既有语义给码 (0=放行)。"""
        resp = _module_entry(
            "agent_eval.cli", "run demo", extra_env={"AEVAL_DB": str(tmp_path / "demo.db")}
        )
        assert resp.returncode == 0, resp.stdout + resp.stderr
        assert "Status: completed" in resp.stdout


class TestMissingCliDependencyGuidance:
    """Scenario: 裸装后执行命令行 —— 得到的是指引，不是指向内部依赖的 traceback。"""

    def test_missing_typer_yields_actionable_guidance(self, tmp_path):
        resp = _run_python(GUARD_PROBE, blocked="typer", argv=["--help"])
        out = resp.stdout + resp.stderr
        assert resp.returncode != 0
        assert "pip install" in out and "[cli]" in out, out
        assert "eval-suite" in out

    def test_output_is_not_a_typer_traceback(self):
        resp = _run_python(GUARD_PROBE, blocked="typer", argv=["--help"])
        out = resp.stdout + resp.stderr
        assert "Traceback" not in out, out
        assert "No module named 'typer'" not in out, out
        assert "agent_eval/_cli_app.py" not in out.replace("\\", "/"), out

    def test_handled_module_set_matches_the_declared_cli_extra(self):
        """守护集合不许和发行声明漂移 —— `cli` 组改了这里就得改。"""
        tomllib = pytest.importorskip("tomllib")
        pyproject = PACKAGE_ROOT.parent.parent / "pyproject.toml"
        if not pyproject.is_file():
            pytest.skip("发行环境不随包带 pyproject.toml")
        declared = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"][
            "optional-dependencies"
        ]["cli"]
        tops = {re.match(r"\s*([A-Za-z0-9._-]+)", r).group(1) for r in declared}
        assert tops == set(_CLI_EXTRA_MODULES), (tops, set(_CLI_EXTRA_MODULES))

    def test_rich_is_not_an_import_time_dependency_of_the_entry(self):
        """记录实测边界: 缺 typer 才是裸装形态的真实故障点 (rich 由 typer 在渲染期拉入)。"""
        resp = _run_python(GUARD_PROBE, blocked="rich", argv=["--help"])
        assert "No module named 'rich'" in resp.stderr or resp.returncode != 0
        assert resp.returncode != MISSING_CLI_DEPENDENCY_EXIT_CODE

    def test_unrelated_import_failure_is_not_masked_by_the_guidance(self):
        """安装坏了/代码自身有 bug 时不许用"请装 [cli]"盖住 —— 那会把人引到错路上。"""
        resp = _run_python(GUARD_PROBE, blocked="agent_eval.core.types", argv=["--help"])
        out = resp.stdout + resp.stderr
        assert resp.returncode != 0
        assert "Traceback" in out, "非依赖缺失的导入失败必须照原样抛出来"
        assert "pip install" not in out, out


class TestExitCodeIsScriptDiscriminable:
    def test_missing_dependency_code_is_distinct_from_every_outcome(self):
        """4.4: 成功与两种失败不共用一个码 (0 放行 / 1 表现 / 2 用法 / 3 不可信 / 4 缺依赖)。"""
        assert MISSING_CLI_DEPENDENCY_EXIT_CODE == 4
        assert len({MISSING_CLI_DEPENDENCY_EXIT_CODE, 0, 1, 2, 3}) == 5

    def test_missing_dependency_run_reports_the_documented_code(self):
        resp = _run_python(GUARD_PROBE, blocked="typer", argv=["--help"])
        assert resp.returncode == MISSING_CLI_DEPENDENCY_EXIT_CODE

    def test_guidance_carries_a_stable_machine_readable_token(self):
        resp = _run_python(GUARD_PROBE, blocked="typer", argv=["--help"])
        out = resp.stdout + resp.stderr
        assert MISSING_CLI_DEPENDENCY_TOKEN in out
        # 脚本只需匹配这一行，不需要读安装表达式的具体写法
        assert out.strip().splitlines()[0].startswith(MISSING_CLI_DEPENDENCY_TOKEN)

    def test_normal_path_never_emits_the_token(self):
        for args in CLI_ARGS:
            resp = _module_entry("agent_eval.cli", args)
            assert MISSING_CLI_DEPENDENCY_TOKEN not in resp.stdout + resp.stderr, args
