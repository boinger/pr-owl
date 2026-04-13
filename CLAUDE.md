# pr-owl

CLI tool that audits outbound GitHub PRs for mergeability and produces remediation plans.

## Tech Stack

- Python >=3.11, managed with `uv`
- Build: hatchling + hatch-vcs (version from git tags)
- CLI: Click | Output: Rich
- Linter: ruff (120 char, E/F/I rules)
- Tests: pytest

## Architecture

```
cli.py → discovery.py → checker.py → planner.py → output.py
                              ↑
                           gh.py (only module touching subprocess)
```

All GitHub interaction goes through `gh` CLI subprocess calls via `gh.py`.
Never use `shell=True`. Never import subprocess outside `gh.py`.

Remediation (rebase, conflict resolution, CI re-run) is handled by the Claude Code
skill (`skills/SKILL.md`), not the CLI. The CLI is a diagnostic tool only.

## Hard Rules

- All exceptions inherit from `PrOwlError` (in `exceptions.py`)
- No catch-all `except Exception` — name specific exception types
- `gh.py` is the only module that calls `subprocess.run`
- `shell=False` always — pass args as a list
- Each `check_pr()` call is wrapped in try/except in the audit loop — one bad PR must not crash the audit

## Commands

```bash
make dev          # install with dev deps
make test         # unit tests (excludes integration)
make integration-test  # tests hitting real GitHub
make lint         # ruff check
make format       # ruff format
make pre-commit   # format + lint + test
make run ARGS="audit --json"  # run CLI with args
```

## Key Files

| Module | Purpose |
|--------|---------|
| `src/pr_owl/cli.py` | Click entry point, audit command, pipeline orchestration |
| `src/pr_owl/gh.py` | gh CLI subprocess wrapper (only subprocess user) |
| `src/pr_owl/models.py` | Dataclasses: PRInfo, HealthReport, ClosedPRInfo, MergeStatus, etc. |
| `src/pr_owl/exceptions.py` | PrOwlError hierarchy |
| `src/pr_owl/state.py` | Cross-run state persistence (seen.json, schema v2, file locking) |
| `src/pr_owl/discovery.py` | PR search (open + closed) + stale filtering |
| `src/pr_owl/checker.py` | PR health classification + blocker detection |
| `src/pr_owl/planner.py` | Remediation plan generation |
| `src/pr_owl/output.py` | Rich table, JSON, plan output |
| `skills/SKILL.md` | Claude Code skill for guided remediation |
