"""宿主私有词汇静态扫描 (specs/graders + design D9)。

框架包内出现宿主私有属性名字面量, 就意味着又一次把某个宿主的词汇写死进了
框架 —— 与本变更要消灭的病根同构。这里用 AST 扫描把这条路封死, 与
``tests/test_import_isolation.py`` 对 ``import app.*`` 的禁令同属单向依赖约束。

宿主的私有名字只能出现在测试配置里, 作为 ``AttributeMapping.with_extra`` 的
运行时条目接入。
"""

import ast
from pathlib import Path

AGENT_EVAL_ROOT = Path(__file__).resolve().parent.parent / "src" / "agent_eval"

# 已知宿主的私有前缀 (真实宿主 AChat 的埋点词汇)
HOST_PRIVATE_PREFIXES = ("agenthub",)


def _iter_python_files() -> list[Path]:
    assert AGENT_EVAL_ROOT.is_dir(), f"missing {AGENT_EVAL_ROOT}"
    return sorted(AGENT_EVAL_ROOT.rglob("*.py"))


def _string_constants(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def test_scanner_covers_the_framework():
    """sanity: 扫描器确实覆盖了框架的全部文件 (含归一化层)。"""
    files = _iter_python_files()
    assert len(files) >= 25
    names = {path.name for path in files}
    assert {"normalize.py", "mapping.py", "observations.py", "metrics.py"} <= names


def test_framework_source_has_no_host_private_attribute_literals():
    violations: list[str] = []
    for path in _iter_python_files():
        for lineno, text in _string_constants(path):
            if any(prefix in text.lower() for prefix in HOST_PRIVATE_PREFIXES):
                violations.append(f"{path.relative_to(AGENT_EVAL_ROOT)}:{lineno}: {text!r}")
    assert violations == [], (
        "agent_eval 内不得出现宿主私有属性名字面量 "
        "(宿主词汇只能作为 AttributeMapping 的运行时条目接入):\n"
        + "\n".join(violations)
    )


def test_default_mapping_uses_only_standard_namespaces():
    """内置条目只允许规范里的属性名: gen_ai.* / error.type / 时间戳等。"""
    from agent_eval.trace.mapping import default_mapping

    allowed_prefixes = ("gen_ai.", "error.type")
    offenders = [
        name
        for name in default_mapping().attribute_names()
        if not name.startswith(allowed_prefixes)
    ]
    assert offenders == [], f"内置映射含非规范属性名: {offenders}"
