# Aeval

[English](./README.md) | [简体中文](./README.zh-CN.md)

**Aeval** is an open-source evaluation framework for AI agents, driven by OpenTelemetry traces. Define evaluation suites in YAML, run your agent against them (repeatedly, with retries and concurrency control), grade each trial with built-in or custom graders, and aggregate results into statistically meaningful metrics — `pass@k` / `pass^k` / consistency / saturation.

> **Naming**: this repository is **Aeval**; on PyPI the distribution is **`aeval-framework`** (`pip install aeval-framework`), the Python module is **`agent_eval`**, and the CLI is **`eval-suite`** — same convention as `pip install pillow` → `import PIL`. (`agent-eval` on PyPI belongs to an unrelated project.)

## Features

- **Suite as YAML** — tasks, graders, trial counts and thresholds in one declarative file with strict validation (semver, unique task ids).
- **9 built-in graders** — deterministic code checks, LLM-as-judge, environment state checks, tool-call validation, transcript analysis, artifact checks, human-in-the-loop scoring, step-level tracing, and metric dispatch.
- **Statistically sound aggregation** — `pass@k` (capability) and `pass^k` (reliability) with binomial extrapolation for `k > n`, consistency and saturation detection.
- **Pluggable everything** — agent runners, trace providers, storage backends, environments and graders are small protocols you implement.
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

- **Transcript process metrics lean optimistic in AChat**: the `transcript` grader's `n_turns` / token-usage process metrics depend on OTel span coverage that is incomplete when Aeval is embedded in AChat sessions — scores there skew optimistic. (The `toolcalls` dimension is fully covered.)
- **RAG & orchestration scenarios are in live calibration**: the mechanics ship and are tested (`env.agent_id` + `rag_search` for RAG suites, `dispatch_mode` + `achat_dispatch` for orchestration), but they have not yet been calibrated against production-scale workloads.
- **PostgreSQL storage & A/B significance testing are Phase 3**: the current storage backend is SQLite; a PostgreSQL backend and statistical significance tests for A/B comparison are on the roadmap, not implemented.
- **No human review UI yet**: the `human` grader works as a *pending* state resolved by score callback through the REST API; a dedicated review UI is not included.

## License

[MIT](./LICENSE)
