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
from typing import TYPE_CHECKING

import schedule

from engineering_hub.journaler.context import JournalContext
from engineering_hub.journaler.engine import ConversationalMLXBackend, ConversationEngine
from engineering_hub.journaler.prompts import (
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

    # MLX sampling parameters
    temp: float = 0.7
    top_p: float = 0.9
    min_p: float = 0.05
    repetition_penalty: float = 1.1

    watch_dirs: list[Path] | None = None

    memory_service: MemoryService | None = None


def run_daemon(config: JournalerConfig) -> None:
    """Main daemon entry point. Blocks until SIGINT/SIGTERM."""
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
    )

    # Init conversation engine
    system_prompt = format_system_prompt(system_template, "(initial scan pending)")
    engine = ConversationEngine(
        backend=backend,
        system_prompt=system_prompt,
        log_dir=config.state_dir,
        max_history=config.max_conversation_history,
        max_tokens=config.max_tokens,
    )

    # Do initial scan
    logger.info("Running initial scan...")
    context.scan()
    engine.update_context(context.get_current_context())
    # Re-set system prompt with actual context
    engine._system_prompt = format_system_prompt(
        system_template, context.get_current_context()
    )

    # Init optional components
    slack: SlackPoster | None = None
    if config.slack_enabled and config.slack_webhook_url:
        from engineering_hub.journaler.slack import SlackPoster

        slack = SlackPoster(webhook_url=config.slack_webhook_url)
        logger.info("Slack integration enabled")

    chat_server: ChatServer | None = None
    if config.chat_enabled:
        from engineering_hub.journaler.chat_server import ChatServer

        chat_server = ChatServer(
            engine=engine,
            context=context,
            host=config.chat_host,
            port=config.chat_port,
            start_time=start_time,
        )

    # Schedule recurring tasks
    schedule.every(config.scan_interval_min).minutes.do(
        _tick,
        context=context,
        engine=engine,
        system_template=system_template,
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
        )
        engine = ConversationEngine(
            backend=backend,
            system_prompt="You are the Journaler.",
            log_dir=config.state_dir,
            max_tokens=config.max_tokens,
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
) -> None:
    """10-minute scan cycle."""
    snapshot = context.scan()
    current_context = context.get_current_context()
    engine.update_context(current_context)
    engine._system_prompt = format_system_prompt(system_template, current_context)

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
    # Run a fresh scan first
    context.scan()
    briefing_context = context.get_briefing_context()
    today_str = date.today().isoformat()
    prompt = format_briefing_prompt(briefing_template, today_str, briefing_context)

    briefing = engine.generate_briefing(briefing_context, prompt)

    # Save to file
    output_dir = config.briefing_output_dir or (config.state_dir / "briefings")
    output_dir.mkdir(parents=True, exist_ok=True)
    briefing_path = output_dir / f"{today_str}.md"
    briefing_path.write_text(
        f"# Morning Briefing — {today_str}\n\n{briefing}",
        encoding="utf-8",
    )
    logger.info(f"Morning briefing generated: {briefing_path}")

    # Post to Slack
    if slack:
        slack.post_briefing(briefing)
