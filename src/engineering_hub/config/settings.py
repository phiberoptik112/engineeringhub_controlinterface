"""Application settings using pydantic-settings."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Default category-to-agent mapping for journal mode
DEFAULT_JOURNAL_CATEGORIES: dict[str, str] = {
    "Project Work to-do": "research",
    "Technical Writing Work": "technical-writer",
    "Technical Review Work": "technical-reviewer",
    "Thoughts to Expand or Clarify": "research",
}


class Settings(BaseSettings):
    """Application settings with environment variable support."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="ENGINEERING_HUB_",
        extra="ignore",
    )

    # Django API settings
    django_api_url: str = Field(
        default="http://localhost:8000/api",
        description="Base URL for Django API",
    )
    django_api_token: str = Field(
        default="",
        description="Django API authentication token",
    )
    django_cache_ttl: int = Field(
        default=300,
        description="Cache TTL for Django API responses in seconds",
    )

    # Anthropic API settings
    anthropic_api_key: str = Field(
        default="",
        description="Anthropic API key for Claude",
    )
    anthropic_model: str = Field(
        default="claude-sonnet-4-5-20250929",
        description="Claude model to use",
    )
    max_tokens: int = Field(
        default=4000,
        description="Maximum tokens for Claude responses",
    )

    # Workspace paths
    workspace_dir: Path = Field(
        default=Path.home() / "org-roam" / "engineering-hub",
        description="Base workspace directory",
    )
    inputs_dir: Path | None = Field(
        default=None,
        description="Working directory for input files (PDFs, DOCX, .md). Defaults to workspace_dir/inputs",
    )

    # Journal mode (vs legacy shared-notes.md)
    use_journal_mode: bool = Field(
        default=True,
        description="Use journal.md with category-based tasks (vs legacy shared-notes.md)",
    )

    # Journal filename (relative to workspace_dir)
    journal_filename: str = Field(
        default="journal.md",
        description="Journal filename when in journal mode",
    )

    # Journal category-to-agent mapping (set via from_yaml, not env)
    journal_categories: dict[str, str] = Field(
        default_factory=lambda: dict(DEFAULT_JOURNAL_CATEGORIES),
        description="Category header -> agent type mapping",
    )

    staging_manifest_name: str = Field(
        default="manifest.json",
        description="Manifest filename in staging directories",
    )

    # Org-roam journal directory (for weekly review and org task dispatch)
    org_journal_dir: Path = Field(
        default=Path.home() / "org-roam" / "journal",
        description="Path to org-roam daily journal directory (YYYY-MM-DD.org files)",
    )

    # Org mode: use org-roam daily journals as the task source instead of journal.md
    use_org_mode: bool = Field(
        default=False,
        description="Use org-roam daily .org files for task dispatch instead of journal.md",
    )

    # Org headings to scan for agent tasks
    org_task_sections: list[str] = Field(
        default_factory=lambda: ["Overnight Agent Tasks"],
        description="Org heading names whose list items are parsed as agent tasks",
    )

    # How many recent days to scan for pending tasks in org mode
    org_lookback_days: int = Field(
        default=2,
        description="Number of recent daily journal files to scan for pending tasks",
    )

    # How many recent days to scan when enriching agent context with historical tasks
    org_context_lookback_days: int = Field(
        default=7,
        description="Number of recent daily journal files to include when building historical task context for agents",
    )

    # Ollama settings (local embeddings)
    ollama_host: str = Field(
        default="http://localhost:11434",
        description="Ollama server URL for local embeddings",
    )
    ollama_embed_model: str = Field(
        default="nomic-embed-text",
        description="Model to use for embeddings. Pull with: ollama pull nomic-embed-text",
    )

    # Memory settings
    memory_enabled: bool = Field(
        default=True,
        description="Enable local vector memory capture and retrieval",
    )
    memory_search_k: int = Field(
        default=5,
        description="Max memory results injected into agent context per task",
    )
    memory_search_threshold: float = Field(
        default=0.35,
        description="Minimum cosine similarity for memory results (0.0-1.0)",
    )

    @property
    def resolved_inputs_dir(self) -> Path:
        """Effective inputs directory — custom path if set, else workspace_dir/inputs."""
        if self.inputs_dir is not None:
            return self.inputs_dir
        return self.workspace_dir / "inputs"

    @property
    def journal_file(self) -> Path:
        """Path to the journal file (when in journal mode)."""
        return self.workspace_dir / self.journal_filename

    @property
    def notes_file(self) -> Path:
        """Path to the notes file (journal or legacy shared-notes)."""
        if self.use_journal_mode:
            return self.journal_file
        return self.workspace_dir / "shared-notes.md"

    @property
    def output_dir(self) -> Path:
        """Path to the outputs directory."""
        return self.workspace_dir / "outputs"

    @property
    def staging_dir(self) -> Path:
        """Path to the staging directory for ingested files."""
        return self.output_dir / "staging"

    @property
    def prompts_dir(self) -> Path:
        """Path to the prompts directory."""
        # First check workspace, then fall back to package prompts
        workspace_prompts = self.workspace_dir / "prompts"
        if workspace_prompts.exists():
            return workspace_prompts
        # Return package prompts dir (relative to this file)
        return Path(__file__).parent.parent.parent.parent / "prompts"

    @classmethod
    def from_yaml(cls, config_path: Path) -> "Settings":
        """Load settings from YAML config file.

        YAML values are used as defaults, environment variables override.
        """
        import yaml

        if not config_path.exists():
            return cls()

        with open(config_path) as f:
            config = yaml.safe_load(f) or {}

        # Flatten nested config for pydantic
        flat_config = {}

        if "django" in config:
            flat_config["django_api_url"] = config["django"].get("api_url")
            flat_config["django_api_token"] = config["django"].get("api_token")
            flat_config["django_cache_ttl"] = config["django"].get("cache_ttl")

        if "anthropic" in config:
            flat_config["anthropic_api_key"] = config["anthropic"].get("api_key")
            flat_config["anthropic_model"] = config["anthropic"].get("model")
            flat_config["max_tokens"] = config["anthropic"].get("max_tokens")

        if "workspace" in config:
            workspace = config["workspace"].get("dir")
            if workspace:
                flat_config["workspace_dir"] = Path(workspace).expanduser()
            inputs = config["workspace"].get("inputs_dir")
            if inputs:
                flat_config["inputs_dir"] = Path(inputs).expanduser()

        if "journal" in config:
            journal = config["journal"]
            if journal.get("use_journal_mode") is not None:
                flat_config["use_journal_mode"] = journal["use_journal_mode"]
            if journal.get("file"):
                flat_config["journal_filename"] = journal["file"]
            if journal.get("categories"):
                flat_config["journal_categories"] = journal["categories"]
            if journal.get("org_journal_dir"):
                flat_config["org_journal_dir"] = Path(journal["org_journal_dir"]).expanduser()
            if journal.get("use_org_mode") is not None:
                flat_config["use_org_mode"] = journal["use_org_mode"]
            if journal.get("org_task_sections"):
                flat_config["org_task_sections"] = journal["org_task_sections"]
            if journal.get("org_lookback_days") is not None:
                flat_config["org_lookback_days"] = journal["org_lookback_days"]
            if journal.get("org_context_lookback_days") is not None:
                flat_config["org_context_lookback_days"] = journal["org_context_lookback_days"]

        if "staging" in config:
            staging = config["staging"]
            if staging.get("manifest_name"):
                flat_config["staging_manifest_name"] = staging["manifest_name"]

        if "ollama" in config:
            ollama = config["ollama"]
            if ollama.get("host"):
                flat_config["ollama_host"] = ollama["host"]
            if ollama.get("embed_model"):
                flat_config["ollama_embed_model"] = ollama["embed_model"]

        if "memory" in config:
            mem = config["memory"]
            if mem.get("enabled") is not None:
                flat_config["memory_enabled"] = mem["enabled"]
            if mem.get("search_k") is not None:
                flat_config["memory_search_k"] = mem["search_k"]
            if mem.get("threshold") is not None:
                flat_config["memory_search_threshold"] = mem["threshold"]

        # Remove None values and empty strings so env vars are not shadowed by blank YAML fields
        flat_config = {k: v for k, v in flat_config.items() if v is not None and v != ""}

        return cls(**flat_config)
