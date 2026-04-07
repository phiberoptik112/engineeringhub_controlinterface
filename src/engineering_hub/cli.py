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
from engineering_hub.journaler.engine import (
    SUPPORTED_EXTENSIONS,
    ConversationEngine,
    _is_model_cached,
)
from engineering_hub.journaler.model_profiles import (
    JournalerChatModelContext,
    build_journaler_mlx_backend,
    ensure_spec_model_path,
    journaler_slash_model_command,
    resolve_journaler_model_spec,
)
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
    journaler_model_ctx: object | None = None,
) -> None:
    """Intercept and execute a /slash command from the journaler chat loop.

    Recognised commands:
      /model                     Show or switch HF model (profile or path).
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
      /open [today|clear|<path>|<title>]  Set session org-roam target for /edit.
      /edit <heading> :: <text>  Append under a heading in the /open target.
      /exit, /quit               Leave the chat (same as bare exit, quit, or :q).
      /model                     Show model, or switch profile / path (see /help).
      /help                      Show available slash commands.
    """
    from engineering_hub.journaler.context_manager import ClearStrategy
    from engineering_hub.journaler.org_writer import (
        add_todo_to_journal,
        append_to_heading,
        assert_org_path_under_roam,
        find_org_by_title,
        mark_done_in_journal,
    )
    parts = raw.split()
    cmd = parts[0].lower()

    if cmd == "/model":
        if journaler_model_ctx is None:
            chat_console.print(
                "[yellow]/model requires internal context; if you see this, file a bug.[/yellow]"
            )
            return
        msg = journaler_slash_model_command(
            raw,
            settings=journaler_model_ctx.settings,
            model_ctx=journaler_model_ctx,
            engine=engine,
            delegator=None,
        )
        chat_console.print(f"[green]{escape(msg)}[/green]")
        return

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
            "  [cyan]/open[/cyan]                       Show current /edit target\n"
            "  [cyan]/open clear[/cyan]                  Clear /edit target\n"
            "  [cyan]/open today[/cyan]                  Target today's daily journal\n"
            "  [cyan]/open <path>[/cyan]                  Target a .org file under org-roam\n"
            "  [cyan]/open <title fragment>[/cyan]        Target unique #+title: match\n"
            "  [cyan]/edit <heading> :: <text>[/cyan]   Append under a heading in /open target\n"
            "  [cyan]/find <title fragment>[/cyan]       Search org-roam files by title\n"
        ) if org_roam_dir else ""

        chat_console.print(
            "\n[bold cyan]Slash commands:[/bold cyan]\n"
            "  [cyan]/load <path> [-r][/cyan]          Load a file or directory into context\n"
            "                                 (-r / --recursive scans subdirectories)\n"
            "  [cyan]/model[/cyan]                     Show active MLX model / profile\n"
            "  [cyan]/model <profile>[/cyan]           Switch to a named journaler.models profile\n"
            "  [cyan]/model path <id-or-path>[/cyan]   Load a Hugging Face id or local path\n"
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
        engine._sync_loaded_files_budget()
        b = engine.budget
        table = Table(title="Token Budget")
        table.add_column("Component", style="cyan")
        table.add_column("Tokens", justify="right", style="green")
        table.add_row("Context window", f"{b.window_size:,}")
        table.add_row("System prompt", f"{b.system_prompt_tokens:,}")
        table.add_row("Context snapshot", f"{b.context_snapshot_tokens:,}")
        table.add_row("Loaded files", f"{b.loaded_files_tokens:,}")
        table.add_row("Corpus injection", f"{b.corpus_injection_tokens:,}")
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

    if cmd == "/open":
        rest = raw[len("/open"):].strip()
        if not rest:
            t = engine.get_roam_edit_target()
            if t:
                chat_console.print(f"[cyan]Current edit target:[/cyan] {t}")
            else:
                chat_console.print(
                    "[dim]No edit target set. Use /open today, a .org path under org-roam, "
                    "or a unique title fragment.[/dim]"
                )
            return
        if rest.lower() == "clear":
            engine.set_roam_edit_target(None)
            chat_console.print("[green]Edit target cleared.[/green]")
            return
        if org_roam_dir is None:
            chat_console.print(
                "[yellow]/open requires org-roam dir (start with journaler chat / config)[/yellow]"
            )
            return
        if rest.lower() == "today":
            if journal_dir is None:
                chat_console.print(
                    "[yellow]/open today requires org-roam dir (start with journaler chat)[/yellow]"
                )
                return
            from engineering_hub.journaler.org_writer import (
                _create_journal_file,
                _today_journal_path,
            )

            today_path = _today_journal_path(journal_dir)
            _create_journal_file(today_path)
            engine.set_roam_edit_target(today_path)
            chat_console.print(f"[green]Opened today's journal for /edit:[/green] {today_path}")
            return
        path_candidate = Path(rest).expanduser()
        is_path_like = path_candidate.suffix.lower() == ".org" or path_candidate.is_file()
        if is_path_like:
            ok_path, res = assert_org_path_under_roam(path_candidate, org_roam_dir)
            if not ok_path:
                chat_console.print(f"[red]{escape(str(res))}[/red]")
                return
            if isinstance(res, Path):
                engine.set_roam_edit_target(res)
            chat_console.print(f"[green]Opened for /edit:[/green] {res}")
            return
        fragment = rest
        ok_find, matches = find_org_by_title(org_roam_dir, fragment)
        if not ok_find:
            chat_console.print(f"[red]Could not search: {org_roam_dir}[/red]")
            return
        if not matches:
            chat_console.print(f"[dim]No org files found matching title '{fragment}'.[/dim]")
            return
        if len(matches) > 1:
            chat_console.print(
                f"[yellow]{len(matches)} files match '{fragment}'; narrow the title or use a path:[/yellow]"
            )
            for i, p in enumerate(matches, start=1):
                chat_console.print(f"  [cyan]{i}.[/cyan] {p}")
            chat_console.print()
            return
        engine.set_roam_edit_target(matches[0])
        chat_console.print(f"[green]Opened for /edit:[/green] {matches[0]}")
        return

    if cmd == "/edit":
        target = engine.get_roam_edit_target()
        if target is None:
            chat_console.print(
                "[yellow]No edit target. Use /open (today, path, or unique title) first.[/yellow]\n"
                "[dim]Usage: /edit <heading> :: <text>[/dim]"
            )
            return
        rest = raw[len("/edit"):].strip()
        if " :: " not in rest:
            chat_console.print("[yellow]Usage: /edit <heading> :: <text>[/yellow]")
            return
        heading, _, text = rest.partition(" :: ")
        heading = heading.strip()
        text = text.strip()
        if not heading or not text:
            chat_console.print("[yellow]Usage: /edit <heading> :: <text>[/yellow]")
            return
        ok, msg = append_to_heading(target, heading, text, create_heading_if_missing=True)
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
            " {start|chat|briefing|export|status|scan|clear|download}[/yellow]"
        )
        return 1

    settings = load_settings(args.config)

    needs_model = sub in ("start", "chat") or (
        sub == "briefing" and not getattr(args, "latest", False)
    ) or (sub == "export" and getattr(args, "summarize", False))

    try:
        spec = resolve_journaler_model_spec(
            settings,
            cli_model=getattr(args, "journaler_model", None),
            cli_profile=getattr(args, "journaler_profile", None),
        )
    except ValueError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        return 1

    had_empty_path = not (spec.model_path or "").strip()
    spec = ensure_spec_model_path(spec, DEFAULT_JOURNALER_MLX_MODEL_ID)
    model_path = spec.model_path

    if had_empty_path and needs_model:
        console.print(
            "[dim]No [cyan]journaler.model_path[/cyan], [cyan]mlx.model_path[/cyan], or "
            "[cyan]journaler.models[/cyan] model_path in config; "
            f"using default Hugging Face id [cyan]{model_path}[/cyan].[/dim]"
        )

    if needs_model and not _is_model_cached(model_path):
        console.print(
            f"[yellow]Model [cyan]{model_path}[/cyan] is not in the local HF cache.[/yellow]\n"
            "  Run [bold cyan]engineering-hub journaler download[/bold cyan] first to pre-fetch it\n"
            "  (recommended — ~17GB for a 32B 4-bit checkpoint), or continue and it will\n"
            "  download automatically now (may take several minutes on a slow connection)."
        )

    from engineering_hub.corpus_service_factory import build_corpus_service_from_settings
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

    corpus_service = build_corpus_service_from_settings(settings)

    config = JournalerConfig(
        model_path=spec.model_path,
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
        max_tokens=spec.max_tokens,
        model_context_window=spec.model_context_window,
        temp=spec.temp,
        top_p=spec.top_p,
        min_p=spec.min_p,
        repetition_penalty=spec.repetition_penalty,
        enable_thinking=spec.enable_thinking,
        mlx_backend=spec.mlx_backend,
        memory_service=memory_service,
        corpus_service=corpus_service,
        load_max_context_fraction=settings.journaler_load_max_context_fraction,
        load_max_chars_absolute=settings.journaler_load_max_chars_absolute,
        load_min_chars=settings.journaler_load_min_chars,
        load_slack_tokens=settings.journaler_load_slack_tokens,
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
            run_daemon(config, settings)
        except KeyboardInterrupt:
            console.print("\n[yellow]Journaler stopped.[/yellow]")
        except Exception as exc:
            console.print(f"[red]Error:[/red] {exc}")
            return 1
        return 0

    elif sub == "chat":
        from engineering_hub.journaler.chat_repl import configure_chat_readline, prompt_line
        from engineering_hub.journaler.engine import ConversationEngine
        from engineering_hub.journaler.prompts import format_system_prompt, load_system_prompt

        chat_model_ctx = JournalerChatModelContext(settings, spec)
        console.print("[bold]Loading Journaler model for interactive chat...[/bold]")
        backend = build_journaler_mlx_backend(chat_model_ctx.spec)
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
            corpus_service=config.corpus_service,
            load_file_budget=config.get_load_file_budget(),
        )

        configure_chat_readline(
            config.state_dir,
            conversation_jsonl=config.state_dir / "conversation.jsonl",
        )

        transcript_path = config.state_dir / "conversation.jsonl"
        max_hist = config.max_conversation_history
        console.print(
            "[green]Journaler ready. "
            "Type your questions (Ctrl-C, /exit, or exit to leave).[/green]\n"
            "[dim]Tip: /model to switch HF profile or path; /load <path> for files; "
            "/help for commands.[/dim]\n"
            "[dim]Context: /status, /budget, /topic — "
            "Clear: /clear, /clear --summarize, /clear --hard[/dim]\n"
            "[dim]Input: Up/Down recall previous lines (saved under .journaler). "
            f"Full transcript: {transcript_path}. "
            f"Longer model memory: raise journaler.max_conversation_history "
            f"(now {max_hist}).[/dim]\n"
        )
        log = logging.getLogger(__name__)
        try:
            while True:
                try:
                    user_input = prompt_line("You: ")
                except (KeyboardInterrupt, EOFError):
                    raise
                if not user_input:
                    continue
                if user_input.lower() in ("exit", "quit", ":q"):
                    break
                if user_input.startswith("/"):
                    try:
                        _handle_chat_slash_command(
                            user_input,
                            engine,
                            console,
                            org_roam_dir=config.org_roam_dir,
                            journaler_model_ctx=chat_model_ctx,
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
            backend = build_journaler_mlx_backend(spec)
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
                load_file_budget=config.get_load_file_budget(),
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

    elif sub == "export":
        from engineering_hub.journaler.conversation_export import (
            build_summarize_prompt,
            load_transcript,
            postprocess_model_org,
            render_raw_org,
            transcript_to_plain_text,
        )
        from engineering_hub.journaler.engine import ConversationEngine
        from engineering_hub.journaler.org_writer import (
            append_to_heading,
            create_org_node,
            find_org_by_title,
        )

        note_path_str = getattr(args, "note", None)
        find_title = getattr(args, "find_title", None)
        new_node_title = getattr(args, "new_node", None)
        summarize = getattr(args, "summarize", False)
        heading = (getattr(args, "heading", None) or "Journaler export").strip() or "Journaler export"
        jsonl_override = getattr(args, "jsonl", None)
        output_path = getattr(args, "output", None)
        export_format = getattr(args, "export_format", None) or "raw"

        if note_path_str and find_title:
            console.print("[red]Error:[/red] Use either --note or --find-title, not both.")
            return 1
        if new_node_title and (note_path_str or find_title):
            console.print(
                "[red]Error:[/red] --new-node cannot be combined with --note or --find-title."
            )
            return 1

        jsonl_path = (
            Path(jsonl_override).expanduser().resolve()
            if jsonl_override
            else settings.journaler_state_dir / "conversation.jsonl"
        )

        if not jsonl_path.is_file():
            console.print(f"[yellow]No transcript at {jsonl_path}[/yellow]")
            return 0

        turns = load_transcript(jsonl_path)
        if not turns:
            console.print("[dim]Transcript is empty; nothing to export.[/dim]")
            return 0

        if summarize:
            console.print("[bold]Loading model for summarized export...[/bold]")
            backend = build_journaler_mlx_backend(spec)
            engine = ConversationEngine(
                backend=backend,
                system_prompt="You format chat transcripts into Emacs org mode.",
                log_dir=config.state_dir,
                max_tokens=config.max_tokens,
                pressure_config=config.get_pressure_config(),
                model_context_window=config.model_context_window,
                corpus_service=config.corpus_service,
                load_file_budget=config.get_load_file_budget(),
            )
            prompt = build_summarize_prompt(transcript_to_plain_text(turns))
            cap = min(1200, config.max_tokens)
            raw_out = engine._raw_complete(prompt, cap)
            body = postprocess_model_org(raw_out)
        else:
            if export_format != "raw":
                console.print(f"[red]Error:[/red] Unknown export format {export_format!r}.")
                return 1
            body = render_raw_org(turns)

        target_note_resolved: Path | None = None
        if find_title:
            ok, matches = find_org_by_title(config.org_roam_dir, find_title)
            if not ok:
                console.print(
                    f"[red]Error:[/red] Could not search org-roam in {config.org_roam_dir}"
                )
                return 1
            if len(matches) == 0:
                console.print(
                    f"[yellow]No note with #+title containing {find_title!r}[/yellow]"
                )
                return 1
            if len(matches) > 1:
                console.print(
                    f"[red]Error:[/red] Title fragment {find_title!r} matched multiple notes:"
                )
                for p in matches:
                    console.print(f"  {p}")
                return 1
            target_note_resolved = matches[0]

        if note_path_str:
            target_note_resolved = Path(note_path_str).expanduser().resolve()

        wrote_file = False
        if new_node_title:
            ok, msg = create_org_node(config.org_roam_dir, new_node_title, body=body)
            if ok:
                console.print(f"[green]{escape(msg)}[/green]")
            else:
                console.print(f"[red]{escape(msg)}[/red]")
                return 1
            wrote_file = True
        elif target_note_resolved is not None:
            ok, msg = append_to_heading(
                target_note_resolved,
                heading,
                body,
                create_heading_if_missing=True,
            )
            color = "green" if ok else "red"
            console.print(f"[{color}]{escape(msg)}[/{color}]")
            if not ok:
                return 1
            wrote_file = True

        if output_path:
            outp = Path(output_path).expanduser().resolve()
            outp.parent.mkdir(parents=True, exist_ok=True)
            outp.write_text(body, encoding="utf-8")
            console.print(f"[dim]Wrote {outp}[/dim]")
            wrote_file = True

        if not wrote_file:
            sys.stdout.write(body)

        return 0

    console.print("[yellow]Unknown journaler command.[/yellow]")
    return 1


def cmd_template(args: argparse.Namespace) -> int:
    """Template analysis, listing, and report drafting commands."""
    sub = getattr(args, "template_command", None)
    if sub is None:
        console.print(
            "[yellow]Usage: engineering-hub template {analyze|list|draft}[/yellow]"
        )
        return 1

    settings = load_settings(args.config)

    if sub == "analyze":
        from engineering_hub.templates.analyzer import TemplateAnalyzer

        docx_dir = Path(args.docx_dir).expanduser().resolve()
        if not docx_dir.is_dir():
            console.print(f"[red]Error:[/red] Directory not found: {docx_dir}")
            return 1

        output_dir = settings.resolved_templates_dir / args.name.lower().replace(" ", "-")
        console.print(f"[bold]Analyzing .docx files in: {docx_dir}[/bold]")

        try:
            analyzer = TemplateAnalyzer(docx_dir, name=args.name)
            skeleton = analyzer.analyze(output_dir)
        except FileNotFoundError as exc:
            console.print(f"[red]Error:[/red] {exc}")
            return 1
        except Exception as exc:
            console.print(f"[red]Error:[/red] Analysis failed: {exc}")
            return 1

        console.print(f"[bold green]Template analysis complete![/bold green]")
        console.print(f"  Name: {skeleton.name}")
        console.print(f"  Source docs: {skeleton.source_doc_count}")
        console.print(f"  Sections: {len(skeleton.sections)}")
        console.print(f"  Table patterns: {len(skeleton.table_patterns)}")
        console.print(f"  Styles: {len(skeleton.styles)}")
        console.print(f"  Output: {output_dir}")
        return 0

    elif sub == "list":
        templates_dir = settings.resolved_templates_dir
        if not templates_dir.exists():
            console.print("[dim]No templates directory found.[/dim]")
            console.print(f"  Expected at: {templates_dir}")
            console.print(
                "  Run [bold cyan]engineering-hub template analyze <dir>[/bold cyan] to create one."
            )
            return 0

        from engineering_hub.templates.models import ReportSkeleton

        skeletons_found = 0
        table = Table(title="Available Report Templates")
        table.add_column("Name", style="cyan")
        table.add_column("Source Docs", justify="right")
        table.add_column("Sections", justify="right")
        table.add_column("Tables", justify="right")
        table.add_column("Path", style="dim")

        for skeleton_file in sorted(templates_dir.rglob("skeleton.json")):
            try:
                sk = ReportSkeleton.load(skeleton_file)
                table.add_row(
                    sk.name,
                    str(sk.source_doc_count),
                    str(len(sk.sections)),
                    str(len(sk.table_patterns)),
                    str(skeleton_file.parent.relative_to(templates_dir)),
                )
                skeletons_found += 1
            except Exception as exc:
                console.print(f"[yellow]Warning:[/yellow] Could not load {skeleton_file}: {exc}")

        if skeletons_found:
            console.print(table)
        else:
            console.print("[dim]No template skeletons found.[/dim]")
            console.print(
                "  Run [bold cyan]engineering-hub template analyze <dir>[/bold cyan] to create one."
            )
        return 0

    elif sub == "draft":
        from engineering_hub.templates.assembler import ReportAssembler
        from engineering_hub.templates.models import ReportSkeleton
        from engineering_hub.templates.org_context import parse_org_note

        skeleton_name = args.skeleton.lower().replace(" ", "-")
        skeleton_path = settings.resolved_templates_dir / skeleton_name / "skeleton.json"
        if not skeleton_path.exists():
            console.print(f"[red]Error:[/red] Skeleton not found: {skeleton_path}")
            console.print("  Run [bold cyan]engineering-hub template list[/bold cyan] to see available templates.")
            return 1

        skeleton = ReportSkeleton.load(skeleton_path)

        project_note = Path(args.project_note).expanduser().resolve()
        if not project_note.exists():
            console.print(f"[red]Error:[/red] Project note not found: {project_note}")
            return 1

        console.print(f"[bold]Drafting report from template: {skeleton.name}[/bold]")
        console.print(f"  Project note: {project_note}")

        context = parse_org_note(project_note)
        context.metadata["template_skeleton_block"] = skeleton.format_for_agent()
        context.metadata["template_reference_docx"] = skeleton.reference_docx_path

        from engineering_hub.context.formatters import ContextFormatter
        from engineering_hub.core.constants import AgentType

        formatted_context = ContextFormatter.format(context, AgentType.TECHNICAL_WRITER)

        if err := _validate_llm_settings(settings):
            return err

        from engineering_hub.agents.backends import create_backend
        from engineering_hub.agents.worker import AgentWorker
        from engineering_hub.core.models import ParsedTask

        backend = create_backend(settings)
        worker = AgentWorker(
            backend=backend,
            prompts_dir=settings.prompts_dir,
            output_dir=settings.output_dir,
        )

        task = ParsedTask(
            agent="technical-writer",
            status="PENDING",
            project_id=context.project.id or None,
            description=f"Draft {skeleton.name} report for {context.project.title}",
            start_line=0,
            end_line=0,
            raw_block="",
        )

        console.print("[bold]Running technical writer agent...[/bold]")
        try:
            result = worker.execute(task, formatted_context)
        except Exception as exc:
            console.print(f"[red]Error:[/red] Agent execution failed: {exc}")
            return 1

        if not result.success:
            console.print(f"[red]Error:[/red] {result.error_message}")
            return 1

        md_output = result.agent_response or ""
        console.print(f"[green]Markdown draft generated ({len(md_output)} chars)[/green]")

        if args.output:
            output_path = Path(args.output).expanduser()
        else:
            output_path = settings.output_dir / "docs" / f"{skeleton_name}-draft.docx"

        if output_path.suffix.lower() == ".docx":
            assembler = ReportAssembler(skeleton)
            assembler.assemble(md_output, output_path)
            console.print("[bold green]Report assembled![/bold green]")
            console.print(f"  Output: {output_path}")

            md_path = output_path.with_suffix(".md")
            md_path.write_text(md_output, encoding="utf-8")
            console.print(f"  Markdown: {md_path}")
        else:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(md_output, encoding="utf-8")
            console.print("[bold green]Draft saved![/bold green]")
            console.print(f"  Output: {output_path}")

        return 0

    console.print("[yellow]Unknown template command.[/yellow]")
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

    # template command
    template_parser = subparsers.add_parser(
        "template", help="Report template analysis and drafting"
    )
    template_sub = template_parser.add_subparsers(dest="template_command")

    analyze_p = template_sub.add_parser(
        "analyze",
        help="Analyze a directory of .docx files and produce a report skeleton",
    )
    analyze_p.add_argument(
        "docx_dir",
        help="Directory containing .docx report files to analyze",
    )
    analyze_p.add_argument(
        "--name",
        default="Report",
        metavar="NAME",
        help="Name for the template (default: Report)",
    )

    template_sub.add_parser("list", help="List available report template skeletons")

    draft_p = template_sub.add_parser(
        "draft",
        help="Draft a report using a template skeleton and org-roam project note",
    )
    draft_p.add_argument(
        "skeleton",
        help="Template skeleton name (as shown by 'template list')",
    )
    draft_p.add_argument(
        "--project-note",
        required=True,
        metavar="ORG_FILE",
        help="Path to an org-roam note with project context",
    )
    draft_p.add_argument(
        "--output",
        type=str,
        default=None,
        metavar="PATH",
        help="Output file path (.docx for assembled report, .md for markdown only)",
    )

    # journaler command
    journaler_parser = subparsers.add_parser(
        "journaler", help="Journaler ambient listener daemon"
    )
    journaler_parser.add_argument(
        "--profile",
        dest="journaler_profile",
        default=None,
        metavar="NAME",
        help="Use named journaler.models profile (overrides journaler.model_profile)",
    )
    journaler_parser.add_argument(
        "--model",
        dest="journaler_model",
        default=None,
        metavar="ID_OR_PATH",
        help="Hugging Face model id or local path (highest precedence)",
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

    export_p = journaler_sub.add_parser(
        "export",
        help="Export chat transcript from conversation.jsonl to org (-o, --note, or --new-node)",
    )
    export_p.add_argument(
        "--jsonl",
        type=str,
        default=None,
        metavar="PATH",
        help="Transcript JSONL (default: workspace .journaler/conversation.jsonl)",
    )
    export_p.add_argument(
        "--format",
        dest="export_format",
        choices=["raw"],
        default="raw",
        help="Export format (default: raw per-turn org)",
    )
    export_p.add_argument(
        "--summarize",
        action="store_true",
        help="Use MLX to emit * Summary and * Open TODOs org sections (loads model)",
    )
    export_p.add_argument(
        "-o",
        "--output",
        type=str,
        default=None,
        metavar="PATH",
        help="Write export body to this file",
    )
    export_p.add_argument(
        "--note",
        type=str,
        default=None,
        metavar="PATH",
        help="Append export under --heading in this existing .org file",
    )
    export_p.add_argument(
        "--heading",
        type=str,
        default="Journaler export",
        metavar="TEXT",
        help="Org heading for --note append (default: Journaler export)",
    )
    export_p.add_argument(
        "--find-title",
        dest="find_title",
        type=str,
        default=None,
        metavar="FRAGMENT",
        help="Resolve single org-roam note by #+title substring (cf. org_journal_dir parent)",
    )
    export_p.add_argument(
        "--new-node",
        dest="new_node",
        type=str,
        default=None,
        metavar="TITLE",
        help="Create a new org-roam node with this #+title and export as body",
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
        "template": cmd_template,
        "journaler": cmd_journaler,
        "load": cmd_load,
        "memory": cmd_memory,
        "weekly-review": cmd_weekly_review,
    }

    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
