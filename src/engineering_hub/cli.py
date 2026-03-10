"""Command-line interface for Engineering Hub."""

import argparse
import logging
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from engineering_hub.config.loader import find_config_file
from engineering_hub.config.settings import Settings
from engineering_hub.orchestration.orchestrator import Orchestrator

console = Console()


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


def cmd_start(args: argparse.Namespace) -> int:
    """Start the orchestrator."""
    settings = load_settings(args.config)

    # Validate required settings
    if not settings.anthropic_api_key:
        console.print(
            "[red]Error:[/red] Anthropic API key not set. "
            "Set ENGINEERING_HUB_ANTHROPIC_API_KEY or add to config."
        )
        return 1

    if not settings.django_api_token:
        console.print(
            "[yellow]Warning:[/yellow] Django API token not set. "
            "API calls will fail."
        )

    if not settings.notes_file.exists():
        console.print(
            f"[red]Error:[/red] Notes file not found: {settings.notes_file}\n"
            "Create the file or run 'engineering-hub init' to set up workspace."
        )
        return 1

    console.print("[bold green]Starting Engineering Hub...[/bold green]")
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
    table.add_row("Django Token", "✓ Set" if settings.django_api_token else "✗ Not set")
    table.add_row("Anthropic Key", "✓ Set" if settings.anthropic_api_key else "✗ Not set")
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

    if not settings.anthropic_api_key:
        console.print("[red]Error:[/red] Anthropic API key not set.")
        return 1

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

    if not settings.anthropic_api_key:
        console.print("[red]Error:[/red] Anthropic API key not set.")
        return 1

    days: int = args.days
    focus: str | None = args.focus

    # ------------------------------------------------------------------
    # 1. Read org-roam journal entries
    # ------------------------------------------------------------------
    from engineering_hub.notes.weekly_reader import OrgJournalReader

    journal_dir = settings.org_journal_dir
    if not journal_dir.exists():
        console.print(
            f"[yellow]Warning:[/yellow] Org journal directory not found: {journal_dir}\n"
            "Set journal.org_journal_dir in config.yaml or create the directory."
        )
        entries = []
    else:
        reader = OrgJournalReader(journal_dir)
        entries = reader.collect_week(days=days)
        console.print(
            f"[dim]Found {len(entries)} journal entries "
            f"in the last {days} days ({journal_dir})[/dim]"
        )

    journal_block = OrgJournalReader(journal_dir).format_context(entries) if entries else (
        "(No journal entries found for this period.)"
    )

    # ------------------------------------------------------------------
    # 2. Collect agent work from memory
    # ------------------------------------------------------------------
    from engineering_hub.memory import MemoryService

    memory_block = "(Memory service not available or no entries found.)"
    try:
        svc = MemoryService.from_workspace(
            workspace_dir=settings.workspace_dir,
            ollama_host=settings.ollama_host,
            ollama_model=settings.ollama_embed_model,
            enabled=settings.memory_enabled,
        )
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        recent_rows = svc.browse_recent(limit=100)
        week_rows = [
            r for r in recent_rows
            if (r.get("created_at") or "") >= cutoff
            and r.get("source") in ("task_output", "agent_message")
        ]

        if week_rows:
            mem_lines: list[str] = []
            for r in week_rows:
                day = (r.get("created_at") or "")[:10]
                agent = f"@{r['agent']}" if r.get("agent") else "agent"
                source_label = "Output" if r["source"] == "task_output" else "Message"
                proj = f" · project {r['project_id']}" if r.get("project_id") else ""
                mem_lines.append(f"**{source_label} · {agent}{proj} · {day}**")
                mem_lines.append(r["content"][:600].strip())
                mem_lines.append("")
            memory_block = "\n".join(mem_lines).rstrip()
            console.print(f"[dim]Found {len(week_rows)} agent work entries in memory[/dim]")
        else:
            memory_block = "(No agent work entries found in memory for this period.)"
            console.print("[dim]No agent work entries found in memory this week[/dim]")

        svc.db.close()
    except Exception as exc:
        console.print(f"[yellow]Warning:[/yellow] Could not read memory: {exc}")

    # ------------------------------------------------------------------
    # 3. Scan outputs/ directory for files modified this week
    # ------------------------------------------------------------------
    output_files_block = "(outputs/ directory not found or empty.)"
    try:
        cutoff_ts = datetime.combine(
            date.today() - timedelta(days=days), datetime.min.time()
        ).timestamp()
        output_dir = settings.output_dir
        if output_dir.exists():
            recent_outputs: list[str] = []
            for f in sorted(output_dir.rglob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
                if f.stat().st_mtime >= cutoff_ts:
                    rel = f.relative_to(output_dir)
                    mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
                    recent_outputs.append(f"- `outputs/{rel}` (modified {mtime})")
            if recent_outputs:
                output_files_block = "\n".join(recent_outputs)
                console.print(f"[dim]Found {len(recent_outputs)} output files modified this week[/dim]")
            else:
                output_files_block = "(No output files modified during this period.)"
    except Exception as exc:
        console.print(f"[yellow]Warning:[/yellow] Could not scan outputs: {exc}")

    # ------------------------------------------------------------------
    # 4. Build full context message
    # ------------------------------------------------------------------
    period_start = (date.today() - timedelta(days=days - 1)).isoformat()
    period_end = date.today().isoformat()

    context_parts = [
        f"Review period: {period_start} through {period_end} ({days} days)",
        "",
        "<journal_content>",
        journal_block,
        "</journal_content>",
        "",
        "<agent_work>",
        memory_block,
        "</agent_work>",
        "",
        "<output_files>",
        output_files_block,
        "</output_files>",
    ]
    if focus:
        context_parts += ["", f"USER FOCUS: {focus}"]

    context = "\n".join(context_parts)

    # ------------------------------------------------------------------
    # 5. Determine output path
    # ------------------------------------------------------------------
    if args.output:
        output_path = Path(args.output).expanduser()
    else:
        iso_year, iso_week, _ = date.today().isocalendar()
        output_path = settings.output_dir / "reviews" / f"weekly-{iso_year}-W{iso_week:02d}.md"

    # ------------------------------------------------------------------
    # 6. Call the weekly reviewer agent
    # ------------------------------------------------------------------
    from engineering_hub.agents.worker import AgentWorker

    worker = AgentWorker(
        api_key=settings.anthropic_api_key,
        model=settings.anthropic_model,
        prompts_dir=settings.prompts_dir,
        output_dir=settings.output_dir,
    )

    console.print("[bold]Running weekly reviewer...[/bold]")
    try:
        worker.run_weekly_review(context=context, output_path=output_path)
    except Exception as exc:
        console.print(f"[red]Error:[/red] Weekly review failed: {exc}")
        return 1

    console.print(f"\n[bold green]Weekly review complete![/bold green]")
    console.print(f"  Report: {output_path}")
    return 0


def cmd_mcp_server(args: argparse.Namespace) -> int:
    """Start the local MCP server."""
    from engineering_hub.mcp.server import run_server

    setup_logging(args.verbose)
    host = args.host
    port = args.port
    console.print(f"[bold green]Starting MCP server on {host}:{port}[/bold green]")
    console.print(
        f"[dim]Auth key: {os.environ.get('ENGINEERING_HUB_MCP_KEY', 'local-dev-key')}[/dim]"
    )
    run_server(host=host, port=port)
    return 0


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
    mcp_parser.add_argument("--host", default="127.0.0.1", help="Bind address")
    mcp_parser.add_argument("--port", type=int, default=3456, help="Port number")

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
        "memory": cmd_memory,
        "weekly-review": cmd_weekly_review,
    }

    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
