"""Application settings using pydantic-settings."""

from pathlib import Path
from typing import Any

from pydantic import Field, SecretStr
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
    django_api_token: SecretStr = Field(
        default=SecretStr(""),
        description="Django API authentication token",
    )
    django_cache_ttl: int = Field(
        default=300,
        description="Cache TTL for Django API responses in seconds",
    )

    # Anthropic API settings
    anthropic_api_key: SecretStr = Field(
        default=SecretStr(""),
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
        default=3,
        description="Number of recent daily journal files to scan for pending tasks. "
        "3 covers a weekend gap (Friday evening → Monday morning).",
    )

    # How many recent days to scan when enriching agent context with historical tasks
    org_context_lookback_days: int = Field(
        default=7,
        description="Number of recent daily journal files to include when building historical task context for agents",
    )

    # Roam graph integration
    roam_wrappers_enabled: bool = Field(
        default=True,
        description="Create .org wrapper nodes in the roam directory for agent outputs",
    )

    # LLM provider selection
    llm_provider: str = Field(
        default="anthropic",
        description="LLM backend: 'anthropic' (cloud API), 'mlx' (local Apple Silicon), "
        "or 'ollama' (local/networked Ollama server)",
    )

    # MLX local model settings (used when llm_provider == "mlx")
    mlx_model_path: str = Field(
        default="",
        description="HuggingFace model ID or local path to MLX snapshot directory",
    )
    mlx_temp: float = Field(
        default=0.7,
        description="Sampling temperature for MLX generation",
    )
    mlx_top_p: float = Field(
        default=0.9,
        description="Top-p (nucleus) sampling for MLX generation",
    )
    mlx_min_p: float = Field(
        default=0.05,
        description="Min-p sampling floor for MLX generation",
    )
    mlx_repetition_penalty: float = Field(
        default=1.1,
        description="Repetition penalty for MLX generation",
    )
    mlx_max_tokens: int = Field(
        default=4096,
        description="Default max tokens for MLX generation",
    )

    # Ollama settings (local embeddings + optional chat generation)
    ollama_host: str = Field(
        default="http://localhost:11434",
        description="Ollama server URL for embeddings and chat generation",
    )
    ollama_embed_model: str = Field(
        default="nomic-embed-text",
        description="Model to use for embeddings. Pull with: ollama pull nomic-embed-text",
    )
    ollama_chat_model: str = Field(
        default="",
        description="Ollama model for chat generation (e.g. 'llama3.1:8b'). "
        "Required when llm_provider is 'ollama'.",
    )
    ollama_chat_timeout: int = Field(
        default=120,
        description="HTTP timeout in seconds for Ollama chat requests",
    )
    ollama_temp: float = Field(
        default=0.7,
        description="Sampling temperature for Ollama generation",
    )
    ollama_top_p: float = Field(
        default=0.9,
        description="Top-p (nucleus) sampling for Ollama generation",
    )

    # Docker container execution settings
    docker_enabled: bool = Field(
        default=False,
        description="Run agent tasks in Docker containers (Anthropic/Ollama only; MLX stays on host)",
    )
    docker_task_image: str = Field(
        default="engineering-hub-task:latest",
        description="Docker image for ephemeral task containers",
    )
    docker_network: str = Field(
        default="engineering-hub-net",
        description="Docker network for task containers to reach Ollama / APIs",
    )
    docker_cpu_limit: float = Field(
        default=2.0,
        description="CPU cores allocated per task container",
    )
    docker_memory_limit: str = Field(
        default="2g",
        description="Memory limit per task container (Docker format, e.g. '2g', '512m')",
    )
    docker_task_timeout: int = Field(
        default=300,
        description="Seconds before force-stopping a task container",
    )
    docker_max_concurrent: int = Field(
        default=3,
        description="Maximum parallel task containers",
    )
    docker_ollama_host: str = Field(
        default="http://ollama:11434",
        description="Ollama URL as seen from inside task containers (Docker service name)",
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

    # Document ingest chunking settings
    chunk_enabled: bool = Field(
        default=True,
        description="Embed document chunks into memory on ingest",
    )
    chunk_max_tokens: int = Field(
        default=512,
        description="Max tokens per chunk for document ingest (aligned with nomic-embed-text context)",
    )

    # ── Journaler daemon settings ──────────────────────────────────
    journaler_enabled: bool = Field(
        default=False,
        description="Enable the Journaler ambient listener daemon",
    )
    journaler_model_path: str = Field(
        default="",
        description="HuggingFace model ID or local path for Journaler MLX model",
    )
    journaler_model_profile: str = Field(
        default="default",
        description="Named profile under journaler.models (when models map is non-empty)",
    )
    journaler_models: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Named Journaler MLX profiles (model_path, sampling, enable_thinking, ...)",
    )
    journaler_model_context_window: int = Field(
        default=32768,
        description="Context window in tokens for pressure management (default when not in profile)",
    )
    journaler_scan_interval_min: int = Field(
        default=10,
        description="Interval in minutes between org-roam scans",
    )
    journaler_briefing_enabled: bool = Field(
        default=True,
        description="Enable scheduled morning briefings",
    )
    journaler_briefing_time: str = Field(
        default="09:00",
        description="Time for morning briefing (HH:MM, local time)",
    )
    journaler_chat_enabled: bool = Field(
        default=True,
        description="Enable HTTP chat endpoint",
    )
    journaler_chat_host: str = Field(
        default="127.0.0.1",
        description="Chat server bind address",
    )
    journaler_chat_port: int = Field(
        default=18790,
        description="Chat server port",
    )
    journaler_slack_enabled: bool = Field(
        default=False,
        description="Enable Slack webhook posting",
    )
    journaler_slack_webhook_url: str = Field(
        default="",
        description="Slack incoming webhook URL (or set JOURNALER_SLACK_WEBHOOK)",
    )
    journaler_max_conversation_history: int = Field(
        default=20,
        description="Number of conversation turns to keep in memory",
    )
    journaler_max_tokens: int = Field(
        default=4096,
        description="Max tokens for Journaler model responses",
    )
    journaler_temp: float = Field(
        default=0.7,
        description="Sampling temperature for Journaler model",
    )
    journaler_top_p: float = Field(
        default=0.9,
        description="Top-p sampling for Journaler model",
    )
    journaler_min_p: float = Field(
        default=0.05,
        description="Min-p sampling for Journaler model",
    )
    journaler_repetition_penalty: float = Field(
        default=1.1,
        description="Repetition penalty for Journaler model",
    )
    journaler_load_max_context_fraction: float = Field(
        default=0.40,
        gt=0.0,
        le=1.0,
        description="Fraction of remaining context (after history, etc.) for each /load chunk",
    )
    journaler_load_max_chars_absolute: int = Field(
        default=200_000,
        ge=1,
        description="Hard ceiling on characters loaded per file (slash /load)",
    )
    journaler_load_min_chars: int = Field(
        default=1024,
        ge=0,
        description="When budget allows, prefer at least this many chars per /load (within token headroom)",
    )
    journaler_load_slack_tokens: int = Field(
        default=256,
        ge=0,
        description="Extra tokens subtracted from headroom when sizing /load (safety margin)",
    )
    journaler_agent_backend: str = Field(
        default="mlx",
        description='Journaler /agent delegation: "mlx", "claude", or "auto"',
    )
    journaler_skills_dir: Path | None = Field(
        default=None,
        description="Directory of skill YAML files for Journaler agent delegation",
    )
    journaler_anthropic_api_key: SecretStr | None = Field(
        default=None,
        description="Optional Anthropic key for Journaler /agent; falls back to anthropic_api_key",
    )
    journaler_scan_org_roam_tree: bool = Field(
        default=True,
        description="When True, rglob the full org_roam_dir for *.org; when False, scan only "
        "journal.org_journal_dir plus journaler.watch_dirs",
    )
    journaler_watch_dirs: list[Path] = Field(
        default_factory=list,
        description="Extra org directories to include in Journaler scans (rglob *.org)",
    )
    journaler_journal_lookback_days: int = Field(
        default=5,
        ge=0,
        description="Include daily journal files from the last N calendar days (with journal_max_files cap)",
    )
    journaler_journal_max_files: int = Field(
        default=5,
        ge=1,
        description="Max number of recent daily journal files to parse for context/tasks",
    )

    # Report template settings
    templates_dir: Path | None = Field(
        default=None,
        description="Directory containing report template skeletons. "
        "Defaults to workspace_dir/templates",
    )

    # Capture template settings
    capture_templates_dir: Path | None = Field(
        default=None,
        description="Directory containing hub capture template YAML files. "
        "Defaults to workspace_dir/capture_templates or repo root capture_templates/",
    )
    emacs_config_path: Path = Field(
        default=Path.home() / ".doom.d" / "config.el",
        description="Path to Emacs config.el for capture template import/export",
    )

    # PDF reference corpus settings
    corpus_enabled: bool = Field(
        default=False,
        description="Enable PDF reference corpus context injection (requires corpus.db)",
    )
    corpus_db_path: Path | None = Field(
        default=None,
        description="Path to corpus.db produced by libraryfiles_corpus ingest",
    )
    corpus_search_k: int = Field(
        default=5,
        description="Max corpus chunks injected into agent context per task",
    )
    corpus_search_threshold: float = Field(
        default=0.40,
        description="Minimum cosine similarity for corpus results (higher than memory threshold)",
    )

    @property
    def corpus_audit_log_path(self) -> Path | None:
        """Path to the retrieval audit JSONL file, derived from corpus_db_path.

        Returns None when corpus_db_path is not configured so callers can
        treat audit logging as an optional no-op.
        """
        if self.corpus_db_path is None:
            return None
        return self.corpus_db_path.expanduser().parent / "retrieval_audit.jsonl"

    @property
    def resolved_templates_dir(self) -> Path:
        """Effective templates directory — custom path if set, else workspace_dir/templates."""
        if self.templates_dir is not None:
            return self.templates_dir
        return self.workspace_dir / "templates"

    @property
    def resolved_capture_templates_dir(self) -> Path:
        """Effective capture templates directory.

        Priority: explicit setting > workspace_dir/capture_templates > repo root fallback.
        """
        if self.capture_templates_dir is not None:
            return self.capture_templates_dir
        workspace_ct = self.workspace_dir / "capture_templates"
        if workspace_ct.exists():
            return workspace_ct
        from engineering_hub.capture.loader import _default_capture_templates_dir
        return _default_capture_templates_dir()

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
    def journaler_state_dir(self) -> Path:
        """Path to the Journaler daemon state directory."""
        return self.workspace_dir / ".journaler"

    @property
    def journaler_briefing_output_dir(self) -> Path:
        """Path to the Journaler briefing output directory."""
        return self.journaler_state_dir / "briefings"

    @property
    def resolved_journaler_model_path(self) -> str:
        """MLX model for Journaler: explicit journaler.model_path, else mlx.model_path."""
        j = (self.journaler_model_path or "").strip()
        if j:
            return j
        return (self.mlx_model_path or "").strip()

    def journaler_delegation_api_key(self) -> str:
        """API key for Claude-backed Journaler /agent (journaler override, else global)."""
        if self.journaler_anthropic_api_key is not None:
            v = self.journaler_anthropic_api_key.get_secret_value()
            if v:
                return v
        return self.anthropic_api_key.get_secret_value()

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
            token = config["django"].get("api_token")
            if token:
                flat_config["django_api_token"] = SecretStr(token)
            flat_config["django_cache_ttl"] = config["django"].get("cache_ttl")

        if "anthropic" in config:
            api_key = config["anthropic"].get("api_key")
            if api_key:
                flat_config["anthropic_api_key"] = SecretStr(api_key)
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

        if "roam" in config:
            roam = config["roam"]
            if roam.get("wrappers_enabled") is not None:
                flat_config["roam_wrappers_enabled"] = roam["wrappers_enabled"]

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
            if ollama.get("chat_model"):
                flat_config["ollama_chat_model"] = ollama["chat_model"]
            if ollama.get("chat_timeout") is not None:
                flat_config["ollama_chat_timeout"] = ollama["chat_timeout"]
            if ollama.get("temp") is not None:
                flat_config["ollama_temp"] = ollama["temp"]
            if ollama.get("top_p") is not None:
                flat_config["ollama_top_p"] = ollama["top_p"]

        if "docker" in config:
            docker = config["docker"]
            if docker.get("enabled") is not None:
                flat_config["docker_enabled"] = docker["enabled"]
            if docker.get("task_image"):
                flat_config["docker_task_image"] = docker["task_image"]
            if docker.get("network"):
                flat_config["docker_network"] = docker["network"]
            if docker.get("cpu_limit") is not None:
                flat_config["docker_cpu_limit"] = docker["cpu_limit"]
            if docker.get("memory_limit"):
                flat_config["docker_memory_limit"] = docker["memory_limit"]
            if docker.get("task_timeout") is not None:
                flat_config["docker_task_timeout"] = docker["task_timeout"]
            if docker.get("max_concurrent") is not None:
                flat_config["docker_max_concurrent"] = docker["max_concurrent"]
            if docker.get("ollama_host"):
                flat_config["docker_ollama_host"] = docker["ollama_host"]

        if "llm_provider" in config:
            flat_config["llm_provider"] = config["llm_provider"]

        if "mlx" in config:
            mlx = config["mlx"]
            if mlx.get("model_path"):
                flat_config["mlx_model_path"] = mlx["model_path"]
            if mlx.get("temp") is not None:
                flat_config["mlx_temp"] = mlx["temp"]
            if mlx.get("top_p") is not None:
                flat_config["mlx_top_p"] = mlx["top_p"]
            if mlx.get("min_p") is not None:
                flat_config["mlx_min_p"] = mlx["min_p"]
            if mlx.get("repetition_penalty") is not None:
                flat_config["mlx_repetition_penalty"] = mlx["repetition_penalty"]
            if mlx.get("max_tokens") is not None:
                flat_config["mlx_max_tokens"] = mlx["max_tokens"]

        if "memory" in config:
            mem = config["memory"]
            if mem.get("enabled") is not None:
                flat_config["memory_enabled"] = mem["enabled"]
            if mem.get("search_k") is not None:
                flat_config["memory_search_k"] = mem["search_k"]
            if mem.get("threshold") is not None:
                flat_config["memory_search_threshold"] = mem["threshold"]

        if "chunking" in config:
            chunking = config["chunking"]
            if chunking.get("enabled") is not None:
                flat_config["chunk_enabled"] = chunking["enabled"]
            if chunking.get("max_tokens") is not None:
                flat_config["chunk_max_tokens"] = chunking["max_tokens"]

        if "journaler" in config:
            j = config["journaler"]
            if j.get("enabled") is not None:
                flat_config["journaler_enabled"] = j["enabled"]
            if j.get("model_path"):
                flat_config["journaler_model_path"] = j["model_path"]
            if j.get("model_profile"):
                flat_config["journaler_model_profile"] = j["model_profile"]
            if j.get("models") is not None:
                flat_config["journaler_models"] = j["models"] or {}
            if j.get("model_context_window") is not None:
                flat_config["journaler_model_context_window"] = j["model_context_window"]
            if j.get("scan_interval_min") is not None:
                flat_config["journaler_scan_interval_min"] = j["scan_interval_min"]
            if j.get("briefing_enabled") is not None:
                flat_config["journaler_briefing_enabled"] = j["briefing_enabled"]
            if j.get("briefing_time"):
                flat_config["journaler_briefing_time"] = j["briefing_time"]
            if j.get("chat_enabled") is not None:
                flat_config["journaler_chat_enabled"] = j["chat_enabled"]
            if j.get("chat_host"):
                flat_config["journaler_chat_host"] = j["chat_host"]
            if j.get("chat_port") is not None:
                flat_config["journaler_chat_port"] = j["chat_port"]
            if j.get("slack_enabled") is not None:
                flat_config["journaler_slack_enabled"] = j["slack_enabled"]
            if j.get("slack_webhook_url"):
                flat_config["journaler_slack_webhook_url"] = j["slack_webhook_url"]
            if j.get("max_conversation_history") is not None:
                flat_config["journaler_max_conversation_history"] = j["max_conversation_history"]
            if j.get("max_tokens") is not None:
                flat_config["journaler_max_tokens"] = j["max_tokens"]
            if j.get("temp") is not None:
                flat_config["journaler_temp"] = j["temp"]
            if j.get("top_p") is not None:
                flat_config["journaler_top_p"] = j["top_p"]
            if j.get("min_p") is not None:
                flat_config["journaler_min_p"] = j["min_p"]
            if j.get("repetition_penalty") is not None:
                flat_config["journaler_repetition_penalty"] = j["repetition_penalty"]
            if j.get("load_max_context_fraction") is not None:
                flat_config["journaler_load_max_context_fraction"] = j["load_max_context_fraction"]
            if j.get("load_max_chars_absolute") is not None:
                flat_config["journaler_load_max_chars_absolute"] = j["load_max_chars_absolute"]
            if j.get("load_min_chars") is not None:
                flat_config["journaler_load_min_chars"] = j["load_min_chars"]
            if j.get("load_slack_tokens") is not None:
                flat_config["journaler_load_slack_tokens"] = j["load_slack_tokens"]
            if j.get("agent_backend"):
                flat_config["journaler_agent_backend"] = j["agent_backend"]
            if j.get("skills_dir"):
                flat_config["journaler_skills_dir"] = Path(j["skills_dir"]).expanduser()
            j_anthropic = j.get("anthropic_api_key")
            if j_anthropic:
                flat_config["journaler_anthropic_api_key"] = SecretStr(str(j_anthropic))
            if j.get("scan_org_roam_tree") is not None:
                flat_config["journaler_scan_org_roam_tree"] = j["scan_org_roam_tree"]
            if j.get("watch_dirs") is not None:
                flat_config["journaler_watch_dirs"] = [
                    Path(p).expanduser() for p in (j["watch_dirs"] or [])
                ]
            if j.get("journal_lookback_days") is not None:
                flat_config["journaler_journal_lookback_days"] = j["journal_lookback_days"]
            if j.get("journal_max_files") is not None:
                flat_config["journaler_journal_max_files"] = j["journal_max_files"]

        if "templates" in config:
            tpl = config["templates"]
            if tpl.get("dir"):
                flat_config["templates_dir"] = Path(tpl["dir"]).expanduser()

        if "capture" in config:
            cap = config["capture"]
            if cap.get("templates_dir"):
                flat_config["capture_templates_dir"] = Path(cap["templates_dir"]).expanduser()
            if cap.get("emacs_config"):
                flat_config["emacs_config_path"] = Path(cap["emacs_config"]).expanduser()

        if "corpus" in config:
            corpus = config["corpus"]
            if corpus.get("enabled") is not None:
                flat_config["corpus_enabled"] = corpus["enabled"]
            if corpus.get("db_path"):
                flat_config["corpus_db_path"] = Path(corpus["db_path"]).expanduser()
            if corpus.get("search_k") is not None:
                flat_config["corpus_search_k"] = corpus["search_k"]
            if corpus.get("threshold") is not None:
                flat_config["corpus_search_threshold"] = corpus["threshold"]

        def _is_empty(v: object) -> bool:
            if v is None or v == "":
                return True
            if isinstance(v, SecretStr) and not v.get_secret_value():
                return True
            return False

        # Remove None values and empty strings so env vars are not shadowed by blank YAML fields
        flat_config = {k: v for k, v in flat_config.items() if not _is_empty(v)}

        return cls(**flat_config)
