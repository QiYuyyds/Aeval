# Contributing to Aeval

Thanks for your interest in contributing! This document covers the minimal setup to get you productive.

## Development Setup

Requirements: **Python 3.11+**, [pnpm](https://pnpm.io) 9+ (only for the dashboard).

```bash
git clone https://github.com/QiYuyyds/Aeval.git
cd Aeval

# Python package (editable install with all extras)
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e "packages/agent-eval[api,cli,dev]"
```

### Tests & Lint

```bash
cd packages/agent-eval
pytest tests/ -q                 # run the test suite
ruff check src tests             # lint
```

The offline test suite needs no external services; a handful of LLM-judge tests are skipped unless API credentials are present.

### Dashboard

```bash
pnpm install
pnpm --filter eval-dashboard dev   # http://localhost:3100
```

The dashboard expects a running eval API; see `apps/dashboard/README.md` and `docs/getting-started.md`.

## Pull Requests

1. Fork the repo and create a topic branch from `main`.
2. Keep changes focused — one concern per PR.
3. Make sure `pytest` and `ruff check` pass locally.
4. For new grader types, runner protocols or storage backends, add tests and a short section in the relevant doc under `docs/`.
5. Open the PR against `main` with a short description of the what and the why.

By submitting a PR you agree that your contribution is licensed under the [MIT License](./LICENSE).
