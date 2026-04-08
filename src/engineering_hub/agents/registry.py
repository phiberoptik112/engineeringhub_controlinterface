"""Agent type registry and configuration."""

from dataclasses import dataclass, field

from engineering_hub.core.constants import AgentType


@dataclass
class AgentConfig:
    """Configuration for an agent type."""

    agent_type: AgentType
    prompt_file: str
    tools: list[str] = field(default_factory=list)
    max_tokens: int = 4096
    enabled: bool = True


# Default agent configurations
DEFAULT_AGENT_CONFIGS = {
    AgentType.RESEARCH: AgentConfig(
        agent_type=AgentType.RESEARCH,
        prompt_file="research-agent.txt",
        tools=["web_search", "web_fetch", "django_api"],
        max_tokens=4096,
    ),
    AgentType.TECHNICAL_WRITER: AgentConfig(
        agent_type=AgentType.TECHNICAL_WRITER,
        prompt_file="technical-writer.txt",
        tools=["create_file", "view", "ingest_files", "django_api"],
        max_tokens=4096,
    ),
    AgentType.STANDARDS_CHECKER: AgentConfig(
        agent_type=AgentType.STANDARDS_CHECKER,
        prompt_file="standards-checker.txt",
        tools=["view", "django_api"],
        max_tokens=4096,
    ),
    AgentType.REF_ENGINEER: AgentConfig(
        agent_type=AgentType.REF_ENGINEER,
        prompt_file="ref-engineer.txt",
        tools=["web_search", "get_project_file", "get_standard_details"],
        max_tokens=4096,
        enabled=False,  # Phase 5
    ),
    AgentType.EVALUATOR: AgentConfig(
        agent_type=AgentType.EVALUATOR,
        prompt_file="evaluator.txt",
        tools=["get_project_file", "view"],
        max_tokens=4096,
        enabled=False,  # Phase 5
    ),
    AgentType.TECHNICAL_REVIEWER: AgentConfig(
        agent_type=AgentType.TECHNICAL_REVIEWER,
        prompt_file="technical-reviewer.txt",
        tools=["ingest_files", "get_project_file"],
        max_tokens=8000,
    ),
    AgentType.WEEKLY_REVIEWER: AgentConfig(
        agent_type=AgentType.WEEKLY_REVIEWER,
        prompt_file="weekly-reviewer.txt",
        tools=[],
        max_tokens=6000,
    ),
}


class AgentRegistry:
    """Registry of available agent types and their configurations."""

    def __init__(self) -> None:
        """Initialize with default configurations."""
        self._configs = dict(DEFAULT_AGENT_CONFIGS)

    def get_config(self, agent_type: AgentType) -> AgentConfig | None:
        """Get configuration for an agent type."""
        return self._configs.get(agent_type)

    def is_enabled(self, agent_type: AgentType) -> bool:
        """Check if an agent type is enabled."""
        config = self._configs.get(agent_type)
        return config is not None and config.enabled

    def get_enabled_agents(self) -> list[AgentType]:
        """Get list of enabled agent types."""
        return [
            agent_type
            for agent_type, config in self._configs.items()
            if config.enabled
        ]

    def enable_agent(self, agent_type: AgentType) -> None:
        """Enable an agent type."""
        if agent_type in self._configs:
            self._configs[agent_type].enabled = True

    def disable_agent(self, agent_type: AgentType) -> None:
        """Disable an agent type."""
        if agent_type in self._configs:
            self._configs[agent_type].enabled = False

    def update_config(self, agent_type: AgentType, **kwargs) -> None:
        """Update configuration for an agent type."""
        if agent_type in self._configs:
            config = self._configs[agent_type]
            for key, value in kwargs.items():
                if hasattr(config, key):
                    setattr(config, key, value)
