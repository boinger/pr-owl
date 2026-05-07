"""Persistent state for cross-run comment delta tracking.

This is the only module that touches the state file on disk. It mirrors the
gh.py discipline (only module that calls subprocess) so future persistence
features land here instead of scattering file I/O across the codebase.

State file lives at $XDG_STATE_HOME/pr-owl/seen.json (fallback
~/.local/state/pr-owl/seen.json). Per-user, Unix-only.

Concurrency: an advisory `fcntl.flock` on the parent directory serializes the
load→merge→save cycle. The save_state path re-loads from disk inside the lock
so that two parallel `pr-owl audit` invocations both observe the latest state
when they merge their own updates. Without that re-load the lock would only
serialize writes, not the read-modify-write cycle, and one writer would
silently overwrite the other's changes.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from pr_owl.exceptions import StateError
from pr_owl.models import MergeStatus

if TYPE_CHECKING:
    from pr_owl.models import HealthReport

logger = logging.getLogger(__name__)

CURRENT_VERSION = 3
_FILENAME = "seen.json"
_READ_ONLY_KEY = "_read_only"  # Sentinel set by load_state on higher-version files.
_SCHEMA_RESET_KEY = "_schema_reset"  # Sentinel set when loaded version differs from CURRENT_VERSION.


def state_path() -> Path:
    """Return the resolved state file path.

    Honors XDG_STATE_HOME; falls back to ~/.local/state. Empty env var is
    treated as unset (POSIX convention).
    """
    xdg = os.environ.get("XDG_STATE_HOME") or ""
    base = Path(xdg) if xdg else Path.home() / ".local" / "state"
    return base / "pr-owl" / _FILENAME


def is_valid_pr_url(url: str) -> bool:
    """True iff `url` is a non-empty github.com PR URL.

    Used as a filter before keying state. Defends against the `gh search`
    returning `url=""` collision case where every PR with empty URL would
    map to the same key.
    """
    return bool(url) and url.startswith("https://github.com/")


def load_state() -> dict:
    """Load state from disk. Returns {} on missing or higher-version files.

    Failure modes:
    - Missing file: returns {} (first run).
    - Corrupt JSON: renames the bad file to seen.json.corrupt-<timestamp>,
      logs a loud warning, returns {}. The next save will write a fresh file.
    - Higher schema version (downgrade): returns {} with `_read_only=True`
      so save_state will refuse to overwrite. Logs a warning. This prevents
      silent data loss when a user upgrades pr-owl, gets a v2 schema, then
      downgrades back to v1 — their v2 state is preserved untouched.
    - OSError on read (perms, dir-instead-of-file): raises StateError.
    """
    path = state_path()
    if not path.exists():
        return {}

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise StateError(f"could not read state file at {path}: {exc}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        backup = path.with_name(f"{path.name}.corrupt-{int(time.time())}")
        try:
            path.rename(backup)
        except OSError as rename_exc:
            logger.warning(
                "state file at %s is corrupt (%s); also failed to rename to %s (%s); "
                "treating as empty. Re-run with --no-state to inspect before pr-owl writes a new file.",
                path,
                exc,
                backup,
                rename_exc,
            )
        else:
            logger.warning(
                "state file at %s was corrupt (%s); preserved as %s; treating as empty. "
                "Re-run with --no-state to inspect before pr-owl writes a new file.",
                path,
                exc,
                backup,
            )
        return {}

    if not isinstance(data, dict):
        # Valid JSON but wrong shape (e.g. a list at the top level). Treat
        # like a corrupt file: preserve and start fresh.
        backup = path.with_name(f"{path.name}.corrupt-{int(time.time())}")
        try:
            path.rename(backup)
        except OSError:
            pass
        logger.warning(
            "state file at %s had unexpected top-level type %s; preserved as %s; treating as empty.",
            path,
            type(data).__name__,
            backup,
        )
        return {}

    version = data.get("version", 0)
    if isinstance(version, int) and version > CURRENT_VERSION:
        logger.warning(
            "state file at %s is from a newer pr-owl version (schema v%d > current v%d); "
            "treating as empty AND refusing to overwrite. Your previous state is preserved. "
            "Upgrade pr-owl to the matching version, or delete %s to start fresh.",
            path,
            version,
            CURRENT_VERSION,
            path,
        )
        return {_READ_ONLY_KEY: True}

    if isinstance(version, int) and version < CURRENT_VERSION:
        # Schema changed; old per-PR fields don't map cleanly to the new ones,
        # so we drop the v<CURRENT baseline entirely and rebuild on next save.
        # First run after upgrade will show no comment deltas; subsequent runs
        # behave normally. Surface as a warning so the user understands why
        # `*` markers vanished for one cycle (default log level is WARNING).
        logger.warning(
            "State schema upgraded (v%d→v%d); comment deltas reset for this run.",
            version,
            CURRENT_VERSION,
        )
        return {_SCHEMA_RESET_KEY: True}

    logger.debug("state: loaded %d PRs from %s", len(data.get("prs") or {}), path)
    return data


def get_last_audit_at(state: dict) -> datetime | None:
    """Extract the last_audit_at timestamp from loaded state.

    Returns None if the field is absent (v1 state files, first run, or
    --no-state). Callers use this to determine the since-last-run cutoff
    for the closed-PR table.
    """
    raw = state.get("last_audit_at")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        logger.warning("state: could not parse last_audit_at '%s'; treating as absent", raw)
        return None


def compute_delta(report: HealthReport, state: dict) -> int:
    """Return new_comments for this report vs the stored baseline.

    First-seen URLs return 0: the PR is new to us, not "all comments are new."
    This matches user intuition — the first time pr-owl sees a PR, nothing is
    flagged; deltas appear on subsequent runs.

    Negative deltas (a comment was deleted) clamp to 0. We never display
    negative numbers; the next save will update the stored count to the new
    smaller value silently.
    """
    if not is_valid_pr_url(report.pr.url):
        return 0
    prs = state.get("prs") or {}
    prior = prs.get(report.pr.url)
    if not prior:
        return 0
    return max(0, report.comment_count - int(prior.get("comment_count", 0)))


def save_state(state: dict, reports: list[HealthReport]) -> None:
    """Persist comment counts to disk, merging with the latest on-disk state.

    The merge predicate is positive: a report is merged into state if and
    only if its `status != UNKNOWN AND error == ""` AND it has a valid github
    PR URL. Reports that fail this check are skipped — their existing state
    entries (if any) are preserved untouched. This is the difference between
    a merge and an overwrite: a transient check failure must not lose a
    baseline.

    Concurrency: takes an advisory `fcntl.flock` on the parent directory.
    Inside the lock, re-loads from disk so the merge sees any updates from
    a parallel `pr-owl audit` that finished while we were checking PRs.
    Without the re-load, the lock only serializes writes — but two parallel
    audits would each read at T0, do their work, take the lock in sequence,
    and the second writer would overwrite the first writer's changes with
    its own stale-at-T0 view. The re-load inside the lock is what makes the
    load→merge→save cycle actually atomic.

    No-ops:
    - State marked read-only (higher schema version detected on load).
    - All reports filtered out (no valid updates to make).
    """
    if state.get(_READ_ONLY_KEY):
        logger.warning("state save skipped: file is from a newer pr-owl version (read-only mode)")
        return

    path = state_path()
    parent = path.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise StateError(f"could not create state directory {parent}: {exc}") from exc

    # Lock the parent directory across the load→merge→save cycle. fcntl.flock
    # is advisory and Unix-only, matching pr-owl's existing platform scope.
    try:
        lock_fd = os.open(parent, os.O_RDONLY)
    except OSError as exc:
        raise StateError(f"could not open state directory {parent} for locking: {exc}") from exc

    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
        except OSError as exc:
            raise StateError(f"could not acquire state lock on {parent}: {exc}") from exc

        # Re-load inside the lock. This is required for correctness, not
        # optimization. See module docstring and the comment above. Do NOT
        # optimize this away by using the `state` argument directly.
        current = _read_inside_lock(path)

        # Race guard: if a newer pr-owl wrote a higher-version file between
        # our initial load and now, _read_inside_lock returns the read-only
        # sentinel. Refuse to overwrite (would downgrade the file).
        if current.get(_READ_ONLY_KEY):
            logger.warning(
                "state save skipped: file at %s was upgraded by a newer pr-owl while this audit ran",
                path,
            )
            return

        # If the on-disk file is from an older schema version, _read_inside_lock
        # returns {} so prs starts empty here — old-schema entries are dropped
        # rather than merged into a v3 file. Prevents mixed-schema files.
        prs = current.get("prs") or {}
        if not isinstance(prs, dict):
            prs = {}

        now = datetime.now(timezone.utc).isoformat()
        updates = 0
        for report in reports:
            if report.status == MergeStatus.UNKNOWN:
                continue
            if report.error:
                continue
            if not is_valid_pr_url(report.pr.url):
                continue
            prs[report.pr.url] = {
                "comment_count": report.comment_count,
                "last_seen_at": now,
            }
            updates += 1

        if updates == 0 and not prs:
            # Nothing to write and no existing state — skip the file write
            # entirely so a stateless first run on a missing file doesn't
            # leave behind an empty state file.
            logger.debug("state: nothing to save (no valid reports, no existing state)")
            return

        new_state = {"version": CURRENT_VERSION, "last_audit_at": now, "prs": prs}

        tmp_path = path.with_name(f"{path.name}.tmp")
        try:
            tmp_path.write_text(json.dumps(new_state, indent=2) + "\n", encoding="utf-8")
            os.replace(tmp_path, path)
            os.chmod(path, 0o600)
        except OSError as exc:
            # Best-effort cleanup of the tmp file if rename failed.
            try:
                tmp_path.unlink()
            except OSError:
                pass
            raise StateError(f"could not write state file at {path}: {exc}") from exc

        logger.debug("state: wrote %d PRs to %s", len(prs), path)
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(lock_fd)


def _read_inside_lock(path: Path) -> dict:
    """Re-read the state file while holding the lock.

    Distinct from load_state() because we only need the on-disk dict — we
    do NOT want to trigger the corrupt-file backup-rename here. If the file
    became corrupt between our initial load and now, that's an exotic
    failure mode; we treat it as empty for the merge and let the next
    invocation handle the recovery.

    Version handling: returns {} for any version != CURRENT_VERSION. This
    prevents two failure modes: (1) merging old-schema PR entries into a new
    file (mixed-schema files), and (2) downgrading a newer file when an old
    binary acquires the lock. The fcntl lock serializes writes but not
    version semantics; this guard supplies the missing version isolation.
    """
    if not path.exists():
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise StateError(f"could not re-read state file at {path}: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("state file at %s became unreadable while holding lock; merging onto empty", path)
        return {}
    if not isinstance(data, dict):
        return {}
    version = data.get("version", 0)
    if not isinstance(version, int):
        return {}
    if version > CURRENT_VERSION:
        # A newer pr-owl wrote this file between our initial load and now.
        # Refuse to overwrite — the lock serializes writes but not version
        # semantics, so downgrades would otherwise slip through. save_state
        # checks for this sentinel and bails before writing.
        return {_READ_ONLY_KEY: True}
    if version < CURRENT_VERSION:
        # Schema upgrade: drop the old baseline and let save_state write our
        # version with prs={} for entries we don't have updates for. This is
        # the same handling load_state does, but inside the lock for the
        # re-read race.
        return {}
    return data
