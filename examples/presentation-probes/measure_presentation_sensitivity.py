"""本脚本是测试套件的补充而非重复 (P0 的 D7 教训: 验收记录不得引用只存在于本机的代码):
机制断言留在 pytest 里常驻, 这里留的是"按同样方法重跑出同样的数"的入口, 以及有凭证后
直接跑真实测量的那条路。运行方式见同目录 README.md。
就同一份归档证据重问 judge 几次, 量结论稳不稳 —— 呈现探针的验收与复现入口。

默认**零凭证**跑两个构造性替身 (change add-judge-presentation-probes 的 tasks 7.1):

- ``anchor_reactive``: 判据按定义把分数回归到提示词展示的预填值 —— 探针 MUST 报出敏感;
- ``body_only``: 判据只读证据正文 —— 探针 MUST NOT 误报。

两条合起来只证明**机制有效**, 不证明真实 judge 敏感或不敏感。本脚本印出的每一个数字
都来自替身判据, 引用时不得冠以"真实 LLM 的翻转率"。

真实锚定敏感度要等一把可用的 judge 凭证: 加 ``--live`` 就走 ``ModelBasedGrader`` 的默认
实现 (``OPENAI_API_KEY``, 可选 ``--model``), 入口与代码一行不改。宿主四把候选凭证截至
2026-09-22 全为 401/402 —— 那种情况下探针会明明白白报「不可计算」并带上失败原因,
而不是给出一个冒充测过的「未检出」。

跑法 (仓库根):

    python examples/presentation-probes/measure_presentation_sensitivity.py
    python examples/presentation-probes/measure_presentation_sensitivity.py --live
    python examples/presentation-probes/measure_presentation_sensitivity.py \\
        --live --trials-json /path/to/trials.json --sample 0.5 --seed 20260922

``--trials-json`` 接受 ``[{"transcript": [...]}]`` 或 ``[TrialResult.model_dump(), ...]``
(从任一 run 导出); 缺省时用脚本内置的 6 份同内容夹具 —— 恰好越过 κ/α 的对齐样本门槛
(5), 让"未检出"能作为一条结论印出来。真归档往往不足 5 trial, 那种情况下报「不可计算」
是对的, 不要为了出数而伪造样本。

默认模式下本脚本自校验: 机制若失效 (敏感没报出来、或不敏感被误报) 退出码为 1。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages" / "agent-eval" / "src"))

from agent_eval.core.types import (  # noqa: E402
    EvalTask,
    GraderConfig,
    GraderType,
    TrialResult,
)
from agent_eval.graders.model_based import ModelBasedGrader  # noqa: E402
from agent_eval.graders.presentation_probes import (  # noqa: E402
    DEFAULT_PROBE_OPERATORS,
    presentation_probe_cost_quote,
    run_presentation_probes,
)

DIMENSIONS = ["quality", "completeness"]
THRESHOLD = 0.7
RUBRIC = "报告须含订单总数"

EXAMPLE_JSON = re.compile(r"^\{.*\}$", re.MULTILINE)


def judge_task() -> EvalTask:
    return EvalTask(
        id="t1",
        prompt="统计这批订单并把总数写进报告",
        max_trials=6,
        graders=[
            GraderConfig(
                type=GraderType.MODEL,
                name="model_based",
                config={"rubric": RUBRIC, "dimensions": DIMENSIONS, "threshold": THRESHOLD},
            )
        ],
    )


def fixture_trials(n: int = 6) -> list[TrialResult]:
    """内置夹具: 同一份证据 n 份。默认 6 份 = 刚好够 κ/α 起算, 不是为了像真数据。"""
    return [
        TrialResult(
            trial_index=i,
            transcript=[
                {"role": "user", "content": "统计这批订单"},
                {"role": "assistant", "content": "订单总数 42, 报告已写好"},
            ],
        )
        for i in range(n)
    ]


def trials_from_json(path: str) -> list[TrialResult]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not raw:
        raise SystemExit(f"{path} 里要是一个非空的 trial 对象数组")
    return [
        TrialResult.model_validate(item if "trial_index" in item else {**item, "trial_index": i})
        for i, item in enumerate(raw)
    ]


def anchor_of(prompt: str) -> str:
    """提示词示例 JSON 里那个预填值 —— 锚就住在这里。"""
    example = EXAMPLE_JSON.search(prompt)
    if example is None:
        raise AssertionError("提示词里没有示例 JSON")
    return re.search(r":\s*([^,}]+)", example.group(0)).group(1).strip()


def _scores(values: dict[str, float]) -> str:
    return "{\n" + ",\n".join(f'"{d}": {v}' for d, v in values.items()) + "\n}"


class AnchorReactiveJudge:
    """构造敏感替身: 分数回归到展示值 (judge 的已知行为之一), 读不出锚时退到 0.5。"""

    def __call__(self, system: str, user: str) -> str:
        anchor = anchor_of(user)
        value = float(anchor) if re.fullmatch(r"\d+(?:\.\d+)?", anchor) else 0.5
        return _scores({d: value for d in DIMENSIONS})


class BodyOnlyJudge:
    """构造不敏感替身: 只读被评正文, 对呈现细节无感。"""

    def __call__(self, system: str, user: str) -> str:
        output = re.search(r"^- 输出: (.*)$", user, re.MULTILINE).group(1)
        value = 1.0 if "订单总数" in output else 0.0
        dims = re.search(r"^## 评分维度\n(.*)$", user, re.MULTILINE).group(1)
        return _scores({d.strip(): value for d in dims.split(",")})


SUBSTRATES: dict[str, Any] = {
    "anchor_reactive": AnchorReactiveJudge,
    "body_only": BodyOnlyJudge,
}


def print_report(report, *, label: str) -> None:
    print(f"\n=== {label} ===")
    print(
        f"抽样: sample={report.sample} seed={report.seed} "
        f"trial {report.trials_sampled}/{report.trials_total} · "
        f"呈现 {report.variants_per_trial} 份/trial · judge 调用 {report.judge_calls} 次"
    )
    print(f"总判定: {report.status}")
    for name, op in report.by_operator.items():
        stat = "n/a" if op.value is None else f"{op.value:.4f}"
        print(
            f"\n[{name}] {op.status} · {op.measure or 'n/a'}={stat} · "
            f"翻转 {op.flipped_trials} trial · 分数位移 max "
            f"{'n/a' if op.max_score_shift is None else format(op.max_score_shift, '.4f')}"
        )
        print(f"  呈现: {', '.join(op.presentations)}")
        print(f"  不变量: {op.invariant}")
        print(f"  判读: {op.reason}")
        if op.flip_presentations:
            print(f"  与基线结论不同的呈现: {', '.join(op.flip_presentations)}")
    print(f"\n解释边界: {report.interpretation_note}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--live", action="store_true", help="用真实 judge 问 (OPENAI_API_KEY)")
    parser.add_argument("--model", default=None, help="真实 judge 的模型标识 (--live 时用)")
    parser.add_argument("--trials-json", default=None, help="归档证据导出的 trial 数组")
    parser.add_argument("--sample", type=float, default=None, help="抽样比例 (0-1], 无默认")
    parser.add_argument("--seed", type=int, default=2026_0922, help="抽样种子, 落进报告")
    args = parser.parse_args(argv)

    if args.sample is None:
        # 内核不给默认抽样比例 (design D5): 调用点必须自己说出要抽多少
        args.sample = 1.0 if args.trials_json is None else 0.5

    task = judge_task()
    # 真 judge 的配置要带模型标识, 否则沿用 grader 的 DEFAULT_JUDGE_MODEL
    if args.live and args.model:
        task.graders[0].config["model"] = args.model

    trials = trials_from_json(args.trials_json) if args.trials_json else fixture_trials()
    quote = presentation_probe_cost_quote(
        n_trials=len(trials), sample=args.sample, operators=DEFAULT_PROBE_OPERATORS
    )

    if args.live:
        print(f"报价 (跑之前先看): {quote['variants_per_trial']} 份呈现 × "
              f"{quote['trials_sampled']} trial = {quote['judge_calls']} 次真实 judge 调用")
        if not os.getenv("OPENAI_API_KEY"):
            print("OPENAI_API_KEY 未设置 —— 探针会如实报「不可计算」并带上失败原因")
        report = run_presentation_probes(
            ModelBasedGrader(), task=task, trials=trials, sample=args.sample, seed=args.seed
        )
        print_report(report, label="live judge (真实幅度, 非替身)")
        return 0

    print("零凭证模式: judge 是构造性替身。以下数字证明探针有效, 不证明真实 judge 敏不敏感。")
    print(f"报价: 每个抽中 trial {quote['variants_per_trial']} 份呈现 = "
          f"{quote['judge_calls']} 次调用 (夹具共 {len(trials)} trial)")
    failures: list[str] = []
    for label, substrate in SUBSTRATES.items():
        report = run_presentation_probes(
            ModelBasedGrader(llm_fn=substrate()),
            task=task,
            trials=trials,
            sample=args.sample,
            seed=args.seed,
        )
        print_report(report, label=f"替身 {label}")
        if label == "anchor_reactive" and not report.any_sensitive:
            failures.append("构造敏感的替身没被报出敏感 —— 探针发现不了东西")
        if label == "body_only" and report.any_sensitive:
            failures.append("构造不敏感的替身被误报 —— 探针成了「总能报出点什么」的那种东西")

    print("\n机制自校验:", "通过" if not failures else "失败")
    for line in failures:
        print(" -", line)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
