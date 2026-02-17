"""Pytest fixtures for Engineering Hub tests."""

from pathlib import Path

import pytest


@pytest.fixture
def sample_shared_notes() -> str:
    """Sample shared notes content."""
    return """---
workspace: engineering-hub
sync_url: http://localhost:8000/api
---

# Engineering Hub - Shared Notes

## Active Engineering Tasks

### @research: PENDING
> Project: [[django://project/1]]
> Task: Research ASTM E336-17a testing requirements
> Deliverable: [[/outputs/research/astm-e336-summary.md]]

### @technical-writer: COMPLETED
> Project: [[django://project/1]]
> Task: Draft test protocol
> Deliverable: [[/outputs/docs/test-protocol.md]]

### @research: IN_PROGRESS
> Project: [[django://project/2]]
> Task: Compare HVAC noise mitigation options
> Context: Focus on residential applications

## Agent Communication Thread

**[2026-02-15 10:00] @research**
Completed research on ASTM E336-17a requirements.
Output: [[/outputs/research/astm-e336-summary.md]]

## Project Context Cache

## Engineering Log
"""


@pytest.fixture
def temp_notes_file(tmp_path: Path, sample_shared_notes: str) -> Path:
    """Create a temporary shared notes file."""
    notes_file = tmp_path / "shared-notes.md"
    notes_file.write_text(sample_shared_notes)
    return notes_file


@pytest.fixture
def temp_workspace(tmp_path: Path, sample_shared_notes: str) -> Path:
    """Create a temporary workspace with all directories."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    # Create shared notes
    notes_file = workspace / "shared-notes.md"
    notes_file.write_text(sample_shared_notes)

    # Create output directories
    (workspace / "outputs" / "research").mkdir(parents=True)
    (workspace / "outputs" / "docs").mkdir(parents=True)
    (workspace / "outputs" / "analysis").mkdir(parents=True)

    return workspace


@pytest.fixture
def mock_django_responses() -> dict:
    """Mock Django API responses."""
    return {
        "project": {
            "id": 1,
            "title": "Office Building Acoustic Assessment",
            "client_name": "Acme Construction",
            "status": "in_progress",
            "budget": "45000.00",
            "description": "Comprehensive acoustic assessment",
        },
        "context": {
            "project": {
                "id": 1,
                "title": "Office Building Acoustic Assessment",
                "client_name": "Acme Construction",
                "status": "in_progress",
                "budget": "45000.00",
                "description": "Comprehensive acoustic assessment",
            },
            "scope": [
                "ASTC testing per ASTM E336-17a",
                "AIIC testing per ASTM E1007-16",
            ],
            "standards": [
                {"type": "ASTM", "id": "ASTM E336-17a"},
                {"type": "ASTM", "id": "ASTM E1007-16"},
            ],
            "recent_files": [],
            "proposals": [],
            "metadata": {"client_technical_level": "moderate"},
        },
    }
