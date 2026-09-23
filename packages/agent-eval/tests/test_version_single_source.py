"""版本出口同源 (change fix-version-drift-and-cli-entry-guard, 组 2 / spec: rest-api).

钉住三件事:
1. 同一进程内接口文档版本 / 能力清单版本 / 版本响应头三处相等;
2. 寄宿挂载形态与独立部署形态读到的接口文档版本相同;
3. `api/` 下不存在第二处写死的版本号字面量 —— 静态扫描, 防的是"新增第二处"。
"""

import ast
import re
from pathlib import Path

from starlette.testclient import TestClient

import agent_eval.api
from agent_eval.api.app import create_app, meta_payload, package_version
from agent_eval.api.standalone import create_standalone_app

VERSION_LITERAL = re.compile(r"\d+\.\d+\.\d+[\w.+-]*")
API_ROOT = Path(next(iter(agent_eval.api.__path__)))


def _doc_string_nodes(tree: ast.AST) -> set[int]:
    """docstring 常量节点 id —— 叙述历史版本的文字不是版本声明。"""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                out.add(id(first.value))
    return out


def _version_literals_in(source: str, path: Path) -> list[str]:
    tree = ast.parse(source)
    docstrings = _doc_string_nodes(tree)
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or id(node) in docstrings:
            continue
        if isinstance(node.value, str) and VERSION_LITERAL.fullmatch(node.value):
            loc = path.relative_to(API_ROOT.parent).as_posix()
            offenders.append(f"{loc}:{node.lineno} = {node.value!r}")
    return offenders


class TestVersionOutletsSameSource:
    def test_openapi_meta_and_header_agree_in_one_process(self):
        """Scenario: 同一进程对外声明的版本只有一个来源 (独立部署形态三出口齐全)。"""
        app = create_standalone_app()
        doc_version = app.openapi()["info"]["version"]
        with TestClient(app) as client:
            meta_version = client.get("/v1/meta").json()["version"]
            header = client.get("/v1/health").headers["X-Aeval-Version"]

        resolved = package_version()
        assert doc_version == resolved, doc_version
        assert meta_version == resolved, meta_version
        assert header == resolved, header

    def test_hosted_openapi_and_meta_agree(self):
        """寄宿形态自己那两处出口同样同源 (它没有版本头, 那是独立部署专属)。"""
        app = create_app()
        resolved = package_version()
        assert app.openapi()["info"]["version"] == resolved
        assert meta_payload()["version"] == resolved

    def test_mounted_openapi_served_under_prefix_matches(self):
        """独立部署里内层 app 被挂到 /v1, 它自己的接口文档也是同一版本。"""
        with TestClient(create_standalone_app()) as client:
            resp = client.get("/v1/openapi.json")
        assert resp.status_code == 200
        assert resp.json()["info"]["version"] == package_version()

    def test_bumping_the_package_version_moves_every_outlet(self):
        """Scenario: 升级包版本后所有出口同步 —— 不改任何接口层代码。"""
        import agent_eval.api.app as app_module
        import agent_eval.api.standalone as standalone_module

        bumped = "9.9.9"
        original = app_module.package_version
        assert package_version() != bumped
        app_module.package_version = lambda: bumped
        standalone_module.package_version = lambda: bumped
        try:
            app = create_standalone_app()
            with TestClient(app) as client:
                assert app.openapi()["info"]["version"] == bumped
                assert client.get("/v1/meta").json()["version"] == bumped
                assert client.get("/v1/health").headers["X-Aeval-Version"] == bumped
            assert create_app().openapi()["info"]["version"] == bumped
            assert meta_payload()["version"] == bumped
        finally:
            app_module.package_version = original
            standalone_module.package_version = original


class TestDeploymentFormsAgree:
    def test_mounted_and_standalone_read_the_same_doc_version(self):
        """Scenario: 两种部署形态给出同一版本 —— 挂载形态不得成为例外。"""
        hosted = create_app().openapi()["info"]["version"]
        standalone = create_standalone_app().openapi()["info"]["version"]
        assert hosted == standalone == package_version()


class TestNoSecondVersionLiteral:
    def test_api_source_is_found_by_the_scan(self):
        """一条永远绿的守护测试比没有更糟 —— 先确认扫描真的覆盖了 api/ 源码。"""
        scanned = sorted(p.name for p in API_ROOT.rglob("*.py"))
        assert {"app.py", "standalone.py"} <= set(scanned), scanned

    def test_guard_actually_catches_a_planted_literal(self):
        """守护测试自己得是活的: 手工植入一处字面量必须被抓到并报出位置。"""
        planted = 'app = FastAPI(title="Aeval API", version="0.1.0")\n'
        offenders = _version_literals_in(planted, API_ROOT / "planted.py")
        assert len(offenders) == 1, offenders
        assert offenders[0].endswith("planted.py:1 = '0.1.0'"), offenders

    def test_api_layer_has_no_hardcoded_version_string(self):
        """Scenario: 出现第二处版本字面量时测试失败并报出该位置。"""
        offenders = []
        for path in sorted(API_ROOT.rglob("*.py")):
            offenders += _version_literals_in(path.read_text(encoding="utf-8"), path)

        assert not offenders, (
            "api/ 下出现写死的版本号字面量, 必须改走 package_version(): " + "; ".join(offenders)
        )
