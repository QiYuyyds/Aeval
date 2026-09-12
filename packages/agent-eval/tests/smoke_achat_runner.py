"""Offline smoke test for examples/achat/http_agent_runner.py (7.5).

Starts a stub HTTP agent on localhost (no real model calls), drives the
adapter through a 2-turn scripted conversation and checks the evidence.
Run:  python packages/agent-eval/tests/smoke_achat_runner.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "examples" / "achat"))

from http_agent_runner import HTTPAgentRunner  # noqa: E402

from agent_eval.core.contract import TrialSession  # noqa: E402
from agent_eval.core.simulator import ScriptedUserSimulator  # noqa: E402
from agent_eval.core.types import EvalTask  # noqa: E402

STATE = {"runs": {}}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # noqa: ANN002 — 静默
        pass

    def _json(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        data = json.loads(self.rfile.read(length) or b"{}")
        turns = len(data.get("messages", []))
        run_id = f"stub-{STATE.get('runs_done', 0)}-{turns}"
        STATE["runs"][run_id] = {
            "status": "completed",
            "transcript": data.get("messages", []),
            "outcome": {"files": {"diagnosis.md": "disk pressure"}},
            "trace_id": "stub-trace",
        }
        self._json({"run_id": run_id})

    def do_GET(self):
        run_id = self.path.rsplit("/", 1)[-1]
        self._json(STATE["runs"][run_id])


async def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        runner = HTTPAgentRunner(base_url=f"http://127.0.0.1:{server.server_port}")
        task = EvalTask(
            id="smoke",
            prompt="first turn",
            graders=[
                {
                    "type": "code",
                    "name": "code_based",
                    "config": {"checks": [{"type": "contains", "value": "turn"}]},
                }
            ],
        )
        session = TrialSession(
            simulator=ScriptedUserSimulator(["second turn"]),
            first_user_message=task.prompt,
        )
        evidence = await runner.run(_view(task), session)
        assert evidence.trace_id == "stub-trace"
        outcome = str(evidence.state_payload())
        assert "diagnosis.md" in outcome
        print("achat http_agent_runner smoke: OK (2-turn conversation completed)")
    finally:
        server.shutdown()


def _view(task):
    from agent_eval.core.types import TaskView

    return TaskView.of(task)


if __name__ == "__main__":
    asyncio.run(main())
