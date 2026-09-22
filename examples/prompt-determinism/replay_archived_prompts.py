"""把归档里的真实 trial 分别喂给换代前后两版 `_build_prompt`, 比较判分输入字节。

这是 tasks 6.1 首选分支（真实历史 run）的入口。它**不需要 LLM 凭证**: 量的不是翻转率,
而是「这次换代改写了多少条真实 trial 的判分输入」—— 输入没变的 trial 不可能因这次修复
而翻判, 所以这个数是真实翻转数的**上界**, 且比替身 judge 给出的翻转数更硬。

依赖宿主库路径与越界读取授权, 因此**不进 CI**, 只作人工验收入口。只读打开
(`mode=ro&immutable=1`) 且只打印聚合量 —— 不打印任何 trial 正文。

跑法 (套件 YAML 提供当年每个任务的 rubric/dimensions, 可传多个):

    python examples/prompt-determinism/replay_archived_prompts.py \
        --db <aeval.db 路径> --suite <suite.yaml 路径> --rev <修复落地前的 rev>

宿主归档那次实测的记录见变更 tasks 6.1 (读到的 run 里 model_based 结论为 0)。
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import re
import sqlite3
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages" / "agent-eval" / "src"))

from agent_eval.core.suite import load_suite  # noqa: E402
from agent_eval.core.types import TrialResult  # noqa: E402
from agent_eval.graders.model_based import ModelBasedGrader  # noqa: E402

GRADER_PATH = "packages/agent-eval/src/agent_eval/graders/model_based.py"


def load_pre_fix_cls(rev: str):
    """从 git 取换代前的 model_based.py 原文加载 —— 不手写仿制品。"""
    text = subprocess.run(
        ["git", "show", f"{rev}:{GRADER_PATH}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout
    if "list(set(" not in text:
        raise SystemExit(
            f"{rev} 的 {GRADER_PATH} 里没有 list(set( —— 那是修复之后的实现, 重放只会得到 0 差异"
        )
    path = Path(tempfile.mkdtemp(prefix="model_based_pre_fix_")) / "pre_fix.py"
    path.write_text(text, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("model_based_pre_fix", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ModelBasedGrader


def model_based_configs(suite_paths: list[Path]) -> dict[str, list[dict[str, Any]]]:
    """task_id → 该任务上每个 model_based 判据的 rubric/dimensions (照原配置拼提示词)。"""
    configs: dict[str, list[dict[str, Any]]] = {}
    for suite_path in suite_paths:
        for task in load_suite(suite_path).tasks:
            for grader in task.graders:
                if grader.name != "model_based":
                    continue
                cfg = dict(grader.config or {})
                configs.setdefault(task.id, []).append(
                    {
                        "rubric": str(cfg.get("rubric", "")),
                        "dimensions": list(cfg.get("dimensions") or ["quality"]),
                    }
                )
    return configs


def tools_line(prompt: str) -> list[str]:
    match = re.search(r"^- 使用的工具: (.*)$", prompt, re.MULTILINE)
    if not match:
        return []
    raw = match.group(1)
    return list(ast.literal_eval(raw if raw != "无" else "[]"))


def replay(
    db_path: Path,
    configs: dict[str, list[dict[str, Any]]],
    suite_paths: list[Path],
    rev: str,
) -> dict[str, Any]:
    pre_fix_cls = load_pre_fix_cls(rev)
    before_grader = pre_fix_cls()
    after_grader = ModelBasedGrader()
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro&immutable=1", uri=True)
    report: dict[str, Any] = {
        "db": db_path.name,
        "pre_fix_rev": rev,
        "suites": [p.name for p in suite_paths],
        "runs": [],
    }

    for run_id, suite_name, data in conn.execute(
        "select run_id, suite_name, data from runs order by started_at"
    ):
        run = json.loads(data)
        trials: dict[str, list] = run.get("trials") or {}
        summary = {
            "run_id": run_id,
            "suite_name": suite_name,
            "statistics_version": run.get("statistics_version"),
            "trials": sum(len(items) for items in trials.values()),
            "model_based_verdicts": 0,
            "grader_versions_seen": Counter(),
            "replayed_trials": 0,
            "multi_tool_trials": 0,
            "input_changed_trials": 0,
            "input_unchanged_trials": 0,
            "no_config_for_task": 0,
        }
        for task_id, items in trials.items():
            cfgs = configs.get(task_id) or []
            for raw in items:
                trial = TrialResult.model_validate(raw)
                graded = [r for r in trial.grader_results if r.grader_name == "model_based"]
                if not graded:
                    continue
                summary["model_based_verdicts"] += len(graded)
                summary["grader_versions_seen"].update(
                    str(result.details.get("grader_version")) for result in graded
                )
                if not cfgs:
                    summary["no_config_for_task"] += 1
                    continue
                summary["replayed_trials"] += 1
                changed = False
                multi_tool = False
                for cfg in cfgs:
                    before = before_grader._build_prompt(trial, cfg["rubric"], cfg["dimensions"])
                    after = after_grader._build_prompt(trial, cfg["rubric"], cfg["dimensions"])
                    multi_tool |= len(set(tools_line(after))) >= 2
                    changed |= before.encode("utf-8") != after.encode("utf-8")
                summary["multi_tool_trials"] += int(multi_tool)
                summary["input_changed_trials"] += int(changed)
                summary["input_unchanged_trials"] += int(not changed)
        summary["grader_versions_seen"] = dict(summary["grader_versions_seen"])
        report["runs"].append(summary)
    conn.close()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", required=True, help="aeval.db 路径 (只读打开)")
    parser.add_argument(
        "--suite", action="append", required=True, help="当年用的套件 YAML, 可重复传"
    )
    parser.add_argument("--rev", required=True, help="修复落地前的 git rev")
    args = parser.parse_args()

    db_path = Path(args.db).resolve()
    if not db_path.is_file():
        print(f"找不到归档库: {db_path}", file=sys.stderr)
        return 2
    suite_paths = [Path(p).resolve() for p in args.suite]
    report = replay(db_path, model_based_configs(suite_paths), suite_paths, args.rev)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
