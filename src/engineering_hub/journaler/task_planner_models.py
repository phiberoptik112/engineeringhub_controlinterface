"""Task planner session state for Journaler chat (overnight queue proposals)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


ProposedTaskStatus = Literal["proposed", "confirmed", "modified", "rejected"]


@dataclass
class ProposedTask:
    """A queued task proposal before commit to pending-tasks.org."""

    agent_type: str
    description: str
    session_id: str
    session_timestamp: datetime
    proposed_at: datetime
    keywords: list[str] = field(default_factory=list)
    project_id: int | None = None
    input_paths: list[str] = field(default_factory=list)
    output_path: str | None = None
    context_flags: list[str] = field(default_factory=list)
    status: ProposedTaskStatus = "proposed"
    confidence: float = 0.0
    clarification_needed: bool = False
    list_index: int = 0  # 1-based display index within session proposals


class TaskPlannerSession:
    """Accumulates proposed tasks for one Journaler chat session."""

    def __init__(self, session_id: str, session_opened_at: datetime) -> None:
        self.session_id = session_id
        self.session_opened_at = session_opened_at
        self._tasks: list[ProposedTask] = []
        self._next_index = 1

    def add_proposal(self, task: ProposedTask) -> ProposedTask:
        task.list_index = self._next_index
        self._next_index += 1
        self._tasks.append(task)
        return task

    def list_all(self) -> list[ProposedTask]:
        return list(self._tasks)

    def get(self, n: int) -> ProposedTask | None:
        """1-based index."""
        for t in self._tasks:
            if t.list_index == n:
                return t
        return None

    def confirm(self, n: int | None = None) -> int:
        """Mark one task or all as confirmed. Returns count updated."""
        if n is None:
            count = 0
            for t in self._tasks:
                if t.status not in ("rejected",):
                    t.status = "confirmed"
                    count += 1
            return count
        t = self.get(n)
        if t is None or t.status == "rejected":
            return 0
        t.status = "confirmed"
        return 1

    def reject(self, n: int) -> bool:
        t = self.get(n)
        if t is None:
            return False
        t.status = "rejected"
        return True

    def edit_description(self, n: int, new_description: str) -> bool:
        t = self.get(n)
        if t is None or t.status == "rejected":
            return False
        t.description = new_description.strip()
        t.status = "modified"
        return True

    def clear_unconfirmed(self) -> int:
        """Drop proposed/modified entries; keep confirmed for commit."""
        before = len(self._tasks)
        self._tasks = [t for t in self._tasks if t.status == "confirmed"]
        return before - len(self._tasks)

    def clear_all_proposals(self) -> int:
        """Discard every non-committed proposal (everything in session)."""
        n = len(self._tasks)
        self._tasks.clear()
        self._next_index = 1
        return n

    def confirmed_tasks(self) -> list[ProposedTask]:
        return [t for t in self._tasks if t.status == "confirmed"]

    def any_confirmed(self) -> bool:
        return any(t.status == "confirmed" for t in self._tasks)

    def remove_by_list_indices(self, indices: set[int]) -> None:
        self._tasks = [t for t in self._tasks if t.list_index not in indices]
