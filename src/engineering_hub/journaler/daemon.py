"""Journaler daemon: main loop, scheduler, and signal handling.

The daemon keeps an MLX model loaded and warm, scans the org-roam workspace
on a configurable interval, generates morning briefings on schedule, and
optionally runs an HTTP chat server for ad-hoc questions.
"""

from __future__ import annotations

import logging
import signal
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import schedule

from engineering_hub.config.settings import Settings
from engineering_hub.journaler.context import JournalContext
from engineering_hub.journaler.models import ContextSnapshot
from engineering_hub.journaler.context_manager import PressureConfig
from engineering_hub.journaler.engine import (
    ConversationalMLXBackend,
    ConversationEngine,
    LoadFileBudgetConfig,
)
from engineering_hub.journaler.model_profiles import (
    JournalerChatModelContext,
    spec_from_journaler_config,
)
from engineering_hub.journaler.prompts import (
    build_skills_block,
    build_workspace_layout,
    format_briefing_markdown,
    format_briefing_prompt,
    format_system_prompt,
    load_briefing_prompt,
    load_system_prompt,
)
from engineering_hub.journaler.briefing_tasks import (
    BackgroundWorkQueue,
    BriefingTaskExtractor,
    _read_recent_chat_context,
    write_work_status,
)
from engineering_hub.journaler.task_integrator import (
    TaskIntegrator,
    build_task_integrator,
)
from engineering_hub.search import SearchProvider

if TYPE_CHECKING:
    from engineering_hub.journaler.chat_server import ChatServer
    from engineering_hub.journaler.delegator import AgentDelegator
    from engineering_hub.journaler.slack import SlackPoster
    from engineering_hub.memory.service import MemoryService

TOPIC_SCOUT_PROMPT = """\
You are the Journaler ambient assistant. The workspace scan just detected \
meaningful journal or queue changes. Based on the change summary and recent \
journal context below, produce a short markdown section with:

1. **Conversation starters** — 3–5 bullet questions or topics worth discussing \
today (reference specific headings or tasks when possible).
2. **Suggested agent commands** — 1–2 concrete `/agent ...` lines the user could \
run now (research, technical-writer, standards-checker, weekly-reviewer, etc.).

Keep it under 200 words. No preamble — start with `### Conversation starters`.

Change summary: {change_summary}

Recent journal context:
{journal_excerpt}
"""

logger = logging.getLogger(__name__)


@dataclass
class JournalerConfig:
    """All settings needed to run the Journaler daemon."""

    model_path: str
    org_roam_dir: Path
    journal_dir: Path
    workspace_dir: Path
    state_dir: Path

    scan_interval_min: int = 10
    briefing_enabled: bool = True
    briefing_time: str = "09:00"
    briefing_output_dir: Path | None = None

    # Discussion briefing (multi-persona roundtable)
    discussion_briefing_enabled: bool = False
    discussion_briefing_time: str = "08:45"
    personas_dir: Path | None = None
    discussion_persona_lookback_days: int = 7
    discussion_max_tokens_per_persona: int = 1024

    # Coordination analyst scan
    coordination_scan_enabled: bool = False
    coordination_scan_interval_min: int = 0

    chat_enabled: bool = True
    chat_host: str = "127.0.0.1"
    chat_port: int = 18790

    slack_enabled: bool = False
    slack_webhook_url: str = ""

    max_context_tokens: int = 4000
    max_briefing_tokens: int = 8000
    max_conversation_history: int = 40
    max_tokens: int = 4096

    # Context management
    model_context_window: int = 32768
    context_management: PressureConfig | None = None

    # MLX sampling parameters
    temp: float = 0.7
    top_p: float = 0.9
    min_p: float = 0.05
    repetition_penalty: float = 1.1

    # MLX load / Qwen3 chat template (None = omit enable_thinking kwarg)
    enable_thinking: bool | None = None
    mlx_backend: str = "auto"

    watch_dirs: list[Path] | None = None
    scan_org_roam_tree: bool = True
    journal_lookback_days: int = 30
    journal_max_files: int = 30

    # Journaler-owned overnight queue file (Orchestrator scans via OrgTaskParser extra_files).
    pending_tasks_file: Path | None = None

    # Periodic deep scan: force-reparses the full journal window regardless of mtime.
    # Set to 0 to disable.
    deep_scan_interval_min: int = 60

    memory_service: MemoryService | None = None

    # Optional PDF reference corpus (libraryfiles-corpus); used for per-turn RAG in chat.
    corpus_service: Any | None = None

    # Local-first web search for delegated /agent tasks.
    web_search_provider: SearchProvider | None = None
    web_search_enabled: bool = False
    web_search_max_results: int = 5
    web_search_max_chars: int = 12_000
    web_search_anthropic_backup_enabled: bool = False
    web_search_anthropic_tool_version: str = "web_search_20250305"
    web_search_anthropic_max_uses: int = 3

    # Slash /load: context-aware size limits (see LoadFileBudgetConfig in engine.py)
    load_max_context_fraction: float = 0.65
    load_max_chars_absolute: int = 200_000
    load_min_chars: int = 1024
    load_slack_tokens: int = 256

    # Agent delegation settings (API key is not stored here — use Settings / ENGINEERING_HUB_* env).
    # agent_backend: Which LLM to use for delegated agent tasks.
    #   "mlx"    — local model (default; reuses Journaler's loaded model, no extra RAM)
    #   "claude" — always Claude API (error if no key)
    #   "auto"   — Claude if api key present, else local MLX
    agent_backend: str = "mlx"
    # skills_dir: Path to the skills/ YAML directory.
    #   Defaults to the skills/ directory at the repo root.
    skills_dir: Path | None = None

    # Daily summary context loop settings.
    # conversation_lookback_days: how many daily_summaries/*.md files to include in
    #   the proactive snapshot (independent of journal_lookback_days).
    conversation_lookback_days: int = 7
    conversation_summary_excerpt_chars: int = 800
    # org_link_on_relation: when a per-turn semantic match is found, append a
    #   cross-reference link to the current day's journal.
    org_link_on_relation: bool = True

    # Proactive topic scout: one-shot MLX hint when a scan tick detects significant
    # journal / pending-tasks / output changes.
    proactive_topic_scout_enabled: bool = True
    proactive_topic_scout_max_tokens: int = 512

    # Background agent work loop: extracts tasks from briefings, delegates via
    # AgentDelegator, and tracks progress in a daily work status file.
    background_work_enabled: bool = False
    background_work_interval_min: int = 60
    background_work_max_tasks_per_day: int = 6
    background_work_auto_approve: bool = False
    background_work_agent_backend: str = "mlx"
    background_work_chat_lookback_days: int = 3

    # Task-Integrator: inline call-and-response loop over the daily journal.
    task_integrator_enabled: bool = False
    task_integrator_interval_min: int = 15
    task_integrator_conversation_section: str = "Agent Conversation"
    task_integrator_output_section: str = "Overnight Agent Tasks"
    task_integrator_excluded_sections: list[str] | None = None
    task_integrator_max_questions: int = 3
    task_integrator_weekdays_only: bool = True
    task_integrator_max_tokens: int = 1024

    def get_pressure_config(self) -> PressureConfig:
        """Return the PressureConfig, defaulting from scalar fields if not set."""
        if self.context_management is not None:
            return self.context_management
        return PressureConfig(
            model_context_window=self.model_context_window,
            max_history_turns=self.max_conversation_history,
        )

    def get_load_file_budget(self) -> LoadFileBudgetConfig:
        """Build slash-command /load caps from this config."""
        return LoadFileBudgetConfig(
            max_context_fraction=self.load_max_context_fraction,
            max_chars_absolute=self.load_max_chars_absolute,
            min_chars=self.load_min_chars,
            slack_tokens=self.load_slack_tokens,
        )


def pressure_config_from_settings(
    settings: Settings,
    *,
    model_context_window: int,
    max_history_turns: int,
) -> PressureConfig:
    """Build Journaler pressure config from YAML-backed settings."""
    raw = dict(settings.journaler_context_management or {})
    raw.setdefault("model_context_window", model_context_window)
    raw.setdefault("max_history_turns", max_history_turns)
    raw.setdefault("end_of_day_time", settings.journaler_end_of_day_time)
    allowed = set(PressureConfig.__dataclass_fields__)
    return PressureConfig(**{k: v for k, v in raw.items() if k in allowed})


def run_daemon(config: JournalerConfig, settings: Settings | None = None) -> None:
    """Main daemon entry point. Blocks until SIGINT/SIGTERM.

    *settings* is used for ``/model`` profile resolution over HTTP; if omitted,
    profile switching falls back to path-only overrides.
    """
    start_time = datetime.now()

    # Ensure state directory exists
    config.state_dir.mkdir(parents=True, exist_ok=True)
    if config.briefing_output_dir:
        config.briefing_output_dir.mkdir(parents=True, exist_ok=True)

    # Load prompts (allow user overrides in state dir)
    system_template = load_system_prompt(config.state_dir)
    briefing_template = load_briefing_prompt(config.state_dir)

    # Init context scanner
    pending_ctx = config.pending_tasks_file
    if pending_ctx is None:
        pending_ctx = config.workspace_dir / ".journaler" / "pending-tasks.org"
    context = JournalContext(
        org_roam_dir=config.org_roam_dir,
        journal_dir=config.journal_dir,
        workspace_dir=config.workspace_dir,
        memory_service=config.memory_service,
        state_dir=config.state_dir,
        watch_dirs=config.watch_dirs,
        scan_org_roam_tree=config.scan_org_roam_tree,
        journal_lookback_days=config.journal_lookback_days,
        journal_max_files=config.journal_max_files,
        pending_tasks_file=pending_ctx,
        conversation_lookback_days=config.conversation_lookback_days,
        conversation_summary_excerpt_chars=config.conversation_summary_excerpt_chars,
    )

    # Init MLX backend (model loads here — takes ~10-30s for 32B)
    logger.info("Initializing Journaler model...")
    backend = ConversationalMLXBackend(
        model_path=config.model_path,
        temp=config.temp,
        top_p=config.top_p,
        min_p=config.min_p,
        repetition_penalty=config.repetition_penalty,
        backend=config.mlx_backend,
        enable_thinking=config.enable_thinking,
    )

    # Init conversation engine with context management
    pressure_cfg = config.get_pressure_config()
    system_prompt = format_system_prompt(system_template, "(initial scan pending)")
    engine = ConversationEngine(
        backend=backend,
        system_prompt=system_prompt,
        log_dir=config.state_dir,
        max_history=config.max_conversation_history,
        max_tokens=config.max_tokens,
        pressure_config=pressure_cfg,
        model_context_window=config.model_context_window,
        corpus_service=config.corpus_service,
        load_file_budget=config.get_load_file_budget(),
        memory_service=config.memory_service,
        journal_dir=config.journal_dir,
        relation_threshold=pressure_cfg.conversation_relation_threshold,
        org_link_on_relation=config.org_link_on_relation,
        web_search_provider=config.web_search_provider,
        web_search_enabled=config.web_search_enabled,
        web_search_max_results=config.web_search_max_results,
        web_search_max_chars=config.web_search_max_chars,
        web_search_anthropic_backup_enabled=config.web_search_anthropic_backup_enabled,
        web_search_anthropic_tool_version=config.web_search_anthropic_tool_version,
        web_search_anthropic_max_uses=config.web_search_anthropic_max_uses,
    )

    # Do initial scan
    logger.info("Running initial scan...")
    context.scan()
    workspace_map = build_workspace_layout(
        config.org_roam_dir, config.workspace_dir, config.journal_dir
    )
    engine.update_context(context.get_current_context())
    # Re-set system prompt with actual context and workspace layout
    engine._system_prompt = format_system_prompt(
        system_template,
        context.get_current_context(),
        workspace_map=workspace_map,
    )

    # Init agent delegator (bridges Journaler chat → AgentWorker)
    from engineering_hub.journaler.delegator import build_delegator

    delegation_key = (
        settings.journaler_delegation_api_key() if settings is not None else ""
    )
    delegator = build_delegator(
        backend,
        anthropic_api_key=delegation_key,
        skills_dir=config.skills_dir,
        default_backend=config.agent_backend,
        output_dir=config.workspace_dir / "outputs",
        proposal_dir=settings.zettelkasten_resolved_proposal_dir if settings is not None else None,
        zettel_state_path=(
            settings.journaler_state_dir / "zettelkasten_state.json"
            if settings is not None else None
        ),
        org_journal_dir=settings.org_journal_dir if settings is not None else None,
        corpus_service=config.corpus_service,
        memory_service=config.memory_service,
    )
    skills_suffix = ""
    if delegator is not None:
        skills_suffix = build_skills_block(delegator)
        if skills_suffix:
            engine._system_prompt = engine._system_prompt.rstrip() + "\n\n" + skills_suffix

    # Init background work queue and task extractor
    work_queue: BackgroundWorkQueue | None = None
    task_extractor: BriefingTaskExtractor | None = None
    if config.background_work_enabled:
        work_queue = BackgroundWorkQueue(config.state_dir / "background_queue")
        task_extractor = BriefingTaskExtractor(
            engine=engine,
            state_dir=config.state_dir,
            delegator=delegator,
            chat_lookback_days=config.background_work_chat_lookback_days,
        )
        logger.info("Background work loop enabled (interval: %dmin, max tasks/day: %d)",
                     config.background_work_interval_min,
                     config.background_work_max_tasks_per_day)

    # Init Task-Integrator (inline call-and-response loop over daily journal)
    task_integrator: TaskIntegrator | None = None
    if config.task_integrator_enabled:
        task_integrator = build_task_integrator(config, engine, delegator)
        logger.info(
            "Task-Integrator enabled (interval: %dmin, conversation section: '* %s')",
            config.task_integrator_interval_min,
            config.task_integrator_conversation_section,
        )

    # Init optional components
    slack: SlackPoster | None = None
    if config.slack_enabled and config.slack_webhook_url:
        from engineering_hub.journaler.slack import SlackPoster

        slack = SlackPoster(webhook_url=config.slack_webhook_url)
        logger.info("Slack integration enabled")

    chat_server: ChatServer | None = None
    runtime_model: JournalerChatModelContext | None = None
    if settings is not None:
        runtime_model = JournalerChatModelContext(
            settings, spec_from_journaler_config(config)
        )

    if config.chat_enabled:
        from engineering_hub.journaler.chat_server import ChatServer

        pending_pf = config.pending_tasks_file
        if pending_pf is None:
            pending_pf = config.workspace_dir / ".journaler" / "pending-tasks.org"
        chat_server = ChatServer(
            engine=engine,
            context=context,
            host=config.chat_host,
            port=config.chat_port,
            start_time=start_time,
            delegator=delegator,
            model_context=runtime_model,
            pending_tasks_file=pending_pf,
            task_integrator=task_integrator,
        )

    # Schedule recurring tasks
    schedule.every(config.scan_interval_min).minutes.do(
        _tick,
        config=config,
        context=context,
        engine=engine,
        system_template=system_template,
        workspace_map=workspace_map,
        skills_suffix=skills_suffix,
    )

    if config.deep_scan_interval_min > 0:
        schedule.every(config.deep_scan_interval_min).minutes.do(
            _deep_scan_tick,
            context=context,
            engine=engine,
            system_template=system_template,
            workspace_map=workspace_map,
            skills_suffix=skills_suffix,
        )
        logger.info(
            f"Deep scan (full journal window) scheduled every "
            f"{config.deep_scan_interval_min} min"
        )

    if config.briefing_enabled:
        schedule.every().day.at(config.briefing_time).do(
            _morning_briefing,
            config=config,
            context=context,
            engine=engine,
            briefing_template=briefing_template,
            slack=slack,
            task_extractor=task_extractor,
            work_queue=work_queue,
        )
        logger.info(f"Morning briefing scheduled at {config.briefing_time}")

    if config.discussion_briefing_enabled:
        schedule.every().day.at(config.discussion_briefing_time).do(
            _discussion_briefing,
            config=config,
            context=context,
            engine=engine,
            task_extractor=task_extractor,
            work_queue=work_queue,
        )
        logger.info(
            "Discussion briefing scheduled at %s", config.discussion_briefing_time
        )

    if config.coordination_scan_enabled and config.coordination_scan_interval_min > 0:
        schedule.every(config.coordination_scan_interval_min).minutes.do(
            _coordination_scan,
            config=config,
            context=context,
            delegator=delegator,
        )
        logger.info(
            "Coordination scan scheduled every %d min",
            config.coordination_scan_interval_min,
        )

    if config.background_work_enabled and work_queue is not None:
        schedule.every(config.background_work_interval_min).minutes.do(
            _background_work_tick,
            config=config,
            context=context,
            engine=engine,
            delegator=delegator,
            work_queue=work_queue,
        )
        logger.info(
            "Background work loop scheduled every %d min",
            config.background_work_interval_min,
        )

    if config.task_integrator_enabled and task_integrator is not None:
        schedule.every(config.task_integrator_interval_min).minutes.do(
            _task_integrator_tick,
            integrator=task_integrator,
        )
        logger.info(
            "Task-Integrator loop scheduled every %d min",
            config.task_integrator_interval_min,
        )

    # Schedule end-of-day context clear
    eod_time = pressure_cfg.end_of_day_time
    schedule.every().day.at(eod_time).do(
        _end_of_day_clear,
        engine=engine,
        config=config,
    )
    logger.info(f"End-of-day context clear scheduled at {eod_time}")

    # Start chat server in background thread
    if chat_server:
        chat_server.start_background()
        logger.info(f"Chat server listening on {config.chat_host}:{config.chat_port}")

    # Register signal handlers
    shutdown = False

    def _handle_signal(signum: int, frame: object) -> None:
        nonlocal shutdown
        sig_name = signal.Signals(signum).name
        logger.info(f"Received {sig_name}, shutting down...")
        shutdown = True

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    logger.info(
        f"Journaler daemon running. Model: {config.model_path}, "
        f"scan every {config.scan_interval_min}min"
    )

    # Main loop
    try:
        while not shutdown:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        if chat_server:
            chat_server.stop()
        logger.info("Journaler daemon stopped.")


def generate_briefing_now(
    config: JournalerConfig,
    context: JournalContext | None = None,
    engine: ConversationEngine | None = None,
) -> str:
    """Generate a briefing on demand (for CLI `journaler briefing` command).

    If context/engine are not provided, creates temporary instances.
    Returns the briefing text.
    """
    briefing_template = load_briefing_prompt(config.state_dir)

    if context is None:
        ptf = config.pending_tasks_file
        if ptf is None:
            ptf = config.workspace_dir / ".journaler" / "pending-tasks.org"
        context = JournalContext(
            org_roam_dir=config.org_roam_dir,
            journal_dir=config.journal_dir,
            workspace_dir=config.workspace_dir,
            memory_service=config.memory_service,
            state_dir=config.state_dir,
            watch_dirs=config.watch_dirs,
            scan_org_roam_tree=config.scan_org_roam_tree,
            journal_lookback_days=config.journal_lookback_days,
            journal_max_files=config.journal_max_files,
            pending_tasks_file=ptf,
            conversation_lookback_days=config.conversation_lookback_days,
            conversation_summary_excerpt_chars=config.conversation_summary_excerpt_chars,
        )
        context.scan()

    briefing_context = context.get_briefing_context()
    today_str = date.today().isoformat()
    prompt = format_briefing_prompt(briefing_template, today_str, briefing_context)

    if engine is None:
        backend = ConversationalMLXBackend(
            model_path=config.model_path,
            temp=config.temp,
            top_p=config.top_p,
            min_p=config.min_p,
            repetition_penalty=config.repetition_penalty,
            backend=config.mlx_backend,
            enable_thinking=config.enable_thinking,
        )
        engine = ConversationEngine(
            backend=backend,
            system_prompt="You are the Journaler.",
            log_dir=config.state_dir,
            max_tokens=config.max_tokens,
            pressure_config=config.get_pressure_config(),
            model_context_window=config.model_context_window,
            corpus_service=config.corpus_service,
            load_file_budget=config.get_load_file_budget(),
        )

    briefing = format_briefing_markdown(
        engine.generate_briefing(
            briefing_context, prompt, max_tokens=config.max_briefing_tokens
        )
    )

    # Save to file
    output_dir = config.briefing_output_dir or (config.state_dir / "briefings")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{today_str}.md"
    output_path.write_text(
        f"# Morning Briefing — {today_str}\n\n{briefing}",
        encoding="utf-8",
    )
    logger.info(f"Briefing written to {output_path}")

    return briefing


def generate_discussion_now(
    config: JournalerConfig,
    context: JournalContext | None = None,
    engine: ConversationEngine | None = None,
) -> str:
    """Generate a Discussion Briefing on demand (for CLI ``journaler briefing --discussion``).

    If context/engine are not provided, creates temporary instances.
    Returns the discussion markdown text.
    """
    from engineering_hub.journaler.discussion_briefing import (
        DiscussionBriefingGenerator,
    )
    from engineering_hub.journaler.persona_history import PersonaHistoryStore

    if context is None:
        ptf = config.pending_tasks_file
        if ptf is None:
            ptf = config.workspace_dir / ".journaler" / "pending-tasks.org"
        context = JournalContext(
            org_roam_dir=config.org_roam_dir,
            journal_dir=config.journal_dir,
            workspace_dir=config.workspace_dir,
            memory_service=config.memory_service,
            state_dir=config.state_dir,
            watch_dirs=config.watch_dirs,
            scan_org_roam_tree=config.scan_org_roam_tree,
            journal_lookback_days=config.journal_lookback_days,
            journal_max_files=config.journal_max_files,
            pending_tasks_file=ptf,
            conversation_lookback_days=config.conversation_lookback_days,
            conversation_summary_excerpt_chars=config.conversation_summary_excerpt_chars,
        )
        context.scan()

    if engine is None:
        backend = ConversationalMLXBackend(
            model_path=config.model_path,
            temp=config.temp,
            top_p=config.top_p,
            min_p=config.min_p,
            repetition_penalty=config.repetition_penalty,
            backend=config.mlx_backend,
            enable_thinking=config.enable_thinking,
        )
        engine = ConversationEngine(
            backend=backend,
            system_prompt="You are the Journaler.",
            log_dir=config.state_dir,
            max_tokens=config.max_tokens,
            pressure_config=config.get_pressure_config(),
            model_context_window=config.model_context_window,
            corpus_service=config.corpus_service,
            load_file_budget=config.get_load_file_budget(),
        )

    shared_context = context.get_briefing_context()
    today_str = date.today().isoformat()

    personas_dir = config.personas_dir
    if personas_dir is None:
        here = Path(__file__).parent
        for parent in [here, here.parent, here.parent.parent, here.parent.parent.parent]:
            candidate = parent / "personas"
            if candidate.is_dir():
                personas_dir = candidate
                break

    if personas_dir is None or not personas_dir.is_dir():
        raise FileNotFoundError(
            "Personas directory not found. Set journaler.personas_dir in config "
            "or create a 'personas/' directory at the repo root."
        )

    history_store = PersonaHistoryStore(config.state_dir / "personas")
    generator = DiscussionBriefingGenerator.from_personas_dir(
        personas_dir=personas_dir,
        history_store=history_store,
        engine=engine,
        max_tokens_per_persona=config.discussion_max_tokens_per_persona,
        persona_lookback_days=config.discussion_persona_lookback_days,
    )

    discussion = generator.generate(
        shared_context,
        date_str=today_str,
        topic="on-demand discussion briefing",
    )

    output_dir = config.briefing_output_dir or (config.state_dir / "briefings")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"discussion-{today_str}.md"
    output_path.write_text(discussion, encoding="utf-8")
    logger.info("Discussion briefing written to %s", output_path)

    return discussion


def generate_summary_now(config: JournalerConfig) -> Path:
    """Generate today's daily summary on demand (for CLI ``journaler summarize`` command).

    Reads today's turns from ``conversation.jsonl``, populates a temporary engine,
    runs ``_end_of_day_clear``, and returns the path to the written summary file.
    Raises ``ValueError`` if there are no turns recorded today.
    """
    import json

    from engineering_hub.journaler.context_manager import ConversationTurn

    log_file = config.state_dir / "conversation.jsonl"
    today_prefix = date.today().isoformat()  # "YYYY-MM-DD"

    today_turns: list[ConversationTurn] = []
    if log_file.exists():
        for line in log_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = entry.get("timestamp", "")
            if ts.startswith(today_prefix) and entry.get("role") not in ("system", ""):
                today_turns.append(
                    ConversationTurn(
                        role=entry["role"],
                        content=entry.get("content", ""),
                        timestamp=ts,
                        tokens=0,
                    )
                )

    backend = ConversationalMLXBackend(
        model_path=config.model_path,
        temp=config.temp,
        top_p=config.top_p,
        min_p=config.min_p,
        repetition_penalty=config.repetition_penalty,
        backend=config.mlx_backend,
        enable_thinking=config.enable_thinking,
    )
    engine = ConversationEngine(
        backend=backend,
        system_prompt="You are the Journaler.",
        log_dir=config.state_dir,
        max_tokens=config.max_tokens,
        pressure_config=config.get_pressure_config(),
        model_context_window=config.model_context_window,
        corpus_service=config.corpus_service,
        load_file_budget=config.get_load_file_budget(),
        memory_service=config.memory_service,
    )

    if not today_turns:
        raise ValueError(
            f"No conversation turns recorded for {today_prefix}. "
            "Start a chat session first, or check that the state directory is correct."
        )

    for turn in today_turns:
        engine.history.turns.append(turn)

    _end_of_day_clear(engine, config)

    summary_path = config.state_dir / "daily_summaries" / f"{today_prefix}.md"
    return summary_path


# ---------------------------------------------------------------------------
# Scheduled task callbacks
# ---------------------------------------------------------------------------


def _topic_hints_path(state_dir: Path, day: date | None = None) -> Path:
    day_str = (day or date.today()).isoformat()
    return state_dir / "topic_hints" / f"{day_str}.md"


def _briefing_excerpt_for_scout(context: JournalContext, *, max_chars: int = 4000) -> str:
    """Trim briefing context to recent journal days for the topic scout prompt."""
    full = context.get_briefing_context()
    if len(full) <= max_chars:
        return full
    return full[:max_chars] + "\n\n...(truncated)..."


def _topic_scout_tick(
    config: JournalerConfig,
    context: JournalContext,
    engine: ConversationEngine,
    snapshot: ContextSnapshot,
) -> str | None:
    """One-shot MLX hint after significant journal changes."""
    try:
        hint = engine._raw_complete(
            TOPIC_SCOUT_PROMPT.format(
                change_summary=snapshot.change_summary,
                journal_excerpt=_briefing_excerpt_for_scout(context),
            ),
            max_tokens=config.proactive_topic_scout_max_tokens,
        )
    except Exception as exc:
        logger.warning("Topic scout failed (non-fatal): %s", exc)
        return None

    hint = hint.strip()
    if not hint:
        return None

    hints_dir = config.state_dir / "topic_hints"
    hints_dir.mkdir(parents=True, exist_ok=True)
    path = _topic_hints_path(config.state_dir)
    stamped = (
        f"# Topic hints — {date.today().isoformat()}\n\n"
        f"_Generated {datetime.now().isoformat(timespec='seconds')} "
        f"after: {snapshot.change_summary}_\n\n"
        f"{hint}\n"
    )
    path.write_text(stamped, encoding="utf-8")
    logger.info("Topic scout written: %s", path)
    return hint


def _tick(
    config: JournalerConfig,
    context: JournalContext,
    engine: ConversationEngine,
    system_template: str,
    workspace_map: str = "",
    skills_suffix: str = "",
) -> None:
    """10-minute scan cycle."""
    snapshot = context.scan()
    current_context = context.get_current_context()
    engine.update_context(current_context)
    prompt = format_system_prompt(
        system_template, current_context, workspace_map=workspace_map
    )
    if skills_suffix:
        prompt = prompt.rstrip() + "\n\n" + skills_suffix
    engine._system_prompt = prompt

    if snapshot.has_significant_changes:
        logger.info(f"Significant changes detected: {snapshot.change_summary}")
        if config.proactive_topic_scout_enabled:
            _topic_scout_tick(config, context, engine, snapshot)
            refreshed = context.get_current_context()
            engine.update_context(refreshed)
            prompt = format_system_prompt(
                system_template, refreshed, workspace_map=workspace_map
            )
            if skills_suffix:
                prompt = prompt.rstrip() + "\n\n" + skills_suffix
            engine._system_prompt = prompt


def _deep_scan_tick(
    context: JournalContext,
    engine: ConversationEngine,
    system_template: str,
    workspace_map: str = "",
    skills_suffix: str = "",
) -> None:
    """Hourly deep-scan cycle.

    Forces a full re-parse of the journal lookback window (ignoring mtimes) so
    that recurring-topic and active-roam-node digests stay fresh even when no
    files have been edited since the last scan.
    """
    snapshot = context.full_window_scan()
    current_context = context.get_current_context()
    engine.update_context(current_context)
    prompt = format_system_prompt(
        system_template, current_context, workspace_map=workspace_map
    )
    if skills_suffix:
        prompt = prompt.rstrip() + "\n\n" + skills_suffix
    engine._system_prompt = prompt

    logger.info(
        f"Deep scan refreshed context: "
        f"{len(snapshot.recurring_topics)} recurring topics, "
        f"{len(snapshot.active_roam_nodes)} active roam nodes, "
        f"{len(snapshot.stale_tasks)} stale tasks"
    )


def _task_integrator_tick(integrator: TaskIntegrator) -> None:
    """Run one Task-Integrator cycle (interview / resolution / approval)."""
    try:
        result = integrator.run_cycle()
    except Exception as exc:
        logger.warning("Task-Integrator cycle failed (non-fatal): %s", exc)
        return
    if result.questions_asked or result.proposals_written or result.tasks_queued:
        logger.info(result.summary())
    else:
        logger.debug(result.summary())


def _extract_and_queue_tasks(
    extractor: BriefingTaskExtractor,
    queue: BackgroundWorkQueue,
    briefing_text: str,
    source: str,
    config: JournalerConfig,
) -> None:
    """Extract tasks from a briefing and add them to today's work queue."""
    try:
        tasks = extractor.extract(briefing_text, source)
    except Exception as exc:
        logger.warning("Task extraction from %s failed: %s", source, exc)
        return
    if not tasks:
        logger.info("No background tasks extracted from %s", source)
        return
    added = queue.add_tasks(tasks)
    logger.info(
        "Background work: extracted %d tasks from %s, %d new (queue: %s)",
        len(tasks), source, added,
        ", ".join(f"{t.suggested_agent}: {t.description[:60]}" for t in tasks[:3]),
    )
    write_work_status(config.state_dir, queue)


def _morning_briefing(
    config: JournalerConfig,
    context: JournalContext,
    engine: ConversationEngine,
    briefing_template: str,
    slack: SlackPoster | None,
    task_extractor: BriefingTaskExtractor | None = None,
    work_queue: BackgroundWorkQueue | None = None,
) -> None:
    """Generate and deliver the morning briefing."""
    context.scan()
    briefing_context = context.get_briefing_context()
    today_str = date.today().isoformat()
    prompt = format_briefing_prompt(briefing_template, today_str, briefing_context)

    briefing = format_briefing_markdown(
        engine.generate_briefing(
            briefing_context, prompt, max_tokens=config.max_briefing_tokens
        )
    )

    output_dir = config.briefing_output_dir or (config.state_dir / "briefings")
    output_dir.mkdir(parents=True, exist_ok=True)
    briefing_path = output_dir / f"{today_str}.md"
    briefing_path.write_text(
        f"# Morning Briefing — {today_str}\n\n{briefing}",
        encoding="utf-8",
    )
    logger.info(f"Morning briefing generated: {briefing_path}")

    if slack:
        slack.post_briefing(briefing)

    if task_extractor is not None and work_queue is not None:
        _extract_and_queue_tasks(
            task_extractor, work_queue, briefing, "morning_briefing", config
        )


def _discussion_briefing(
    config: JournalerConfig,
    context: JournalContext,
    engine: ConversationEngine,
    task_extractor: BriefingTaskExtractor | None = None,
    work_queue: BackgroundWorkQueue | None = None,
) -> None:
    """Generate the Topics Discussion Briefing (multi-persona roundtable)."""
    from engineering_hub.journaler.discussion_briefing import (
        DiscussionBriefingGenerator,
    )
    from engineering_hub.journaler.persona_history import PersonaHistoryStore

    context.scan()
    shared_context = context.get_briefing_context()
    today_str = date.today().isoformat()

    personas_dir = config.personas_dir
    if personas_dir is None:
        here = Path(__file__).parent
        for parent in [here, here.parent, here.parent.parent, here.parent.parent.parent]:
            candidate = parent / "personas"
            if candidate.is_dir():
                personas_dir = candidate
                break

    if personas_dir is None or not personas_dir.is_dir():
        logger.warning(
            "Discussion briefing: personas directory not found — skipping. "
            "Set journaler.personas_dir in config."
        )
        return

    history_store = PersonaHistoryStore(config.state_dir / "personas")
    generator = DiscussionBriefingGenerator.from_personas_dir(
        personas_dir=personas_dir,
        history_store=history_store,
        engine=engine,
        max_tokens_per_persona=config.discussion_max_tokens_per_persona,
        persona_lookback_days=config.discussion_persona_lookback_days,
    )

    discussion = generator.generate(
        shared_context,
        date_str=today_str,
        topic="morning discussion briefing",
    )

    output_dir = config.briefing_output_dir or (config.state_dir / "briefings")
    output_dir.mkdir(parents=True, exist_ok=True)
    discussion_path = output_dir / f"discussion-{today_str}.md"
    discussion_path.write_text(discussion, encoding="utf-8")
    logger.info("Discussion briefing generated: %s", discussion_path)

    if task_extractor is not None and work_queue is not None:
        _extract_and_queue_tasks(
            task_extractor, work_queue, discussion, "discussion_briefing", config
        )


def _coordination_scan(
    config: JournalerConfig,
    context: JournalContext,
    delegator: AgentDelegator | None,
) -> None:
    """Run the Coordination Analyst agent against recent journal context.

    Writes output to ``{state_dir}/outputs/coordination/YYYY-MM-DD.md`` and
    appends the result to the coordination-liaison persona history store so it
    surfaces in the next discussion briefing.
    """
    if delegator is None:
        logger.warning(
            "Coordination scan skipped: AgentDelegator not available."
        )
        return

    context.scan()
    today_str = date.today().isoformat()
    journal_context = context.get_briefing_context()

    from engineering_hub.journaler.persona_history import PersonaHistoryStore

    try:
        output = delegator.delegate(
            "coordination-analyst",
            description=(
                "Scan the provided journal context for client coordination signals, "
                "scope drift indicators, and implied engineering tasks. "
                f"Context date: {today_str}."
            ),
            backend="mlx",
            journaler_context=journal_context,
        )
    except Exception as exc:
        logger.warning("Coordination scan failed: %s", exc)
        return

    if not output or output.strip().startswith("Agent task failed"):
        logger.warning("Coordination scan returned no output: %s", output[:200])
        return

    output_dir = config.state_dir / "outputs" / "coordination"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{today_str}.md"
    output_path.write_text(output, encoding="utf-8")
    logger.info("Coordination scan written: %s", output_path)

    history_store = PersonaHistoryStore(config.state_dir / "personas")
    history_store.append(
        "coordination-liaison",
        today_str,
        "coordination scan",
        output,
        source="coordination_scan",
    )


def _background_work_tick(
    config: JournalerConfig,
    context: JournalContext,
    engine: ConversationEngine,
    delegator: AgentDelegator | None,
    work_queue: BackgroundWorkQueue,
) -> None:
    """Periodic background work loop: pick a task, delegate, track output."""
    if delegator is None:
        return

    today_str = date.today().isoformat()

    if work_queue.completed_today_count() >= config.background_work_max_tasks_per_day:
        logger.debug("Background work: daily task cap reached (%d)",
                      config.background_work_max_tasks_per_day)
        return

    task = work_queue.next_pending()
    if task is None:
        logger.debug("Background work: no pending tasks in queue")
        return

    if not config.background_work_auto_approve:
        logger.info(
            "Background work: task pending approval — %s (%s agent, %s priority)",
            task.description[:80], task.suggested_agent, task.priority,
        )
        return

    work_queue.mark_in_progress(task.id)
    logger.info(
        "Background work: delegating '%s' to %s agent",
        task.description[:80], task.suggested_agent,
    )

    briefing_context = context.get_briefing_context()
    chat_excerpt = _read_recent_chat_context(
        config.state_dir, lookback_days=config.background_work_chat_lookback_days
    )
    enriched_context = briefing_context
    if chat_excerpt and chat_excerpt != "(no recent chat activity)":
        enriched_context += f"\n\n### Recent Chat Activity\n{chat_excerpt}"

    try:
        result = delegator.delegate(
            task.suggested_agent,
            task.description,
            backend=config.background_work_agent_backend,
            journaler_context=enriched_context,
        )
    except Exception as exc:
        logger.warning("Background work task failed: %s", exc)
        work_queue.mark_skipped(task.id, reason=f"delegation error: {exc}")
        write_work_status(config.state_dir, work_queue)
        return

    if not result or result.strip().startswith("Agent task failed"):
        work_queue.mark_skipped(task.id, reason="agent returned no output")
        write_work_status(config.state_dir, work_queue)
        return

    output_dir = config.state_dir / "outputs" / "background" / today_str
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{task.id}.md"
    output_path.write_text(
        f"# Background Task: {task.description}\n\n"
        f"Agent: {task.suggested_agent} | Source: {task.source}\n"
        f"Priority: {task.priority} | Completed: "
        f"{datetime.now().isoformat(timespec='seconds')}\n\n"
        f"---\n\n{result}\n",
        encoding="utf-8",
    )

    work_queue.mark_completed(task.id, output_path=str(output_path))
    write_work_status(config.state_dir, work_queue)
    logger.info("Background work completed: %s → %s", task.description[:60], output_path)


def _read_todays_briefing(config: JournalerConfig) -> str:
    """Read this morning's briefing for EOD reconciliation."""
    today_str = date.today().isoformat()
    output_dir = config.briefing_output_dir or (config.state_dir / "briefings")
    path = output_dir / f"{today_str}.md"
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        return text[:4000]
    except OSError:
        return ""


def _read_todays_work_status(config: JournalerConfig) -> str:
    """Read today's background agent work status."""
    today_str = date.today().isoformat()
    path = config.state_dir / "agent_work_status" / f"{today_str}.md"
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:2000]
    except OSError:
        return ""


def _end_of_day_clear(
    engine: ConversationEngine,
    config: JournalerConfig,
) -> None:
    """End-of-day housekeeping: compress today's conversation, archive, and reset.

    Writes a daily summary to ``<state_dir>/daily_summaries/YYYY-MM-DD.md``
    and optionally captures it to memory if ``capture_daily_to_memory`` is
    enabled in the PressureConfig.
    """
    history = engine.history

    if not history.turns:
        logger.debug("End-of-day clear: no conversation history to archive.")
        return

    all_turns = [t for t in history.turns if t.role != "system"]
    if not all_turns:
        history.turns.clear()
        return

    all_text = "\n".join(
        f"{t.role.upper()} ({t.timestamp[:16]}): {t.content}"
        for t in all_turns
    )

    briefing_text = _read_todays_briefing(config)
    work_status_text = _read_todays_work_status(config)
    chat_excerpt = _read_recent_chat_context(
        config.state_dir, lookback_days=0, max_chars=3000
    )

    eod_prompt_parts = [
        "Summarize today's activity for archival. ",
        "Include the following sections:\n\n",
        "## Planned vs. Done\n",
        "What the morning briefing identified vs. what was actually accomplished "
        "(by the user in chat and by background agents). Note which briefing items "
        "the user actively engaged with in chat vs. which were ignored — this tells "
        "tomorrow's briefing what matters.\n\n",
        "## Key Decisions\n",
        "Important decisions made during the day.\n\n",
        "## Open Questions\n",
        "Unresolved questions that carry into tomorrow.\n\n",
        "## Still Outstanding\n",
        "Items from the briefing or background queue that weren't addressed and why.\n\n",
        "## Workflow Friction\n",
        "Repeated context-switching, unclear task scope, missing information, "
        "tools or commands that could have helped.\n\n",
        "## Tomorrow's Seed\n",
        "1-3 concrete items the morning briefing should prioritize tomorrow, "
        "based on unfinished work and user engagement patterns.\n\n",
        "---\n\n",
    ]

    if briefing_text:
        eod_prompt_parts.append(f"## Morning Briefing\n{briefing_text}\n\n")
    if work_status_text:
        eod_prompt_parts.append(
            f"## Background Agent Work Status\n{work_status_text}\n\n"
        )
    if chat_excerpt and chat_excerpt != "(no recent chat activity)":
        eod_prompt_parts.append(f"## Chat History (today)\n{chat_excerpt}\n\n")
    eod_prompt_parts.append(f"## Conversation Turns\n{all_text}")

    try:
        summary = engine._raw_complete(
            "".join(eod_prompt_parts),
            max_tokens=1200,
        )
    except Exception as exc:
        logger.warning(f"End-of-day summary generation failed: {exc}")
        summary = "(Summary generation failed — raw turns archived to conversation.jsonl)"

    date_str = datetime.now().strftime("%Y-%m-%d")
    summary_dir = config.state_dir / "daily_summaries"
    summary_dir.mkdir(parents=True, exist_ok=True)
    summary_path = summary_dir / f"{date_str}.md"
    summary_path.write_text(
        f"# Journaler Daily Summary — {date_str}\n\n"
        f"Exchanges: {len(all_turns)}\n\n"
        f"{summary}\n",
        encoding="utf-8",
    )
    logger.info(f"Daily summary written to {summary_path}")

    # Optionally capture to memory
    pressure_cfg = config.get_pressure_config()
    if pressure_cfg.capture_daily_to_memory and config.memory_service:
        try:
            config.memory_service.capture(
                content=f"Journaler daily summary ({date_str}):\n{summary}",
                source="journaler",
                tags=["daily_summary", date_str],
            )
        except Exception as exc:
            logger.warning(f"Failed to capture daily summary to memory: {exc}")

    # Archive all turns to JSONL then reset
    archived = list(history.turns)
    engine._log_archived_turns(archived)

    history.turns.clear()
    engine.budget.history_tokens = 0

    logger.info(
        f"End-of-day clear: {len(archived)} turns archived, "
        f"summary saved to {summary_path.name}"
    )
