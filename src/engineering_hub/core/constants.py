"""Constants and enums for Engineering Hub."""

from enum import Enum


class TaskStatus(str, Enum):
    """Status values for tasks in shared notes."""

    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"


class AgentType(str, Enum):
    """Available agent types."""

    RESEARCH = "research"
    TECHNICAL_WRITER = "technical-writer"
    STANDARDS_CHECKER = "standards-checker"
    REF_ENGINEER = "ref_engineer"
    EVALUATOR = "evaluator"


# Default agent for unknown types
DEFAULT_AGENT = AgentType.RESEARCH

# Mapping of agent types to their prompt files
AGENT_PROMPT_FILES = {
    AgentType.RESEARCH: "research-agent.txt",
    AgentType.TECHNICAL_WRITER: "technical-writer.txt",
    AgentType.STANDARDS_CHECKER: "standards-checker.txt",
    AgentType.REF_ENGINEER: "ref-engineer.txt",
    AgentType.EVALUATOR: "evaluator.txt",
}
