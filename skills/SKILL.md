---
name: pr-owl
description: |
  Audit outbound GitHub PRs for mergeability and guide remediation.
  Use when: "check my PRs", "PR status", "stale PRs", "merge readiness",
  "pr-owl", "outbound PRs", "PR health", "what PRs need attention",
  "fix my PRs", "rebase needed", "upstream updated", "get rebased",
  "merge upstream", "fix conflicts", "bring branch current",
  "sync with upstream", "get up to date".
---

# pr-owl: PR Health Audit & Remediation

## Prerequisites

```bash
which pr-owl && gh auth status 2>&1 | head -3
```

If not installed:
```bash
uv tool install --from git+https://github.com/boinger/pr-owl.git pr-owl
```

## Mode Detection

- **"fix"**, **"fix my PRs"**, **"rebase"**, **"upstream"**, **"conflicts"**, **"sync"**, **"up to date"** → **Fix mode**
- **"audit"**, **"check"**, **"status"** → **Audit mode**
- **"details"** → **Details mode**
- Ambiguous → **Audit mode**

---

## Fix Mode

When the user wants to fix things, act. Don't audit-then-interpret-then-ask.

### Step 1: Get PR data

Write JSON to temp file (NEVER dump to terminal), then summarize to yourself:

```bash
pr-owl audit --json 2>/dev/null > /tmp/pr-owl-audit.json && echo "Audit saved"
```

Then use the Read tool to read `/tmp/pr-owl-audit.json`. Parse it yourself to build the fix plan. Present a brief summary to the user of what you found and what you're about to do.

### Step 2: For each non-ready PR, fix it

Process in this order: BEHIND first, then CONFLICTS/CI_FAILING, then report BLOCKED.

**BEHIND** (remote, no clone needed):
```bash
gh pr update-branch <number> -R <repo> --rebase
```

**CONFLICTS or CI_FAILING** (needs local clone):
1. Find the clone (see Clone Discovery below)
2. Run the Safety Checks (see Safety Protocol below)
3. Determine remotes (see Remote Identification below)
4. Rebase:
```bash
ORIG_BRANCH=$(git rev-parse --abbrev-ref HEAD)
git fetch <upstream_remote> <base_ref>
git fetch <push_remote> <head_ref>
git checkout <head_ref> || git checkout -b <head_ref> <push_remote>/<head_ref>
git rebase <upstream_remote>/<base_ref>
```
5. If conflicts: read the conflicting files, resolve the conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`), `git add` each resolved file, `git rebase --continue`. Repeat for each conflicting commit.
6. Push: `git push --force-with-lease <push_remote> <head_ref>`
7. Restore: `git checkout $ORIG_BRANCH`

**CI_FAILING with no conflicts** (branch is current but CI failed):
Check if failures are transient or real:
```bash
gh run view <run_id> -R <repo> --log-failed 2>&1 | tail -30
```
- If logs show `error_api`, `rate limit`, `timeout`, or infrastructure errors → re-run failed jobs:
  ```bash
  gh run rerun <run_id> -R <repo> --failed
  ```
- If logs show real test failures (assertion errors, lint errors, type errors) → report to user with the specific failures. Don't re-run blindly.

**BLOCKED** (reviews): report status. Can't force reviews.

### Step 3: Verify

After fixing, re-run the audit to confirm:
```bash
pr-owl audit 2>&1
```
Report what changed: which PRs moved from CONFLICTS/CI_FAILING to READY or BEHIND.

### Clone Discovery

Search these paths in order for `owner/repo` (e.g., `grafana/loki`):
1. `~/Projects/<owner>/<repo>` (owner-nested, e.g., give-back workspaces)
2. `~/Projects/<repo>` (flat layout)
3. Ask the user: "Where is your local clone of `owner/repo`?"

For each candidate directory that exists, verify with `git remote -v` that ANY remote URL contains `owner/repo`. This handles forks correctly (clone has `origin → your-fork`, `upstream → base-repo`).

Never `find /` or guess blindly. If no clone is found and the user doesn't know, skip that PR and report it.

### Remote Identification

A fork clone typically has two remotes:
- `origin` → your fork (e.g., `boinger/loki`) — this is where you push
- `upstream` → the base repo (e.g., `grafana/loki`) — this is what you rebase against

To determine which is which:
```bash
git remote -v
```
- The remote whose URL contains the PR's `repo` field (the base repo, e.g., `grafana/loki`) is the **upstream remote** (rebase target)
- The remote whose URL contains the PR's `head_repo` field (your fork, e.g., `boinger/loki`) is the **push remote**

If both point to the same repo (same-repo PR, not a fork), use the same remote for both.

### Safety Protocol

Before touching any clone:
- `git status --porcelain` — if any output, stash or skip (don't lose work)
- Check for `.git/REBASE_HEAD`, `.git/MERGE_HEAD`, `.git/CHERRY_PICK_HEAD`, `.git/BISECT_LOG` — if any exist, an operation is in progress. Either abort it (`git rebase --abort`) or skip.
- Record current branch: `ORIG_BRANCH=$(git rev-parse --abbrev-ref HEAD)` — always restore when done, even on failure
- If rebase fails mid-way: `git rebase --abort` then `git checkout $ORIG_BRANCH` — never leave a repo mid-rebase
- Push with `--force-with-lease` only (never `--force`)
- Verify clone identity: `git remote -v` must show a URL matching the PR's repo before operating
- For fork PRs: push to the fork remote, not upstream. The `head_repo` field in the JSON identifies which remote to push to.

---

## Audit Mode

Show the table directly:
```bash
pr-owl audit 2>&1
```

Adapt: `--stale-days 14`, `--repo owner/repo`, `--status CONFLICTS`

After showing the table, briefly summarize what needs attention and offer to fix.

---

## Details Mode

```bash
pr-owl audit --details 2>&1
```

---

## Notes

- The CLI is diagnostic only. All remediation runs as direct git/gh commands from this skill.
- `--workers` controls concurrent health checks (default 5, 1=serial for debugging).
- JSON goes to stdout, table/details go to stderr.
- `headRepository.nameWithOwner` is always empty from `gh pr view`. The JSON uses `headRepositoryOwner.login` + `headRepository.name` to construct `head_repo`.
