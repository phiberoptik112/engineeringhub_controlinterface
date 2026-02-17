"""Application settings using pydantic-settings."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    @property
    def notes_file(self) -> Path:
        """Path to the shared notes file."""
        return self.workspace_dir / "shared-notes.md"

    @property
    def output_dir(self) -> Path:
        """Path to the outputs directory."""
        return self.workspace_dir / "outputs"

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

        # Remove None values
        flat_config = {k: v for k, v in flat_config.items() if v is not None}

        return cls(**flat_config)
