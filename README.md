# Aeval

[English](./README.md) | [简体中文](./README.zh-CN.md)

**Aeval** is an open-source evaluation framework for AI agents, driven by OpenTelemetry traces. Define evaluation suites in YAML, run your agent against them (repeatedly, with retries and concurrency control), grade each trial with built-in or custom graders, and aggregate results into statistically meaningful metrics — `pass@k` / `pass^k` / consistency / saturation.

> **Naming**: this repository is **Aeval**; on PyPI the distribution is **`aeval-framework`** (`pip install aeval-framework`), the Python module is **`agent_eval`**, and the CLI is **`eval-suite`** — same convention as `pip install pillow` → `import PIL`. (`agent-eval` on PyPI belongs to an unrelated project.)

## Features

- **Suite as YAML** — tasks, graders, trial counts and thresholds in one declarative file with strict validation (semver, unique task ids).
- **9 built-in graders** — deterministic code checks, LLM-as-judge, environment state checks, tool-call validation, transcript analysis, artifact checks, human-in-the-loop scoring, step-level tracing, and metric dispatch.
- **Statistically sound aggregation** — `pass@k` (capability) and `pass^k` (reliability) as finite-sample unbiased combinatorial estimates over **valid trials only**; `k > n` values are binomial extrapolations always flagged as such; every rate carries a Wilson 95% interval, every score a bootstrap interval plus mean/σ/`worst_of_n`, and process metrics p50/p95. Grader crashes, unconfigured criteria and unavailable judges are classified `invalid` and stay out of the denominator instead of being folded in as agent failures.
- **Evidence you can trace, gaps you can read** — every observation a trial delivers is tagged with **who observed it and when** (`observed_by: harness | runner | subject`), so a grader can be told "only trust what the evaluation side independently probed" and an agent's self-report cannot alone mark a trial passed. Spans are normalized once through a **version-pinned OTel GenAI translation table**, so a host's private attribute names are runtime config rather than framework constants. Every verdict reports which evidence it actually consumed and which fields were absent and why; a field that could not be read is *missing*, **not 0**. How a trial ended is decided by the framework (timeout / step·token·cost budget exhausted / error / cancelled each land in their own bucket — budget exhaustion counts as a failure but is tallied separately, timeout goes invalid and stays out of the denominator), and the four-way token split plus `cost_usd` are reported as an axis **parallel** to the pass rate, priced from an external table.
- **Collection and grading are separate phases** — evidence is archived per trial *before* grading, so re-grading an existing run never re-invokes the agent: fix a grader, swap the judge, tighten an evidence declaration and re-score the same bytes. Each verdict is appended with the caliber that produced it (grader implementation version, attribute-mapping and spec revision, judge model, timestamp) and moved by a `current` pointer — **historical verdicts are never overwritten**, so "how many trials did the judge upgrade flip" is a question you can actually answer.
- **Pluggable everything** — agent runners, trace providers, storage backends, environments and graders are small protocols you implement; so are the attribute mapping table, the evidence redaction hook (wholly replaced, never stacked on the default) and the price table.
- **REST API & SSE** — mount the eval API in any FastAPI app or serve it standalone (`/v1`), with run event streaming.
- **CLI** — `eval-suite run / validate / list / show / compare / serve`.
- **Datasets & LLM metrics** — build datasets from traces, regenerate suites, and score outputs with RAG-style quality metrics (answer relevancy, faithfulness, context recall/precision).

## Installation

```bash
pip install aeval-framework            # core (orchestration, graders, storage)
pip install "aeval-framework[api]"     # + REST API service
pip install "aeval-framework[cli]"     # + eval-suite CLI
```

Trace export to [Arize Phoenix](https://github.com/Arize-ai/phoenix) is optional: install `arize-phoenix` yourself and Aeval will pick it up lazily.

## Quickstart

`examples/minimal` runs fully offline with the bundled mock agent:

```bash
pip install "aeval-framework[cli]"
eval-suite run examples/minimal/suite.yaml
eval-suite list runs
eval-suite show <run_id>
```

A suite looks like this:

```yaml
name: my-first-suite
version: 1.0.0
tasks:
  - id: simple-qa
    prompt: What files are in the workspace?
    max_trials: 3
    graders:
      - type: code
        name: code_based
        config:
          checks:
            - type: contains
              value: "files"
              target: transcript
```

Point `eval-suite run` at your own agent by implementing the tiny `AgentRunner` protocol (see `examples/achat` for an HTTP adapter) and registering it via the `agent_eval.runners` entry-point group or the `--runner` option.

## Dashboard

A Next.js dashboard lives at [`apps/dashboard`](./apps/dashboard) (overview, suite management, live run reports via SSE, trial drill-down, A/B comparison).

## Documentation

- [Getting started](./docs/getting-started.md)
- [Integration guide](./docs/integration-guide.md)
- [Grader reference](./docs/grader-reference.md)
- [YAML format](./docs/yaml-format.md)
- [CLI reference](./docs/cli-reference.md)
- [Architecture](./docs/architecture.md)

## Known Limitations

Being upfront about what v0.1.0 does and does not do well:

- **Process evidence depends on being readable**: the earlier note here blamed AChat's `transcript` scores skewing optimistic on "incomplete OTel span coverage" — that diagnosis was wrong. The framework was reading a host-private set of span attribute names. Trace evidence is now normalized through a version-pinned OTel GenAI translation table (host names are mapping entries), and a field that cannot be read is reported as *missing with a reason* rather than zero — so an unmeasurable metric shrinks the denominator instead of flattering the score (missing evidence raises the `invalid` ratio, and the gate refuses to trust a run whose ratio is too high). The costs that genuinely remain: tool arguments and results are **not captured by default**, so parameter-level judgements are unavailable until a suite opts in (and even then only as redacted digests); `cost_usd` needs an externally configured price table and is reported as not computable without one; `token_budget` / `cost_budget` judgements are only as timely as the provider's usage reporting.
- **RAG & orchestration scenarios are in live calibration**: the mechanics ship and are tested (`env.agent_id` + `rag_search` for RAG suites, `dispatch_mode` + `achat_dispatch` for orchestration), but they have not yet been calibrated against production-scale workloads.
- **Statistical comparison is interval-overlap only**: `compare` (CLI, REST and Dashboard) refuses a directional conclusion when the two 95% intervals overlap, and treats runs as not comparable when either their statistics caliber or their **evidence boundary** differs (argument-capture switch, spec/mapping versions, redactor identity) — `not_comparable_reason` names which dimension differs, and a run with no recorded boundary is treated as not directly comparable rather than assumed to match today's. It does not run a formal hypothesis test, correct for multiple comparisons, or model trial-level correlation — with small `n` the intervals are wide, and "not significant" is not evidence of "no change".
- **`pass@1` semantics changed in this release**: it is now the unbiased success proportion over valid trials (`c/n`), where the pre-fix value meant "at least one success in n". **Historical runs are not recomputed** — they carry `statistics_version = null` and are reported as an unknown caliber. After upgrading, a build that a gate previously passed may start failing (the old gate passed on luck and on folded-in grader failures); see [getting started](./docs/getting-started.md) for what to check. The same rule covers the fields added alongside this change: historical trials report `termination_reason = "unknown"` and a null evidence boundary / resource axis — never 0.
- **Provenance is declarable, not enforceable**: the `observed_by` level on a reading is set by whoever produced it. Evidence the framework collects itself — the environment's `probe()` call, invoked by the framework before teardown — is stamped `harness` and cannot be raised by the evaluated side, but an integration that labels its own self-report as `harness` in the object it returns is not something the data can contradict. What this change buys is that lying now takes an explicit, recorded form (`allow_subject: true` in a suite, a fabricated probe in an adapter) and shows up on the verdict; actually *forcing* the boundary needs environment isolation, which Aeval deliberately does not do (no containers, no egress control — it is a dev-time tool you install with `pip`).
- **Separating collection from grading is a breaking contract change**: `AgentRunner.run(view, session)` now returns a `TrialEvidence` instead of the old `(trace_id, transcript, outcome)` triple, and there is **no compatibility layer** — a bypassable provenance split would be no split. Two consequences are stated rather than hidden. (1) Runs archived before this change are **readable but not re-gradable**: they kept only triple-shaped output, so provenance and evidence boundaries were lost at the scene, and re-scoring them from a merger of the two would just produce another wrong number. They read back with `evidence = null`, `regradeable = false`, an unknown statistics caliber, and an explicit reason line in the API/CLI (`Regrade: no — …`), and are not comparable with post-change runs. (2) `regrade` is currently a **library-only** entry point (`EvalRunner.regrade_run` / `verdict_drift`); the HTTP and CLI surfaces deliberately do not expose it yet, since they carry the same-major-version compatibility promise and warrant their own change.
- **`state_check` no longer trusts what the agent says it did**: an environment-state verdict is decided by evaluation-side probes first and adapter-delivered state second, and a pass supported *only* by self-reported content is rejected as `invalid` (`subject_only_evidence`) unless a suite opts into `allow_subject`. Suites that previously went green because the agent mentioned a file name will now fail or go invalid. That is a false green being removed, not a regression — but it will look like one in CI until the suites are re-read.
- **PostgreSQL storage is Phase 3**: the current storage backend is SQLite.
- **No human review UI yet**: the `human` grader works as a *pending* state resolved by score callback through the REST API; a dedicated review UI is not included.

## License

[MIT](./LICENSE)
