"""Command-line interface for Engineering Hub."""

import argparse
import logging
import sys
from datetime import date
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
    table.add_row("Notes File", str(settings.notes_file))
    table.add_row("Notes Exists", "✓" if settings.notes_file.exists() else "✗")
    table.add_row("Output Dir", str(settings.output_dir))
    table.add_row("Django API", settings.django_api_url)
    table.add_row("Django Token", "✓ Set" if settings.django_api_token else "✗ Not set")
    table.add_row("Anthropic Key", "✓ Set" if settings.anthropic_api_key else "✗ Not set")
    table.add_row("Model", settings.anthropic_model)

    console.print(table)

    # Show pending tasks if notes file exists
    if settings.notes_file.exists():
        from engineering_hub.notes.manager import SharedNotesManager

        manager = SharedNotesManager(
            settings.notes_file,
            use_journal_mode=settings.use_journal_mode,
            journal_categories=settings.journal_categories,
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
    }

    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
