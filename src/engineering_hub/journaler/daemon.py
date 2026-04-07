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
    format_briefing_prompt,
    format_system_prompt,
    load_briefing_prompt,
    load_system_prompt,
)

if TYPE_CHECKING:
    from engineering_hub.journaler.chat_server import ChatServer
    from engineering_hub.journaler.slack import SlackPoster
    from engineering_hub.memory.service import MemoryService

logger = logging.getLogger(__name__)


@dataclass
class JournalerConfig:
    """All settings needed to run the Journaler daemon."""

    model_path: str
    org_roam_dir: Path
    workspace_dir: Path
    state_dir: Path

    scan_interval_min: int = 10
    briefing_enabled: bool = True
    briefing_time: str = "07:00"
    briefing_output_dir: Path | None = None

    chat_enabled: bool = True
    chat_host: str = "127.0.0.1"
    chat_port: int = 18790

    slack_enabled: bool = False
    slack_webhook_url: str = ""

    max_context_tokens: int = 4000
    max_briefing_tokens: int = 8000
    max_conversation_history: int = 20
    max_tokens: int = 4000

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

    memory_service: MemoryService | None = None

    # Optional PDF reference corpus (libraryfiles-corpus); used for per-turn RAG in chat.
    corpus_service: Any | None = None

    # Slash /load: context-aware size limits (see LoadFileBudgetConfig in engine.py)
    load_max_context_fraction: float = 0.40
    load_max_chars_absolute: int = 200_000
    load_min_chars: int = 1024
    load_slack_tokens: int = 256

    # Agent delegation settings
    # anthropic_api_key: Anthropic API key for Claude-backed agent delegation.
    #   If empty, delegation falls back to the local MLX model.
    anthropic_api_key: str = ""
    # agent_backend: Which LLM to use for delegated agent tasks.
    #   "auto"   — Claude if api key present, else local MLX (default)
    #   "claude" — always Claude API (error if no key)
    #   "mlx"    — always local model (reuses Journaler's loaded model, no extra RAM)
    agent_backend: str = "auto"
    # skills_dir: Path to the skills/ YAML directory.
    #   Defaults to the skills/ directory at the repo root.
    skills_dir: Path | None = None

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
    context = JournalContext(
        org_roam_dir=config.org_roam_dir,
        workspace_dir=config.workspace_dir,
        memory_service=config.memory_service,
        state_dir=config.state_dir,
        watch_dirs=config.watch_dirs,
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
    )

    # Do initial scan
    logger.info("Running initial scan...")
    context.scan()
    workspace_map = build_workspace_layout(config.org_roam_dir, config.workspace_dir)
    engine.update_context(context.get_current_context())
    # Re-set system prompt with actual context and workspace layout
    engine._system_prompt = format_system_prompt(
        system_template,
        context.get_current_context(),
        workspace_map=workspace_map,
    )

    # Init agent delegator (bridges Journaler chat → AgentWorker)
    from engineering_hub.journaler.delegator import build_delegator

    delegator = build_delegator(
        backend,
        anthropic_api_key=config.anthropic_api_key,
        skills_dir=config.skills_dir,
        default_backend=config.agent_backend,
        output_dir=config.workspace_dir / "outputs",
    )
    skills_suffix = ""
    if delegator is not None:
        skills_suffix = build_skills_block(delegator)
        if skills_suffix:
            engine._system_prompt = engine._system_prompt.rstrip() + "\n\n" + skills_suffix

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

        chat_server = ChatServer(
            engine=engine,
            context=context,
            host=config.chat_host,
            port=config.chat_port,
            start_time=start_time,
            delegator=delegator,
            model_context=runtime_model,
        )

    # Schedule recurring tasks
    schedule.every(config.scan_interval_min).minutes.do(
        _tick,
        context=context,
        engine=engine,
        system_template=system_template,
        workspace_map=workspace_map,
        skills_suffix=skills_suffix,
    )

    if config.briefing_enabled:
        schedule.every().day.at(config.briefing_time).do(
            _morning_briefing,
            config=config,
            context=context,
            engine=engine,
            briefing_template=briefing_template,
            slack=slack,
        )
        logger.info(f"Morning briefing scheduled at {config.briefing_time}")

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
        context = JournalContext(
            org_roam_dir=config.org_roam_dir,
            workspace_dir=config.workspace_dir,
            memory_service=config.memory_service,
            state_dir=config.state_dir,
            watch_dirs=config.watch_dirs,
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

    briefing = engine.generate_briefing(briefing_context, prompt)

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


# ---------------------------------------------------------------------------
# Scheduled task callbacks
# ---------------------------------------------------------------------------


def _tick(
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


def _morning_briefing(
    config: JournalerConfig,
    context: JournalContext,
    engine: ConversationEngine,
    briefing_template: str,
    slack: SlackPoster | None,
) -> None:
    """Generate and deliver the morning briefing."""
    context.scan()
    briefing_context = context.get_briefing_context()
    today_str = date.today().isoformat()
    prompt = format_briefing_prompt(briefing_template, today_str, briefing_context)

    briefing = engine.generate_briefing(briefing_context, prompt)

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

    try:
        summary = engine._raw_complete(
            f"Summarize today's conversation for archival. "
            f"Include key decisions, open questions, and action items.\n\n"
            f"{all_text}",
            max_tokens=800,
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
