"""Command-line interface for Engineering Hub."""

import argparse
import logging
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

from rich.console import Console
from rich.logging import RichHandler
from rich.markup import escape
from rich.table import Table

from engineering_hub.config.loader import find_config_file
from engineering_hub.config.settings import Settings
from engineering_hub.journaler.constants import DEFAULT_JOURNALER_MLX_MODEL_ID
from engineering_hub.journaler.engine import SUPPORTED_EXTENSIONS, ConversationEngine, _is_model_cached
from engineering_hub.orchestration.orchestrator import Orchestrator

console = Console()


class JournalerChatExit(Exception):
    """Raised to leave the interactive journaler chat loop (e.g. /exit)."""


def setup_logging(verbose: bool = False) -> None:
    """Set up logging with rich handler."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True)],
    )


def load_settings(config_path: Path | None = None) -> Settings:
    """Load settings from config file and environment."""
    if config_path is None:
        config_path = find_config_file()

    if config_path:
        console.print(f"[dim]Loading config from: {config_path}[/dim]")
        return Settings.from_yaml(config_path)

    return Settings()


def _validate_llm_settings(settings: Settings) -> int | None:
    """Validate LLM provider settings. Returns an error code or None if OK."""
    provider = settings.llm_provider.lower()
    if provider == "anthropic" and not settings.anthropic_api_key.get_secret_value():
        console.print(
            "[red]Error:[/red] Anthropic API key not set. "
            "Set ENGINEERING_HUB_ANTHROPIC_API_KEY or add to config."
        )
        return 1
    if provider == "mlx" and not settings.mlx_model_path:
        console.print(
            "[red]Error:[/red] MLX model path not set. "
            "Set mlx.model_path in config or ENGINEERING_HUB_MLX_MODEL_PATH."
        )
        return 1
    return None


def cmd_start(args: argparse.Namespace) -> int:
    """Start the orchestrator."""
    settings = load_settings(args.config)

    if err := _validate_llm_settings(settings):
        return err

    if not settings.django_api_token.get_secret_value():
        console.print(
            "[yellow]Warning:[/yellow] Django API token not set. "
            "API calls will fail."
        )

    parsed_url = urlparse(settings.django_api_url)
    if parsed_url.scheme == "http" and parsed_url.hostname not in ("localhost", "127.0.0.1", "::1"):
        console.print(
            "[yellow]Warning:[/yellow] Django API URL uses plain HTTP with a remote host. "
            "API tokens will be sent in cleartext. Consider using HTTPS."
        )

    if not settings.notes_file.exists():
        console.print(
            f"[red]Error:[/red] Notes file not found: {settings.notes_file}\n"
            "Create the file or run 'engineering-hub init' to set up workspace."
        )
        return 1

    provider = settings.llm_provider.lower()
    console.print("[bold green]Starting Engineering Hub...[/bold green]")
    console.print(f"  Provider: {provider}")
    if provider == "mlx":
        console.print(f"  MLX Model: {settings.mlx_model_path}")
    else:
        console.print(f"  Model: {settings.anthropic_model}")
    console.print(f"  Notes: {settings.notes_file}")
    console.print(f"  Outputs: {settings.output_dir}")

    try:
        orchestrator = Orchestrator(settings)
        orchestrator.run()
    except KeyboardInterrupt:
        console.print("\n[yellow]Shutting down...[/yellow]")
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        return 1

    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Show orchestrator status."""
    settings = load_settings(args.config)

    table = Table(title="Engineering Hub Status")
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("Workspace", str(settings.workspace_dir))

    if settings.use_org_mode:
        notes_display = str(settings.org_journal_dir)
        notes_exists = settings.org_journal_dir.exists()
        table.add_row("Task Source (org)", notes_display)
        table.add_row("Org Journal Exists", "✓" if notes_exists else "✗")
    else:
        table.add_row("Notes File", str(settings.notes_file))
        table.add_row("Notes Exists", "✓" if settings.notes_file.exists() else "✗")

    table.add_row("Output Dir", str(settings.output_dir))
    table.add_row("Django API", settings.django_api_url)
    has_token = "✓ Set" if settings.django_api_token.get_secret_value() else "✗ Not set"
    table.add_row("Django Token", has_token)

    provider = settings.llm_provider.lower()
    table.add_row("LLM Provider", provider)
    if provider == "mlx":
        table.add_row("MLX Model", settings.mlx_model_path or "✗ Not set")
    else:
        has_key = "✓ Set" if settings.anthropic_api_key.get_secret_value() else "✗ Not set"
        table.add_row("Anthropic Key", has_key)
        table.add_row("Model", settings.anthropic_model)

    console.print(table)

    # Show pending tasks
    from engineering_hub.notes.manager import SharedNotesManager

    if settings.use_org_mode:
        notes_path = settings.org_journal_dir
        path_exists = settings.org_journal_dir.exists()
    else:
        notes_path = settings.notes_file
        path_exists = settings.notes_file.exists()

    if path_exists:
        manager = SharedNotesManager(
            notes_path,
            use_journal_mode=settings.use_journal_mode,
            journal_categories=settings.journal_categories,
            use_org_mode=settings.use_org_mode,
            org_task_sections=settings.org_task_sections,
            org_lookback_days=settings.org_lookback_days,
        )
        pending = manager.get_pending_tasks()

        if pending:
            console.print(f"\n[bold]Pending Tasks ({len(pending)}):[/bold]")
            for task in pending:
                console.print(f"  • @{task.agent}: {task.description[:60]}")
        else:
            console.print("\n[dim]No pending tasks[/dim]")

    return 0


def cmd_run_once(args: argparse.Namespace) -> int:
    """Run once to process all pending tasks, then exit."""
    settings = load_settings(args.config)

    if err := _validate_llm_settings(settings):
        return err

    if not settings.notes_file.exists():
        console.print(f"[red]Error:[/red] Notes file not found: {settings.notes_file}")
        return 1

    console.print("[bold]Processing pending tasks...[/bold]")

    try:
        orchestrator = Orchestrator(settings)
        results = orchestrator.process_pending_now()

        if not results:
            console.print("[dim]No pending tasks to process[/dim]")
            return 0

        # Show results
        success_count = sum(1 for r in results if r.success)
        fail_count = len(results) - success_count

        console.print(f"\n[bold]Results:[/bold]")
        console.print(f"  ✓ Completed: {success_count}")
        console.print(f"  ✗ Failed: {fail_count}")

        for result in results:
            if result.success:
                console.print(
                    f"  [green]✓[/green] @{result.task.agent}: {result.task.description[:40]}..."
                )
                if result.output_path:
                    console.print(f"    Output: {result.output_path}")
            else:
                console.print(
                    f"  [red]✗[/red] @{result.task.agent}: {result.task.description[:40]}..."
                )
                console.print(f"    Error: {result.error_message}")

        return 0 if fail_count == 0 else 1

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        return 1


def cmd_init(args: argparse.Namespace) -> int:
    """Initialize workspace with template files."""
    workspace = Path(args.workspace).expanduser()

    if workspace.exists() and not args.force:
        console.print(
            f"[yellow]Warning:[/yellow] Workspace already exists: {workspace}\n"
            "Use --force to overwrite."
        )
        return 1

    console.print(f"[bold]Initializing workspace at: {workspace}[/bold]")

    # Create directories
    (workspace / "outputs" / "research").mkdir(parents=True, exist_ok=True)
    (workspace / "outputs" / "docs").mkdir(parents=True, exist_ok=True)
    (workspace / "outputs" / "analysis").mkdir(parents=True, exist_ok=True)

    # Create config.yaml
    config_content = f"""# Engineering Hub Configuration
django:
  api_url: "http://localhost:8000/api"
  # api_token: "your-token-here"  # Or set ENGINEERING_HUB_DJANGO_API_TOKEN
  cache_ttl: 300

anthropic:
  # api_key: "your-key-here"  # Or set ENGINEERING_HUB_ANTHROPIC_API_KEY
  model: "claude-sonnet-4-5-20250929"
  max_tokens: 4000

workspace:
  dir: "{workspace}"

journal:
  use_journal_mode: true
  categories:
    "Project Work to-do": "research"
    "Technical Writing Work": "technical-writer"
    "Thoughts to Expand or Clarify": "research"
"""
    (workspace / "config.yaml").write_text(config_content)
    console.print("  ✓ Created config.yaml")

    # Create journal.md (default) and shared-notes.md (legacy)
    journal_content = """---
workspace: engineering-hub
sync_url: http://localhost:8000/api
---

# Engineering Hub Journal

## {today}

### Incoming Comms
- 

### Project Work to-do
- [ ] 

### Technical Writing Work
- [ ] 

### Thoughts to Expand or Clarify
- [ ] 

## Agent Communication Thread

<!-- Agent messages will be appended here -->

## Engineering Log

<!-- Dated entries with decisions and findings -->
"""
    today = date.today().isoformat()
    journal_content = journal_content.replace("{today}", today)

    (workspace / "journal.md").write_text(journal_content)
    console.print("  ✓ Created journal.md")

    # Legacy shared-notes.md for users who set use_journal_mode: false
    notes_content = """---
workspace: engineering-hub
sync_url: http://localhost:8000/api
---

# Engineering Hub - Shared Notes

## Active Engineering Tasks

<!-- Add tasks here using the format:
### @agent-name: PENDING
> Project: [[django://project/ID]]
> Task: Description of what to do
> Deliverable: [[/outputs/research/filename.md]]
-->

## Agent Communication Thread

<!-- Agent messages will be appended here -->

## Project Context Cache

<!-- Auto-updated project info from Django -->

## Engineering Log

<!-- Dated entries with decisions and findings -->
"""
    (workspace / "shared-notes.md").write_text(notes_content)
    console.print("  ✓ Created shared-notes.md")

    console.print("\n[green]Workspace initialized![/green]")
    console.print("\nNext steps:")
    console.print("  1. Edit config.yaml and add your API tokens")
    console.print("  2. Add tasks to journal.md (use - [ ] under category sections)")
    console.print(
        '     Example: - [ ] Draft report for [[django://project/25]] '
        "→ [[/outputs/docs/report.md]]",
        markup=False,
    )
    console.print("  3. Run: engineering-hub start")

    return 0


def cmd_weekly_review(args: argparse.Namespace) -> int:
    """Run the weekly reviewer agent and write a synthesis report."""
    settings = load_settings(args.config)

    if err := _validate_llm_settings(settings):
        return err

    from engineering_hub.agents.backends import create_backend
    from engineering_hub.agents.worker import AgentWorker
    from engineering_hub.orchestration.weekly_review_builder import WeeklyReviewBuilder

    builder = WeeklyReviewBuilder(settings)
    context = builder.build_context(days=args.days, focus=args.focus)
    output_path = (
        Path(args.output).expanduser()
        if args.output
        else builder.default_output_path()
    )

    backend = create_backend(settings)
    worker = AgentWorker(
        backend=backend,
        prompts_dir=settings.prompts_dir,
        output_dir=settings.output_dir,
    )

    console.print("[bold]Running weekly reviewer...[/bold]")
    try:
        worker.run_weekly_review(context=context, output_path=output_path)
    except Exception as exc:
        console.print(f"[red]Error:[/red] Weekly review failed: {exc}")
        return 1

    console.print(f"[bold green]Weekly review complete![/bold green]")
    console.print(f"  Report: {output_path}")
    return 0


def cmd_mcp_server(args: argparse.Namespace) -> int:
    """Start the local MCP server."""
    from engineering_hub.mcp.server import run_server

    transport = args.transport

    if transport == "stdio":
        logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    else:
        setup_logging(args.verbose)
        console.print(
            f"[bold green]Starting MCP server ({transport}) "
            f"on {args.host}:{args.port}[/bold green]"
        )

    run_server(transport=transport, host=args.host, port=args.port)
    return 0


def _handle_chat_slash_command(
    raw: str,
    engine: ConversationEngine,
    chat_console: Console,
    org_roam_dir: Path | None = None,
) -> None:
    """Intercept and execute a /slash command from the journaler chat loop.

    Recognised commands:
      /load <path> [-r]          Load a file or directory into context.
      /files                     List all currently loaded files.
      /files clear               Remove all loaded files from the context.
      /clear [--hard|--summarize] Clear conversation history (soft by default).
      /status                    Show context management state (pressure, turns, etc.)
      /budget                    Show token budget breakdown.
      /topic                     Show the currently detected conversation topic.
      /find <title fragment>     Search org-roam files by #+title:.
      /task <description>        Add a TODO to today's journal.
      /done <fragment>           Mark a matching TODO as done in today's journal.
      /note <heading> :: <text>  Append text under a heading in today's journal.
      /exit, /quit               Leave the chat (same as bare exit, quit, or :q).
      /help                      Show available slash commands.
    """
    from engineering_hub.journaler.context_manager import ClearStrategy
    from engineering_hub.journaler.org_writer import (
        add_todo_to_journal,
        append_to_heading,
        find_org_by_title,
        mark_done_in_journal,
    )

    parts = raw.split()
    cmd = parts[0].lower()

    if cmd in ("/exit", "/quit"):
        raise JournalerChatExit()

    # Determine the daily journal directory (child of org_roam_dir named "journal")
    journal_dir: Path | None = None
    if org_roam_dir is not None:
        candidate = org_roam_dir / "journal"
        journal_dir = candidate if candidate.exists() else org_roam_dir

    if cmd == "/help":
        write_cmds = (
            "\n  [bold]File operations (requires org-roam dir):[/bold]\n"
            "  [cyan]/task <description>[/cyan]          Add a TODO to today's journal\n"
            "  [cyan]/done <fragment>[/cyan]             Mark a matching TODO as done\n"
            "  [cyan]/note <heading> :: <text>[/cyan]   Append text under a heading in today's journal\n"
            "  [cyan]/find <title fragment>[/cyan]       Search org-roam files by title\n"
        ) if org_roam_dir else ""

        chat_console.print(
            "\n[bold cyan]Slash commands:[/bold cyan]\n"
            "  [cyan]/load <path> [-r][/cyan]          Load a file or directory into context\n"
            "                                 (-r / --recursive scans subdirectories)\n"
            "  [cyan]/files[/cyan]                     List loaded files\n"
            "  [cyan]/files clear[/cyan]               Remove all loaded files from context\n"
            "  [cyan]/clear[/cyan]                     Clear conversation history (keeps context snapshot)\n"
            "  [cyan]/clear --summarize[/cyan]         Compress history into a summary, then clear\n"
            "  [cyan]/clear --hard[/cyan]              Full reset: conversation + scan state\n"
            "  [cyan]/status[/cyan]                    Show context pressure and token usage\n"
            "  [cyan]/budget[/cyan]                    Show token budget breakdown\n"
            "  [cyan]/topic[/cyan]                     Show currently detected conversation topic\n"
            + write_cmds +
            "  [cyan]/exit[/cyan], [cyan]/quit[/cyan]          Leave chat (or type exit, quit, :q)\n"
            "  [cyan]/help[/cyan]                      Show this help\n"
        )
        return

    if cmd == "/files":
        # /files clear — remove all loaded files
        if len(parts) >= 2 and parts[1].lower() == "clear":
            engine.clear_loaded_files()
            chat_console.print("[green]Loaded files cleared.[/green]")
            return
        # /files — list loaded files
        entries = engine.list_loaded_files()
        if not entries:
            chat_console.print("[dim]No files loaded.[/dim]")
        else:
            chat_console.print(f"\n[bold]Loaded files ({len(entries)}):[/bold]")
            for label, char_count in entries:
                chat_console.print(f"  [cyan]{label}[/cyan]  ({char_count:,} chars)")
            chat_console.print()
        return

    if cmd == "/clear":
        flags = {p.lower() for p in parts[1:]}
        if "--hard" in flags:
            strategy = ClearStrategy.HARD
        elif "--summarize" in flags:
            strategy = ClearStrategy.SUMMARIZE
        else:
            strategy = ClearStrategy.SOFT
        msg = engine.clear(strategy)
        chat_console.print(f"[green]{escape(msg)}[/green]")
        return

    if cmd == "/status":
        status = engine.get_status()
        table = Table(title="Context Status")
        table.add_column("Field", style="cyan")
        table.add_column("Value", style="green")
        for key, val in status.items():
            table.add_row(key.replace("_", " ").title(), str(val))
        chat_console.print(table)
        return

    if cmd == "/budget":
        engine.budget.history_tokens = engine.history.total_tokens
        b = engine.budget
        table = Table(title="Token Budget")
        table.add_column("Component", style="cyan")
        table.add_column("Tokens", justify="right", style="green")
        table.add_row("Context window", f"{b.window_size:,}")
        table.add_row("System prompt", f"{b.system_prompt_tokens:,}")
        table.add_row("Context snapshot", f"{b.context_snapshot_tokens:,}")
        table.add_row("Conversation history", f"{b.history_tokens:,}")
        table.add_row("Reserved for generation", f"{b.reserved_for_generation:,}")
        table.add_row("─" * 20, "─" * 10)
        table.add_row("[bold]Used[/bold]", f"[bold]{b.used:,}[/bold]")
        table.add_row("[bold]Available[/bold]", f"[bold]{b.available:,}[/bold]")
        table.add_row("[bold]Utilization[/bold]", f"[bold]{b.utilization:.0%}[/bold]")
        chat_console.print(table)
        return

    if cmd == "/topic":
        topic = engine.topic_tracker.current_topic
        if topic:
            chat_console.print(f"[cyan]Current topic:[/cyan] {topic}")
        else:
            chat_console.print("[dim]No topic detected yet.[/dim]")
        return

    if cmd == "/load":
        if len(parts) < 2:
            chat_console.print("[yellow]Usage: /load <path> [-r][/yellow]")
            return

        recursive = "-r" in parts or "--recursive" in parts
        path_str = next(
            (p for p in parts[1:] if not p.startswith("-")),
            None,
        )
        if not path_str:
            chat_console.print("[yellow]Usage: /load <path> [-r][/yellow]")
            return

        path = Path(path_str).expanduser()

        if path.is_dir():
            ok, msg = engine.load_directory(
                path,
                extensions=SUPPORTED_EXTENSIONS,
                recursive=recursive,
            )
        else:
            ok, msg = engine.load_file(path, extensions=SUPPORTED_EXTENSIONS)

        color = "green" if ok else "red"
        for line in msg.splitlines():
            chat_console.print(f"[{color}]{escape(line)}[/{color}]")
        return

    if cmd == "/find":
        if org_roam_dir is None:
            chat_console.print("[yellow]/find requires org_roam_dir (start with 'journaler start' or 'journaler chat')[/yellow]")
            return
        if len(parts) < 2:
            chat_console.print("[yellow]Usage: /find <title fragment>[/yellow]")
            return
        fragment = " ".join(parts[1:])
        ok, matches = find_org_by_title(org_roam_dir, fragment)
        if not ok:
            chat_console.print(f"[red]Could not search: {org_roam_dir}[/red]")
        elif not matches:
            chat_console.print(f"[dim]No org files found matching '{fragment}'.[/dim]")
        else:
            chat_console.print(f"\n[bold]Found {len(matches)} file(s):[/bold]")
            for p in matches:
                chat_console.print(f"  [cyan]{p}[/cyan]")
            chat_console.print()
        return

    if cmd == "/task":
        if journal_dir is None:
            chat_console.print("[yellow]/task requires org-roam dir (start with 'journaler chat')[/yellow]")
            return
        if len(parts) < 2:
            chat_console.print("[yellow]Usage: /task <description>[/yellow]")
            return
        description = " ".join(parts[1:])
        ok, msg = add_todo_to_journal(journal_dir, description)
        color = "green" if ok else "red"
        chat_console.print(f"[{color}]{escape(msg)}[/{color}]")
        return

    if cmd == "/done":
        if journal_dir is None:
            chat_console.print("[yellow]/done requires org-roam dir (start with 'journaler chat')[/yellow]")
            return
        if len(parts) < 2:
            chat_console.print("[yellow]Usage: /done <description fragment>[/yellow]")
            return
        fragment = " ".join(parts[1:])
        ok, msg = mark_done_in_journal(journal_dir, fragment)
        color = "green" if ok else "red"
        chat_console.print(f"[{color}]{escape(msg)}[/{color}]")
        return

    if cmd == "/note":
        if journal_dir is None:
            chat_console.print("[yellow]/note requires org-roam dir (start with 'journaler chat')[/yellow]")
            return
        # Syntax: /note <heading> :: <text>
        rest = raw[len("/note"):].strip()
        if " :: " not in rest:
            chat_console.print("[yellow]Usage: /note <heading> :: <text>[/yellow]")
            return
        heading, _, text = rest.partition(" :: ")
        heading = heading.strip()
        text = text.strip()
        if not heading or not text:
            chat_console.print("[yellow]Usage: /note <heading> :: <text>[/yellow]")
            return
        from datetime import datetime as _dt
        from engineering_hub.journaler.org_writer import _today_journal_path, _create_journal_file
        today_path = _today_journal_path(journal_dir)
        _create_journal_file(today_path)
        ok, msg = append_to_heading(today_path, heading, text, create_heading_if_missing=True)
        color = "green" if ok else "red"
        chat_console.print(f"[{color}]{escape(msg)}[/{color}]")
        return

    chat_console.print(
        f"[yellow]Unknown command '{cmd}'. Type /help for available commands.[/yellow]"
    )


def cmd_journaler(args: argparse.Namespace) -> int:
    """Journaler daemon commands."""
    sub = getattr(args, "journaler_command", None)
    if sub is None:
        console.print(
            "[yellow]Usage: engineering-hub journaler"
            " {start|chat|briefing|status|scan|clear|download}[/yellow]"
        )
        return 1

    settings = load_settings(args.config)
    model_path = settings.resolved_journaler_model_path

    needs_model = sub in ("start", "chat") or (
        sub == "briefing" and not getattr(args, "latest", False)
    )
    if not model_path:
        model_path = DEFAULT_JOURNALER_MLX_MODEL_ID
        if needs_model:
            console.print(
                "[dim]No [cyan]journaler.model_path[/cyan] or [cyan]mlx.model_path[/cyan] in config; "
                f"using default Hugging Face id [cyan]{model_path}[/cyan].[/dim]"
            )

    if needs_model and not _is_model_cached(model_path):
        console.print(
            f"[yellow]Model [cyan]{model_path}[/cyan] is not in the local HF cache.[/yellow]\n"
            "  Run [bold cyan]engineering-hub journaler download[/bold cyan] first to pre-fetch it\n"
            "  (recommended — ~17GB for a 32B 4-bit checkpoint), or continue and it will\n"
            "  download automatically now (may take several minutes on a slow connection)."
        )

    from engineering_hub.journaler.context import JournalContext
    from engineering_hub.journaler.daemon import JournalerConfig, generate_briefing_now, run_daemon

    memory_service = None
    if settings.memory_enabled:
        try:
            from engineering_hub.memory.service import MemoryService

            memory_service = MemoryService.from_workspace(
                workspace_dir=settings.workspace_dir,
                ollama_host=settings.ollama_host,
                ollama_model=settings.ollama_embed_model,
                enabled=settings.memory_enabled,
            )
        except Exception as exc:
            console.print(f"[yellow]Warning:[/yellow] Memory service unavailable: {exc}")

    config = JournalerConfig(
        model_path=model_path,
        org_roam_dir=settings.org_journal_dir.parent,
        workspace_dir=settings.workspace_dir,
        state_dir=settings.journaler_state_dir,
        scan_interval_min=settings.journaler_scan_interval_min,
        briefing_enabled=settings.journaler_briefing_enabled,
        briefing_time=settings.journaler_briefing_time,
        briefing_output_dir=settings.journaler_briefing_output_dir,
        chat_enabled=settings.journaler_chat_enabled,
        chat_host=settings.journaler_chat_host,
        chat_port=settings.journaler_chat_port,
        slack_enabled=settings.journaler_slack_enabled,
        slack_webhook_url=settings.journaler_slack_webhook_url,
        max_conversation_history=settings.journaler_max_conversation_history,
        max_tokens=settings.journaler_max_tokens,
        temp=settings.journaler_temp,
        top_p=settings.journaler_top_p,
        min_p=settings.journaler_min_p,
        repetition_penalty=settings.journaler_repetition_penalty,
        memory_service=memory_service,
    )

    if sub == "start":
        console.print("[bold green]Starting Journaler daemon...[/bold green]")
        console.print(f"  Model: {config.model_path}")
        console.print(f"  Org-roam: {config.org_roam_dir}")
        console.print(f"  Scan interval: {config.scan_interval_min}min")
        if config.briefing_enabled:
            console.print(f"  Briefing at: {config.briefing_time}")
        if config.chat_enabled:
            console.print(f"  Chat: http://{config.chat_host}:{config.chat_port}")
        try:
            run_daemon(config)
        except KeyboardInterrupt:
            console.print("\n[yellow]Journaler stopped.[/yellow]")
        except Exception as exc:
            console.print(f"[red]Error:[/red] {exc}")
            return 1
        return 0

    elif sub == "chat":
        from engineering_hub.journaler.engine import ConversationalMLXBackend, ConversationEngine
        from engineering_hub.journaler.prompts import format_system_prompt, load_system_prompt

        console.print("[bold]Loading Journaler model for interactive chat...[/bold]")
        backend = ConversationalMLXBackend(
            model_path=config.model_path,
            temp=config.temp,
            top_p=config.top_p,
            min_p=config.min_p,
            repetition_penalty=config.repetition_penalty,
        )
        ctx = JournalContext(
            org_roam_dir=config.org_roam_dir,
            workspace_dir=config.workspace_dir,
            memory_service=config.memory_service,
            state_dir=config.state_dir,
        )
        ctx.scan()

        system_template = load_system_prompt(config.state_dir)
        from engineering_hub.journaler.prompts import build_workspace_layout
        workspace_map = build_workspace_layout(config.org_roam_dir, config.workspace_dir)
        system_prompt = format_system_prompt(
            system_template,
            ctx.get_current_context(),
            workspace_map=workspace_map,
        )
        engine = ConversationEngine(
            backend=backend,
            system_prompt=system_prompt,
            log_dir=config.state_dir,
            max_history=config.max_conversation_history,
            max_tokens=config.max_tokens,
            pressure_config=config.get_pressure_config(),
            model_context_window=config.model_context_window,
        )

        console.print(
            "[green]Journaler ready. Type your questions (Ctrl-C, /exit, or exit to leave).[/green]\n"
            "[dim]Tip: /load <path> to inject a file, /task <desc> to add a TODO, /help for all commands.[/dim]\n"
            "[dim]Context: /status, /budget, /topic — Clear: /clear, /clear --summarize, /clear --hard[/dim]\n"
        )
        log = logging.getLogger(__name__)
        try:
            while True:
                try:
                    user_input = input("You: ").strip()
                except (KeyboardInterrupt, EOFError):
                    raise
                if not user_input:
                    continue
                if user_input.lower() in ("exit", "quit", ":q"):
                    break
                if user_input.startswith("/"):
                    try:
                        _handle_chat_slash_command(
                            user_input, engine, console, org_roam_dir=config.org_roam_dir
                        )
                    except JournalerChatExit:
                        break
                    except Exception as exc:
                        log.exception("Journaler slash command failed")
                        console.print(
                            f"[red]Command failed:[/red] {escape(str(exc))}\n"
                            "[dim]Type /help for commands. You can keep chatting.[/dim]\n"
                        )
                    continue
                try:
                    response = engine.chat(user_input)
                except Exception as exc:
                    log.exception("Journaler chat turn failed")
                    console.print(
                        f"\n[red]Something went wrong:[/red] {escape(str(exc))}\n"
                        "[dim]You can try again, or type /exit to leave.[/dim]\n"
                    )
                    continue
                console.print(f"\n[bold]Journaler:[/bold] {escape(response)}\n")
        except (KeyboardInterrupt, EOFError):
            pass
        console.print("\n[dim]Chat ended.[/dim]")
        return 0

    elif sub == "briefing":
        if args.latest:
            briefing_dir = config.briefing_output_dir or (config.state_dir / "briefings")
            if briefing_dir and briefing_dir.exists():
                files = sorted(briefing_dir.glob("*.md"), reverse=True)
                if files:
                    console.print(files[0].read_text(encoding="utf-8"))
                    return 0
            console.print("[dim]No briefings available yet.[/dim]")
            return 0

        console.print("[bold]Generating briefing on demand...[/bold]")
        try:
            briefing = generate_briefing_now(config)
            console.print(f"\n{escape(briefing)}")
        except Exception as exc:
            console.print(f"[red]Error:[/red] {exc}")
            return 1
        return 0

    elif sub == "status":
        state_file = config.state_dir / "state.json"
        if state_file.exists():
            import json

            data = json.loads(state_file.read_text(encoding="utf-8"))
            table = Table(title="Journaler Status")
            table.add_column("Setting", style="cyan")
            table.add_column("Value", style="green")
            table.add_row("Model", config.model_path)
            table.add_row("State Dir", str(config.state_dir))
            table.add_row("Last Scan", data.get("last_scan", "never"))
            table.add_row("Tracked Files", str(len(data.get("file_mtimes", {}))))
            table.add_row("Chat Endpoint", f"http://{config.chat_host}:{config.chat_port}")
            console.print(table)
        else:
            console.print("[dim]Journaler has not run yet. No state file found.[/dim]")
            console.print(f"  Expected at: {state_file}")
        return 0

    elif sub == "scan":
        console.print("[bold]Running scan...[/bold]")
        ctx = JournalContext(
            org_roam_dir=config.org_roam_dir,
            workspace_dir=config.workspace_dir,
            memory_service=config.memory_service,
            state_dir=config.state_dir,
        )
        snapshot = ctx.scan()
        console.print(f"  Last scan: {snapshot.last_scan}")
        console.print(f"  Pending tasks: {len(snapshot.pending_tasks)}")
        console.print(f"  Completed tasks: {len(snapshot.completed_tasks)}")
        console.print(f"  Changes: {snapshot.change_summary}")
        return 0

    elif sub == "clear":
        from engineering_hub.journaler.context_manager import ClearStrategy
        from engineering_hub.journaler.engine import ConversationalMLXBackend, ConversationEngine

        hard = getattr(args, "hard", False)
        summarize = getattr(args, "summarize", False)

        if hard:
            strategy = ClearStrategy.HARD
        elif summarize:
            strategy = ClearStrategy.SUMMARIZE
        else:
            strategy = ClearStrategy.SOFT

        if strategy == ClearStrategy.SUMMARIZE:
            console.print("[bold]Loading model to compress history before clearing...[/bold]")
            backend = ConversationalMLXBackend(
                model_path=config.model_path,
                temp=config.temp,
                top_p=config.top_p,
                min_p=config.min_p,
                repetition_penalty=config.repetition_penalty,
            )
        else:
            backend = None  # type: ignore[assignment]

        # Build a temporary engine pointed at the existing state dir
        # (history is loaded from conversation.jsonl — this is a best-effort
        # in-process clear; primary effect is the JSONL audit trail)
        if backend is not None:
            engine = ConversationEngine(
                backend=backend,
                system_prompt="",
                log_dir=config.state_dir,
                max_tokens=config.max_tokens,
                pressure_config=config.get_pressure_config(),
                model_context_window=config.model_context_window,
            )
            msg = engine.clear(strategy)
        else:
            from engineering_hub.journaler.context_manager import (
                ConversationHistory,
                ContextCompressor,
                execute_clear,
            )
            history = ConversationHistory()
            compressor = ContextCompressor(engine_call=lambda p, t: "")
            msg = execute_clear(strategy, history, compressor)

        console.print(f"[green]{escape(msg)}[/green]")

        if strategy == ClearStrategy.HARD:
            state_file = config.state_dir / "state.json"
            context_cache = config.state_dir / "context_cache.json"
            for f in (state_file, context_cache):
                if f.exists():
                    f.unlink()
                    console.print(f"[dim]Removed {f.name}[/dim]")

        return 0

    elif sub == "download":
        resolved_local = Path(model_path).expanduser()
        if resolved_local.is_dir():
            console.print(f"[green]Model is a local directory — nothing to download:[/green] {resolved_local}")
            return 0

        if _is_model_cached(model_path):
            try:
                from huggingface_hub import try_to_load_from_cache

                cached_path = try_to_load_from_cache(model_path, "config.json")
                parent = Path(cached_path).parent if cached_path else "HF cache"
            except Exception:
                parent = "HF cache"
            console.print(f"[green]Model already cached:[/green] {parent}")
            return 0

        console.print(f"[bold]Downloading Journaler model:[/bold] {model_path}")
        console.print("[dim](This may take several minutes for a ~34GB 8-bit checkpoint)[/dim]\n")
        try:
            from huggingface_hub import snapshot_download

            local_dir = snapshot_download(repo_id=model_path)
            console.print(f"\n[bold green]Download complete.[/bold green] Cached at: {local_dir}")
        except ImportError:
            console.print(
                "[red]Error:[/red] huggingface_hub is not installed. "
                "Install with: pip install 'engineering-hub[mlx]'"
            )
            return 1
        except Exception as exc:
            console.print(f"[red]Download failed:[/red] {exc}")
            return 1
        return 0

    console.print("[yellow]Unknown journaler command.[/yellow]")
    return 1


def cmd_memory(args: argparse.Namespace) -> int:
    """Query or inspect the local memory database."""
    setup_logging(args.verbose)
    settings = load_settings(args.config)

    from engineering_hub.memory import MemoryService

    svc = MemoryService.from_workspace(
        workspace_dir=settings.workspace_dir,
        ollama_host=settings.ollama_host,
        ollama_model=settings.ollama_embed_model,
        enabled=settings.memory_enabled,
    )

    if args.memory_command == "stats":
        stats = svc.get_stats()
        console.print_json(data=stats)

    elif args.memory_command == "search":
        results = svc.search(query=args.query, k=args.k)
        if not results:
            console.print("[dim]No results.[/dim]")
        for r in results:
            console.print(r.as_context_snippet())
            console.print()

    elif args.memory_command == "recent":
        rows = svc.browse_recent(limit=args.limit)
        if not rows:
            console.print("[dim]No memories stored yet.[/dim]")
        for r in rows:
            date_str = (r.get("created_at") or "")[:10]
            console.print(f"[cyan]{date_str}[/cyan] [{r['source']}] {r['content'][:120]}")

    else:
        console.print("[yellow]Usage: engineering-hub memory {stats|search|recent}[/yellow]")

    svc.db.close()
    return 0


def cmd_load(args: argparse.Namespace) -> int:
    """Load a file or directory into the persistent memory store."""
    setup_logging(args.verbose)
    settings = load_settings(args.config)

    path = Path(args.path).expanduser().resolve()
    if not path.exists():
        console.print(f"[red]Error:[/red] Path not found: {path}")
        return 1

    from engineering_hub.memory.service import MemoryService

    svc = MemoryService.from_workspace(
        workspace_dir=settings.workspace_dir,
        ollama_host=settings.ollama_host,
        ollama_model=settings.ollama_embed_model,
        enabled=settings.memory_enabled,
    )

    if not settings.memory_enabled:
        console.print(
            "[yellow]Warning:[/yellow] Memory is disabled in config. "
            "Enable it with `memory.enabled: true`."
        )
        return 1

    extra_tags: list[str] = list(args.tag) if args.tag else []
    project_id: int | None = args.project

    def _collect_files(root: Path) -> list[Path]:
        if root.is_file():
            return [root]
        pattern = "**/*" if args.recursive else "*"
        return sorted(
            p for p in root.glob(pattern)
            if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
        )

    candidates = _collect_files(path)
    if not candidates:
        console.print(
            f"[yellow]No supported files found at:[/yellow] {path}\n"
            f"Supported extensions: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )
        return 1

    console.print(f"[bold]Loading {len(candidates)} file(s) into memory...[/bold]")

    stored = 0
    skipped = 0
    for file_path in candidates:
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            console.print(f"  [red]✗[/red] {file_path.name}: {exc}")
            skipped += 1
            continue

        tags = ["manual", f"file:{file_path.name}"] + extra_tags
        row_id = svc.capture(
            content=content,
            source="manual",
            project_id=project_id,
            tags=tags,
        )
        if row_id is not None:
            size_kb = len(content) / 1024
            console.print(f"  [green]✓[/green] {file_path.name} ({size_kb:.1f} KB) → memory #{row_id}")
            stored += 1
        else:
            console.print(f"  [yellow]![/yellow] {file_path.name}: stored without embedding (Ollama unavailable?)")
            stored += 1

    svc.db.close()
    console.print(
        f"\n[bold green]Done.[/bold green] {stored} file(s) captured, {skipped} skipped."
    )
    return 0 if skipped == 0 else 1


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        prog="engineering-hub",
        description="Agent-first workspace for acoustic engineering consulting",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    parser.add_argument(
        "-c", "--config",
        type=Path,
        help="Path to config file",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # start command
    start_parser = subparsers.add_parser("start", help="Start the orchestrator")

    # status command
    status_parser = subparsers.add_parser("status", help="Show status")

    # run-once command
    run_parser = subparsers.add_parser(
        "run-once",
        help="Process pending tasks once and exit",
    )

    # init command
    init_parser = subparsers.add_parser("init", help="Initialize workspace")
    init_parser.add_argument(
        "-w", "--workspace",
        type=str,
        default="~/org-roam/engineering-hub",
        help="Workspace directory",
    )
    init_parser.add_argument(
        "-f", "--force",
        action="store_true",
        help="Overwrite existing files",
    )

    # mcp-server command
    mcp_parser = subparsers.add_parser("mcp-server", help="Start local MCP memory server")
    mcp_parser.add_argument(
        "--transport", "-t",
        choices=["stdio", "http"],
        default="stdio",
        help="MCP transport: stdio (default, for Claude Desktop/Cursor) or http (network)",
    )
    mcp_parser.add_argument("--host", default="127.0.0.1", help="Bind address (http only)")
    mcp_parser.add_argument("--port", type=int, default=8000, help="Port number (http only)")

    # weekly-review command
    weekly_parser = subparsers.add_parser(
        "weekly-review",
        help="Run the weekly reviewer agent and produce a synthesis report",
    )
    weekly_parser.add_argument(
        "--days",
        type=int,
        default=7,
        metavar="N",
        help="Number of days to look back (default: 7)",
    )
    weekly_parser.add_argument(
        "--focus",
        type=str,
        default=None,
        metavar="TEXT",
        help="Optional focus area to weight the analysis toward",
    )
    weekly_parser.add_argument(
        "--output",
        type=str,
        default=None,
        metavar="PATH",
        help="Override output file path (default: outputs/reviews/weekly-YYYY-WNN.md)",
    )

    # journaler command
    journaler_parser = subparsers.add_parser(
        "journaler", help="Journaler ambient listener daemon"
    )
    journaler_sub = journaler_parser.add_subparsers(dest="journaler_command")
    journaler_sub.add_parser("start", help="Start the Journaler daemon")
    journaler_sub.add_parser("chat", help="Interactive chat with the Journaler model")

    briefing_p = journaler_sub.add_parser("briefing", help="Generate or view a briefing")
    briefing_p.add_argument(
        "--latest", action="store_true", help="View the latest briefing instead of generating"
    )

    journaler_sub.add_parser("status", help="Show Journaler status")
    journaler_sub.add_parser("scan", help="Run a single org-roam scan")
    journaler_sub.add_parser(
        "download", help="Pre-download the Journaler model to local HF cache"
    )

    clear_p = journaler_sub.add_parser(
        "clear", help="Clear conversation history (soft by default)"
    )
    clear_p.add_argument(
        "--hard",
        action="store_true",
        help="Full reset: clear conversation and wipe scan state",
    )
    clear_p.add_argument(
        "--summarize",
        action="store_true",
        help="Compress history into a summary before clearing (requires model load)",
    )

    # load command
    load_parser = subparsers.add_parser(
        "load",
        help="Load a file or directory into the persistent memory store",
    )
    load_parser.add_argument(
        "path",
        help="File or directory to load",
    )
    load_parser.add_argument(
        "-r", "--recursive",
        action="store_true",
        help="Recurse into subdirectories (directory mode only)",
    )
    load_parser.add_argument(
        "--project",
        type=int,
        default=None,
        metavar="PROJECT_ID",
        help="Associate loaded content with a Django project ID",
    )
    load_parser.add_argument(
        "--tag",
        action="append",
        metavar="TAG",
        help="Extra tag to attach (can be used multiple times)",
    )

    # memory command
    memory_parser = subparsers.add_parser("memory", help="Inspect the local memory database")
    memory_sub = memory_parser.add_subparsers(dest="memory_command")

    memory_sub.add_parser("stats", help="Show memory statistics")

    recent_p = memory_sub.add_parser("recent", help="Browse recent memories")
    recent_p.add_argument("--limit", type=int, default=20, help="Number of entries")

    search_p = memory_sub.add_parser("search", help="Semantic search")
    search_p.add_argument("query", help="Search query")
    search_p.add_argument("--k", type=int, default=5, help="Max results")

    args = parser.parse_args()
    setup_logging(args.verbose)

    if args.command is None:
        parser.print_help()
        return 0

    commands = {
        "start": cmd_start,
        "status": cmd_status,
        "run-once": cmd_run_once,
        "init": cmd_init,
        "mcp-server": cmd_mcp_server,
        "journaler": cmd_journaler,
        "load": cmd_load,
        "memory": cmd_memory,
        "weekly-review": cmd_weekly_review,
    }

    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
