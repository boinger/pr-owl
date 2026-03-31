"""Data models for pr-owl."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


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
    FAILING_CHECKS = "FAILING_CHECKS"
    IS_DRAFT = "IS_DRAFT"
    BRANCH_PROTECTION = "BRANCH_PROTECTION"
    UNKNOWN_BLOCKER = "UNKNOWN_BLOCKER"


@dataclass
class Blocker:
    type: BlockerType
    description: str
    details: list[str] = field(default_factory=list)


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

    @classmethod
    def from_search_result(cls, data: dict) -> PRInfo:
        return cls(
            number=data["number"],
            title=data["title"],
            repo=data["repository"]["nameWithOwner"],
            url=data["url"],
            is_draft=data.get("isDraft", False),
            created_at=data["createdAt"],
            updated_at=data["updatedAt"],
        )

    @property
    def updated_at_dt(self) -> datetime:
        return datetime.fromisoformat(self.updated_at.replace("Z", "+00:00"))


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

    @property
    def is_ready(self) -> bool:
        return self.status == MergeStatus.READY and not self.blockers

    @property
    def checks_passing(self) -> list[CICheck]:
        return [c for c in self.checks if c.is_passing]

    @property
    def checks_failing(self) -> list[CICheck]:
        return [c for c in self.checks if c.is_failing]

    @property
    def checks_pending(self) -> list[CICheck]:
        return [c for c in self.checks if c.is_pending]


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
