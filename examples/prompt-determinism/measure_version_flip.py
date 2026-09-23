"""离线量一次评分器换代会翻多少判 —— 判分输入确定性的验收证据。

零凭证、零 LLM 调用。两个版本都跑真实的判分路径:

- **换代前**: 从 git 取指定 rev 的 `model_based.py` 原文, 按独立模块加载 (不手写仿制品 ——
  仿制品会测到一个根本没存在的缺陷形状);
- **换代后**: 当前源码里的 `ModelBasedGrader`。

同一份归档证据先由换代前判定并落盘, 再走 `regrade_run` 换成换代后的评分器重判,
`verdict_drift` 因此答得出「翻了几条、翻在哪根口径轴上」。

judge 是**确定性的呈现序敏感替身** (清单里第一个工具名 <'f' 才给 completeness 1.0):
真实翻转率取决于真实 judge 对呈现序的敏感度, 本脚本量的是"判分输入变了多少条 + 归因轴
是否只有判分实现版本", 不是某个模型的翻转率。

跑法 (在仓库根执行, 脚本要用 git 取换代前原文):

    python examples/prompt-determinism/measure_version_flip.py --rev <修复落地前的 rev>

`--rev` 必填且不校验不可: 传修复后的 rev 会得到"输入没变、0 翻转", 而 0 翻转不能当作
"修复无影响"的证据 (见变更 tasks 6.3) —— 脚本对此直接拒绝而非出数。
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages" / "agent-eval" / "src"))

from agent_eval.core.runner import EvalRunner  # noqa: E402
from agent_eval.core.types import (  # noqa: E402
    EvalSuite,
    EvalTask,
    GraderConfig,
    GraderType,
    TrialEvidence,
)
from agent_eval.examples.mock_runner import MockTraceProvider  # noqa: E402
from agent_eval.graders.model_based import ModelBasedGrader  # noqa: E402
from agent_eval.storage.memory import MemoryStorage  # noqa: E402

GRADER_PATH = "packages/agent-eval/src/agent_eval/graders/model_based.py"
TOOLS = ["web_search", "fs_write", "fs_read", "calculator", "sql_query"]
TRIALS = 6
RUBRIC = "报告须含订单总数"
DIMENSIONS = ["correctness", "completeness"]


def show(rev: str) -> str:
    return subprocess.run(
        ["git", "show", f"{rev}:{GRADER_PATH}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout


def load_grader_cls(text: str, module_name: str):
    """把一段 model_based.py 源码作为独立模块加载 (与当前源码互不覆盖)。"""
    path = Path(tempfile.mkdtemp(prefix=f"{module_name}_")) / f"{module_name}.py"
    path.write_text(text, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ModelBasedGrader


def first_tool(prompt: str) -> str:
    match = re.search(r"^- 使用的工具: (.*)$", prompt, re.MULTILINE)
    if not match:
        return ""
    listing = ast.literal_eval(match.group(1) if match.group(1) != "无" else "[]")
    return str(listing[0]) if listing else ""


def order_sensitive_judge(system: str, user: str) -> str:
    """替身判据: 只看清单里第一个列出的工具 —— 对呈现序敏感, 但自身完全确定。"""
    completeness = 1.0 if first_tool(user) < "f" else 0.2
    return json.dumps({"correctness": 1.0, "completeness": completeness})


class MultiToolAgent:
    """交付含 5 种工具调用 (各两次) 的 transcript: 工具清单走的是去重后再排序那条路。"""

    name = "multi_tool_mock"

    async def run(self, view, session) -> TrialEvidence:
        transcript: list[dict[str, Any]] = [{"role": "user", "content": view.prompt}]
        for tool in TOOLS + list(reversed(TOOLS)):
            transcript.append({"role": "tool_call", "tool_name": tool, "content": "ok"})
        transcript.append({"role": "assistant", "content": "已汇总"})
        return TrialEvidence.runner_reported(
            trace_id=f"trace_{view.id}", transcript=transcript, state={"success": True}
        )


class ProbeEnv:
    """结束前可取证的环境: 结论不依赖被评方自报, 重评才谈得上可比。"""

    def __init__(self) -> None:
        self.files = {"report.md": "# 订单汇总"}

    async def setup(self, task) -> None:
        return None

    async def teardown(self, task) -> None:
        return None

    async def snapshot(self) -> dict[str, Any]:
        return {"files": {}}

    async def probe(self, channel: str = "") -> list[dict[str, Any]]:
        return [{"files": dict(self.files)}]

    async def verify_clean(self, baseline, harness_readings=None) -> dict[str, Any]:
        return {"clean": True, "differences": []}

    async def restore(self, baseline) -> None:
        return None


def suite() -> EvalSuite:
    task = EvalTask(
        id="multi_tool_task",
        prompt="统计这批订单并写进报告",
        max_trials=TRIALS,
        graders=[
            GraderConfig(
                type=GraderType.MODEL,
                name="model_based",
                config={"rubric": RUBRIC, "dimensions": DIMENSIONS, "threshold": 0.7},
            )
        ],
    )
    return EvalSuite(name="prompt-determinism", version="1.0.0", tasks=[task])


def prompt_of(grader, trial) -> str:
    return grader._build_prompt(trial, RUBRIC, DIMENSIONS)


def changed_grader_names(before: dict[str, str], after: dict[str, str]) -> list[str]:
    return sorted(
        key
        for key in set(before) | set(after)
        if before.get(key) != after.get(key)
    )


async def measure(pre_fix_cls) -> dict[str, Any]:
    storage = MemoryStorage()
    runner = EvalRunner(
        agent_runner=MultiToolAgent(),
        trace_provider=MockTraceProvider(default_spans=[]),
        storage=storage,
        environment=ProbeEnv(),
        graders=[pre_fix_cls(llm_fn=order_sensitive_judge)],
    )
    run = await runner.run_suite(suite())
    trials = run.trials["multi_tool_task"]

    before_grader = pre_fix_cls(llm_fn=order_sensitive_judge)
    after_grader = ModelBasedGrader(llm_fn=order_sensitive_judge)
    inputs_changed = sum(
        1
        for trial in trials
        if prompt_of(before_grader, trial) != prompt_of(after_grader, trial)
    )
    before_success = [trial.success for trial in trials]
    before_first_tools = [first_tool(prompt_of(before_grader, trial)) for trial in trials]

    # 换成分代后的评分器再走真实重评路径 (不改判据、不改证据、不碰被评系统)
    runner._graders["model_based"] = ModelBasedGrader(llm_fn=order_sensitive_judge)
    regaded = await runner.regrade_run(run.run_id)
    after_success = [t.success for t in regaded.trials["multi_tool_task"]]

    pairs: dict[int, list] = {}
    for attempt in await storage.list_grade_attempts(run.run_id):
        pairs.setdefault(attempt.trial_index, []).append(attempt)

    return {
        "run_id": run.run_id,
        "hash_seed": os.environ.get("PYTHONHASHSEED", "random"),
        "grader_versions": [
            pre_fix_cls.implementation_version,
            ModelBasedGrader.implementation_version,
        ],
        "trials": len(trials),
        "judge_input_changed_trials": inputs_changed,
        "verdict_drift": await runner.verdict_drift(run.run_id),
        "before_first_tool_as_listed": before_first_tools,
        "after_first_tool_as_listed": [
            first_tool(prompt_of(after_grader, trial)) for trial in trials
        ],
        "before_success": before_success,
        "after_success": after_success,
        "flips_observed": sum(
            1
            for a, b in zip(before_success, after_success, strict=True)
            if a != b
        ),
        "other_caliber_axes_equal": all(
            a[0].judge_models == a[1].judge_models
            and a[0].mapping_version == a[1].mapping_version
            and a[0].spec_version == a[1].spec_version
            and a[0].statistics_version == a[1].statistics_version
            for a in pairs.values()
        ),
        "changed_grader_names": sorted(
            {
                name
                for pair in pairs.values()
                for name in changed_grader_names(
                    pair[0].grader_versions, pair[-1].grader_versions
                )
            }
        ),
        "statistics_version_after_regrade": regaded.statistics_version,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--rev",
        required=True,
        help=f"修复落地前的 git rev (其 {GRADER_PATH} 里应仍有 list(set(...)))",
    )
    args = parser.parse_args()

    source = show(args.rev)
    if "list(set(" not in source:
        print(
            f"{args.rev} 的 {GRADER_PATH} 里没有 list(set( —— 那是修复之后的实现。\n"
            "用它测只会得到「输入没变、0 翻转」, 而 0 翻转不能当作「修复无影响」的证据。",
            file=sys.stderr,
        )
        return 2
    print(json.dumps(asyncio.run(measure(load_grader_cls(source, "model_based_pre_fix"))),
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
