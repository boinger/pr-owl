# Contributing to pr-owl

Run `make pre-commit` before opening a PR. This formats, lints, and runs the full test suite.

## Setup

```bash
git clone https://github.com/boinger/pr-owl.git
cd pr-owl
make dev
```

## Workflow

1. Fork the repo and create a branch from `main`
2. Make your changes
3. Run `make pre-commit` — all checks must pass
4. Open a PR with a clear description of what and why

## Guidelines

- **One concern per PR.** Don't bundle unrelated changes.
- **Commit messages:** Imperative mood, describe what changed and why. "Add timeout to subprocess calls" not "Added timeouts".
- **Tests:** If you change behavior, update or add tests. Run `make test` to verify.

## Architecture

A few guardrails to know about:

- `gh.py` is the **only module** that calls `subprocess.run`. All GitHub CLI interaction goes through it. Never import subprocess elsewhere.
- All exceptions inherit from `PrOwlError` in `exceptions.py`. No catch-all `except Exception` (the one in `cli.py` is an intentional resilience boundary).
- `shell=False` always. Pass args as a list, never a string.

## Running Tests

```bash
make test              # unit tests only
make integration-test  # hits real GitHub API (needs gh auth)
make lint              # ruff check
make pre-commit        # all of the above
```
