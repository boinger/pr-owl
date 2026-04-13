"""Data models for pr-owl."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from pr_owl.exceptions import PrOwlError


class MergeStatus(str, Enum):
    READY = "READY"
    BEHIND = "BEHIND"
    CONFLICTS = "CONFLICTS"
    BLOCKED = "BLOCKED"
    CI_FAILING = "CI_FAILING"
    DRAFT = "DRAFT"
    UNKNOWN = "UNKNOWN"


class BlockerType(str, Enum):
    BEHIND_BASE = "BEHIND_BASE"
    HAS_CONFLICTS = "HAS_CONFLICTS"
    MISSING_REVIEWS = "MISSING_REVIEWS"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    FAILING_CHECKS = "FAILING_CHECKS"
    IS_DRAFT = "IS_DRAFT"
    BRANCH_PROTECTION = "BRANCH_PROTECTION"
    UNKNOWN_BLOCKER = "UNKNOWN_BLOCKER"


@dataclass
class Blocker:
    type: BlockerType
    description: str
    details: list[str] = field(default_factory=list)

    @property
    def actionable(self) -> bool:
        """Whether the PR author can potentially act on this blocker."""
        return self.type in (
            BlockerType.BEHIND_BASE,
            BlockerType.HAS_CONFLICTS,
            BlockerType.FAILING_CHECKS,
            BlockerType.IS_DRAFT,
            BlockerType.CHANGES_REQUESTED,
        )


@dataclass
class CICheck:
    name: str
    status: str
    conclusion: str | None
    workflow_name: str
    details_url: str

    @property
    def is_passing(self) -> bool:
        return self.conclusion in ("SUCCESS", "NEUTRAL", "success", "neutral")

    @property
    def is_failing(self) -> bool:
        return self.conclusion in ("FAILURE", "ERROR", "TIMED_OUT", "failure", "error")

    @property
    def is_pending(self) -> bool:
        return self.conclusion is None or self.status in ("QUEUED", "IN_PROGRESS", "WAITING", "PENDING", "pending")


@dataclass
class PRInfo:
    number: int
    title: str
    repo: str
    url: str
    is_draft: bool
    created_at: str
    updated_at: str
    # Parsed once in __post_init__, cached. init=False keeps it off the
    # constructor signature; default=None is a type lie but correct at
    # runtime because __post_init__ always sets it (or raises).
    _updated_at_dt: datetime | None = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.updated_at, str) or not self.updated_at:
            raise PrOwlError(f"PRInfo.updated_at is empty or not a string: {self.updated_at!r}")
        try:
            self._updated_at_dt = datetime.fromisoformat(self.updated_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise PrOwlError(f"Malformed PRInfo.updated_at: {self.updated_at!r}") from exc

    @classmethod
    def from_search_result(cls, data: dict) -> PRInfo:
        try:
            repo_obj = data.get("repository") or {}
            repo = repo_obj.get("nameWithOwner", "")
            if not repo:
                raise PrOwlError(f"Search result missing repository: {data!r}")
            return cls(
                number=data["number"],
                title=data["title"],
                repo=repo,
                url=data.get("url", ""),
                is_draft=data.get("isDraft", False),
                created_at=data.get("createdAt", ""),
                updated_at=data.get("updatedAt") or "",
            )
        except KeyError as exc:
            raise PrOwlError(f"Search result missing required key {exc}: {data!r}") from exc

    @property
    def updated_at_dt(self) -> datetime:
        # _updated_at_dt is always populated by __post_init__ (or it raised).
        assert self._updated_at_dt is not None
        return self._updated_at_dt


@dataclass
class HealthReport:
    pr: PRInfo
    status: MergeStatus
    blockers: list[Blocker] = field(default_factory=list)
    mergeable: str = ""
    merge_state_status: str = ""
    review_decision: str = ""
    checks: list[CICheck] = field(default_factory=list)
    head_ref: str = ""
    base_ref: str = ""
    head_repo: str = ""
    error: str = ""
    # Comment activity counters. issue_comment_count and review_event_count are
    # populated by check_pr from the gh response. new_issue_comments and
    # new_review_events are populated by _annotate_comment_deltas in cli.py
    # against the persisted state from the previous audit run. Defaults keep
    # error-path construction sites (HealthReport(pr=pr, status=UNKNOWN, error=...))
    # working without modification.
    issue_comment_count: int = 0
    review_event_count: int = 0
    new_issue_comments: int = 0
    new_review_events: int = 0

    @property
    def is_ready(self) -> bool:
        return self.status == MergeStatus.READY and not self.blockers

    @property
    def has_actionable_blockers(self) -> bool:
        """Whether any blocker is potentially fixable by the PR author."""
        return any(b.actionable for b in self.blockers)

    @property
    def checks_passing(self) -> list[CICheck]:
        return [c for c in self.checks if c.is_passing]

    @property
    def checks_failing(self) -> list[CICheck]:
        return [c for c in self.checks if c.is_failing]

    @property
    def checks_pending(self) -> list[CICheck]:
        return [c for c in self.checks if c.is_pending]


class ClosedDisposition(str, Enum):
    MERGED = "MERGED"
    CLOSED = "CLOSED"


@dataclass
class ClosedPRInfo:
    """A PR that closed since the last audit. Separate from HealthReport because
    closed PRs don't carry blockers, CI checks, or remediation plans."""

    pr: PRInfo
    disposition: ClosedDisposition
    days_open: int
    review_count: int
    closed_at: str

    @classmethod
    def from_search_result(cls, data: dict) -> ClosedPRInfo:
        """Build from a gh search prs --state closed result."""
        pr = PRInfo.from_search_result(data)
        state = (data.get("state") or "").lower()
        disposition = ClosedDisposition.MERGED if state == "merged" else ClosedDisposition.CLOSED

        closed_at = data.get("closedAt") or ""
        created_at = data.get("createdAt") or ""
        days_open = 0
        if closed_at and created_at:
            try:
                closed_dt = datetime.fromisoformat(closed_at.replace("Z", "+00:00"))
                created_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                days_open = max(0, (closed_dt - created_dt).days)
            except ValueError:
                pass

        return cls(
            pr=pr,
            disposition=disposition,
            days_open=days_open,
            review_count=0,  # populated by enrichment step
            closed_at=closed_at,
        )


@dataclass
class RemediationStep:
    description: str
    command: str = ""
    automatable: bool = False


@dataclass
class RemediationPlan:
    report: HealthReport
    steps: list[RemediationStep] = field(default_factory=list)
    summary: str = ""
