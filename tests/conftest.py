"""Shared fixtures for pr-owl tests."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from pr_owl.models import PRInfo

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict | list:
    """Load a JSON fixture file."""
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def sample_pr() -> PRInfo:
    """A sample PRInfo for testing."""
    return PRInfo(
        number=42,
        title="Add widget support",
        repo="acme/repo",
        url="https://github.com/acme/repo/pull/42",
        is_draft=False,
        created_at="2026-01-15T10:00:00Z",
        updated_at="2026-03-20T14:00:00Z",
    )


@pytest.fixture
def draft_pr() -> PRInfo:
    """A draft PRInfo for testing."""
    return PRInfo(
        number=99,
        title="Fix logging",
        repo="acme/other",
        url="https://github.com/acme/other/pull/99",
        is_draft=True,
        created_at="2026-02-01T08:00:00Z",
        updated_at="2026-03-28T12:00:00Z",
    )


@pytest.fixture
def mock_subprocess():
    """Patch subprocess.run for gh.py tests."""
    with patch("subprocess.run") as mock:
        yield mock
