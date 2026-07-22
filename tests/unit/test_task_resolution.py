from __future__ import annotations

from engineering_hub.journaler.models import TrackedTask
from engineering_hub.journaler.task_resolution import (
    detect_prose_completions,
    is_task_already_completed,
    merge_tracked_tasks,
    normalize_task_key,
)


def test_normalize_task_key_strips_agent_prefix() -> None:
    assert normalize_task_key("@research: ASTM E336 review") == "astm e336 review"


def test_merge_tracked_tasks_completed_wins() -> None:
    pending = TrackedTask(
        text="Draft report",
        status="pending",
        source_path="journals/2026-06-10.org",
        source_date="2026-06-10",
        source_heading="Work",
        line_hint=5,
        task_key="draft report",
        completion_kind="checkbox",
        first_seen="2026-06-10",
        last_seen="2026-06-10",
    )
    completed = TrackedTask(
        text="Draft report",
        status="completed",
        source_path="journals/2026-06-12.org",
        source_date="2026-06-12",
        source_heading="Work",
        line_hint=8,
        task_key="draft report",
        completion_kind="checkbox",
        first_seen="2026-06-12",
        last_seen="2026-06-12",
    )
    merged = merge_tracked_tasks([pending, completed])
    assert len(merged) == 1
    assert merged[0].status == "completed"
    assert merged[0].first_seen == "2026-06-10"


def test_detect_prose_completions() -> None:
    pending = [
        TrackedTask(
            text="ASTM E336 protocol draft",
            status="pending",
            source_path="journals/2026-06-10.org",
            source_date="2026-06-10",
            source_heading="",
            line_hint=1,
            task_key=normalize_task_key("ASTM E336 protocol draft"),
            completion_kind="checkbox",
            first_seen="2026-06-10",
            last_seen="2026-06-10",
        )
    ]
    hits = detect_prose_completions(
        pending,
        new_text="Finished ASTM E336 protocol draft today.",
        old_text="",
    )
    assert len(hits) == 1
    assert hits[0].task_key == normalize_task_key("ASTM E336 protocol draft")


def test_is_task_already_completed_substring() -> None:
    keys = {normalize_task_key("ASTM E336 protocol draft")}
    assert is_task_already_completed("Finish ASTM E336 protocol draft section", keys)
