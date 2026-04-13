"""Tests for pr_owl.state — comment delta tracking persistence."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from pr_owl.exceptions import StateError
from pr_owl.models import HealthReport, MergeStatus, PRInfo
from pr_owl.state import (
    CURRENT_VERSION,
    compute_delta,
    get_last_audit_at,
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
    issue_comments: int = 0,
    review_events: int = 0,
    status: MergeStatus = MergeStatus.READY,
    error: str = "",
    url: str | None = None,
) -> HealthReport:
    pr = PRInfo(
        number=number,
        title=f"PR #{number}",
        repo=repo,
        url=url if url is not None else f"https://github.com/{repo}/pull/{number}",
        is_draft=False,
        created_at="2026-01-15T10:00:00Z",
        updated_at="2026-03-20T14:00:00Z",
    )
    return HealthReport(
        pr=pr,
        status=status,
        error=error,
        issue_comment_count=issue_comments,
        review_event_count=review_events,
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
    save_state({}, [_make_report(issue_comments=3, review_events=1)])
    loaded = load_state()
    assert loaded["version"] == CURRENT_VERSION
    assert "https://github.com/acme/api/pull/1" in loaded["prs"]
    assert loaded["prs"]["https://github.com/acme/api/pull/1"]["issue_comments"] == 3
    assert loaded["prs"]["https://github.com/acme/api/pull/1"]["review_events"] == 1


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
        json.dumps({"version": CURRENT_VERSION + 99, "prs": {"x": {"issue_comments": 5}}}),
        encoding="utf-8",
    )
    result = load_state()
    assert result.get("_read_only") is True
    # The file is preserved untouched.
    raw = json.loads(isolated_state.read_text())
    assert raw["version"] == CURRENT_VERSION + 99


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
    save_state({}, [_make_report(issue_comments=2)])
    assert isolated_state.exists()
    assert isolated_state.parent.is_dir()


def test_save_state_sets_file_mode_0600(isolated_state: Path) -> None:
    save_state({}, [_make_report(issue_comments=1)])
    mode = isolated_state.stat().st_mode & 0o777
    assert mode == 0o600


def test_save_state_skipped_when_read_only(isolated_state: Path) -> None:
    save_state({"_read_only": True}, [_make_report(issue_comments=5)])
    assert not isolated_state.exists()


def test_save_state_filters_unknown_status(isolated_state: Path) -> None:
    reports = [
        _make_report(number=1, issue_comments=5, status=MergeStatus.READY),
        _make_report(number=2, issue_comments=99, status=MergeStatus.UNKNOWN),
    ]
    save_state({}, reports)
    loaded = load_state()
    urls = loaded["prs"].keys()
    assert "https://github.com/acme/api/pull/1" in urls
    assert "https://github.com/acme/api/pull/2" not in urls


def test_save_state_filters_reports_with_error(isolated_state: Path) -> None:
    reports = [
        _make_report(number=1, issue_comments=5),
        _make_report(number=2, issue_comments=99, error="something broke"),
    ]
    save_state({}, reports)
    loaded = load_state()
    assert "https://github.com/acme/api/pull/2" not in loaded["prs"]


def test_save_state_skips_invalid_urls(isolated_state: Path) -> None:
    bad = _make_report(issue_comments=5, url="")
    save_state({}, [bad])
    # Empty URL means no entries to save AND no existing state — file should not exist.
    assert not isolated_state.exists()


def test_save_state_merge_preserves_existing_entries(isolated_state: Path) -> None:
    """The most important rule: a PR that errored on this run must keep its baseline."""
    # First run: PRs A and B both succeed.
    save_state(
        {},
        [
            _make_report(number=1, issue_comments=5),
            _make_report(number=2, issue_comments=10),
        ],
    )
    # Second run: PR A succeeds with new count, PR B errors out.
    state = load_state()
    save_state(
        state,
        [
            _make_report(number=1, issue_comments=7),
            _make_report(number=2, issue_comments=0, status=MergeStatus.UNKNOWN, error="api failed"),
        ],
    )
    final = load_state()
    # PR A updated.
    assert final["prs"]["https://github.com/acme/api/pull/1"]["issue_comments"] == 7
    # PR B preserved at the original baseline (10), NOT clobbered by the error-path 0.
    assert final["prs"]["https://github.com/acme/api/pull/2"]["issue_comments"] == 10


def test_save_state_atomic_write_no_tmp_left_behind(isolated_state: Path) -> None:
    save_state({}, [_make_report(issue_comments=1)])
    leftovers = list(isolated_state.parent.glob("seen.json.tmp"))
    assert leftovers == []


def test_save_state_concurrent_writers_dont_corrupt(isolated_state: Path) -> None:
    """Two threads saving simultaneously must produce a valid file with both updates merged."""

    def saver(report_number: int) -> None:
        save_state({}, [_make_report(number=report_number, issue_comments=report_number * 10)])

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
    assert loaded["prs"]["https://github.com/acme/api/pull/1"]["issue_comments"] == 10
    assert loaded["prs"]["https://github.com/acme/api/pull/2"]["issue_comments"] == 20


def test_save_state_writes_review_events(isolated_state: Path) -> None:
    save_state({}, [_make_report(issue_comments=2, review_events=4)])
    loaded = load_state()
    entry = loaded["prs"]["https://github.com/acme/api/pull/1"]
    assert entry["issue_comments"] == 2
    assert entry["review_events"] == 4
    assert "last_seen_at" in entry


# ---------------------------------------------------------------------------
# compute_delta()
# ---------------------------------------------------------------------------


def test_compute_delta_first_seen_returns_zero() -> None:
    report = _make_report(issue_comments=5, review_events=2)
    assert compute_delta(report, {}) == (0, 0)


def test_compute_delta_unchanged() -> None:
    report = _make_report(issue_comments=5, review_events=2)
    state = {"prs": {report.pr.url: {"issue_comments": 5, "review_events": 2}}}
    assert compute_delta(report, state) == (0, 0)


def test_compute_delta_increase() -> None:
    report = _make_report(issue_comments=7, review_events=3)
    state = {"prs": {report.pr.url: {"issue_comments": 5, "review_events": 2}}}
    assert compute_delta(report, state) == (2, 1)


def test_compute_delta_decrease_clamps_to_zero() -> None:
    report = _make_report(issue_comments=3, review_events=1)
    state = {"prs": {report.pr.url: {"issue_comments": 5, "review_events": 2}}}
    assert compute_delta(report, state) == (0, 0)


def test_compute_delta_mixed() -> None:
    report = _make_report(issue_comments=6, review_events=1)
    state = {"prs": {report.pr.url: {"issue_comments": 5, "review_events": 2}}}
    assert compute_delta(report, state) == (1, 0)


def test_compute_delta_invalid_url_returns_zero() -> None:
    report = _make_report(issue_comments=5, url="")
    state = {"prs": {"https://github.com/acme/api/pull/1": {"issue_comments": 0, "review_events": 0}}}
    assert compute_delta(report, state) == (0, 0)


def test_compute_delta_handles_missing_prs_key() -> None:
    report = _make_report(issue_comments=5)
    assert compute_delta(report, {"version": 1}) == (0, 0)


# ---------------------------------------------------------------------------
# save_state — version and last_audit_at
# ---------------------------------------------------------------------------


def test_save_state_writes_last_audit_at(isolated_state: Path) -> None:
    save_state({}, [_make_report(issue_comments=1)])
    loaded = load_state()
    assert "last_audit_at" in loaded
    # Should be a valid ISO timestamp.
    from datetime import datetime

    dt = datetime.fromisoformat(loaded["last_audit_at"].replace("Z", "+00:00"))
    assert dt.year >= 2026


def test_save_state_writes_version_2(isolated_state: Path) -> None:
    save_state({}, [_make_report(issue_comments=1)])
    loaded = load_state()
    assert loaded["version"] == 2


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


def test_v1_state_file_loads_normally(isolated_state: Path) -> None:
    """A v1 file (no last_audit_at) loads fine; get_last_audit_at returns None."""
    isolated_state.parent.mkdir(parents=True, exist_ok=True)
    v1_data = {
        "version": 1,
        "prs": {
            "https://github.com/acme/api/pull/1": {
                "issue_comments": 5,
                "review_events": 2,
                "last_seen_at": "2026-04-01T00:00:00Z",
            }
        },
    }
    isolated_state.write_text(json.dumps(v1_data), encoding="utf-8")
    loaded = load_state()
    assert loaded["version"] == 1
    assert "https://github.com/acme/api/pull/1" in loaded["prs"]
    assert get_last_audit_at(loaded) is None


def test_v2_state_file_roundtrips(isolated_state: Path) -> None:
    """A v2 state file with last_audit_at loads correctly and preserves the timestamp."""
    v2_data = {
        "version": 2,
        "last_audit_at": "2026-04-13T10:00:00+00:00",
        "prs": {
            "https://github.com/acme/api/pull/1": {
                "issue_comments": 3,
                "review_events": 1,
                "last_seen_at": "2026-04-13T10:00:00Z",
            }
        },
    }
    isolated_state.parent.mkdir(parents=True, exist_ok=True)
    isolated_state.write_text(json.dumps(v2_data), encoding="utf-8")
    loaded = load_state()
    assert loaded["version"] == 2
    assert loaded["last_audit_at"] == "2026-04-13T10:00:00+00:00"
    last = get_last_audit_at(loaded)
    assert last is not None
    assert last.year == 2026
    assert last.month == 4
    assert last.day == 13
