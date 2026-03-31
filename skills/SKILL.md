---
name: pr-owl
description: |
  Audit outbound GitHub PRs for mergeability and guide remediation.
  Use when: "check my PRs", "PR status", "stale PRs", "merge readiness",
  "pr-owl", "outbound PRs", "PR health", "what PRs need attention",
  "fix my PRs".
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

- **"fix"**, **"fix my PRs"**, **"rebase"** → **Fix mode**
- **"audit"**, **"check"**, **"status"** → **Audit mode**
- **"details"** → **Details mode**
- Ambiguous → **Audit mode**

---

## Fix Mode

When the user wants to fix things, act. Don't audit-then-interpret-then-ask.

### Step 1: Get PR data

```bash
pr-owl audit --json 2>/dev/null > /tmp/pr-owl-audit.json && python3 -c "
import json
data = json.load(open('/tmp/pr-owl-audit.json'))
for r in data:
    pr = r['pr']
    bl = [b['type'] for b in r['blockers']]
    print(f'{r[\"status\"]:12} {pr[\"repo\"]}#{pr[\"number\"]} head={r[\"head_ref\"]} base={r[\"base_ref\"]} head_repo={r[\"head_repo\"]} blockers={bl}')
"
```

### Step 2: For each non-ready PR, fix it

**BEHIND** (remote, no clone needed):
```bash
gh pr update-branch <number> -R <repo> --rebase
```

**CONFLICTS or CI_FAILING** (needs local clone):
1. Find the clone (see Clone Discovery below)
2. Run the Safety Checks
3. Rebase:
```bash
ORIG_BRANCH=$(git rev-parse --abbrev-ref HEAD)
git fetch <upstream_remote> <base_ref>
git fetch <push_remote> <head_ref>
git checkout <head_ref> || git checkout -b <head_ref> <push_remote>/<head_ref>
git rebase <upstream_remote>/<base_ref>
```
4. If conflicts: read the conflicting files, resolve the conflict markers, `git add` each, `git rebase --continue`. Repeat until done.
5. Push: `git push --force-with-lease <push_remote> <head_ref>`
6. Restore: `git checkout $ORIG_BRANCH`

**CI transient failures** (e.g., `error_api` in check logs):
```bash
gh run rerun <run_id> -R <repo> --failed
```

**BLOCKED** (reviews): report status, can't force reviews.

### Clone Discovery

1. Check `~/Projects/<repo-name>` (e.g., `~/Projects/loki` for `grafana/loki`)
2. Verify with `git remote -v` that any remote URL contains `owner/repo`
3. If not found, ask the user: "Where is your local clone of `<owner/repo>`?"
4. Never `find /` or guess blindly.

### Safety Protocol

Before touching any clone:
- `git status --porcelain` — if any output, stash or skip
- Check for `.git/REBASE_HEAD`, `.git/MERGE_HEAD`, `.git/CHERRY_PICK_HEAD`, `.git/BISECT_LOG` — if any exist, abort or skip
- Record current branch: `git rev-parse --abbrev-ref HEAD` — always restore when done
- If rebase fails or is interrupted: `git rebase --abort` — never leave a repo mid-rebase
- Push with `--force-with-lease` only (not `--force`)
- Verify clone identity: `git remote -v` must show a URL matching the PR's repo
- For fork PRs: push to the fork remote (origin), not upstream. The `head_repo` field in JSON tells you which remote to push to. Note: `headRepository.nameWithOwner` is always empty from `gh pr view` — use `headRepositoryOwner.login` + `headRepository.name` instead.

---

## Audit Mode

Show the table directly:
```bash
pr-owl audit 2>&1
```

Adapt: `--stale-days 14`, `--repo owner/repo`, `--status CONFLICTS`

---

## Details Mode

```bash
pr-owl audit --details 2>&1
```

---

## JSON (skill-internal only)

Only use JSON when you need to extract specific data (check URLs, head_repo for fix mode). Never dump it to the terminal. Always write to `/tmp/pr-owl-audit.json` and use the compact summary command above.

## Notes

- The CLI is diagnostic only. All remediation runs as direct git/gh commands from this skill.
- `--workers` controls concurrent health checks (default 5, 1=serial for debugging).
- JSON goes to stdout, table/details go to stderr.
