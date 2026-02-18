"""Core data models for Engineering Hub."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from engineering_hub.core.constants import AgentType, TaskStatus


class ParsedTask(BaseModel):
    """A task extracted from the shared notes file."""

    agent: str
    status: TaskStatus
    project_id: int | None = None
    description: str
    context: str | None = None
    deliverable: str | None = None
    start_line: int
    end_line: int
    raw_block: str
    # Journal mode fields (for category-based extraction)
    journal_date: str | None = None
    category: str | None = None

    @property
    def task_id(self) -> str:
        """Stable identifier for deduplication (journal or legacy)."""
        if self.journal_date is not None:
            return f"{self.journal_date}:{self.category}:{self.start_line}"
        return str(self.start_line)

    @property
    def agent_type(self) -> AgentType:
        """Get the agent type enum, defaulting to research if unknown."""
        try:
            return AgentType(self.agent)
        except ValueError:
            return AgentType.RESEARCH


class Project(BaseModel):
    """Project information from Django backend."""

    id: int
    title: str
    client_name: str
    status: str
    budget: str | None = None
    description: str | None = None
    start_date: str | None = None
    end_date: str | None = None


class Scope(BaseModel):
    """Scope item from project."""

    item: str
    standards: list[str] = Field(default_factory=list)


class Standard(BaseModel):
    """Standard reference."""

    type: str  # e.g., "ASTM", "ISO"
    id: str  # e.g., "ASTM E336-17a"


class FileInfo(BaseModel):
    """File information from Django."""

    id: int
    title: str
    file_type: str
    url: str | None = None
    created_at: str | None = None


class ProjectContext(BaseModel):
    """Rich project context for agents."""

    project: Project
    scope: list[str] = Field(default_factory=list)
    standards: list[Standard] = Field(default_factory=list)
    recent_files: list[FileInfo] = Field(default_factory=list)
    proposals: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentMessage(BaseModel):
    """A message in the agent communication thread."""

    timestamp: datetime
    agent: str
    content: str

    def format_for_notes(self) -> str:
        """Format message for writing to shared notes."""
        ts = self.timestamp.strftime("%Y-%m-%d %H:%M")
        return f"**[{ts}] @{self.agent}**\n{self.content}"


class TaskResult(BaseModel):
    """Result of task execution."""

    task: ParsedTask
    success: bool
    output_path: str | None = None
    error_message: str | None = None
    agent_response: str | None = None
