"""Application settings using pydantic-settings."""

from pathlib import Path
from typing import Any

import yaml
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
        populate_by_name=True,
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

    # Rental scout workspace (criteria.yaml, seen_listings.db, latest_digest.json)
    rental_scout_workspace_dir: Path = Field(
        default=Path.home() / "dev" / "rental_scout",
        description="Workspace directory for the Bay Area Rental Scout pipeline and artifacts",
    )
    rental_scout_python_path: Path | None = Field(
        default=None,
        description=(
            "Python interpreter for rental-scout pipeline subprocesses. "
            "When unset, uses {workspace_dir}/.venv/bin/python if present, "
            "otherwise the current process interpreter."
        ),
    )

    # Blender MCP (HTTP addon inside a running Blender session)
    blender_enabled: bool = Field(
        default=False,
        description="Enable Blender MCP client integration for agents and MCP proxy",
    )
    blender_mcp_url: str = Field(
        default="http://127.0.0.1:8765/mcp",
        description="HTTP MCP endpoint exposed by the Blender addon",
    )
    blender_auth_token: str | None = Field(
        default=None,
        description=(
            "Optional bearer token for Blender MCP auth. "
            "Prefer ENGINEERING_HUB_BLENDER_AUTH_TOKEN env var."
        ),
    )
    blender_connect_timeout_s: float = Field(
        default=10.0,
        description="Timeout in seconds for Blender MCP client connections",
    )
    blender_tools_cache_ttl_s: float = Field(
        default=60.0,
        description="Seconds to cache remote Blender tool listings",
    )
    blender_tool_allowlist: list[str] | None = Field(
        default=None,
        description=(
            "When set, only these remote tool names may be invoked. "
            "None means all tools except denylist entries."
        ),
    )
    blender_tool_denylist: list[str] | None = Field(
        default_factory=lambda: ["run_python_script"],
        description="Remote Blender tool names blocked from agent invocation",
    )

    # Horn Iterator (parametric exponential-horn sweep, LVT alert system)
    horn_iterator_enabled: bool = Field(
        default=True,
        description="Enable the horn iterator agent, CLI, and /horn slash command",
    )
    horn_iterator_output_dir: Path | None = Field(
        default=None,
        description=(
            "Directory for horn sweep exports (CSV/org). "
            "Defaults to {workspace_dir}/horn_iterator when unset."
        ),
    )
    horn_iterator_flare_rate_per_m: float | None = Field(
        default=None,
        description="Override the exponential flare rate m (/m); blueprint default 17.8",
    )
    horn_iterator_throat_area_mm2: float | None = Field(
        default=None,
        description="Override the diffraction-slot throat area S_T (mm^2); default 1050",
    )
    horn_iterator_slot_area_mm2: float | None = Field(
        default=None,
        description="Override the diffraction slot area used in geometry (mm^2); default 1050",
    )
    horn_iterator_adapter_length_mm: float | None = Field(
        default=None,
        description="Override the fixed pre-flare adapter length (mm); default 52",
    )

    # Org mode: use org-roam daily journals as the task source instead of journal.md
    use_org_mode: bool = Field(
        default=False,
        description="Use org-roam daily .org files for task dispatch instead of journal.md",
    )

    # Org headings to scan for agent tasks
    org_task_sections: list[str] = Field(
        default_factory=lambda: ["Overnight Agent Tasks", "Pending Agent Tasks"],
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

    # ── Pi coding-agent (code-engineer) settings ────────────────────
    pi_bin: str = Field(
        default="pi",
        description="Path to the pi CLI (or 'node /path/to/pi.js')",
    )
    pi_mode: str = Field(
        default="json",
        description="Pi non-interactive mode: 'json' (structured events) or 'print'",
    )
    pi_task_timeout: int = Field(
        default=1800,
        description="Seconds before force-killing a Pi run",
    )
    pi_max_concurrent: int = Field(
        default=1,
        description="Max parallel Pi runs (repos are stateful; keep low)",
    )
    pi_default_tools: str = Field(
        default="",
        description="Comma-separated Pi tool allowlist; empty = full toolset",
    )
    pi_provider: str = Field(
        default="anthropic",
        description="Pi provider: 'anthropic', 'openai', or 'google'",
    )
    pi_model: str = Field(
        default="claude-sonnet-4-5",
        description="Pi model pattern/id (supports 'provider/id' and ':thinking')",
    )
    pi_share_hub_api_key: bool = Field(
        default=True,
        description="Pass the hub's Anthropic key to Pi via --api-key (else use Pi's own creds)",
    )
    pi_offline: bool = Field(
        default=True,
        description="Set PI_OFFLINE=1 and PI_SKIP_VERSION_CHECK=1 for deterministic Pi runs",
    )
    code_projects: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Registered local repos for code-engineer: name -> {path, default_branch, ...}",
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
    journaler_end_of_day_time: str = Field(
        default="15:30",
        description="Time to generate the daily conversation summary and archive history (HH:MM, local time)",
    )

    # Discussion briefing settings
    journaler_discussion_briefing_enabled: bool = Field(
        default=False,
        description="Enable scheduled Topics Discussion Briefing (multi-persona roundtable)",
    )
    journaler_discussion_briefing_time: str = Field(
        default="08:45",
        description="Time for discussion briefing (HH:MM, local time); runs before morning briefing",
    )
    journaler_personas_dir: Path | None = Field(
        default=None,
        description="Path to personas/*.yaml directory; defaults to repo personas/ directory",
    )
    journaler_discussion_persona_lookback_days: int = Field(
        default=7,
        description="Days of per-persona history to inject into each discussion call",
    )
    journaler_discussion_max_tokens_per_persona: int = Field(
        default=1024,
        description="Max tokens generated per persona in the discussion briefing",
    )

    # Coordination scan settings
    journaler_coordination_scan_enabled: bool = Field(
        default=False,
        description="Enable scheduled coordination analyst scan (scans journal for client coordination signals)",
    )
    journaler_coordination_scan_interval_min: int = Field(
        default=0,
        description="Interval in minutes between coordination scans (0 = disabled)",
    )
    journaler_proactive_topic_scout_enabled: bool = Field(
        default=True,
        description="Run a one-shot MLX topic scout when a scan tick detects significant journal changes",
    )
    journaler_proactive_topic_scout_max_tokens: int = Field(
        default=512,
        ge=64,
        le=4096,
        description="Max tokens for proactive topic scout generation on significant scan ticks",
    )

    # Background agent work loop
    journaler_background_work_enabled: bool = Field(
        default=False,
        description="Enable background agent work loop that extracts tasks from briefings and delegates them",
    )
    journaler_background_work_interval_min: int = Field(
        default=60,
        ge=10,
        description="Interval in minutes between background work loop ticks",
    )
    journaler_background_work_max_tasks_per_day: int = Field(
        default=6,
        ge=1,
        le=20,
        description="Maximum number of background tasks to run per day",
    )
    journaler_background_work_auto_approve: bool = Field(
        default=False,
        description="When True, background work loop auto-delegates extracted tasks without user confirmation",
    )
    journaler_background_work_agent_backend: str = Field(
        default="mlx",
        description="Agent backend for background work tasks (mlx, claude, auto)",
    )
    journaler_background_work_chat_lookback_days: int = Field(
        default=3,
        ge=0,
        le=30,
        description="Days of conversation.jsonl chat history to include in task extraction and delegation context",
    )

    # Task-Integrator: inline call-and-response loop over the daily journal
    journaler_task_integrator_enabled: bool = Field(
        default=False,
        description="Enable the Task-Integrator loop that interviews the user inline in the daily journal and proposes agent tasks",
    )
    journaler_task_integrator_interval_min: int = Field(
        default=15,
        ge=5,
        description="Interval in minutes between Task-Integrator cycles",
    )
    journaler_task_integrator_conversation_section: str = Field(
        default="Agent Conversation",
        description="Daily-journal heading where the Task-Integrator writes interview questions and proposals",
    )
    journaler_task_integrator_output_section: str = Field(
        default="Overnight Agent Tasks",
        description="Daily-journal heading where approved @agent: tasks are queued for the Orchestrator",
    )
    journaler_task_integrator_excluded_sections: list[str] = Field(
        default_factory=lambda: [
            "Agent Conversation",
            "Overnight Agent Tasks",
            "Completed Agent Tasks",
            "Pending Agent Tasks",
            "Timesheet",
            "Journaler Cross-References",
        ],
        description="Daily-journal headings the Task-Integrator must not read as intake (agent-managed sections)",
    )
    journaler_task_integrator_max_questions: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Maximum interview questions the Task-Integrator asks per topic",
    )
    journaler_task_integrator_weekdays_only: bool = Field(
        default=True,
        description="When True, the Task-Integrator only queues approved tasks Mon-Fri",
    )
    journaler_task_integrator_max_tokens: int = Field(
        default=1024,
        ge=128,
        description="Max tokens for Task-Integrator interview/resolution model calls",
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
        default=40,
        description="Number of conversation turns to keep in memory",
    )
    journaler_max_tokens: int = Field(
        default=4096,
        description="Max tokens for Journaler model responses",
    )
    journaler_max_thinking_tokens: int = Field(
        default=8192,
        ge=0,
        description=(
            "Extra token budget for <think> reasoning blocks (thinking models); "
            "does not count against max_tokens"
        ),
    )
    journaler_thinking_max_tokens: int = Field(
        default=16384,
        description="Minimum generation budget when enable_thinking is true "
        "(thinking + answer share one cap)",
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
        default=0.65,
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
        default=128,
        ge=0,
        description="Extra tokens subtracted from headroom when sizing /load (safety margin)",
    )
    journaler_load_recent_max_files: int = Field(
        default=5,
        ge=1,
        description="Default number of files /load_recent loads when no count is given",
    )
    journaler_load_recent_days: int = Field(
        default=30,
        ge=0,
        description="/load_recent only considers files created in the last N days (0 = no cutoff)",
    )
    journaler_load_recent_roots: list[Path] = Field(
        default_factory=list,
        description="Optional explicit scan roots for /load_recent (overrides the default set)",
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
        default=30,
        ge=0,
        description="Include daily journal files from the last N calendar days (with journal_max_files cap)",
    )
    journaler_journal_max_files: int = Field(
        default=30,
        ge=1,
        description="Max number of recent daily journal files to parse for context/tasks",
    )
    journaler_pending_tasks_file: Path | None = Field(
        default=None,
        description="Journaler-owned org file for overnight agent queue (default: workspace .journaler/pending-tasks.org)",
    )
    journaler_default_task_mode: str = Field(
        default="immediate",
        description='Journaler routing: "immediate" (default) or "propose" (DISPATCH confirm flow)',
    )
    journaler_conversation_lookback_days: int = Field(
        default=7,
        ge=0,
        description="Number of past daily conversation summaries to include in the proactive context snapshot",
    )
    journaler_conversation_summary_excerpt_chars: int = Field(
        default=800,
        ge=200,
        description="Characters per past daily conversation summary in Journaler context",
    )
    journaler_roam_task_lookback_days: int = Field(
        default=14,
        ge=0,
        description="Include org-roam project notes modified within N days for task registry scans",
    )
    journaler_roam_task_max_files: int = Field(
        default=30,
        ge=1,
        description="Max roam project notes to parse for pending/completed tasks",
    )
    journaler_prose_completion_detection: bool = Field(
        default=True,
        description="Detect prose completion mentions in journal edits (e.g. 'finished X')",
    )
    journaler_context_management: dict[str, Any] = Field(
        default_factory=dict,
        description="Raw journaler.context_management YAML values for PressureConfig",
    )
    journaler_output_limit: dict[str, Any] = Field(
        default_factory=dict,
        description="Raw journaler.output_limit YAML values for OutputLimitConfig "
        "(policy, generation budget, summarize/continue/stop behavior)",
    )
    journaler_org_link_on_relation: bool = Field(
        default=True,
        description="When True, write a cross-reference link into today's journal when a related past conversation is detected",
    )

    # Zettelkasten proposal workflow
    zettelkasten_enabled: bool = Field(
        default=True,
        description="Enable the org-roam Zettelkasten proposal workflow",
    )
    zettelkasten_proposal_dir: Path | None = Field(
        default=None,
        description="Directory for generated Zettelkasten proposal JSON/org review files",
    )
    zettelkasten_markers: list[str] = Field(
        default_factory=lambda: ["#idea", "#extract", "TODO extract", "TODO: extract"],
        description="Journal markers that identify candidate atomic notes",
    )
    zettelkasten_journal_lookback_days: int = Field(
        default=7,
        ge=1,
        description="Recent daily journal days scanned by zettel propose",
    )
    zettelkasten_link_top_k: int = Field(
        default=5,
        ge=0,
        description="Maximum semantic link suggestions per proposed note",
    )
    zettelkasten_link_similarity_threshold: float = Field(
        default=0.75,
        ge=0.0,
        le=1.0,
        description="Minimum cosine similarity for Zettelkasten link suggestions",
    )

    # ── Per-agent model routing ─────────────────────────────────────
    agents_reasoning_model: str = Field(
        default="",
        description="Model tag for reasoning-class agents (standards-checker, evaluator, "
        "ref-engineer, weekly-reviewer). For Ollama: 'qwen3:32b'. "
        "For MLX: HF model ID or local path. Empty = fall back to global model.",
    )
    agents_tool_use_model: str = Field(
        default="",
        description="Model tag for tool-use-class agents (research, technical-writer, "
        "technical-reviewer). For Ollama: 'qwen3.6:35b-a3b'. "
        "For MLX: HF model ID or local path. Empty = fall back to global model.",
    )
    agents_reasoning_max_tokens: int = Field(
        default=8192,
        description="Default max_tokens for reasoning-class agents.",
    )
    agents_tool_use_max_tokens: int = Field(
        default=4096,
        description="Default max_tokens for tool-use-class agents.",
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

    # Context pipeline diagnostic (opt-in; see engineering-hub diagnostic context-pipeline)
    context_pipeline_diagnostic_enabled: bool = Field(
        default=False,
        description="When True, persist formatted context and outputs under outputs/diagnostics/context-pipeline/<run_id>/",
    )
    diagnostic_context_audit_prompt: bool = Field(
        default=False,
        description="Append CONTEXT AUDIT block to agent system prompts (diagnostic only). "
        "Env: ENGINEERING_HUB_DIAGNOSTIC_CONTEXT_AUDIT_PROMPT.",
    )
    diagnostic_debug_context_max_chars: int = Field(
        default=50_000,
        ge=4_000,
        description="Max chars of formatted context logged at DEBUG when diagnostics are enabled.",
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
    corpus_embedder_config: dict = Field(
        default_factory=dict,
        description=(
            "Embedder config passed to libraryfiles_corpus build_embedder(). "
            "Keys: provider (ollama|auto|mlx|huggingface), mode (local|api), "
            "model, hf_model, mlx_model, token. "
            "Empty dict (default) uses Ollama via OllamaEmbedder."
        ),
    )

    # Agent web search settings (local-first, used by Journaler /agent)
    agent_web_search_enabled: bool = Field(
        default=False,
        description="Enable web search context injection for delegated /agent tasks",
    )
    agent_web_search_provider: str = Field(
        default="searxng",
        description='Agent web search provider. Currently supported: "searxng".',
    )
    agent_web_search_searxng_url: str = Field(
        default="http://localhost:8080",
        description="Base URL for a local/self-hosted SearXNG instance",
    )
    agent_web_search_max_results: int = Field(
        default=5,
        ge=1,
        description="Maximum web search results injected into /agent context",
    )
    agent_web_search_max_chars: int = Field(
        default=12_000,
        ge=1_000,
        description="Maximum characters of formatted web results injected into /agent context",
    )
    agent_web_search_timeout_seconds: float = Field(
        default=10.0,
        gt=0.0,
        description="Timeout in seconds for local web search requests",
    )
    agent_web_search_anthropic_backup_enabled: bool = Field(
        default=False,
        description="Allow Claude server-side web search when local search is unavailable",
    )
    agent_web_search_anthropic_tool_version: str = Field(
        default="web_search_20250305",
        description="Anthropic server-side web search tool version for fallback mode",
    )
    agent_web_search_anthropic_max_uses: int = Field(
        default=3,
        ge=1,
        description="Maximum Anthropic server-side web searches in fallback mode",
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
    def zettelkasten_resolved_proposal_dir(self) -> Path:
        """Effective directory for Zettelkasten proposal buffers."""
        if self.zettelkasten_proposal_dir is not None:
            return self.zettelkasten_proposal_dir
        return self.output_dir / "zettelkasten"

    @property
    def journaler_briefing_output_dir(self) -> Path:
        """Path to the Journaler briefing output directory."""
        return self.journaler_state_dir / "briefings"

    @property
    def resolved_journaler_pending_tasks_file(self) -> Path:
        """Org file the Journaler writes for queued overnight tasks."""
        if self.journaler_pending_tasks_file is not None:
            return Path(self.journaler_pending_tasks_file).expanduser().resolve()
        return (self.workspace_dir / ".journaler" / "pending-tasks.org").resolve()

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

        if "rental_scout" in config:
            rental_scout = config["rental_scout"]
            if rental_scout.get("workspace_dir"):
                flat_config["rental_scout_workspace_dir"] = Path(
                    rental_scout["workspace_dir"]
                ).expanduser()
            if rental_scout.get("python_path"):
                flat_config["rental_scout_python_path"] = Path(
                    rental_scout["python_path"]
                ).expanduser()

        if "blender" in config:
            blender = config["blender"]
            if blender.get("enabled") is not None:
                flat_config["blender_enabled"] = bool(blender["enabled"])
            if blender.get("mcp_url"):
                flat_config["blender_mcp_url"] = str(blender["mcp_url"])
            if blender.get("auth_token"):
                flat_config["blender_auth_token"] = str(blender["auth_token"])
            if blender.get("connect_timeout_s") is not None:
                flat_config["blender_connect_timeout_s"] = float(
                    blender["connect_timeout_s"]
                )
            if blender.get("tools_cache_ttl_s") is not None:
                flat_config["blender_tools_cache_ttl_s"] = float(
                    blender["tools_cache_ttl_s"]
                )
            if blender.get("tool_allowlist") is not None:
                flat_config["blender_tool_allowlist"] = list(blender["tool_allowlist"])
            if blender.get("tool_denylist") is not None:
                flat_config["blender_tool_denylist"] = list(blender["tool_denylist"])

        if "horn_iterator" in config:
            horn = config["horn_iterator"]
            if horn.get("enabled") is not None:
                flat_config["horn_iterator_enabled"] = bool(horn["enabled"])
            if horn.get("output_dir"):
                flat_config["horn_iterator_output_dir"] = Path(
                    horn["output_dir"]
                ).expanduser()
            if horn.get("flare_rate_per_m") is not None:
                flat_config["horn_iterator_flare_rate_per_m"] = float(
                    horn["flare_rate_per_m"]
                )
            if horn.get("throat_area_mm2") is not None:
                flat_config["horn_iterator_throat_area_mm2"] = float(
                    horn["throat_area_mm2"]
                )
            if horn.get("slot_area_mm2") is not None:
                flat_config["horn_iterator_slot_area_mm2"] = float(
                    horn["slot_area_mm2"]
                )
            if horn.get("adapter_length_mm") is not None:
                flat_config["horn_iterator_adapter_length_mm"] = float(
                    horn["adapter_length_mm"]
                )

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

        if "pi" in config:
            pi = config["pi"]
            if isinstance(pi, dict):
                if pi.get("bin"):
                    flat_config["pi_bin"] = pi["bin"]
                if pi.get("mode"):
                    flat_config["pi_mode"] = pi["mode"]
                if pi.get("task_timeout") is not None:
                    flat_config["pi_task_timeout"] = pi["task_timeout"]
                if pi.get("max_concurrent") is not None:
                    flat_config["pi_max_concurrent"] = pi["max_concurrent"]
                if pi.get("default_tools") is not None:
                    flat_config["pi_default_tools"] = pi["default_tools"]
                if pi.get("provider"):
                    flat_config["pi_provider"] = pi["provider"]
                if pi.get("model"):
                    flat_config["pi_model"] = pi["model"]
                if pi.get("share_hub_api_key") is not None:
                    flat_config["pi_share_hub_api_key"] = pi["share_hub_api_key"]
                if pi.get("offline") is not None:
                    flat_config["pi_offline"] = pi["offline"]

        if isinstance(config.get("code_projects"), dict):
            flat_config["code_projects"] = config["code_projects"]

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
            if j.get("end_of_day_time"):
                flat_config["journaler_end_of_day_time"] = j["end_of_day_time"]
            if j.get("discussion_briefing_enabled") is not None:
                flat_config["journaler_discussion_briefing_enabled"] = j["discussion_briefing_enabled"]
            if j.get("discussion_briefing_time"):
                flat_config["journaler_discussion_briefing_time"] = j["discussion_briefing_time"]
            if j.get("personas_dir"):
                flat_config["journaler_personas_dir"] = Path(j["personas_dir"]).expanduser()
            if j.get("discussion_persona_lookback_days") is not None:
                flat_config["journaler_discussion_persona_lookback_days"] = int(
                    j["discussion_persona_lookback_days"]
                )
            if j.get("discussion_max_tokens_per_persona") is not None:
                flat_config["journaler_discussion_max_tokens_per_persona"] = int(
                    j["discussion_max_tokens_per_persona"]
                )
            if j.get("coordination_scan_enabled") is not None:
                flat_config["journaler_coordination_scan_enabled"] = j["coordination_scan_enabled"]
            if j.get("coordination_scan_interval_min") is not None:
                flat_config["journaler_coordination_scan_interval_min"] = int(
                    j["coordination_scan_interval_min"]
                )
            if j.get("proactive_topic_scout_enabled") is not None:
                flat_config["journaler_proactive_topic_scout_enabled"] = bool(
                    j["proactive_topic_scout_enabled"]
                )
            if j.get("proactive_topic_scout_max_tokens") is not None:
                flat_config["journaler_proactive_topic_scout_max_tokens"] = int(
                    j["proactive_topic_scout_max_tokens"]
                )
            if j.get("background_work_enabled") is not None:
                flat_config["journaler_background_work_enabled"] = bool(
                    j["background_work_enabled"]
                )
            if j.get("background_work_interval_min") is not None:
                flat_config["journaler_background_work_interval_min"] = int(
                    j["background_work_interval_min"]
                )
            if j.get("background_work_max_tasks_per_day") is not None:
                flat_config["journaler_background_work_max_tasks_per_day"] = int(
                    j["background_work_max_tasks_per_day"]
                )
            if j.get("background_work_auto_approve") is not None:
                flat_config["journaler_background_work_auto_approve"] = bool(
                    j["background_work_auto_approve"]
                )
            if j.get("background_work_agent_backend"):
                flat_config["journaler_background_work_agent_backend"] = str(
                    j["background_work_agent_backend"]
                )
            if j.get("background_work_chat_lookback_days") is not None:
                flat_config["journaler_background_work_chat_lookback_days"] = int(
                    j["background_work_chat_lookback_days"]
                )
            if j.get("task_integrator_enabled") is not None:
                flat_config["journaler_task_integrator_enabled"] = bool(
                    j["task_integrator_enabled"]
                )
            if j.get("task_integrator_interval_min") is not None:
                flat_config["journaler_task_integrator_interval_min"] = int(
                    j["task_integrator_interval_min"]
                )
            if j.get("task_integrator_conversation_section"):
                flat_config["journaler_task_integrator_conversation_section"] = str(
                    j["task_integrator_conversation_section"]
                )
            if j.get("task_integrator_output_section"):
                flat_config["journaler_task_integrator_output_section"] = str(
                    j["task_integrator_output_section"]
                )
            if isinstance(j.get("task_integrator_excluded_sections"), list):
                flat_config["journaler_task_integrator_excluded_sections"] = [
                    str(s) for s in j["task_integrator_excluded_sections"]
                ]
            if j.get("task_integrator_max_questions") is not None:
                flat_config["journaler_task_integrator_max_questions"] = int(
                    j["task_integrator_max_questions"]
                )
            if j.get("task_integrator_weekdays_only") is not None:
                flat_config["journaler_task_integrator_weekdays_only"] = bool(
                    j["task_integrator_weekdays_only"]
                )
            if j.get("task_integrator_max_tokens") is not None:
                flat_config["journaler_task_integrator_max_tokens"] = int(
                    j["task_integrator_max_tokens"]
                )
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
            if j.get("max_thinking_tokens") is not None:
                flat_config["journaler_max_thinking_tokens"] = j["max_thinking_tokens"]
            if j.get("thinking_max_tokens") is not None:
                flat_config["journaler_thinking_max_tokens"] = j["thinking_max_tokens"]
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
            if j.get("load_recent_max_files") is not None:
                flat_config["journaler_load_recent_max_files"] = j["load_recent_max_files"]
            if j.get("load_recent_days") is not None:
                flat_config["journaler_load_recent_days"] = j["load_recent_days"]
            if j.get("load_recent_roots") is not None:
                flat_config["journaler_load_recent_roots"] = [
                    Path(p).expanduser() for p in (j["load_recent_roots"] or [])
                ]
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
            if j.get("pending_tasks_file"):
                flat_config["journaler_pending_tasks_file"] = Path(
                    j["pending_tasks_file"]
                ).expanduser()
            if j.get("default_task_mode"):
                flat_config["journaler_default_task_mode"] = str(
                    j["default_task_mode"]
                ).strip()
            if j.get("conversation_lookback_days") is not None:
                flat_config["journaler_conversation_lookback_days"] = int(
                    j["conversation_lookback_days"]
                )
            if j.get("conversation_summary_excerpt_chars") is not None:
                flat_config["journaler_conversation_summary_excerpt_chars"] = int(
                    j["conversation_summary_excerpt_chars"]
                )
            if j.get("roam_task_lookback_days") is not None:
                flat_config["journaler_roam_task_lookback_days"] = int(
                    j["roam_task_lookback_days"]
                )
            if j.get("roam_task_max_files") is not None:
                flat_config["journaler_roam_task_max_files"] = int(
                    j["roam_task_max_files"]
                )
            if j.get("prose_completion_detection") is not None:
                flat_config["journaler_prose_completion_detection"] = bool(
                    j["prose_completion_detection"]
                )
            if isinstance(j.get("context_management"), dict):
                flat_config["journaler_context_management"] = j["context_management"]
            if isinstance(j.get("output_limit"), dict):
                flat_config["journaler_output_limit"] = j["output_limit"]
            if j.get("org_link_on_relation") is not None:
                flat_config["journaler_org_link_on_relation"] = bool(
                    j["org_link_on_relation"]
                )

        if "agents" in config:
            agents = config["agents"]
            models = agents.get("models", {})
            if models.get("reasoning"):
                flat_config["agents_reasoning_model"] = models["reasoning"]
            if models.get("tool_use"):
                flat_config["agents_tool_use_model"] = models["tool_use"]
            if agents.get("reasoning_max_tokens") is not None:
                flat_config["agents_reasoning_max_tokens"] = agents["reasoning_max_tokens"]
            if agents.get("tool_use_max_tokens") is not None:
                flat_config["agents_tool_use_max_tokens"] = agents["tool_use_max_tokens"]

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
            if corpus.get("embedder") and isinstance(corpus["embedder"], dict):
                flat_config["corpus_embedder_config"] = dict(corpus["embedder"])

        if "agent_web_search" in config:
            web = config["agent_web_search"]
            if isinstance(web, dict):
                if web.get("enabled") is not None:
                    flat_config["agent_web_search_enabled"] = web["enabled"]
                if web.get("provider"):
                    flat_config["agent_web_search_provider"] = web["provider"]
                if web.get("searxng_url"):
                    flat_config["agent_web_search_searxng_url"] = web["searxng_url"]
                if web.get("max_results") is not None:
                    flat_config["agent_web_search_max_results"] = web["max_results"]
                if web.get("max_chars") is not None:
                    flat_config["agent_web_search_max_chars"] = web["max_chars"]
                if web.get("timeout_seconds") is not None:
                    flat_config["agent_web_search_timeout_seconds"] = web[
                        "timeout_seconds"
                    ]
                if web.get("anthropic_backup_enabled") is not None:
                    flat_config["agent_web_search_anthropic_backup_enabled"] = web[
                        "anthropic_backup_enabled"
                    ]
                if web.get("anthropic_tool_version"):
                    flat_config["agent_web_search_anthropic_tool_version"] = web[
                        "anthropic_tool_version"
                    ]
                if web.get("anthropic_max_uses") is not None:
                    flat_config["agent_web_search_anthropic_max_uses"] = web[
                        "anthropic_max_uses"
                    ]

        if "diagnostics" in config:
            diag = config["diagnostics"]
            if isinstance(diag, dict):
                cp = diag.get("context_pipeline")
                if isinstance(cp, dict):
                    if cp.get("enabled") is not None:
                        flat_config["context_pipeline_diagnostic_enabled"] = cp["enabled"]
                    if cp.get("context_audit_prompt") is not None:
                        flat_config["diagnostic_context_audit_prompt"] = cp[
                            "context_audit_prompt"
                        ]
                    if cp.get("debug_context_max_chars") is not None:
                        flat_config["diagnostic_debug_context_max_chars"] = cp[
                            "debug_context_max_chars"
                        ]

        if "zettelkasten" in config:
            zettel = config["zettelkasten"]
            if zettel.get("enabled") is not None:
                flat_config["zettelkasten_enabled"] = zettel["enabled"]
            if zettel.get("proposal_dir"):
                flat_config["zettelkasten_proposal_dir"] = Path(
                    zettel["proposal_dir"]
                ).expanduser()
            if zettel.get("markers") is not None:
                flat_config["zettelkasten_markers"] = list(zettel["markers"] or [])
            if zettel.get("journal_lookback_days") is not None:
                flat_config["zettelkasten_journal_lookback_days"] = zettel[
                    "journal_lookback_days"
                ]
            if zettel.get("link_top_k") is not None:
                flat_config["zettelkasten_link_top_k"] = zettel["link_top_k"]
            if zettel.get("link_similarity_threshold") is not None:
                flat_config["zettelkasten_link_similarity_threshold"] = zettel[
                    "link_similarity_threshold"
                ]

        def _is_empty(v: object) -> bool:
            if v is None or v == "":
                return True
            if isinstance(v, SecretStr) and not v.get_secret_value():
                return True
            return False

        # Remove None values and empty strings so env vars are not shadowed by blank YAML fields
        flat_config = {k: v for k, v in flat_config.items() if not _is_empty(v)}

        return cls(**flat_config)
