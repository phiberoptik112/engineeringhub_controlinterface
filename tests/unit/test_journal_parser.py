"""Tests for journal parser."""

import pytest

from engineering_hub.core.constants import TaskStatus
from engineering_hub.notes.journal_parser import JournalParser


SAMPLE_JOURNAL = """---
workspace: engineering-hub
---

# Engineering Hub Journal

## 2025-02-17

### Incoming Comms
- Email from client X
- Call scheduled

### Project Work to-do
- [ ] Draft test protocol for [[django://project/25]] → [[/outputs/docs/protocol-25.md]]
- [ ] Review scope for project 26
- [x] Already done task

### Technical Writing Work
- [ ] Expand section 3 on ASTM E336 in [[django://project/25]] report
- [ ] Draft executive summary for [[django://project/25]] → [[/outputs/docs/exec-summary-25.md]]

### Thoughts to Expand or Clarify
- [ ] Research impact of humidity on STC ratings

## 2025-02-18

### Technical Writing Work
- [ ] New task from next day
"""

CATEGORY_MAPPING = {
    "Project Work to-do": "research",
    "Technical Writing Work": "technical-writer",
    "Thoughts to Expand or Clarify": "research",
}


class TestJournalParser:
    """Tests for JournalParser."""

    def test_parse_frontmatter(self) -> None:
        """Test parsing YAML frontmatter."""
        parser = JournalParser(SAMPLE_JOURNAL, CATEGORY_MAPPING)
        frontmatter = parser.parse_frontmatter()

        assert frontmatter["workspace"] == "engineering-hub"

    def test_parse_tasks_extracts_unchecked_only(self) -> None:
        """Test that only unchecked items under mapped categories become tasks."""
        parser = JournalParser(SAMPLE_JOURNAL, CATEGORY_MAPPING)
        tasks = parser.parse_tasks()

        # 2 from Project Work to-do (skip [x]), 2 from Technical Writing, 1 from Thoughts
        # 1 from 2025-02-18 Technical Writing
        assert len(tasks) == 6

    def test_parse_task_with_project_and_deliverable(self) -> None:
        """Test parsing task with project ref and deliverable."""
        parser = JournalParser(SAMPLE_JOURNAL, CATEGORY_MAPPING)
        tasks = parser.parse_tasks()

        protocol_task = next(
            t for t in tasks if "Draft test protocol" in t.description
        )
        assert protocol_task.agent == "research"
        assert protocol_task.project_id == 25
        assert protocol_task.deliverable == "/outputs/docs/protocol-25.md"
        assert protocol_task.journal_date == "2025-02-17"
        assert protocol_task.category == "Project Work to-do"
        assert protocol_task.status == TaskStatus.PENDING

    def test_parse_task_extracts_input_paths(self) -> None:
        """Test that [[path]] wikilinks are extracted as input_paths, excluding deliverable."""
        content = """---
workspace: test
---

## 2025-02-17

### Technical Review Work
- [ ] Arbitrate draft [[/path/to/draft.tex]] for [[django://project/25]] → [[/outputs/reviews/decision-matrix-25.md]]
"""
        parser = JournalParser(content, {"Technical Review Work": "technical-reviewer"})
        tasks = parser.parse_tasks()

        assert len(tasks) == 1
        task = tasks[0]
        assert "/path/to/draft.tex" in task.input_paths
        assert "/outputs/reviews/decision-matrix-25.md" not in task.input_paths
        assert not any("django://" in p for p in task.input_paths)

    def test_parse_task_without_deliverable(self) -> None:
        """Test parsing task without explicit deliverable or project wikilink."""
        parser = JournalParser(SAMPLE_JOURNAL, CATEGORY_MAPPING)
        tasks = parser.parse_tasks()

        scope_task = next(t for t in tasks if "Review scope" in t.description)
        # No [[django://project/N]] wikilink, so project_id is None
        assert scope_task.project_id is None
        assert scope_task.deliverable is None

    def test_parse_skips_incoming_comms(self) -> None:
        """Test that Incoming Comms (no mapping) produces no tasks."""
        parser = JournalParser(SAMPLE_JOURNAL, CATEGORY_MAPPING)
        tasks = parser.parse_tasks()

        assert not any(t.category == "Incoming Comms" for t in tasks)

    def test_parse_skips_in_progress(self) -> None:
        """Test that items with (in progress) are skipped."""
        content = """---
workspace: test
---

## 2025-02-17

### Project Work to-do
- [ ] Normal task
- [ ] Task in progress (in progress)
"""
        parser = JournalParser(content, {"Project Work to-do": "research"})
        tasks = parser.parse_tasks()

        assert len(tasks) == 1
        assert "Normal task" in tasks[0].description

    def test_task_id(self) -> None:
        """Test task_id for journal tasks."""
        parser = JournalParser(SAMPLE_JOURNAL, CATEGORY_MAPPING)
        tasks = parser.parse_tasks()

        task = tasks[0]
        assert task.task_id == f"{task.journal_date}:{task.category}:{task.start_line}"

    def test_get_communication_thread_position(self) -> None:
        """Test finding communication thread section."""
        content = """---
workspace: test
---

## 2025-02-17

### Project Work to-do
- [ ] Task

## Agent Communication Thread

Messages here
"""
        parser = JournalParser(content, {"Project Work to-do": "research"})
        pos = parser.get_communication_thread_position()
        assert pos is not None
        assert pos > 0
