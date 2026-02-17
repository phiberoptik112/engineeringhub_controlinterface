"""Tests for notes parser."""

import pytest

from engineering_hub.core.constants import TaskStatus
from engineering_hub.notes.parser import NotesParser


class TestNotesParser:
    """Tests for NotesParser."""

    def test_parse_frontmatter(self, sample_shared_notes: str) -> None:
        """Test parsing YAML frontmatter."""
        parser = NotesParser(sample_shared_notes)
        frontmatter = parser.parse_frontmatter()

        assert frontmatter["workspace"] == "engineering-hub"
        assert frontmatter["sync_url"] == "http://localhost:8000/api"

    def test_parse_frontmatter_empty(self) -> None:
        """Test parsing content without frontmatter."""
        parser = NotesParser("# No frontmatter here\n\nJust content.")
        frontmatter = parser.parse_frontmatter()

        assert frontmatter == {}

    def test_parse_tasks(self, sample_shared_notes: str) -> None:
        """Test parsing all tasks."""
        parser = NotesParser(sample_shared_notes)
        tasks = parser.parse_tasks()

        assert len(tasks) == 3

    def test_parse_pending_task(self, sample_shared_notes: str) -> None:
        """Test parsing a PENDING task."""
        parser = NotesParser(sample_shared_notes)
        tasks = parser.parse_tasks()

        pending_tasks = [t for t in tasks if t.status == TaskStatus.PENDING]
        assert len(pending_tasks) == 1

        task = pending_tasks[0]
        assert task.agent == "research"
        assert task.status == TaskStatus.PENDING
        assert task.project_id == 1
        assert "ASTM E336-17a" in task.description
        assert task.deliverable == "/outputs/research/astm-e336-summary.md"

    def test_parse_completed_task(self, sample_shared_notes: str) -> None:
        """Test parsing a COMPLETED task."""
        parser = NotesParser(sample_shared_notes)
        tasks = parser.parse_tasks()

        completed_tasks = [t for t in tasks if t.status == TaskStatus.COMPLETED]
        assert len(completed_tasks) == 1

        task = completed_tasks[0]
        assert task.agent == "technical-writer"
        assert task.status == TaskStatus.COMPLETED

    def test_parse_in_progress_task(self, sample_shared_notes: str) -> None:
        """Test parsing an IN_PROGRESS task."""
        parser = NotesParser(sample_shared_notes)
        tasks = parser.parse_tasks()

        in_progress_tasks = [t for t in tasks if t.status == TaskStatus.IN_PROGRESS]
        assert len(in_progress_tasks) == 1

        task = in_progress_tasks[0]
        assert task.agent == "research"
        assert task.project_id == 2
        assert task.context == "Focus on residential applications"

    def test_get_pending_tasks(self, sample_shared_notes: str) -> None:
        """Test getting only pending tasks."""
        parser = NotesParser(sample_shared_notes)
        pending = parser.get_pending_tasks()

        assert len(pending) == 1
        assert pending[0].status == TaskStatus.PENDING

    def test_parse_task_without_project(self) -> None:
        """Test parsing task without project reference."""
        content = """---
workspace: test
---

## Active Engineering Tasks

### @research: PENDING
> Task: General research task
"""
        parser = NotesParser(content)
        tasks = parser.parse_tasks()

        assert len(tasks) == 1
        assert tasks[0].project_id is None
        assert tasks[0].description == "General research task"

    def test_get_communication_thread_position(self, sample_shared_notes: str) -> None:
        """Test finding communication thread section."""
        parser = NotesParser(sample_shared_notes)
        position = parser.get_communication_thread_position()

        assert position is not None
        assert position > 0
