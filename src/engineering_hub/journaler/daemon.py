"""Journaler daemon: main loop, scheduler, and signal handling.

The daemon keeps an MLX model loaded and warm, scans the org-roam workspace
on a configurable interval, generates morning briefings on schedule, and
optionally runs an HTTP chat server for ad-hoc questions.
"""

from __future__ import annotations

import logging
import os
import signal
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import schedule

from engineering_hub.config.settings import Settings
from engineering_hub.journaler.activity_log import ActivityLogConfig, JournalerActivityLog
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
from engineering_hub.journaler.status_snapshot import (
    build_status_suggestions,
    write_status_file,
)
from engineering_hub.search import SearchProvider

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
    journal_dir: Path
    workspace_dir: Path
    state_dir: Path

    scan_interval_min: int = 10
    briefing_enabled: bool = True
    briefing_time: str = "09:00"
    briefing_output_dir: Path | None = None

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
    journal_lookback_days: int = 5
    journal_max_files: int = 5

    # Journaler-owned overnight queue file (Orchestrator scans via OrgTaskParser extra_files).
    pending_tasks_file: Path | None = None

    # Periodic deep scan: force-reparses the full journal window regardless of mtime.
    # Set to 0 to disable.
    deep_scan_interval_min: int = 60

    memory_service: MemoryService | None = None

    # Optional PDF reference corpus (libraryfiles-corpus); used for per-turn RAG in chat.
    corpus_service: Any | None = None

    # Org-mode daemon activity stream (for Doom Emacs auto-revert workflows).
    activity_log_enabled: bool = False
    activity_log_mode: str = "daily_journal"
    activity_log_path: Path | None = None
    activity_log_heading: str = "Journaler Activity"
    activity_log_include_suggestions: bool = True

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
    allowed = set(PressureConfig.__dataclass_fields__)
    return PressureConfig(**{k: v for k, v in raw.items() if k in allowed})


def _build_daemon_status_payload(
    config: JournalerConfig,
    context: JournalContext,
    engine: ConversationEngine,
    start_time: datetime,
    *,
    status: str = "running",
    last_event: str = "",
) -> dict[str, Any]:
    """Build the lightweight status sidecar written for monitor clients."""
    now = datetime.now()
    snapshot = context._snapshot
    uptime_seconds = int((now - start_time).total_seconds())
    engine_status = engine.get_status()
    return {
        "pid": os.getpid(),
        "status": status,
        "started_at": start_time.isoformat(timespec="seconds"),
        "last_heartbeat": now.isoformat(timespec="seconds"),
        "uptime": _format_uptime(uptime_seconds),
        "last_event": last_event,
        "model_path": config.model_path,
        "model_loaded": engine._backend.is_loaded(),
        "chat_enabled": config.chat_enabled,
        "chat_url": f"http://{config.chat_host}:{config.chat_port}",
        "scan_interval_min": config.scan_interval_min,
        "deep_scan_interval_min": config.deep_scan_interval_min,
        "briefing_enabled": config.briefing_enabled,
        "briefing_time": config.briefing_time,
        "last_scan": snapshot.last_scan,
        "pending_tasks": len(snapshot.pending_tasks),
        "completed_tasks": len(snapshot.completed_tasks),
        "stale_tasks": len(snapshot.stale_tasks),
        "tracked_files": len(context._state.file_mtimes),
        "history": engine.get_history_summary(),
        "engine": engine_status,
    }


def _write_daemon_status(
    config: JournalerConfig,
    context: JournalContext,
    engine: ConversationEngine,
    start_time: datetime,
    *,
    status: str = "running",
    last_event: str = "",
) -> dict[str, Any]:
    payload = _build_daemon_status_payload(
        config,
        context,
        engine,
        start_time,
        status=status,
        last_event=last_event,
    )
    try:
        write_status_file(config.state_dir, payload)
    except OSError as exc:
        logger.warning("Failed to write Journaler daemon status: %s", exc)
    return payload


def _status_suggestions(payload: dict[str, Any]) -> list[str]:
    snapshot = dict(payload)
    snapshot["daemon_online"] = payload.get("status") == "running"
    snapshot["http_online"] = bool(payload.get("chat_enabled"))
    snapshot["heartbeat_fresh"] = True
    return build_status_suggestions(snapshot)


def _format_uptime(seconds: int) -> str:
    hours, remainder = divmod(max(0, seconds), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}h {minutes}m {seconds}s"


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

    activity_log = JournalerActivityLog(
        ActivityLogConfig(
            enabled=config.activity_log_enabled,
            mode=config.activity_log_mode,
            path=config.activity_log_path,
            heading=config.activity_log_heading,
            include_suggestions=config.activity_log_include_suggestions,
        ),
        org_roam_dir=config.org_roam_dir,
        journal_dir=config.journal_dir,
    )

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
        activity_log=activity_log,
        )

    # Schedule recurring tasks
    schedule.every(config.scan_interval_min).minutes.do(
        _tick,
        context=context,
        engine=engine,
        system_template=system_template,
        workspace_map=workspace_map,
        skills_suffix=skills_suffix,
        config=config,
        start_time=start_time,
        activity_log=activity_log,
    )

    if config.deep_scan_interval_min > 0:
        schedule.every(config.deep_scan_interval_min).minutes.do(
            _deep_scan_tick,
            context=context,
            engine=engine,
            system_template=system_template,
            workspace_map=workspace_map,
            skills_suffix=skills_suffix,
            config=config,
            start_time=start_time,
            activity_log=activity_log,
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
            activity_log=activity_log,
            start_time=start_time,
        )
        logger.info(f"Morning briefing scheduled at {config.briefing_time}")

    # Schedule end-of-day context clear
    eod_time = pressure_cfg.end_of_day_time
    schedule.every().day.at(eod_time).do(
        _end_of_day_clear,
        engine=engine,
        config=config,
        activity_log=activity_log,
        start_time=start_time,
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
    status_payload = _write_daemon_status(
        config,
        context,
        engine,
        start_time,
        last_event="daemon_start",
    )
    activity_log.append_event(
        "daemon_start",
        "Journaler daemon started",
        details={
            "Model": config.model_path,
            "Scan interval": f"{config.scan_interval_min} min",
            "Chat": f"http://{config.chat_host}:{config.chat_port}"
            if config.chat_enabled
            else "disabled",
        },
        suggestions=_status_suggestions(status_payload),
        properties={"pid": os.getpid()},
    )

    # Main loop
    last_heartbeat = 0.0
    try:
        while not shutdown:
            schedule.run_pending()
            now = time.monotonic()
            if now - last_heartbeat >= 5:
                _write_daemon_status(
                    config,
                    context,
                    engine,
                    start_time,
                    last_event="heartbeat",
                )
                last_heartbeat = now
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        if chat_server:
            chat_server.stop()
        status_payload = _write_daemon_status(
            config,
            context,
            engine,
            start_time,
            status="stopped",
            last_event="daemon_stop",
        )
        activity_log.append_event(
            "daemon_stop",
            "Journaler daemon stopped",
            details={"Uptime": status_payload.get("uptime", "")},
            properties={"pid": os.getpid()},
            level="warning",
        )
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

    briefing = engine.generate_briefing(
        briefing_context, prompt, max_tokens=config.max_briefing_tokens
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


# ---------------------------------------------------------------------------
# Scheduled task callbacks
# ---------------------------------------------------------------------------


def _tick(
    context: JournalContext,
    engine: ConversationEngine,
    system_template: str,
    workspace_map: str = "",
    skills_suffix: str = "",
    config: JournalerConfig | None = None,
    start_time: datetime | None = None,
    activity_log: JournalerActivityLog | None = None,
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

    if config is not None and start_time is not None:
        payload = _write_daemon_status(
            config,
            context,
            engine,
            start_time,
            last_event="scan_complete",
        )
        if activity_log is not None:
            activity_log.append_event(
                "scan_complete",
                "Journaler scan complete",
                details={
                    "Last scan": snapshot.last_scan,
                    "Pending tasks": len(snapshot.pending_tasks),
                    "Completed tasks": len(snapshot.completed_tasks),
                    "Stale tasks": len(snapshot.stale_tasks),
                    "Changes": snapshot.change_summary or "none",
                },
                suggestions=_status_suggestions(payload),
            )


def _deep_scan_tick(
    context: JournalContext,
    engine: ConversationEngine,
    system_template: str,
    workspace_map: str = "",
    skills_suffix: str = "",
    config: JournalerConfig | None = None,
    start_time: datetime | None = None,
    activity_log: JournalerActivityLog | None = None,
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
    if config is not None and start_time is not None:
        payload = _write_daemon_status(
            config,
            context,
            engine,
            start_time,
            last_event="deep_scan_complete",
        )
        if activity_log is not None:
            activity_log.append_event(
                "deep_scan_complete",
                "Journaler deep scan refreshed context",
                details={
                    "Recurring topics": len(snapshot.recurring_topics),
                    "Active roam nodes": len(snapshot.active_roam_nodes),
                    "Stale tasks": len(snapshot.stale_tasks),
                },
                suggestions=_status_suggestions(payload),
            )


def _morning_briefing(
    config: JournalerConfig,
    context: JournalContext,
    engine: ConversationEngine,
    briefing_template: str,
    slack: SlackPoster | None,
    activity_log: JournalerActivityLog | None = None,
    start_time: datetime | None = None,
) -> None:
    """Generate and deliver the morning briefing."""
    context.scan()
    briefing_context = context.get_briefing_context()
    today_str = date.today().isoformat()
    prompt = format_briefing_prompt(briefing_template, today_str, briefing_context)

    briefing = engine.generate_briefing(
        briefing_context, prompt, max_tokens=config.max_briefing_tokens
    )

    output_dir = config.briefing_output_dir or (config.state_dir / "briefings")
    output_dir.mkdir(parents=True, exist_ok=True)
    briefing_path = output_dir / f"{today_str}.md"
    briefing_path.write_text(
        f"# Morning Briefing — {today_str}\n\n{briefing}",
        encoding="utf-8",
    )
    logger.info(f"Morning briefing generated: {briefing_path}")
    if start_time is not None:
        payload = _write_daemon_status(
            config,
            context,
            engine,
            start_time,
            last_event="briefing_generated",
        )
        if activity_log is not None:
            activity_log.append_event(
                "briefing_generated",
                "Morning briefing generated",
                details={"Path": briefing_path},
                suggestions=_status_suggestions(payload),
            )

    if slack:
        slack.post_briefing(briefing)


def _end_of_day_clear(
    engine: ConversationEngine,
    config: JournalerConfig,
    activity_log: JournalerActivityLog | None = None,
    start_time: datetime | None = None,
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
    if activity_log is not None:
        activity_log.append_event(
            "end_of_day_clear",
            "End-of-day conversation clear complete",
            details={
                "Archived turns": len(archived),
                "Summary": summary_path,
            },
        )
