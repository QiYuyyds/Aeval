"""
Core types and contracts for the Aeval evaluation framework.
"""

from agent_eval.core.contract import (
    AgentRunner,
    EnvironmentManager,
    Grader,
    Storage,
    TraceProvider,
)
from agent_eval.core.metrics import (
    aggregate_metrics,
    bootstrap_ci,
    classify_trial,
    pass_at_k,
    pass_power_k,
    percentile,
    split_trials_by_verdict,
    valid_trials,
    wilson_interval,
    worst_of_n,
)
from agent_eval.core.types import (
    STATISTICS_VERSION,
    EvalSuite,
    EvalTask,
    GraderConfig,
    GraderResult,
    GraderType,
    InvalidReason,
    PassKEstimate,
    RunResult,
    RunSummary,
    ScoreDistribution,
    ScoreStrategy,
    TaskSummary,
    TrialResult,
    TrialVerdict,
)

__all__ = [
    # Types
    "EvalTask",
    "EvalSuite",
    "GraderConfig",
    "GraderResult",
    "GraderType",
    "InvalidReason",
    "PassKEstimate",
    "RunResult",
    "RunSummary",
    "ScoreDistribution",
    "ScoreStrategy",
    "TaskSummary",
    "TrialResult",
    "TrialVerdict",
    "STATISTICS_VERSION",
    # Contracts
    "AgentRunner",
    "EnvironmentManager",
    "Grader",
    "Storage",
    "TraceProvider",
    # Metrics
    "aggregate_metrics",
    "bootstrap_ci",
    "classify_trial",
    "pass_at_k",
    "pass_power_k",
    "percentile",
    "split_trials_by_verdict",
    "valid_trials",
    "wilson_interval",
    "worst_of_n",
]
