"""Tests for pr_owl.state — activity-flag tracking persistence."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from pr_owl.exceptions import StateError
from pr_owl.models import HealthReport, MergeStatus, PRInfo
from pr_owl.state import (
    CURRENT_VERSION,
    get_last_audit_at,
    has_new_activity,
    is_valid_pr_url,
    load_state,
    save_state,
    state_path,
)


@pytest.fixture
def isolated_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point XDG_STATE_HOME at a tmp_path so tests never touch the real state file."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    return tmp_path / "pr-owl" / "seen.json"


def _make_report(
    number: int = 1,
    repo: str = "acme/api",
    comment_count: int = 0,
    status: MergeStatus = MergeStatus.READY,
    error: str = "",
    url: str | None = None,
    updated_at: str = "2026-03-20T14:00:00Z",
) -> HealthReport:
    pr = PRInfo(
        number=number,
        title=f"PR #{number}",
        repo=repo,
        url=url if url is not None else f"https://github.com/{repo}/pull/{number}",
        is_draft=False,
        created_at="2026-01-15T10:00:00Z",
        updated_at=updated_at,
    )
    return HealthReport(
        pr=pr,
        status=status,
        error=error,
        comment_count=comment_count,
    )


# ---------------------------------------------------------------------------
# state_path()
# ---------------------------------------------------------------------------


def test_state_path_uses_xdg_state_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert state_path() == tmp_path / "pr-owl" / "seen.json"


def test_state_path_falls_back_to_local_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    expected = Path.home() / ".local" / "state" / "pr-owl" / "seen.json"
    assert state_path() == expected


def test_state_path_treats_empty_xdg_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", "")
    expected = Path.home() / ".local" / "state" / "pr-owl" / "seen.json"
    assert state_path() == expected


# ---------------------------------------------------------------------------
# is_valid_pr_url()
# ---------------------------------------------------------------------------


def test_is_valid_pr_url_accepts_github_https() -> None:
    assert is_valid_pr_url("https://github.com/owner/repo/pull/1")


def test_is_valid_pr_url_rejects_empty() -> None:
    assert not is_valid_pr_url("")


def test_is_valid_pr_url_rejects_non_github() -> None:
    assert not is_valid_pr_url("https://gitlab.com/owner/repo/-/merge_requests/1")


def test_is_valid_pr_url_rejects_garbage() -> None:
    assert not is_valid_pr_url("not-a-url")


# ---------------------------------------------------------------------------
# load_state()
# ---------------------------------------------------------------------------


def test_load_state_missing_file_returns_empty(isolated_state: Path) -> None:
    assert load_state() == {}


def test_load_state_roundtrip(isolated_state: Path) -> None:
    save_state({}, [_make_report(comment_count=4)])
    loaded = load_state()
    assert loaded["version"] == CURRENT_VERSION
    assert "https://github.com/acme/api/pull/1" in loaded["prs"]
    assert loaded["prs"]["https://github.com/acme/api/pull/1"]["comment_count"] == 4


def test_load_state_corrupt_file_renames_and_returns_empty(isolated_state: Path) -> None:
    isolated_state.parent.mkdir(parents=True, exist_ok=True)
    isolated_state.write_text("{not valid json", encoding="utf-8")
    result = load_state()
    assert result == {}
    # The corrupt file should have been renamed for forensics.
    backups = list(isolated_state.parent.glob("seen.json.corrupt-*"))
    assert len(backups) == 1
    assert "{not valid json" in backups[0].read_text()


def test_load_state_wrong_top_level_type(isolated_state: Path) -> None:
    isolated_state.parent.mkdir(parents=True, exist_ok=True)
    isolated_state.write_text("[1, 2, 3]", encoding="utf-8")
    result = load_state()
    assert result == {}
    assert list(isolated_state.parent.glob("seen.json.corrupt-*"))


def test_load_state_higher_version_marks_read_only(isolated_state: Path) -> None:
    isolated_state.parent.mkdir(parents=True, exist_ok=True)
    isolated_state.write_text(
        json.dumps({"version": CURRENT_VERSION + 99, "prs": {"x": {"comment_count": 5}}}),
        encoding="utf-8",
    )
    result = load_state()
    assert result.get("_read_only") is True
    # The file is preserved untouched.
    raw = json.loads(isolated_state.read_text())
    assert raw["version"] == CURRENT_VERSION + 99


def test_load_state_lower_version_resets_baseline(isolated_state: Path, caplog: pytest.LogCaptureFixture) -> None:
    """T1: a v2 state file (older schema) is treated as empty under v3 — so deltas
    don't compute against incompatible old baselines. DX3 surfaces a warning log."""
    isolated_state.parent.mkdir(parents=True, exist_ok=True)
    isolated_state.write_text(
        json.dumps({"version": 2, "prs": {"https://github.com/x/y/pull/1": {"issue_comments": 5, "review_events": 2}}}),
        encoding="utf-8",
    )
    with caplog.at_level("WARNING"):
        result = load_state()
    # No "prs" key — the old baseline is dropped.
    assert "prs" not in result
    # DX3: surfaced as a warning so the user understands why * markers vanished for one cycle.
    assert any("State schema upgraded" in rec.message for rec in caplog.records)


def test_save_state_rebuilds_after_version_mismatch(isolated_state: Path) -> None:
    """T2: first v3 save after loading a v2 file does NOT preserve untouched v2 entries.
    The output file contains only entries from the current reports + version=CURRENT."""
    isolated_state.parent.mkdir(parents=True, exist_ok=True)
    # Pre-existing v2 file with two PRs.
    isolated_state.write_text(
        json.dumps(
            {
                "version": 2,
                "prs": {
                    "https://github.com/old/repo/pull/99": {"issue_comments": 1, "review_events": 0},
                    "https://github.com/old/repo/pull/100": {"issue_comments": 4, "review_events": 2},
                },
            }
        ),
        encoding="utf-8",
    )
    state = load_state()
    save_state(state, [_make_report(number=1, comment_count=3)])
    loaded = load_state()
    assert loaded["version"] == CURRENT_VERSION
    # Only the report we just saved is present — the v2 entries were dropped, not merged.
    assert list(loaded["prs"].keys()) == ["https://github.com/acme/api/pull/1"]
    assert loaded["prs"]["https://github.com/acme/api/pull/1"]["comment_count"] == 3
    # No leftover v2 keys.
    entry = loaded["prs"]["https://github.com/acme/api/pull/1"]
    assert "issue_comments" not in entry
    assert "review_events" not in entry


def test_read_inside_lock_rejects_higher_version(isolated_state: Path) -> None:
    """T4: if a newer pr-owl writes a higher-version file between our load and save,
    save_state must refuse to overwrite (would otherwise downgrade)."""
    isolated_state.parent.mkdir(parents=True, exist_ok=True)
    # Initial v3 file we'll "load" from.
    isolated_state.write_text(
        json.dumps({"version": CURRENT_VERSION, "prs": {}}),
        encoding="utf-8",
    )
    state = load_state()  # State as we saw it at audit start.
    # Now simulate a newer pr-owl version writing a higher-version file.
    isolated_state.write_text(
        json.dumps({"version": CURRENT_VERSION + 1, "prs": {"new": {"comment_count": 99}}}),
        encoding="utf-8",
    )
    # save_state should NOT overwrite — the newer file's contents stay intact.
    save_state(state, [_make_report(number=1, comment_count=3)])
    final = json.loads(isolated_state.read_text())
    assert final["version"] == CURRENT_VERSION + 1
    assert "new" in final["prs"]


def test_load_state_unreadable_file_raises_state_error(isolated_state: Path) -> None:
    isolated_state.parent.mkdir(parents=True, exist_ok=True)
    isolated_state.write_text("{}", encoding="utf-8")
    isolated_state.chmod(0o000)
    try:
        with pytest.raises(StateError, match="could not read"):
            load_state()
    finally:
        isolated_state.chmod(0o600)  # cleanup so tmp_path teardown works


# ---------------------------------------------------------------------------
# save_state()
# ---------------------------------------------------------------------------


def test_save_state_creates_dir_and_file(isolated_state: Path) -> None:
    assert not isolated_state.exists()
    save_state({}, [_make_report(comment_count=2)])
    assert isolated_state.exists()
    assert isolated_state.parent.is_dir()


def test_save_state_sets_file_mode_0600(isolated_state: Path) -> None:
    save_state({}, [_make_report(comment_count=1)])
    mode = isolated_state.stat().st_mode & 0o777
    assert mode == 0o600


def test_save_state_skipped_when_read_only(isolated_state: Path) -> None:
    save_state({"_read_only": True}, [_make_report(comment_count=5)])
    assert not isolated_state.exists()


def test_save_state_filters_unknown_status(isolated_state: Path) -> None:
    reports = [
        _make_report(number=1, comment_count=5, status=MergeStatus.READY),
        _make_report(number=2, comment_count=99, status=MergeStatus.UNKNOWN),
    ]
    save_state({}, reports)
    loaded = load_state()
    urls = loaded["prs"].keys()
    assert "https://github.com/acme/api/pull/1" in urls
    assert "https://github.com/acme/api/pull/2" not in urls


def test_save_state_filters_reports_with_error(isolated_state: Path) -> None:
    reports = [
        _make_report(number=1, comment_count=5),
        _make_report(number=2, comment_count=99, error="something broke"),
    ]
    save_state({}, reports)
    loaded = load_state()
    assert "https://github.com/acme/api/pull/2" not in loaded["prs"]


def test_save_state_skips_invalid_urls(isolated_state: Path) -> None:
    bad = _make_report(comment_count=5, url="")
    save_state({}, [bad])
    # Empty URL means no entries to save AND no existing state — file should not exist.
    assert not isolated_state.exists()


def test_save_state_merge_preserves_existing_entries(isolated_state: Path) -> None:
    """The most important rule: a PR that errored on this run must keep its baseline."""
    # First run: PRs A and B both succeed.
    save_state(
        {},
        [
            _make_report(number=1, comment_count=5),
            _make_report(number=2, comment_count=10),
        ],
    )
    # Second run: PR A succeeds with new count, PR B errors out.
    state = load_state()
    save_state(
        state,
        [
            _make_report(number=1, comment_count=7),
            _make_report(number=2, comment_count=0, status=MergeStatus.UNKNOWN, error="api failed"),
        ],
    )
    final = load_state()
    # PR A updated.
    assert final["prs"]["https://github.com/acme/api/pull/1"]["comment_count"] == 7
    # PR B preserved at the original baseline (10), NOT clobbered by the error-path 0.
    assert final["prs"]["https://github.com/acme/api/pull/2"]["comment_count"] == 10


def test_save_state_atomic_write_no_tmp_left_behind(isolated_state: Path) -> None:
    save_state({}, [_make_report(comment_count=1)])
    leftovers = list(isolated_state.parent.glob("seen.json.tmp"))
    assert leftovers == []


def test_save_state_concurrent_writers_dont_corrupt(isolated_state: Path) -> None:
    """Two threads saving simultaneously must produce a valid file with both updates merged."""

    def saver(report_number: int) -> None:
        save_state({}, [_make_report(number=report_number, comment_count=report_number * 10)])

    t1 = threading.Thread(target=saver, args=(1,))
    t2 = threading.Thread(target=saver, args=(2,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # File must be valid JSON.
    loaded = load_state()
    # Both writers re-loaded inside the lock, so both PRs must be present.
    urls = loaded["prs"].keys()
    assert "https://github.com/acme/api/pull/1" in urls
    assert "https://github.com/acme/api/pull/2" in urls
    assert loaded["prs"]["https://github.com/acme/api/pull/1"]["comment_count"] == 10
    assert loaded["prs"]["https://github.com/acme/api/pull/2"]["comment_count"] == 20


def test_save_state_writes_comment_count(isolated_state: Path) -> None:
    save_state({}, [_make_report(comment_count=6)])
    loaded = load_state()
    entry = loaded["prs"]["https://github.com/acme/api/pull/1"]
    assert entry["comment_count"] == 6
    assert "last_seen_at" in entry


# ---------------------------------------------------------------------------
# has_new_activity()
# ---------------------------------------------------------------------------


def test_has_new_activity_first_sighting_with_late_update() -> None:
    """The user's exact reported bug: PR opened during weekend, maintainer
    commented Monday, pr-owl ran for the first time on this PR Monday afternoon.
    `updated_at` is after the previous `last_audit_at` → True."""
    report = _make_report(updated_at="2026-05-11T07:00:00Z")
    state = {"last_audit_at": "2026-05-09T10:00:00+00:00"}
    assert has_new_activity(report.pr, state) is True


def test_has_new_activity_first_sighting_no_last_audit_at_returns_false() -> None:
    """Very-first-ever audit: no cutoff to compare against, don't flag anything."""
    report = _make_report(updated_at="2026-05-11T07:00:00Z")
    assert has_new_activity(report.pr, {}) is False


def test_has_new_activity_first_sighting_pr_predates_last_audit_returns_false() -> None:
    """First-sighting PR that's older than the last audit (we missed it earlier).
    Its activity is not "new" from the user's perspective — they already had
    a chance to see it."""
    report = _make_report(updated_at="2026-05-09T10:00:00Z")
    state = {"last_audit_at": "2026-05-11T10:00:00+00:00"}
    assert has_new_activity(report.pr, state) is False


def test_has_new_activity_repeat_sighting_uses_per_pr_last_seen() -> None:
    """Per-PR last_seen_at takes precedence over global last_audit_at.
    last_audit_at is deliberately set to a value that would yield a different
    answer; we verify the per-PR cutoff wins."""
    report = _make_report(updated_at="2026-05-10T15:00:00Z")
    state = {
        "last_audit_at": "2026-05-09T10:00:00+00:00",  # would say True, but per-PR wins
        "prs": {report.pr.url: {"last_seen_at": "2026-05-10T20:00:00+00:00"}},
    }
    assert has_new_activity(report.pr, state) is False


def test_has_new_activity_repeat_sighting_strictly_after() -> None:
    """Repeat sighting where activity is strictly after the per-PR cutoff → True."""
    report = _make_report(updated_at="2026-05-11T05:00:00Z")
    state = {"prs": {report.pr.url: {"last_seen_at": "2026-05-10T20:00:00+00:00"}}}
    assert has_new_activity(report.pr, state) is True


def test_has_new_activity_boundary_equality_returns_false() -> None:
    """Strict `>`: equality is NOT new. Important because save_state sets
    last_seen_at to a value reflecting the current run, and the next audit's
    `updated_at` (if unchanged) must not falsely flag."""
    report = _make_report(updated_at="2026-05-11T10:00:00Z")
    state = {"last_audit_at": "2026-05-11T10:00:00+00:00"}
    assert has_new_activity(report.pr, state) is False


def test_has_new_activity_malformed_cutoff_returns_false() -> None:
    """Defensive: a malformed cutoff string in state must not crash the audit."""
    report = _make_report(updated_at="2026-05-11T07:00:00Z")
    state = {"last_audit_at": "not-an-iso-string"}
    assert has_new_activity(report.pr, state) is False


def test_has_new_activity_invalid_url_returns_false() -> None:
    """Non-github URLs are never new-activity-flagged."""
    report = _make_report(url="", updated_at="2026-05-11T07:00:00Z")
    state = {"last_audit_at": "2026-05-09T10:00:00+00:00"}
    assert has_new_activity(report.pr, state) is False


def test_has_new_activity_repeat_sighting_missing_last_seen_at_returns_false() -> None:
    """If the prior record exists but lacks last_seen_at (schema corruption,
    not normal v3 shape), the cutoff is None and we conservatively return
    False — one missed `*` flag, but save_state writes a proper last_seen_at
    on the next run, so the next audit recovers automatically.

    Note: we do NOT fall back to global last_audit_at here. Mixing per-PR and
    global cutoffs in the same record would conflate two different semantics
    and produce surprising results for users; the conservative behavior is to
    treat a corrupted record as "no cutoff."
    """
    report = _make_report(updated_at="2026-05-11T07:00:00Z")
    state = {
        "last_audit_at": "2026-05-09T10:00:00+00:00",
        "prs": {report.pr.url: {"comment_count": 5}},  # missing last_seen_at
    }
    assert has_new_activity(report.pr, state) is False


# ---------------------------------------------------------------------------
# save_state — version and last_audit_at
# ---------------------------------------------------------------------------


def test_save_state_writes_last_audit_at(isolated_state: Path) -> None:
    save_state({}, [_make_report(comment_count=1)])
    loaded = load_state()
    assert "last_audit_at" in loaded
    # Should be a valid ISO timestamp.
    from datetime import datetime

    dt = datetime.fromisoformat(loaded["last_audit_at"].replace("Z", "+00:00"))
    assert dt.year >= 2026


def test_save_state_writes_current_version(isolated_state: Path) -> None:
    save_state({}, [_make_report(comment_count=1)])
    loaded = load_state()
    assert loaded["version"] == CURRENT_VERSION


# ---------------------------------------------------------------------------
# get_last_audit_at
# ---------------------------------------------------------------------------


def test_get_last_audit_at_returns_datetime_when_present() -> None:
    from datetime import datetime, timezone

    state = {"last_audit_at": "2026-04-10T12:00:00+00:00"}
    result = get_last_audit_at(state)
    assert isinstance(result, datetime)
    assert result == datetime(2026, 4, 10, 12, 0, 0, tzinfo=timezone.utc)


def test_get_last_audit_at_returns_none_when_missing() -> None:
    assert get_last_audit_at({}) is None


def test_get_last_audit_at_returns_none_for_invalid_value() -> None:
    assert get_last_audit_at({"last_audit_at": "not-a-date"}) is None


def test_v1_state_file_resets_under_v3(isolated_state: Path) -> None:
    """A v1 file is treated as empty under v3 (deltas reset for one cycle)."""
    isolated_state.parent.mkdir(parents=True, exist_ok=True)
    v1_data = {
        "version": 1,
        "prs": {
            "https://github.com/acme/api/pull/1": {
                "issue_comments": 5,
                "review_events": 1,
                "last_seen_at": "2026-04-01T00:00:00Z",
            }
        },
    }
    isolated_state.write_text(json.dumps(v1_data), encoding="utf-8")
    loaded = load_state()
    assert "prs" not in loaded
    assert get_last_audit_at(loaded) is None


def test_v2_state_file_resets_under_v3(isolated_state: Path) -> None:
    """A v2 state file's baseline is dropped under v3 — prevents incorrect deltas
    against incompatible old per-PR fields."""
    v2_data = {
        "version": 2,
        "last_audit_at": "2026-04-13T10:00:00+00:00",
        "prs": {
            "https://github.com/acme/api/pull/1": {
                "issue_comments": 5,
                "review_events": 2,
                "last_seen_at": "2026-04-13T10:00:00Z",
            }
        },
    }
    isolated_state.parent.mkdir(parents=True, exist_ok=True)
    isolated_state.write_text(json.dumps(v2_data), encoding="utf-8")
    loaded = load_state()
    # Version mismatch: load_state returns the schema-reset sentinel without prs/last_audit_at.
    assert "prs" not in loaded
    assert get_last_audit_at(loaded) is None
