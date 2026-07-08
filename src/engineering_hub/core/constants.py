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
    TECHNICAL_REVIEWER = "technical-reviewer"
    WEEKLY_REVIEWER = "weekly-reviewer"
    LATEX_WRITER = "latex-writer"
    PANNING_FOR_GOLD = "panning-for-gold"
    CODE_ENGINEER = "code-engineer"
    ZETTELKASTEN_CURATOR = "zettelkasten-curator"
    LVT_TASK_EXTRACTOR = "lvt-task-extractor"
    COORDINATION_ANALYST = "coordination-analyst"
    ACOUSTIC_SIM_EXPERT = "acoustic-sim-expert"
    RENTAL_SCOUT = "rental-scout"
    BLENDER = "blender"
    HORN_ITERATOR = "horn-iterator"
    CAREER_COACH = "career-coach"
    PRODUCT_MANAGER = "product-manager"
    COMPLIANCE_ADVISOR = "compliance-advisor"


def is_ingest_task(description: str) -> bool:
    """Check if task description indicates a file ingest action."""
    desc_lower = description.lower()
    return "ingest" in desc_lower and ("from" in desc_lower or "source" in desc_lower)


# Default agent for unknown types
DEFAULT_AGENT = AgentType.RESEARCH

# Mapping of agent types to their prompt files
AGENT_PROMPT_FILES = {
    AgentType.RESEARCH: "research-agent.txt",
    AgentType.TECHNICAL_WRITER: "technical-writer.txt",
    AgentType.STANDARDS_CHECKER: "standards-checker.txt",
    AgentType.REF_ENGINEER: "ref-engineer.txt",
    AgentType.EVALUATOR: "evaluator.txt",
    AgentType.TECHNICAL_REVIEWER: "technical-reviewer.txt",
    AgentType.WEEKLY_REVIEWER: "weekly-reviewer.txt",
    AgentType.LATEX_WRITER: "latex-writer.txt",
    AgentType.PANNING_FOR_GOLD: "panning-for-gold.txt",
    AgentType.CODE_ENGINEER: "code-engineer.txt",
    AgentType.ZETTELKASTEN_CURATOR: "zettelkasten-curator.txt",
    AgentType.LVT_TASK_EXTRACTOR: "lvt-task-extractor.txt",
    AgentType.COORDINATION_ANALYST: "coordination-analyst.txt",
    AgentType.ACOUSTIC_SIM_EXPERT: "acoustic-sim-expert.txt",
    AgentType.RENTAL_SCOUT: "rental-scout.txt",
    AgentType.BLENDER: "blender.txt",
    AgentType.HORN_ITERATOR: "horn-iterator.txt",
    AgentType.CAREER_COACH: "career-coach.txt",
    AgentType.PRODUCT_MANAGER: "product-manager.txt",
    AgentType.COMPLIANCE_ADVISOR: "compliance-advisor.txt",
}
