"""Standalone API (/v1) tests (change extract-aeval-repo, task 4.2).

Covers: /v1/graders reachable with X-Aeval-Version header, /v1/meta version +
capability payload, 503 without runner, and the guarantee that the reusable
create_app() itself stays unprefixed (host mounts keep their behaviour).
"""

import time

from starlette.testclient import TestClient

from agent_eval.api.app import create_app
from agent_eval.api.standalone import create_standalone_app, package_version
from agent_eval.core.types import STATISTICS_VERSION


def _client(runner=None):
    return TestClient(create_standalone_app(runner=runner))


class TestStandaloneAPI:
    def test_graders_reachable_with_version_header(self):
        with _client() as client:
            resp = client.get("/v1/graders")
        assert resp.status_code == 200
        names = {g["name"] for g in resp.json()["graders"]}
        assert "code_based" in names
        assert resp.headers["X-Aeval-Version"] == package_version()

    def test_meta_version_and_capabilities(self):
        with _client() as client:
            resp = client.get("/v1/meta")
        assert resp.status_code == 200
        data = resp.json()
        assert data["version"] == package_version()
        assert data["package"] == "aeval-framework"
        assert data["api_prefix"] == "/v1"
        caps = data["capabilities"]
        assert "code_based" in caps["graders"]
        assert "sqlite" in caps["storage"]
        assert caps["sse"] is True
        assert caps["validity_verdicts"] is True
        # 统计口径声明 (任务 5.2): 版本 + D7 数值默认值
        stats = data["statistics"]
        assert stats["version"] == STATISTICS_VERSION
        assert stats["confidence_level"] == 0.95
        assert stats["bootstrap_rounds"] == 1000
        assert stats["min_valid_trials_for_saturation"] == 5
        assert stats["gate_invalid_ratio_limit"] == 0.2

    def test_meta_publishes_vocabulary_presets(self):
        """可选词汇预设必须可被调用方枚举 (不必读源码猜字符串)。"""
        from agent_eval.trace.mapping import VOCABULARY_SPEC_VERSIONS, known_vocabularies

        with _client() as client:
            evidence = client.get("/v1/meta").json()["evidence"]

        assert evidence["vocabularies"]["known"] == list(known_vocabularies())
        assert evidence["vocabularies"]["default"] == "otel-genai"
        assert evidence["vocabularies"]["spec_versions"] == VOCABULARY_SPEC_VERSIONS
        # 既有键语义不变: 仍是「不选词汇时按哪一版约定读」
        assert evidence["spec_version"] == VOCABULARY_SPEC_VERSIONS["otel-genai"]

    def test_run_routes_503_without_runner(self):
        with _client() as client:
            resp = client.post("/v1/runs", json={"suite_name": "s"})
            assert resp.status_code == 503
            # 注册表类端点不受 runner 缺失影响
            assert client.get("/v1/graders").status_code == 200
            assert client.get("/v1/health").status_code == 200

    def test_inner_routes_all_prefixed_under_v1(self):
        with _client() as client:
            for path in ("/suites", "/tasks", "/runs", "/graders", "/datasets"):
                # 未带 /v1 前缀的内部路由不可达 (outer app 只有 /v1/meta 与 mount)
                assert client.get(path).status_code == 404, path
            # /v1/suites 可达; runner 未注入时按 create_app 既有语义返回 503
            assert client.get("/v1/suites").status_code == 503

    def test_version_header_on_error_responses(self):
        with _client() as client:
            resp = client.get("/v1/definitely-not-a-route")
        assert resp.status_code == 404
        assert resp.headers["X-Aeval-Version"] == package_version()


class TestCreateAppUnchanged:
    def test_create_app_routes_stay_unprefixed(self):
        """寄宿挂载 (/api/eval) 的既有路由行为不变 (元信息为新增只读端点)。"""
        app = create_app()
        with TestClient(app) as client:
            assert client.get("/graders").status_code == 200
            assert client.get("/health").status_code == 200
            assert client.get("/v1/graders").status_code == 404

    def test_hosted_mount_exposes_statistics_version(self):
        """Scenario: 寄宿挂载形态 → 口径版本仍可经该形态的元信息接口取得"""
        with TestClient(create_app()) as client:
            meta = client.get("/meta").json()
        assert meta["statistics"]["version"] == STATISTICS_VERSION
        assert meta["api_prefix"] == ""

    def test_create_app_has_no_version_middleware(self):
        """X-Aeval-Version 头只属于独立部署, 不影响寄宿响应。"""
        with TestClient(create_app()) as client:
            resp = client.get("/health")
        assert resp.status_code == 200
        assert "x-aeval-version" not in {k.lower() for k in resp.headers}


class TestPackageVersion:
    def test_package_version_readable(self):
        # editable 安装环境下 importlib.metadata 可解析; 回退路径返回模块常量
        version = package_version()
        assert version and version.count(".") == 2

    def test_run_with_mock_runner_completes(self):
        from agent_eval.core.runner import EvalRunner
        from agent_eval.examples.mock_runner import MockAgentRunner, MockTraceProvider
        from agent_eval.storage.memory import MemoryStorage

        runner = EvalRunner(
            agent_runner=MockAgentRunner(success_rate=1.0, latency_range=(0.0, 0.01)),
            trace_provider=MockTraceProvider(),
            storage=MemoryStorage(),
        )
        app = create_standalone_app(runner=runner)
        suite = {
            "name": "standalone-suite",
            "tasks": [
                {
                    "id": "t1",
                    "prompt": "hi",
                    "graders": [{
                        "type": "code",
                        "name": "code_based",
                        "config": {"checks": [
                            {"type": "contains", "value": "Mock response",
                             "target": "transcript"}
                        ]},
                    }],
                    "max_trials": 1,
                }
            ],
        }
        with TestClient(app) as client:
            assert client.post("/v1/suites", json=suite).status_code == 200
            run_id = client.post("/v1/runs", json={"suite_name": "standalone-suite"}).json()[
                "run_id"
            ]
            # 轮询到终态
            deadline = time.time() + 10
            while time.time() < deadline:
                run = client.get(f"/v1/runs/{run_id}").json()
                if run["status"] in ("completed", "failed", "cancelled"):
                    break
                time.sleep(0.05)
            assert run["status"] == "completed"
            assert run["summary"]["pass_at_k"]["1"] == 1.0
            assert run["statistics_version"] == STATISTICS_VERSION
            assert run["summary"]["valid_trials"] == 1
            assert run["summary"]["invalid_trials"] == 0
            # 版本头在数据响应上也存在
            assert client.get(f"/v1/runs/{run_id}").headers[
                "X-Aeval-Version"
            ] == package_version()
